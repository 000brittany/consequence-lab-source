"""Executable deterministic existence witnesses, never autonomous policies."""
import argparse
import asyncio
import json
from pathlib import Path
from .archive import Archive
from .audit import verify_experiment
from .backends import ScriptedMock
from .clock import ManualClock
from .jsonio import digest,clone,canonical
from .rooms_v2 import RoomsV2,domain,task_specs
from .rooms_v2_manifest import generate,assemble,CELLS
from .rooms_v2_mocks import scripts,action
from .rooms_v2_reference import check_manifest,options
from .rooms_v2_run import new_run
from .runner import Runner


def book(a,p,room,time='09',disposition='yield_period'):
    return action('act',disposition,resource='/schedule',arguments={
        'operation':'book','appointment_id':f'p{p}:{a}','room_id':room,'time_id':time})


def diagnostic_templates():
    """Small legacy wiring prices, separate from generated office instances."""
    return {a:{'client_id':f'client:{a}','preferred_cell':'R0@09','usual_times':['09','10'],
        'usual_rooms':['R0','R1'],'room_setup_minutes':{'R0':0,'R1':1},
        'time_displacement_minutes':{'09':0,'10':0},'fallback_travel_minutes':6} for a in ('A','B')}


async def execute_witness(condition,strategy,seed=0,archive=None,priority_phase=0):
    m=generate(condition,seed,priority_phase=priority_phase)
    r=new_run(m,ScriptedMock(scripts(strategy)),clock=ManualClock(),archive=archive)
    await r.run(5);restored=Runner.restore(r.checkpoint())
    await restored.run(192);await r.run(192)
    assert restored.state==r.state and restored.records==r.records
    verification=verify_experiment(r.records,r.world);out=r.world.measure(r.state['world'])
    assert out['system']['completed']==24,(condition,strategy,out)
    if archive:
        r.checkpoint(Path(archive)/'checkpoint.json');Archive.read(archive)
    return {'condition':condition,'strategy':strategy,'seed':seed,'manifest_sha256':digest(m),
        'priority_phase':priority_phase,'generations':r.state['ledger']['calls'],'costs':{a:x['booking_cost'] for a,x in out['individual'].items()},
        'completed':out['system']['completed'],'restored_exact':True,'verification':verification,
        'archive_bytes':sum(p.stat().st_size for p in Path(archive).rglob('*') if p.is_file()) if archive else None,
        'mock_counter':r.backend.counter_id,'provider_calls':0}


def check_rule_boundary():
    """Independent reference options checked against every cell/fallback action.

    Exercise empty and peer-occupied states; this is a local transition oracle,
    not a policy optimizer or proof about arbitrary message content.
    """
    checked=0;assignments=0
    for condition in ('easy','difficult'):
        for seed in range(4):
            m=generate(condition,seed,periods=4);assignments+=check_manifest(m)['feasible_assignments_enumerated']
            for p in range(4):
                for a in ('A','B'):
                    for occupied in (None,*CELLS):
                        # Build a new current-period world without mutating a run.
                        w=domain(m);w.update(period=p+1,phase='active')
                        world=RoomsV2()
                        for actor,t in task_specs(m,p+1).items():world.add_task(w,actor,t['id'],t['payload'])
                        for c in m['external_reservations'][p]:w['cells'][c]={'kind':'external','owner':None,'id':f'external:{p+1}:{c}','responsive':False}
                        peer='B' if a=='A' else 'A'
                        if occupied and w['cells'][occupied] is None:
                            room,time=occupied.split('@');req=m['requests'][p][peer]
                            if time not in req['allowed_times'] or room not in req['allowed_rooms'] or time in req['blocked_times']:continue
                            world.apply(w,peer,'/schedule',{'operation':'book','appointment_id':f'p{p+1}:{peer}','room_id':room,'time_id':time})
                        client={'template':m['templates'][a],'current':m['requests'][p][a]}
                        legal={(r,t):price for r,t,price in options(client,a,w['cells'])}
                        for room in ('R0','R1',f'overflow:{a}'):
                            for time in ('09','10'):
                                shadow=clone(w);args={'operation':'book','appointment_id':f'p{p+1}:{a}','room_id':room,'time_id':time}
                                try:result=world.apply(shadow,a,'/schedule',args)
                                except ValueError:
                                    assert (room,time) not in legal;assert shadow==w
                                else:
                                    assert (room,time) in legal and result['quoted_cost']==legal[room,time]
                                    world.validate(shadow,['A','B'])
                                checked+=1
    return {'transitions_checked':checked,'assignments_enumerated':assignments,'status':'PASS'}


async def structural_fixture(archive=None):
    """Authored causal wiring across tasks, including eviction and re-exposure.

    This is not a proof that the text is needed by an optimal agent. The
    counterfactual compares this fixed mock's responses to changed read content.
    """
    m=generate(periods=3);m=assemble('difficult',diagnostic_templates(),[
        {a:{'allowed_times':['09','10'],'blocked_times':[]} for a in ('A','B')} for _ in range(3)])
    marker='author-created-rule-v1-431';path='/home/kept.txt'
    programs={'A':[action('write',path=path,text=marker),book('A',1,'overflow:A'),
        action('inspect','yield_tick',path='/home'),action('read',path=path),
        {'__if_read_contains':{'contains':marker,'then':book('A',2,'R1','10'),'else':book('A',2,'overflow:A')}},
        action('write','yield_tick',path=path,text='revised-rule-v2'),action('read',path=path),book('A',3,'overflow:A')],
        'B':[book('B',p,'overflow:B') for p in (1,2,3)]}
    r=new_run(m,ScriptedMock(programs),clock=ManualClock(),archive=archive,recent_cycles=1)
    # Creation, booking, B's booking, then cross-period inspection: original
    # write is outside A's one-cycle fixture context before the explicit read.
    while True:
        rec=await r.step()
        if rec['agent']=='A' and rec['computation']['selected_action']['tool']=='read':break
    # Branch immediately before the read, preserving all histories/world/budgets.
    read=rec;saved_records=r.records[:-1];state=read['state_before']
    # Recover the genuine checkpoint from the preceding preserved boundary.
    # Mock cursor and manual clock are both reconstructed from that snapshot.
    from .archive import seal
    from .provenance import development_record
    cp=seal({'schema_version':1,'state':state,'records':saved_records,
             'clock':{'kind':'manual','elapsed':state['ledger']['elapsed_seconds']},'provenance':development_record()})
    control=Runner.restore(cp);changed=Runner.branch(cp,{'actor':'A','path':path,'text':'neutral replacement'})
    await control.run(30);await changed.run(30);await r.run(30)
    assert control.state==r.state
    difference=changed.world.measure(changed.state['world'])['individual']['A']['booking_cost']-r.world.measure(r.state['world'])['individual']['A']['booking_cost']
    assert difference==5
    own=[x for x in r.records if x['event_type']=='decision.completed' and x['agent']=='A']
    read=next(x for x in own if x['computation']['selected_action']['tool']=='read')
    following=own[own.index(read)+1]
    assert marker not in canonical(read['generation']['request']).decode()
    assert marker in canonical(following['generation']['request']).decode()
    assert following['registration']['supplied_artifacts'][0]['revision']==1
    for record in r.records:
        if record.get('agent')=='B':assert marker not in canonical(record['generation']['request']).decode()
    verification=verify_experiment(r.records,r.world);verify_experiment(changed.records,changed.world)
    if archive:
        r.checkpoint(Path(archive)/'checkpoint.json');Archive.read(archive)
        changed_path=Path(archive).with_name(Path(archive).name+'-counterfactual')
        changed.archive=Archive(changed_path)
        for record in changed.records:changed.archive.commit(record)
        changed.checkpoint(changed_path/'checkpoint.json');Archive.read(changed_path)
    return {'scope':'Authored mock causal response; no spontaneous representation or optimal-memory claim',
        'counterfactual_A_final_cost_difference':difference,'verification':verification,
        'read_decision':read['decision_id'],'exposure_decision':following['decision_id'],'provider_calls':0}


async def validate(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    manifests=[generate(c,s,priority_phase=p) for s in range(4) for p in (0,1) for c in ('easy','difficult')]
    reports=[check_manifest(m) for m in manifests]
    (output/'manifests.json').write_text(json.dumps(manifests,indent=2,sort_keys=True))
    rows=[]
    for condition in ('easy','difficult'):
        for strategy in ('independent','fallback','exchange','delta','wait_narrow'):
            rows.append(await execute_witness(condition,strategy,archive=output/f'{condition}-{strategy}'))
    indexed={(x['condition'],x['strategy']):x for x in rows}
    # Per-client benefits are empirical results, not universal generator promises.
    # The prespecified panel reports all signs, including losses from efficient allocation.
    for c in ('easy','difficult'):
        assert indexed[c,'delta']['costs']==indexed[c,'exchange']['costs']
        assert indexed[c,'delta']['generations']<indexed[c,'exchange']['generations']
    from .config import implementation_fingerprint
    report={'implementation':implementation_fingerprint(),'status':'PASS','provider_calls':0,'scope':'Mock existence witnesses, not scientific behavior or dialogue optimality',
        'rule_boundary':check_rule_boundary(),'manifest_count':len(manifests),'reference_assignments':sum(x['feasible_assignments_enumerated'] for x in reports),
        'runs':rows,'structural_fixture':await structural_fixture(output/'structure'),
        'cost_convention':'No financial information fee; extra generations, opportunity use and deadlines are recorded separately.'}
    (output/'validation.json').write_text(json.dumps(report,indent=2,sort_keys=True));return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);a=p.parse_args()
    print(json.dumps(asyncio.run(validate(a.output)),indent=2,sort_keys=True))


if __name__=='__main__':main()

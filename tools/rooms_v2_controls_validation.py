"""Independent physical boundaries and complete control-policy archives; no provider."""
import argparse
import asyncio
import json
from pathlib import Path
from consequence_lab.archive import Archive
from consequence_lab.audit import verify_experiment
from consequence_lab.backends import ScriptedMock
from consequence_lab.clock import ManualClock
from consequence_lab.jsonio import clone,canonical
from consequence_lab.rooms_v2 import RoomsV2,domain,task_specs
from consequence_lab.rooms_v2_manifest import generate,CELLS
from consequence_lab.rooms_v2_mocks import action
from consequence_lab.rooms_v2_reference import options,check_manifest
from consequence_lab.rooms_v2_run import new_run
from consequence_lab.runner import Runner


def boundaries():
    checked=assignments=0
    for level in ('low','moderate','high'):
        for seed in range(4):
            m=generate(level,seed,periods=4);assignments+=check_manifest(m)['feasible_assignments_enumerated']
            for p in range(4):
                for actor in ('A','B'):
                    for occupied in (None,*CELLS):
                        w=domain(m);w.update(period=p+1,phase='active');world=RoomsV2()
                        for a,t in task_specs(m,p+1).items():world.add_task(w,a,t['id'],t['payload'])
                        for c in m['external_reservations'][p]:w['cells'][c]={'kind':'external','owner':None,'id':f'external:{p+1}:{c}','responsive':False}
                        peer='B' if actor=='A' else 'A'
                        if occupied and w['cells'][occupied] is None:
                            room,time=occupied.split('@');req=m['requests'][p][peer]
                            if room not in req['allowed_rooms'] or time not in req['allowed_times'] or time in req['blocked_times']:continue
                            world.apply(w,peer,'/schedule',{'operation':'book','appointment_id':f'p{p+1}:{peer}','room_id':room,'time_id':time})
                        legal={(room,time):price for room,time,price in options({'template':m['templates'][actor],'current':m['requests'][p][actor]},actor,w['cells'])}
                        for room in ('R0','R1',f'overflow:{actor}'):
                            for time in ('09','10'):
                                shadow=clone(w)
                                try:result=world.apply(shadow,actor,'/schedule',{'operation':'book','appointment_id':f'p{p+1}:{actor}','room_id':room,'time_id':time})
                                except ValueError:
                                    assert (room,time) not in legal and shadow==w
                                else:
                                    assert result['quoted_cost']==legal[room,time];world.validate(shadow,['A','B'])
                                checked+=1
    return {'status':'PASS','levels':['low','moderate','high'],'transitions_checked':checked,
            'finite_assignments_enumerated':assignments,'scope':'Independent cost/eligibility/capacity oracle; not a hidden-information policy'}


async def fixture(level,mode,path):
    m=generate(level,3,periods=4,communication=mode);private='/home/kept.txt';shared='/volumes/common/publication.txt'
    def book(a,p):return action('act','yield_period',resource='/schedule',arguments={
        'operation':'book','appointment_id':f'p{p}:{a}','room_id':f'overflow:{a}',
        'time_id':m['requests'][p-1][a]['allowed_times'][0]})
    marker='authored-private-structure-v1';revised='authored-private-structure-v2';body='authored-publication-body'
    program={'A':[action('write',path=private,text=marker),book('A',1),
        action('read','yield_tick',path=private),action('read',path=shared),book('A',2),
        action('write',path=private,text=revised),book('A',3),action('read',path=private),book('A',4)],
        'B':[action('write',path=shared if mode!='disabled' else '/home/status',text=body),book('B',1),
             action('write',path=shared if mode!='disabled' else '/home/status',text=body+'-v2'),book('B',2),
             action('remove',path=shared if mode!='disabled' else '/home/status'),book('B',3),
             action('write',path=shared if mode!='disabled' else '/home/status',text=body+'-v3'),book('B',4)]}
    r=new_run(m,ScriptedMock(program),clock=ManualClock(),archive=path,recent_cycles=1)
    await r.run(1);cp=r.checkpoint();restored=Runner.restore(cp)
    branch=Runner.branch(cp,{'actor':'A','path':private,'text':'controlled replacement'})
    await r.run(64);await restored.run(64);await branch.run(64)
    assert r.state==restored.state and r.records==restored.records
    assert r.state['status']=='finished' and r.world.measure(r.state['world'])['system']['completed']==8
    own=[x for x in r.records if x.get('agent')=='A'];private_reads=[x for x in own if x['computation']['selected_action']['tool']=='read' and x['computation']['selected_action']['arguments']['path']==private]
    assert private_reads[0]['consequences']['memory_operations'][0]['revision']==1
    assert private_reads[-1]['consequences']['memory_operations'][0]['revision']==2
    assert marker not in canonical(private_reads[0]['generation']['request']).decode()
    assert revised in canonical(own[-1]['generation']['request']).decode()
    public_read=next(x for x in own if x['computation']['selected_action']['tool']=='read' and x['computation']['selected_action']['arguments']['path']==shared)
    following=own[own.index(public_read)+1]
    if mode=='disabled':
        assert public_read['consequences']['tool_response']['status']=='rejected'
        assert body not in canonical(following['generation']['request']).decode()
    else:
        assert public_read['consequences']['memory_operations'][0]['revision']==2
        assert body+'-v2' in canonical(following['generation']['request']).decode()
    assert all(('shared_updates' in x['registration']['observation'])==(mode=='public_metadata') for x in own)
    verify_experiment(r.records,r.world);verify_experiment(branch.records,branch.world)
    r.checkpoint(path/'checkpoint.json');Archive.read(path)
    changed=path.with_name(path.name+'-counterfactual');branch.archive=Archive(changed)
    for record in branch.records:branch.archive.commit(record)
    branch.checkpoint(changed/'checkpoint.json');Archive.read(changed)
    return {'pressure':level,'communication':mode,'archive':str(path.name),'branch_archive':changed.name,
            'completed':8,'generations':r.state['ledger']['calls'],'restored_exact':True,
            'verification':verify_experiment(r.records,r.world),'provider_calls':0,
            'interpretation':'Authored creation/read/revision/exposure and branch; no spontaneous representation claim',
            'archive_bytes':sum(p.stat().st_size for p in path.rglob('*') if p.is_file())}


async def validate(output):
    output.mkdir(parents=True,exist_ok=False);result={'status':'PASS','provider_calls':0,'boundaries':boundaries(),'fixtures':[]}
    for level in ('low','moderate','high'):
        for mode in ('disabled','public_poll','public_metadata'):
            result['fixtures'].append(await fixture(level,mode,output/(level+'-'+mode)))
    (output/'validation.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n');return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    print(json.dumps(asyncio.run(validate(args.output)),indent=2,sort_keys=True))


if __name__=='__main__':main()

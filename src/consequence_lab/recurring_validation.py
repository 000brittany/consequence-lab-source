"""Independent finite-policy verification plus actual runner execution. Mock only."""
from fractions import Fraction

from . import reference_policy
from .audit import verify_experiment
from .backends import ScriptedMock, input_bytes
from .catalogue import MASKS, catalogue, manifest
from .clock import ManualClock
from .jsonio import canonical, clone, digest
from .recurring import RecurringRooms, new_run
from .recurring_mocks import scripts


def check_reference():
    bases={}
    for r,mask in zip((-1,0,1),MASKS):
        run=new_run(manifest(mask=mask,periods=1,scope='diagnostic'),clock=ManualClock());run.settle()
        bases[r]=run.state['world']
    checked=0
    def check(before,actor,action,after,result):
        nonlocal checked
        if action=='wait': return
        w=clone(bases[before.restricted]);world=RecurringRooms()
        w['rooms']={f'R{i}':f'p1:{a}' if a else None for i,a in enumerate(before.slots)}
        for i,a in enumerate(('A','B')):
            app=w['appointments'][f'p1:{a}']
            if before.done[i]:
                room=next((f'R{j}' for j,x in enumerate(before.slots) if x==a),f'overflow:{a}')
                app.update(status='completed',room=room,cost=before.cost[i])
        original=clone(w)
        try:
            got=world.apply(w,actor,'/schedule',{'operation':'book','appointment_id':f'p1:{actor}',
                'room_id':f'overflow:{actor}' if action=='F' else action})
            assert result=='accepted' and got['status']=='ok'
        except ValueError as exc:
            expected='Appointment already terminal' if result=='already_completed' else result
            assert str(exc)==expected,(str(exc),expected)
            assert w==original,'Rejected booking applied partial effects'
        assert tuple(w['rooms'][r].split(':')[-1] if w['rooms'][r] else None for r in ('R0','R1'))==after.slots
        assert tuple(w['appointments'][f'p1:{a}']['cost'] for a in ('A','B'))==after.cost
        world.validate(w,['A','B']);checked+=1
    report=reference_policy.verify_all(check)
    report['implemented_booking_transitions_checked']=checked
    report['scope']='Finite three-state, one-period witness; not arbitrary LLM or communication policies'
    return report


async def execute_witness(strategy,mask,policy='candidate',information=1):
    m=manifest(mask=mask,periods=1,policy=policy,information=information,scope='diagnostic')
    r=new_run(m,backend=ScriptedMock(scripts(m['order'],strategy,max_calls=20)),clock=ManualClock())
    await r.run(50)
    assert r.state['status']=='finished'
    verification=verify_experiment(r.records,r.world)
    out=r.world.measure(r.state['world'])
    assert all(x['completed']==1 for x in out['individual'].values())
    decisions=[x for x in r.records if x['event_type']=='decision.completed']
    assert len(decisions)==r.state['ledger']['calls']
    for record in decisions:
        assert input_bytes(record['generation']['request'])==record['accounting']['usage']['input_tokens']
    assert r.state==type(r).restore(r.checkpoint()).state
    return {'mask':mask,'strategy':strategy,'policy':policy,'information':information,
            'A':out['individual']['A']['booking_cost'],'B':out['individual']['B']['booking_cost'],
            'generations':r.state['ledger']['calls'],'input_tokens':r.state['ledger']['input_tokens'],
            'output_tokens':r.state['ledger']['output_tokens'],'token_counter':r.backend.counter_id,
            'decision_ids':[x['decision_id'] for x in decisions],'verification':verification}


async def witnesses():
    ref=check_reference();rows=[]
    targets={'R0':([0,6,0],[0,0,6],[2,3,3]),'R1':([2,2,0],[2,6,6],[2,3,4]),
             'read':([0,2,0],[0,6,6],[3,4,4])}
    for strategy,(ac,bc,n) in targets.items():
        for i,mask in enumerate(MASKS):
            row=await execute_witness(strategy,mask)
            assert (row['A'],row['B'],row['generations'])==(ac[i],bc[i],n[i]),row
            rows.append(row)
    sensitivity=[]
    for policy,q in [('legacy',1),('candidate',1),('candidate',2),('candidate',3)]:
        group=[await execute_witness('read',mask,policy,q) for mask in MASKS]
        expected=([0,6,0],[0,0,6],[3,3,4]) if policy=='legacy' else targets['read']
        assert [r['A'] for r in group]==expected[0]
        assert [r['B'] for r in group]==expected[1]
        assert [r['generations'] for r in group]==expected[2]
        sensitivity.extend(group)
    means={strategy:{key:str(sum(Fraction(r[key]) for r in rows if r['strategy']==strategy)/3)
                     for key in ('A','B','generations')} for strategy in targets}
    negative=[]
    for strategy in ('R0','late_read'):
        m=manifest('L',periods=1,scope='diagnostic')
        r=new_run(m,backend=ScriptedMock(scripts(m['order'],strategy,max_calls=20)),clock=ManualClock())
        await r.run(30);verify_experiment(r.records,r.world)
        out=r.world.measure(r.state['world'])['individual']
        negative.append({'strategy':strategy,'A':out['A']['booking_cost'],'B':out['B']['booking_cost'],'generations':r.state['ledger']['calls']})
    assert negative==[{'strategy':'R0','A':0,'B':2,'generations':2},{'strategy':'late_read','A':2,'B':0,'generations':4}]
    return {'status':'PASS','paid_model_calls':0,'reference':ref,'integrated':rows,'sensitivity':sensitivity,'means':means,'late_notice_control':negative}


async def validate_catalogue(periods=4):
    manifests=catalogue(periods=periods);reports=[]
    assert len(manifests)==40 and canonical(manifests)==canonical(catalogue(periods=periods))
    for m in manifests:
        r=new_run(m,clock=ManualClock());await r.run(1000)
        assert r.state['status']=='finished'
        assert all(t['status']=='completed' for t in r.state['tasks'].values())
        report=verify_experiment(r.records,r.world)
        assert r.state==type(r).restore(r.checkpoint()).state
        reports.append({'fixture_id':m['fixture_id'],'manifest_sha256':digest(m),'generations':r.state['ledger']['calls'],
                        'completed':r.world.measure(r.state['world'])['system']['completed'],'verification':report})
    return {'status':'PASS','count':len(reports),'periods':periods,'manifests_sha256':digest(manifests),'runs':reports,
            'scope':'Deterministic validation catalogue, not a scientific sample','paid_model_calls':0}

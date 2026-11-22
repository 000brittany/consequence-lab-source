"""Fast source-domain experiments, explicitly not model/Runner context validation."""
import hashlib
import json
from collections import Counter
from copy import deepcopy as cp
from pathlib import Path
from consequence_lab import direct_messages
from consequence_lab.backends import ScriptedMock
from consequence_lab.jsonio import canonical
from consequence_lab_mixed.run import new_run
from consequence_lab_mixed.world import MixedRooms,clock,period
from tools.mixed_policies import Policy


def simulate(m,policy,*,capture=None):
    runner=new_run(m,ScriptedMock());s=runner.state;w=s['world'];world=runner.world
    agents={a:Policy(a,policy,m['parameters'] if policy=='learn_law' else None) for a in s['agents']};decisions=0;errors=Counter();diagnostics=Counter();daily=Counter();observations=[];trace=[]
    # One day-end snapshot for exposure/forecast datasets. No hidden manifest is passed to a policy.
    while s['status']=='running':
        event=world.next_event(s)
        before=cp(s) if capture else None
        if event:
            if event['kind']=='day.close':
                observations.append({'day':w['day'],'public':world.observe(w,'A')['calendar'],
                    'own':{a:cp(world.observe(w,a)['own_outstanding_requests']) for a in agents},
                    'all_released':cp(list(w['requests'].values()))})
            result=world.apply_event(s,event);item={'type':'environment','event':event,'result':result}
        else:
            actor=s['scheduler']['order'][s['scheduler']['cursor']]
            o=world.observe(w,actor);o.update(direct_messages.observe(s,actor))
            if policy=='learn_oracle':
                # Privileged sensitivity control: perfect remaining emergency counts,
                # NOT a causal learner, law-knowing policy or admissible agent input.
                o['oracle_pressure']={str(p):sum(q['owner']==actor and q['kind']=='emergency' and bool(q['technician'] or q['equipment']) and
                    max(clock(w),(p-1)*20)<q['arrival_clock']<p*20 for q in m['requests']) for p in range(1,13)}
            if policy in ('pooled','clairvoyant'):
                o['calendar']=cp(list(w['reservations'].values()))
                o['own_outstanding_requests']=[cp(r) for r in w['requests'].values() if r['status']=='pending']
            if policy=='clairvoyant':
                allr={r['id']:cp(r) for r in m['requests'] if r['arrival_clock']<=clock(w)+40 and max(d for d,t in r['slots'])<=w['day']+10 and (r['id'] not in w['requests'] or w['requests'][r['id']]['status']=='pending')}
                for rid,r in w['requests'].items():
                    if r['status']=='pending':allr[rid]=cp(r)
                o['planning_requests']=[r for rid,r in allr.items() if rid not in w['reservations']]
            a=agents[actor].decide(cp(o))
            world.validate_action(w,actor,a)
            stamp=clock(w)
            try:result=world.apply(w,actor,a['arguments']['resource'],a['arguments']['arguments'])
            except ValueError as e:result={'status':'rejected','error':str(e)};errors[str(e)]+=1
            prior={'world':{'manifest':m,'control':cp(w['control'])},'config':s['config']}
            # The scheduling control before apply is unchanged by domain actions.
            world.finish_decision(prior,s,actor,a,None,[],result)
            s['agents'][actor]['history']=[{'messages':[{'content':json.dumps({'direct_messages':o['direct_messages']})}]}]
            decisions+=1;daily[w['day']]+=1
            item={'type':'decision','actor':actor,'observation_sha256':hashlib.sha256(canonical(o)).hexdigest(),'action':a,'result':result}
            if capture:item['observation']=o
        trace.append(item)
        if capture:capture(before,s,item,{a:cp(p.memory) for a,p in agents.items()})
        if decisions>2160:raise ValueError('generation ceiling exceeded')
    world.validate(w,s['agents'])
    for p in agents.values():diagnostics.update(p.diagnostics)
    counts=Counter(o['status'] for o in w['outcomes']);reasons=Counter(o['reason'] for o in w['outcomes'] if o['status']=='failed')
    pair=Counter((x['sender'],x['recipient']) for x in w['direct_messages'])
    return {'policy':policy,'seed':m['seed'],'allocation':m['allocations_cents'][0],'parameters':m['parameters'],
        'completed':counts['completed'],'failed':counts['failed'],'cancelled':counts['cancelled'],
        'by_agent':{a:dict(Counter(o['status'] for o in w['outcomes'] if o['owner']==a)) for a in agents},
        'failures':dict(reasons),'decisions':decisions,'max_daily_decisions':max(daily.values()),'errors':dict(errors),
        'messages':len(w['direct_messages']),'max_per_pair':max(pair.values(),default=0),'diagnostics':dict(diagnostics),
        'external_services':sum(o['status']=='completed' and o['reservation']['room_id'].startswith('external:') for o in w['outcomes']),
        'spent':sum(a['spent'] for a in w['accounts'].values()),'expired':sum(a['expired'] for a in w['accounts'].values()),
        'accounts':cp(w['accounts']),'outcomes':cp(w['outcomes']),'trace':trace,'snapshots':observations,'learning':{a:p.learner.forecasts for a,p in agents.items()}}

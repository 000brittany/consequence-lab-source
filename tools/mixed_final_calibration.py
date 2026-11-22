"""Final offline comparison on unchanged materialized mixed-v1 workloads."""
import argparse,gzip,hashlib,json,time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy as cp
from pathlib import Path
from consequence_lab import direct_messages
from consequence_lab.backends import ScriptedMock
from consequence_lab.jsonio import canonical
from consequence_lab_mixed.manifest import generate
from consequence_lab_mixed.run import new_run
from tools.mixed_final_policy import Planner
from tools.mixed_policies import Policy
from tools.mixed_reference import check_world,check_transition

def policy(actor,condition,timing):
    if condition=='reactive':return Policy(actor,'reactive')
    if condition in ('early','due','solo','oracle','pooled'):
        return Planner(actor,timing=condition if condition in ('early','due') else timing)
    if condition.startswith('conditional_'):
        _,group,decline=condition.split('_');return Planner(actor,timing=timing,group=group,accommodation=True,reciprocal=True,decline='' if decline=='none' else decline)
    kind,group=condition.split('_')
    return Planner(actor,timing=timing,group=group,information=kind!='sham',accommodation=kind=='coop')

def projection(s,world,actor,condition):
    w=s['world'];o=world.observe(w,actor);o.update(direct_messages.observe(s,actor));now=o['decision_clock_tick']
    if condition=='pooled':
        o['pooled_requests']=[cp(r) for r in w['requests'].values() if r['status']=='pending' and r['client_status']=='confirmed' and r['id'] not in w['reservations']]
    elif condition=='oracle':
        # Exact upcoming own arrival cards, not revisions: deliberately privileged
        # seven-day information-value bound, never supplied to an admissible policy.
        o['pooled_requests']=[cp(r) for r in w['requests'].values() if r['owner']==actor and r['status']=='pending' and r['id'] not in w['reservations']]
        o['pooled_requests'] += [cp(r) for r in w['manifest']['requests'] if r['owner']==actor and now<r['arrival_clock']<=now+28]
    return o

def simulate(m,condition,timing='early',independent=False):
    r=new_run(m,ScriptedMock());s=r.state;w=s['world'];world=r.world;agents={a:policy(a,condition,timing) for a in 'ABC'};trace=[];errors=Counter();daily=Counter();checks=0
    while s['status']=='running':
        before=cp(s) if independent else None;e=world.next_event(s)
        if e:
            answer=world.apply_event(s,e);item={'type':'environment','event':e,'result':answer}
            if e['kind']=='day.close':check_world(w);checks+=1
        else:
            actor=s['scheduler']['order'][s['scheduler']['cursor']];o=projection(s,world,actor,condition);cmd=agents[actor].decide(cp(o));world.validate_action(w,actor,cmd)
            prior={'world':{'manifest':m,'control':cp(w['control'])},'config':s['config']};args=cmd['arguments']
            try:answer=world.apply(w,actor,args['resource'],args['arguments'])
            except ValueError as exc:answer={'status':'rejected','error':str(exc)};errors[str(exc)]+=1
            item={'type':'decision','actor':actor,'observation_sha256':hashlib.sha256(canonical(o)).hexdigest(),'action':cmd,'result':answer}
            if independent:check_transition(before,s,item)
            world.finish_decision(prior,s,actor,cmd,None,[],answer)
            s['agents'][actor]['history']=[{'messages':[{'content':json.dumps({'direct_messages':o['direct_messages']})}]}];daily[w['day']]+=1
        if independent and e:check_transition(before,s,item)
        trace.append(item)
    world.validate(w,s['agents']);check_world(w)
    outcomes=Counter(x['status'] for x in w['outcomes']);diag=Counter()
    for agent in agents.values():diag.update(agent.diagnostics)
    messages=w['direct_messages'];pairs=Counter((x['sender'],x['recipient']) for x in messages)
    result={'condition':condition,'timing':timing,'seed':m['seed'],'allocation':m['allocations_cents'][0],**{k:outcomes[k] for k in ('completed','failed','cancelled')},
        'by_agent':{a:dict(Counter(x['status'] for x in w['outcomes'] if x['owner']==a)) for a in 'ABC'},
        'outcomes':cp(w['outcomes']),'accounts':cp(w['accounts']),'spent':sum(a['spent'] for a in w['accounts'].values()),
        'external':sum(x['status']=='completed' and x['reservation']['room_id'].startswith('external:') for x in w['outcomes']),
        'messages':len(messages),'message_bytes':sum(len(x['text'].encode()) for x in messages),'max_pair':max(pairs.values(),default=0),
        'decisions':sum(daily.values()),'max_daily':max(daily.values()),'errors':dict(errors),'diagnostics':dict(diag),
        'independent_day_end_checks':checks,'independent_transition_checks':len(trace) if independent else 0,
        'final_world_sha256':hashlib.sha256(canonical(w)).hexdigest()}
    return {'manifest':m,'result':result,'trace':trace,'scope':'offline authored control, no model contexts or autonomous structure claims'}

def replay(data,independent=False):
    r=new_run(data['manifest'],ScriptedMock());s=r.state;w=s['world'];world=r.world;checks=0
    for item in data['trace']:
        before=cp(s) if independent else None
        if item['type']=='environment':
            assert world.next_event(s)==item['event'];assert world.apply_event(s,item['event'])==item['result']
        else:
            assert world.next_event(s) is None;actor=s['scheduler']['order'][s['scheduler']['cursor']];assert actor==item['actor']
            o=projection(s,world,actor,data['result']['condition']);assert hashlib.sha256(canonical(o)).hexdigest()==item['observation_sha256']
            cmd=item['action'];world.validate_action(w,actor,cmd);args=cmd['arguments'];prior={'world':{'manifest':w['manifest'],'control':cp(w['control'])},'config':s['config']}
            try:answer=world.apply(w,actor,args['resource'],args['arguments'])
            except ValueError as exc:answer={'status':'rejected','error':str(exc)}
            assert answer==item['result']
            if independent:check_transition(before,s,item)
            world.finish_decision(prior,s,actor,cmd,None,[],answer);s['agents'][actor]['history']=[{'messages':[{'content':json.dumps({'direct_messages':o['direct_messages']})}]}]
        if independent and item['type']=='environment':check_transition(before,s,item)
        checks+=1
    assert s['status']=='finished' and w['outcomes']==data['result']['outcomes'];check_world(w)
    assert hashlib.sha256(canonical(w)).hexdigest()==data['result']['final_world_sha256']
    return {'transitions':checks,'independent':independent,'status':'passed'}

def job(spec):
    seed,fund,condition,timing,out=spec;start=time.monotonic();m=generate(seed,allocation_cents=fund);data=simulate(m,condition,timing)
    # Every case is source-replayed; independent financial/capacity checks are daily.
    audit=replay(data);data['audit']=audit
    path=Path(out)/'traces'/f'{seed}-{fund}-{condition}.json.gz';path.write_bytes(gzip.compress(json.dumps(data,separators=(',',':')).encode(),mtime=0))
    result={k:v for k,v in data['result'].items() if k!='outcomes'};result.update(trace_file=path.name,trace_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),seconds=time.monotonic()-start)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--seeds',nargs='+',type=int,required=True);p.add_argument('--funds',nargs='+',type=int,required=True);p.add_argument('--conditions',nargs='+',required=True);p.add_argument('--timing',default='early');p.add_argument('--workers',type=int,default=2);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);(a.output/'traces').mkdir();rows=[];specs=[(s,b,c,a.timing,str(a.output)) for s in a.seeds for b in a.funds for c in a.conditions];start=time.monotonic()
    with ProcessPoolExecutor(a.workers) as pool:
        for result in pool.map(job,specs):
            rows.append(result);(a.output/'results.json').write_text(json.dumps({'rows':rows,'conditions':a.conditions,'seeds':a.seeds,'funds':a.funds,'timing':a.timing,'seconds':time.monotonic()-start},indent=2)+'\n');print(result['seed'],result['allocation'],result['condition'],result['completed'],round(result['seconds'],1),flush=True)

"""Replay saved domain traces, verify exact inputs and separate finance/client rules."""
import argparse,gzip,hashlib,json
from copy import deepcopy as cp
from pathlib import Path
from collections import Counter
from consequence_lab.backends import ScriptedMock
from consequence_lab import direct_messages
from consequence_lab.jsonio import canonical
from consequence_lab_mixed.run import new_run
from consequence_lab_mixed.world import clock
from tools.mixed_reference import check_transition
from tools.mixed_incentives import mutual_opportunities


def replay(path,*,independent=True,exchange=False,compatibility=False):
 d=json.loads(gzip.decompress(path.read_bytes()));m=d['manifest'];legacy_sha=m['prototype_sha256']
 if compatibility:
  from consequence_lab_mixed.manifest import generate
  current=generate(m['seed'],allocation_cents=m['allocations_cents'][0],**m['parameters'])
  a,b=cp(m),cp(current)
  for x in (a,b):x.pop('prototype_sha256',None);x.pop('origin',None)
  assert a==b,'Scientific manifest changed beyond declared source/origin metadata'
  m=current
 r=new_run(m,ScriptedMock());s=r.state;w=s['world'];world=r.world
 checks=0;inputs=0;maxinfo=0;maxdomain=0;usage=Counter();opps=[];affordability=Counter();futurebookings=0;lateamend=0
 for index,item in enumerate(d['trace']):
  before=cp(s) if independent else None
  ev=world.next_event(s)
  if item['type']=='environment':
   assert ev==item['event'];answer=world.apply_event(s,ev);assert answer==item['result']
   if ev['kind']=='potential.event':
    e=answer['event']
    if e['kind']=='amend' and answer['applicable']:
     original=next(q for q in m['requests'] if q['id']==e['appointment_id'])
     lateamend+=e['clock']//4+1>min(day for day,t in original['slots'])
  else:
   assert ev is None
   actor=s['scheduler']['order'][s['scheduler']['cursor']];assert actor==item['actor']
   o=world.observe(w,actor);o.update(direct_messages.observe(s,actor))
   if d['result']['policy']=='learn_oracle':
    o['oracle_pressure']={str(p):sum(q['owner']==actor and q['kind']=='emergency' and bool(q['technician'] or q['equipment']) and max(clock(w),(p-1)*20)<q['arrival_clock']<p*20 for q in m['requests']) for p in range(1,13)}
   if d['result']['policy'] in ('pooled','clairvoyant'):
    o['calendar']=cp(list(w['reservations'].values()));o['own_outstanding_requests']=[cp(q) for q in w['requests'].values() if q['status']=='pending']
   if d['result']['policy']=='clairvoyant':
    allr={q['id']:cp(q) for q in m['requests'] if q['arrival_clock']<=clock(w)+40 and max(day for day,t in q['slots'])<=w['day']+10 and (q['id'] not in w['requests'] or w['requests'][q['id']]['status']=='pending')}
    for rid,q in w['requests'].items():
     if q['status']=='pending':allr[rid]=cp(q)
    o['planning_requests']=[q for rid,q in allr.items() if rid not in w['reservations']]
   assert hashlib.sha256(canonical(o)).hexdigest()==item['observation_sha256'];inputs+=1
   cmd=item['action'];world.validate_action(w,actor,cmd);args=cmd['arguments'];payload=args['arguments']
   if args['resource']=='/schedule' and payload['operation'] in ('book','reschedule','hold'):
    futurebookings+=payload['day']>w['day'];p=str((payload['day']-1)//5+1)
    if payload['room_id'].startswith('external:'):affordability['external_attempts']+=1;affordability['external_affordable']+=w['accounts'][p]['available']>=7000
   if exchange and args['resource']=='/messages':
    try:packet=json.loads(payload['text'])
    except ValueError:packet={}
    if packet.get('kind')=='need':opps.extend(mutual_opportunities(s))
   prior={'world':{'manifest':m,'control':cp(w['control'])},'config':s['config']}
   try:answer=world.apply(w,actor,args['resource'],payload)
   except ValueError as e:answer={'status':'rejected','error':str(e)}
   assert answer==item['result']
   if independent:
    interim=cp(s);check_transition(before,interim,item)
   from consequence_lab.opportunities import category
   cat=category(cmd,w);usage[(w['day'],w['tick'],actor,cat)]+=1
   world.finish_decision(prior,s,actor,cmd,None,[],answer)
   s['agents'][actor]['history']=[{'messages':[{'content':json.dumps({'direct_messages':o['direct_messages']})}]}]
  if independent and item['type']=='environment':check_transition(before,s,item)
  checks+=1
 assert s['status']=='finished';world.validate(w,s['agents'])
 assert w['outcomes']==d['result']['outcomes']
 for (day,tick,a,cat),n in usage.items():
  assert n<=(2 if cat=='information' else 1 if cat=='domain' else 3)
 return {'file':str(path.name),'transitions':checks,'actor_inputs':inputs,'independent_rules':independent,
     'future_booking_decisions':futurebookings,'late_applicable_amendments':lateamend,'fallback':dict(affordability),
     'exchange_candidates':opps,'legacy_prototype_sha256':legacy_sha,'replay_prototype_sha256':m['prototype_sha256'],'compatibility':compatibility,'scope':'domain replay, independent arithmetic/eligibility/event-causation; no model/Runner accounting'}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--limit',type=int,default=0);p.add_argument('--exchange',action='store_true');p.add_argument('--fast',action='store_true');p.add_argument('--compatibility',action='store_true');a=p.parse_args()
 a.output.mkdir(parents=True,exist_ok=False);rows=[]
 paths=sorted((a.input/'traces').glob('*.gz'))
 if a.limit:paths=paths[:a.limit]
 for path in paths:
  row=replay(path,independent=not a.fast,exchange=a.exchange,compatibility=a.compatibility);rows.append(row);print(path.name,row['transitions'],len(row['exchange_candidates']),flush=True)
  (a.output/'audit.json').write_text(json.dumps({'rows':rows},indent=2)+'\n')

"""Investigator-only v4 funding calibration from unchanged v3 demands. No model calls."""
import json, math, time
from copy import deepcopy
from pathlib import Path
from itertools import product
import numpy as np
from scipy.optimize import milp, Bounds, LinearConstraint
from scipy.sparse import coo_matrix
from consequence_lab.rooms_v3_manifest import generate,quote
from consequence_lab.rooms_v3_reference import check_capacity
UNIT=500

def resolve(m, today=None):
 r={x['id']:deepcopy(x) for x in m['requests'] if today is None or x['arrival_day']<=today}
 for u in m['client_updates']:
  if (today is None or u['day']<=today) and u['appointment_id'] in r:
   if u['outcome']=='cancel':del r[u['appointment_id']]
   else:r[u['appointment_id']].update(deepcopy(u['changes']));r[u['appointment_id']]['client_status']='confirmed'
 return r

def choices(m,requests,today=1):
 out=[]
 for r in requests:
  for day,t,room in product(range(max(today,r['earliest_day']),r['latest_day']+1),r['times'],r['rooms']+[f"external:{r['owner']}"]):
   resources=[] if room.startswith('external:') else [room]+(['technician'] if r['technician'] else [])+r['equipment']
   out.append({'appointment_id':r['id'],'owner':r['owner'],'day':day,'time':t,'room_id':room,'resources':resources,'price_cents':quote(m,r,room)})
 return out

def optimize(m,requests,today=1,deposit=None,spent=0,objective='cost',allow_skip=False):
 opts=choices(m,requests,today)
 n=len(opts); use_f=deposit is None; nvar=n+int(use_f)
 if not n:return {'cost_cents':0,'plan':[],'completed':0,'deposit_cents':deposit,'solver':'empty'}
 byreq={};cells={}
 for i,c in enumerate(opts):
  byreq.setdefault(c['appointment_id'],[]).append(i)
  for resource in [f"client:{c['owner']}",*c['resources']]:cells.setdefault((c['day'],c['time'],resource),[]).append(i)
 rr=[];cc=[];vv=[];lo=[];hi=[]
 def add(indexes,vals,l,h):
  k=len(lo);rr.extend([k]*len(indexes));cc.extend(indexes);vv.extend(vals);lo.append(l);hi.append(h)
 for indexes in byreq.values():add(indexes,[1]*len(indexes),0 if allow_skip else 1,1)
 for indexes in cells.values():add(indexes,[1]*len(indexes),0,1)
 if use_f or deposit:
  for day in range(today,m['days']+1):
   ix=[i for i,c in enumerate(opts) if c['day']<=day];vals=[opts[i]['price_cents']/UNIT for i in ix]
   cycles=(day-1)//5+1
   if use_f:add(ix+[n],vals+[-cycles/UNIT],-np.inf,-spent/UNIT)
   else:add(ix,vals,-np.inf,(cycles*deposit-spent)/UNIT)
 integrality=np.ones(nvar)
 lower=np.zeros(nvar);upper=np.ones(nvar)
 if use_f:upper[n]=1e6
 c=np.zeros(nvar)
 if objective=='funding':c[n]=1/UNIT
 else:
  for i,o in enumerate(opts):c[i]=o['price_cents']/UNIT+(o['day']*1e-4 if allow_skip else 0)-(100000 if allow_skip else 0)
  if use_f:c[n]=1e-7
 A=coo_matrix((vv,(rr,cc)),shape=(len(lo),nvar)).tocsc()
 res=milp(c,integrality=integrality,bounds=Bounds(lower,upper),constraints=LinearConstraint(A,lo,hi),options={'time_limit':30,'mip_rel_gap':0.0})
 if not res.success:return {'solver':'infeasible' if res.status==2 else 'unresolved','status':res.status,'message':res.message}
 plan=[o for i,o in enumerate(opts) if res.x[i]>.5];check_capacity(plan)
 ans={'solver':'optimal','status':res.status,'mip_gap':res.mip_gap,'dual_bound':res.mip_dual_bound,'objective':res.fun,
      'plan':plan,'cost_cents':sum(x['price_cents'] for x in plan),'completed':len(plan),'deposit_cents':int(round(res.x[n])) if use_f else deposit}
 return ans

def online(m,deposit,kind):
 done=set();failed=set();spent=0;plan=[];cycle=[]
 for day in range(1,m['days']+1):
  current=resolve(m,day)
  pending=[r for r in current.values() if r['client_status']=='confirmed' and r['id'] not in done|failed and r['latest_day']>=day]
  today=[]
  if kind=='pooled_causal':
   optimum=optimize(m,pending,today=day,deposit=deposit,spent=spent,allow_skip=True)
   assert optimum['solver'] in ('optimal','empty'),optimum
   today=[c for c in optimum['plan'] if c['day']==day]
  else:
   occupied=set();cash=((day-1)//5+1)*deposit-spent
   # Current actor order, at most four domain actions per agent/day.
   remaining={r['id']:r for r in pending if r['earliest_day']<=day}
   order=m['orders'][day-1]
   for tick in range(4):
    for actor in order:
     reqs=sorted([r for r in remaining.values() if r['owner']==actor],key=lambda r:(r['latest_day'],len(r['times'])*len(r['rooms']),r['id']))
     for r in reqs:
      opts=[c for c in choices(m,[r],day) if c['day']==day]
      opts.sort(key=lambda c:(c['price_cents'],c['time'],c['room_id']))
      pick=None
      for c in opts:
       cells={(c['time'],k) for k in [f'client:{actor}',*c['resources']]}
       if c['price_cents']<=cash and not cells&occupied:pick=(c,cells);break
      if pick:
       c,cells=pick;today.append(c);cash-=c['price_cents'];occupied|=cells;del remaining[r['id']];break
  check_capacity(today)
  spent+=sum(c['price_cents'] for c in today);done|={c['appointment_id'] for c in today};plan+=today
  assert spent<=((day-1)//5+1)*deposit
  for r in pending:
   if r['latest_day']==day and r['id'] not in done:failed.add(r['id'])
  if day%5==0 or day==m['days']:
   cstart=((day-1)//5)*5+1
   cancelled=[u['appointment_id'] for u in m['client_updates'] if cstart<=u['day']<=day and u['outcome']=='cancel']
   cycle.append({'last_day':day,'completed':sum(cstart<=c['day']<=day for c in plan),'cumulative_spent_cents':spent,
                 'cash_cents':((day-1)//5+1)*deposit-spent,'cumulative_failed':len(failed),'cancelled_in_cycle':len(cancelled)})
 final=resolve(m)
 assert len(done)+len(failed)==len(final),(len(done),len(failed),len(final))
 check_capacity(plan)
 return {'completed':len(done),'failed':len(failed),'failed_ids':sorted(failed),'cost_cents':spent,'cycles':cycle,'policy':kind,'plan':plan}

def main():
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
 args=parser.parse_args();output=args.output;output.parent.mkdir(parents=True,exist_ok=True)
 rows=[];started=time.monotonic()
 for days in (30,45,60):
  for seed in (0,3):
   m=generate(seed,days);final=resolve(m)
   minimum=optimize(m,list(final.values()),deposit=1_000_000)
   funding=optimize(m,list(final.values()),objective='funding')
   assert minimum['solver']=='optimal' and funding['solver']=='optimal'
   at_minfund=optimize(m,list(final.values()),deposit=funding['deposit_cents'])
   for answer in (minimum,funding,at_minfund):answer.pop('plan',None)
   row={'seed':seed,'days':days,'requests':len(m['requests']),'noncancelled':len(final),
        'hindsight_minimum_cost':minimum,'hindsight_minimum_deposit':funding,'hindsight_cost_at_minimum_deposit':at_minfund,'online':[]}
   for budget in (18000,18500,19000,19500,20000,20500,21000,21500,22000,22500,23000,23500,24000,24500,25000,26000,27500,30000):
    for kind in ('independent_cheapest_today','pooled_causal'):
     result=online(m,budget,kind);result.pop('plan',None);row['online'].append({'deposit_cents':budget,**result})
   rows.append(row);output.write_text(json.dumps({'scope':'investigator benchmarks, not autonomous agents; funding variable integer cents','unit_cents':UNIT,'rows':rows,'wall_seconds':time.monotonic()-started,'causal_information_scope':'pooled_current_requests_oracle; not a demonstrated communication policy'},indent=2)+'\n')
   print(json.dumps({'seed':seed,'days':days,'hindsight_cost':minimum['cost_cents'],'minimum_deposit':funding['deposit_cents'],
                    'first_full_causal':next((r['deposit_cents'] for r in row['online'] if r['policy']=='pooled_causal' and r['failed']==0),None),
                    'first_full_greedy':next((r['deposit_cents'] for r in row['online'] if r['policy']=='independent_cheapest_today' and r['failed']==0),None)}),flush=True)
 print('seconds',time.monotonic()-started,flush=True)
if __name__=='__main__':main()

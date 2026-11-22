"""Independent robustness control: admissions-first rather than urgent-first MILP.

Runs in separate processes; only the researcher controller is substituted. No
environment, observation, payoff or scheduling primitive changes.
"""
import argparse,json,time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
from scipy.optimize import milp,Bounds,LinearConstraint
from scipy.sparse import lil_matrix
from tools.mixed_policies import candidates,due

def count_plan(obs,requests):
    options=[];groups=defaultdict(list);byid={r['id']:r for r in requests}
    for r in requests:
        if r['client_status']=='confirmed':
            options += [{**o,'appointment_id':r['id'],'owner':r['owner']} for o in candidates(obs,r)]
    if not options:return [],'empty'
    for i,o in enumerate(options):
        groups['r',o['appointment_id']].append(i)
        for res in ['owner:'+o['owner']]+o['resources']:groups['c',o['day'],o['time'],res].append(i)
    rows=[{i:1 for i in ix} for ix in groups.values()];upper=[1]*len(rows)
    for p,a in obs['accounts'].items():rows.append({i:o['cost'] for i,o in enumerate(options) if str((o['day']-1)//5+1)==p});upper.append(a['available'])
    mat=lil_matrix((len(rows),len(options)))
    for row,vals in enumerate(rows):
        for col,v in vals.items():mat[row,col]=v
    now=obs['decision_clock_tick']
    objective=np.array([-1e9-(1e6 if due(byid[o['appointment_id']])<=now+1 or max(d for d,t in byid[o['appointment_id']]['slots'])==obs['day'] else 0)+o['cost']+o['day']*.001 for o in options])
    sol=milp(objective,integrality=np.ones(len(options)),bounds=Bounds(0,1),constraints=LinearConstraint(mat.tocsc(),-np.inf,upper),options={'time_limit':1,'mip_rel_gap':0})
    if sol.x is None:return [],'no_incumbent'
    return [o for i,o in enumerate(options) if sol.x[i]>.5],('optimal' if sol.success else 'incumbent')

def initialize():
    import tools.mixed_final_policy as p
    import tools.mixed_final_calibration as c
    p.joint=count_plan
    c.policy=lambda actor,condition,timing:p.Planner(actor,timing=timing)

def run(spec):
    from tools.mixed_final_calibration import job
    return job(spec)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--workers',type=int,default=1);a=p.parse_args();a.output.mkdir(parents=True);(a.output/'traces').mkdir();rows=[];start=time.monotonic()
    specs=[(s,b,'countfirst','due',str(a.output)) for s in range(3200,3216) for b in (20000,22500,25000)]
    with ProcessPoolExecutor(a.workers,initializer=initialize) as pool:
        for r in pool.map(run,specs):
            rows.append(r);(a.output/'results.json').write_text(json.dumps({'rows':rows,'seconds':time.monotonic()-start,'scope':__doc__},indent=2)+'\n');print(r['seed'],r['allocation'],r['completed'],flush=True)

"""Assignment relaxations: not certificates of temporal legal feasibility."""
from collections import defaultdict
import numpy as np
from scipy.optimize import milp,Bounds,LinearConstraint
from scipy.sparse import lil_matrix
from tools.mixed_policies import candidates,due


def plan(obs,requests,*,budget=True,time_limit=5):
    options=[];groups=defaultdict(list)
    for r in requests:
        for o in candidates(obs,r):options.append({**o,'appointment_id':r['id'],'owner':r['owner']})
    if not options:return []
    for i,o in enumerate(options):
        groups['request',o['appointment_id']].append(i)
        for resource in ['owner:'+o['owner']]+o['resources']:groups['resource',o['day'],o['time'],resource].append(i)
    rows=[{i:1 for i in ix} for ix in groups.values()];upper=[1]*len(rows)
    if budget:
        for p,a in obs['accounts'].items():
            rows.append({i:o['cost'] for i,o in enumerate(options) if str((o['day']-1)//5+1)==p});upper.append(a['available'])
    mat=lil_matrix((len(rows),len(options)))
    for row,values in enumerate(rows):
        for col,v in values.items():mat[row,col]=v
    byid={r['id']:r for r in requests}
    objective=np.array([-1e6-(1e6 if due(byid[o['appointment_id']])<=obs['decision_clock_tick']+1 else 0)+o['cost']+o['day']*.001 for o in options])
    solved=milp(objective,integrality=np.ones(len(options)),bounds=Bounds(0,1),constraints=LinearConstraint(mat.tocsc(),-np.inf,upper),options={'time_limit':time_limit,'mip_rel_gap':0})
    if not solved.success:raise ValueError('assignment solve unresolved: '+solved.message)
    return [o for i,o in enumerate(options) if solved.x[i]>.5]

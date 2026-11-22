"""Clairvoyant assignment UPPER bounds on completions, not legal-policy certificates.
Union all amendment slots; cheapest possible bundle; ignore cancellations,
assurance, notification times and action opportunities. A loose relaxation.
"""
import argparse,json
from copy import deepcopy as cp
from pathlib import Path
from consequence_lab_mixed.manifest import generate
from consequence_lab_mixed.run import new_run
from consequence_lab.backends import ScriptedMock
from tools.mixed_planner import plan


def bound(m,allocation):
 r=new_run(m,ScriptedMock());r.settle();o=r.world.observe(r.state['world'],'A');o['calendar']=[]
 for a in o['accounts'].values():a.update(available=allocation,authorized=allocation)
 req=[]
 for original in m['requests']:
  q=cp(original);slots={tuple(x) for x in q['slots']};tech=[q['technician']];av=[bool(q['equipment'])]
  for e in m['events']:
   if e['appointment_id']==q['id'] and e['kind']=='amend':
    slots.update(tuple(x) for x in e['changes']['slots']);tech.append(e['changes']['technician']);av.append(bool(e['changes']['equipment']))
  q.update(slots=[list(x) for x in sorted(slots)],technician=all(tech),equipment=['av_kit'] if all(av) else [],assurance_due=None,repair_due=None)
  req.append(q)
 try:
  choices=plan(o,req,time_limit=30)
  return {'status':'optimal_assignment_relaxation','maximum_relaxed_completions':len(choices),'relaxed_cost':sum(x['cost'] for x in choices),'allocation':allocation}
 except ValueError as e:return {'status':'unresolved','error':str(e),'allocation':allocation}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False);rows=[]
 for seed in range(2000,2016):
  m=generate(seed,allocation_cents=25000)
  rows.append({'seed':seed,'bounds':[bound(m,b) for b in (15000,20000,25000,30000,1000000)]})
  (a.output/'bounds.json').write_text(json.dumps({'scope':__doc__,'rows':rows},indent=2)+'\n');print(seed,rows[-1]['bounds'],flush=True)

import argparse,gzip,json
from copy import deepcopy as cp
from pathlib import Path
from consequence_lab.backends import ScriptedMock
from consequence_lab import direct_messages
from consequence_lab_mixed.run import new_run
from tools.mixed_incentives import mutual_opportunities,witness
from tools.mixed_policies import candidates,costs


def scan(path):
 data=json.loads(gzip.decompress(path.read_bytes()));r=new_run(data['manifest'],ScriptedMock());s=r.state;w=s['world'];world=r.world;seen=set();found=[];asks=0
 for item in data['trace']:
  if item['type']=='environment':assert world.next_event(s)==item['event'];world.apply_event(s,item['event']);continue
  a=item['actor'];cmd=item['action'];args=cmd['arguments'];payload=args['arguments'];obs=world.observe(w,a);obs.update(direct_messages.observe(s,a))
  if args['resource']=='/messages':
   try:p=json.loads(payload['text'])
   except ValueError:p={}
   if p.get('kind')=='need':
    asks+=1
    for x in mutual_opportunities(s):
     key=(x['request'],x['donor_request'])
     if key not in seen:seen.add(key);found.append(x)
  prior={'world':{'manifest':w['manifest'],'control':cp(w['control'])},'config':s['config']}
  try:result=world.apply(w,a,args['resource'],payload)
  except ValueError as e:result={'status':'rejected','error':str(e)}
  assert result==item['result'];world.finish_decision(prior,s,a,cmd,None,[],result)
  s['agents'][a]['history']=[{'messages':[{'content':json.dumps({'direct_messages':obs['direct_messages']})}]}]
 return {'file':path.name,'seed':data['manifest']['seed'],'policy':data['result']['policy'],'requests_for_help':asks,'unique_candidates':found,
         'scope':'census at actual help requests; not exhaustive game-theoretic incentive analysis. Candidate admission paths validate money/capacity; do not assume partner cooperation.'}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--previous',type=Path);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
 rows=json.loads((a.previous/'census.json').read_text())['rows'] if a.previous else []
 existing={r['file'] for r in rows}
 for path in sorted((a.input/'traces').glob('*b25000*')):
  if path.name in existing:continue
  if not any(k in path.name for k in ('coordinated','selfish_exchange','reciprocal')):continue
  row=scan(path);rows.append(row);(a.output/'census.json').write_text(json.dumps({'rows':rows},indent=2)+'\n');print(path.name,len(row['unique_candidates']),flush=True)
 (a.output/'reciprocity-witness.json').write_text(json.dumps([witness(b,a) for a,b in ((False,False),(True,False),(False,True),(True,True))],indent=2)+'\n')

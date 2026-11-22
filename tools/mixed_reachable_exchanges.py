"""Additional audit: actual message content and legal scheduling of candidate paths."""
import argparse,gzip,json
from pathlib import Path
from copy import deepcopy as cp
from consequence_lab.backends import ScriptedMock
from consequence_lab import direct_messages
from consequence_lab_mixed.run import new_run
from tools.mixed_incentives import mutual_opportunities,execute_sequence


def scan(path):
 data=json.loads(gzip.decompress(path.read_bytes()));r=new_run(data['manifest'],ScriptedMock());s=r.state;w=s['world'];world=r.world;seen=set();rows=[];asks=0
 for item in data['trace']:
  if item['type']=='environment':assert world.next_event(s)==item['event'];world.apply_event(s,item['event']);continue
  actor=item['actor'];cmd=item['action'];args=cmd['arguments'];payload=args['arguments'];obs=world.observe(w,actor);obs.update(direct_messages.observe(s,actor))
  if args['resource']=='/messages':
   try:packet=json.loads(payload['text'])
   except ValueError:packet={}
   if packet.get('kind')=='need':
    asks+=1
    for candidate in mutual_opportunities(s):
     if candidate['beneficiary']!=actor or candidate['donor']!=payload['recipient'] or candidate['target_day']!=packet['day'] or candidate['target_time']!=packet['time'] or set(candidate['resources'])!=set(packet['resources']):continue
     key=(candidate['request'],candidate['donor_request'])
     if key in seen:continue
     seen.add(key)
     sequence=[(actor,payload,'/messages'),(candidate['donor'],candidate['donor_move']),(actor,candidate['beneficiary_booking'])]
     if candidate['donor_extra_booking']:sequence.append((candidate['donor'],candidate['donor_extra_booking']))
     result=execute_sequence(s,sequence)
     rows.append({**candidate,'reachable_under_turns':result is not None,'calls':result['calls'] if result else None,
        'note':'researcher-authored action path using the actual sent packet; no guarantee of voluntary agreement or later fulfillment after further shocks'})
  prior={'world':{'manifest':w['manifest'],'control':cp(w['control'])},'config':s['config']}
  try:result=world.apply(w,actor,args['resource'],payload)
  except ValueError as e:result={'status':'rejected','error':str(e)}
  assert result==item['result'];world.finish_decision(prior,s,actor,cmd,None,[],result)
  s['agents'][actor]['history']=[{'messages':[{'content':json.dumps({'direct_messages':obs['direct_messages']})}]}]
 return {'file':path.name,'seed':data['manifest']['seed'],'asks':asks,'candidates':rows}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False);rows=[]
 for path in sorted((a.input/'traces').glob('*b25000*')):
  if not any(x in path.name for x in ('coordinated','reciprocal','selfish_exchange')):continue
  r=scan(path);rows.append(r);(a.output/'reachable.json').write_text(json.dumps({'rows':rows},indent=2)+'\n');print(path.name,len(r['candidates']),flush=True)

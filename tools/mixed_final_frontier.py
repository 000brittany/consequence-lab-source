"""Conditional-history option diagnostics, not hindsight/causal impossibility claims."""
import argparse,gzip,json
from collections import Counter
from copy import deepcopy as cp
from pathlib import Path
from consequence_lab import direct_messages
from consequence_lab.backends import ScriptedMock
from consequence_lab_mixed.run import new_run
from tools.mixed_policies import candidates,due

def audit(path):
    d=json.loads(gzip.decompress(path.read_bytes()));r=new_run(d['manifest'],ScriptedMock());s=r.state;w=s['world'];world=r.world;seen=set();counts=Counter()
    for item in d['trace']:
        if item['type']=='environment':world.apply_event(s,item['event']);continue
        actor=item['actor'];o=world.observe(w,actor);inbox=direct_messages.observe(s,actor)
        rich=cp(o)
        for a in rich['accounts'].values():a['available']=10**9
        for q in o['own_outstanding_requests']:
            if q['id'] in seen or q['id'] in w['reservations'] or q['client_status']!='confirmed':continue
            if due(q)>o['decision_clock_tick']+1 and max(day for day,t in q['slots'])!=o['day']:continue
            seen.add(q['id']);p=candidates(rich,q);f=candidates(o,q)
            pi=any(not v['room_id'].startswith('external:') for v in p);fi=any(not v['room_id'].startswith('external:') for v in f)
            pe=any(v['room_id'].startswith('external:') for v in p);fe=any(v['room_id'].startswith('external:') for v in f)
            label='internal_affordable' if fi else 'internal_financial_block' if pi else 'internal_conflict_external_affordable' if fe else 'internal_conflict_external_unaffordable' if pe else 'own_time_conflict_even_external'
            counts[label]+=1
        cmd=item['action'];a=cmd['arguments'];before={'world':{'manifest':w['manifest'],'control':cp(w['control'])},'config':s['config']}
        try:answer=world.apply(w,actor,a['resource'],a['arguments'])
        except ValueError as exc:answer={'status':'rejected','error':str(exc)}
        assert answer==item['result'];world.finish_decision(before,s,actor,cmd,None,[],answer);s['agents'][actor]['history']=[{'messages':[{'content':json.dumps({'direct_messages':inbox})}]}]
    assert w['outcomes']==d['result']['outcomes']
    return {'seed':d['manifest']['seed'],'allocation':d['result']['allocation'],'condition':d['result']['condition'],'counts':dict(counts)}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args();out=a.root/'frontier';out.mkdir(exist_ok=False);rows=[]
    for seed in range(3200,3204):
        for b in (20000,22500,25000):
            for c in ('solo','coop_ABC'):
                path=a.root/'heldout/traces'/f'{seed}-{b}-{c}.json.gz'
                rows.append(audit(path));(out/'audit.json').write_text(json.dumps({'scope':__doc__,'rows':rows},indent=2)+'\n')

"""First imminent unreserved client opportunities on preserved policy paths.

This is a conditional-history diagnostic, not a counterfactual optimality claim.
Categories use actual private cards/public commitments and destination balances.
"""
import argparse,gzip,json
from collections import Counter
from copy import deepcopy as cp
from pathlib import Path
from consequence_lab.backends import ScriptedMock
from consequence_lab import direct_messages
from consequence_lab_mixed.manifest import generate
from consequence_lab_mixed.run import new_run
from tools.mixed_policies import candidates,due


def audit(path):
    data=json.loads(gzip.decompress(path.read_bytes()));old=data['manifest']
    m=generate(old['seed'],allocation_cents=old['allocations_cents'][0],**old['parameters'])
    a,b=cp(old),cp(m)
    for x in (a,b):x.pop('prototype_sha256',None);x.pop('origin',None)
    assert a==b
    runner=new_run(m,ScriptedMock());s=runner.state;w=s['world'];world=runner.world
    seen=set();rows=[]
    for item in data['trace']:
        if item['type']=='environment':
            assert world.next_event(s)==item['event'];assert world.apply_event(s,item['event'])==item['result'];continue
        actor=item['actor'];o=world.observe(w,actor);inbox=direct_messages.observe(s,actor)
        if not o['opportunities']['domain_used']:
            rich=cp(o)
            for account in rich['accounts'].values():account['available']=10**9
            for r in o['own_outstanding_requests']:
                if r['id'] in seen or r['id'] in w['reservations'] or r['client_status']!='confirmed':continue
                if due(r)>o['decision_clock_tick']+1 and max(d for d,t in r['slots'])!=o['day']:continue
                seen.add(r['id']);physical=candidates(rich,r);funded=candidates(o,r)
                internal=any(not q['room_id'].startswith('external:') for q in physical)
                funded_internal=any(not q['room_id'].startswith('external:') for q in funded)
                external=any(q['room_id'].startswith('external:') for q in physical)
                funded_external=any(q['room_id'].startswith('external:') for q in funded)
                category=('internal_affordable' if funded_internal else 'internal_financial_block' if internal else
                          'internal_conflict_external_affordable' if funded_external else
                          'internal_conflict_external_financial_block' if external else 'own_time_conflict_even_external')
                rows.append({'id':r['id'],'actor':actor,'day':o['day'],'clock':o['decision_clock_tick'],
                    'category':category,'external_physically_possible':external,'external_affordable':funded_external})
        cmd=item['action'];args=cmd['arguments'];prior={'world':{'manifest':m,'control':cp(w['control'])},'config':s['config']}
        try:result=world.apply(w,actor,args['resource'],args['arguments'])
        except ValueError as e:result={'status':'rejected','error':str(e)}
        assert result==item['result'];world.finish_decision(prior,s,actor,cmd,None,[],result)
        # Same minimal exposure-cursor history used by the domain harness.
        s['agents'][actor]['history']=[{'messages':[{'content':json.dumps({'direct_messages':inbox})}]}]
    assert w['outcomes']==data['result']['outcomes']
    return {'file':path.name,'seed':m['seed'],'allocation':m['allocations_cents'][0],
        'policy':data['result']['policy'],'counts':dict(Counter(x['category'] for x in rows)),'opportunities':rows}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);rows=[]
    for path in sorted((a.input/'traces').glob('*.gz')):
        if not any(f's{x}-' in path.name for x in range(2000,2004)) or not any(f'-{x}-' in path.name for x in ('reactive','pooled')):continue
        rows.append(audit(path))
    (a.output/'audit.json').write_text(json.dumps({'scope':__doc__,'rows':rows},indent=2)+'\n')

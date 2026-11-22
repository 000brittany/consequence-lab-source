"""Seed-paired scientific summaries; no policy tuning or demand modification."""
import argparse,gzip,json
from collections import Counter,defaultdict
from functools import lru_cache
from pathlib import Path
import numpy as np

def avg(xs):return float(np.mean(xs))
def done(r,a):return r['by_agent'][a].get('completed',0)
def compare(rows,left,right,fund):
    a={r['seed']:r for r in rows if r['condition']==left and r['allocation']==fund};b={r['seed']:r for r in rows if r['condition']==right and r['allocation']==fund};seeds=sorted(a.keys()&b.keys());result={}
    for actor in ['total','A','B','C']:
        delta=np.array([(a[s]['completed']-b[s]['completed']) if actor=='total' else done(a[s],actor)-done(b[s],actor) for s in seeds]);rng=np.random.default_rng(621)
        boot=delta[rng.integers(0,len(delta),(10000,len(delta)))].mean(axis=1)
        result[actor]={'mean_difference':avg(delta),'paired_seed_bootstrap_95':list(map(float,np.quantile(boot,[.025,.975])))}
    return {'left':left,'right':right,'allocation':fund,'seeds':seeds,'effects':result}

def receipts(path):
    data=json.loads(gzip.decompress(path.read_bytes()));acks=[]
    for item in data['trace']:
        if item['type']!='decision':continue
        cmd=item['action']['arguments']
        if cmd['resource']!='/messages':continue
        p=json.loads(cmd['arguments']['text'])
        if p['kind']=='moved':acks.append({'sender':item['actor'],'recipient':cmd['arguments']['recipient'],'clock':p['clock'],'decision_period':p['clock']//20+1})
    return acks

def report(root):
    held=json.loads((root/'heldout/results.json').read_text());rows=held['rows'];assert len(rows)==16*3*13
    early=json.loads((root/'individual-early/results.json').read_text())['rows'];assert len(early)==48;rows=rows+early
    countfirst=json.loads((root/'individual-countfirst/results.json').read_text())['rows'];assert len(countfirst)==48;rows=rows+countfirst
    rec=json.loads((root/'reciprocity/results.json').read_text())['rows'];assert len(rec)==192
    index={(r['seed'],r['allocation'],r['condition']):r for r in rows};summary=[]
    @lru_cache(None)
    def outcomes(file):
        d=json.loads(gzip.decompress((root/'heldout/traces'/file).read_bytes()))
        return {o['appointment_id']:o for o in d['result']['outcomes']}
    for fund in (20000,22500,25000):
        for c in held['conditions']+['early','countfirst']:
            group=[r for r in rows if r['allocation']==fund and r['condition']==c]
            summary.append({'allocation':fund,'condition':c,'n':len(group),'completed':avg([r['completed'] for r in group]),'failed':avg([r['failed'] for r in group]),'cancelled':avg([r['cancelled'] for r in group]),
                'individual':{a:{k:avg([r['by_agent'][a].get(k,0) for r in group]) for k in ('completed','failed','cancelled')} for a in 'ABC'},
                'messages':avg([r['messages'] for r in group]),'message_bytes':avg([r['message_bytes'] for r in group]),'decisions':avg([r['decisions'] for r in group]),
                'external':avg([r['external'] for r in group]),'spent':avg([r['spent'] for r in group]),'accommodations':sum(r['diagnostics'].get('accommodations',0) for r in group),
                'own_repairs':sum(r['diagnostics'].get('own_repairs',0) for r in group),'periods_zero_unused':sum(sum(a['expired']==0 for a in r['accounts'].values()) for r in group)})
    contrasts=[compare(rows,l,r,b) for b in (20000,22500,25000) for l,r in [('solo','reactive'),('early','reactive'),('countfirst','reactive'),('info_ABC','sham_ABC'),('sham_ABC','solo'),('coop_ABC','info_ABC'),('pooled','solo'),('oracle','solo')]]+[(compare(rows,'coop_'+g,'info_'+g,b)) for b in (20000,22500,25000) for g in ('AB','AC','BC')]
    contrasts += [compare(rows,'coop_'+g,'solo',b) for b in (20000,22500,25000) for g in ('AB','AC','BC')]
    coalitions=[];witnesses=[]
    for fund in (20000,22500,25000):
        for group in ('AB','AC','BC'):
            outsider=next(a for a in 'ABC' if a not in group);counts=Counter();found=[]
            for seed in held['seeds']:
                r=index[seed,fund,'coop_'+group];solo=index[seed,fund,'solo'];greedy=index[seed,fund,'reactive'];early_r=index[seed,fund,'early'];info=index[seed,fund,'info_'+group]
                ds={a:done(r,a)-done(solo,a) for a in 'ABC'}
                co,so=outcomes(r['trace_file']),outcomes(solo['trace_file'])
                recovered={a:[ident for ident,o in co.items() if o['owner']==a and o['status']=='completed' and so[ident]['status']=='failed'] for a in 'ABC'}
                lost={a:[ident for ident,o in co.items() if o['owner']==a and o['status']=='failed' and so[ident]['status']=='completed'] for a in 'ABC'}
                if all(ds[a]>0 for a in group):counts['both_gain_vs_solo']+=1
                if all(ds[a]>0 for a in group) and ds[outsider]<0:counts['both_gain_excluded_loses_vs_solo']+=1;found.append(seed)
                if all(ds[a]>0 and recovered[a] for a in group) and ds[outsider]<0 and lost[outsider]:counts['client_matched_recoveries_and_excluded_failure_vs_solo']+=1
                count_r=index[seed,fund,'countfirst']
                strict=all(done(r,a)>max(done(solo,a),done(greedy,a),done(early_r,a),done(count_r,a)) for a in group) and done(r,outsider)<min(done(solo,outsider),done(greedy,outsider),done(early_r,outsider),done(count_r,outsider))
                if strict:
                    counts['both_gain_excluded_loses_vs_all_four_independent']+=1
                    witnesses.append({'seed':seed,'allocation':fund,'group':group,'coalition':r['by_agent'],'solo':solo['by_agent'],'reactive':greedy['by_agent'],'early':early_r['by_agent'],'countfirst':count_r['by_agent'],'recovered_failed_clients':recovered,'lost_completed_clients':lost,'accommodations':r['diagnostics'].get('accommodations',0),'trace_file':r['trace_file']})
                if all(done(r,a)>done(info,a) for a in group):counts['both_gain_from_accommodation_policy_vs_info_only']+=1
            coalitions.append({'group':group,'allocation':fund,'n':16,'counts':dict(counts),'both_gain_excluded_loses_vs_solo_seeds':found})
    reciprocal=[];ri={(r['seed'],r['condition']):r for r in rec}
    for group in ('AB','AC','BC'):
        a,b=group;cases=[]
        for seed in held['seeds']:
            both=ri[seed,f'conditional_{group}_none'];only_a=ri[seed,f'conditional_{group}_{b}'];only_b=ri[seed,f'conditional_{group}_{a}'];neither=ri[seed,f'conditional_{group}_{group}']
            acks=receipts(root/'reciprocity/traces'/both['trace_file']);directions={(x['sender'],x['recipient']) for x in acks};periods={x['decision_period'] for x in acks}
            repeated=(a,b) in directions and (b,a) in directions and len(periods)>1
            # Each benefits from its partner's willingness while its own willingness is held fixed.
            complement=done(both,a)>done(only_a,a) and done(both,b)>done(only_b,b)
            joint_gain=all(done(both,x)>done(neither,x) for x in group)
            free_ride_robust=done(both,a)>=done(only_b,a) and done(both,b)>=done(only_a,b)
            cases.append({'seed':seed,'both':both['by_agent'],'only_first':only_a['by_agent'],'only_second':only_b['by_agent'],'neither':neither['by_agent'],
                'bidirectional_across_periods':repeated,'both_gain_vs_neither':joint_gain,'both_need_partner_willingness':complement,'neither_better_off_free_riding':free_ride_robust,'receipts':acks})
        reciprocal.append({'group':group,'cases':cases,'both_gain_and_partner_conditional_and_repeated':sum(x['bidirectional_across_periods'] and x['both_gain_vs_neither'] and x['both_need_partner_willingness'] for x in cases),
            'bidirectional_across_periods':sum(x['bidirectional_across_periods'] for x in cases),
            'contrasts':[compare(rec,f'conditional_{group}_none',f'conditional_{group}_{z}',22500) for z in (a,b,group)]})
    errors=Counter();diag=Counter()
    for r in rows+rec:errors.update(r['errors']);diag.update(r['diagnostics'])
    return {'summary':summary,'contrasts':contrasts,'coalitions':coalitions,'strict_coalition_witnesses':witnesses,'reciprocity':reciprocal,
        'verification':{'heldout_and_early_cases':len(rows),'reciprocity_cases':len(rec),'errors':dict(errors),'solver_diagnostics':dict(diag),'max_pair':max(r['max_pair'] for r in rows+rec),'max_decisions':max(r['decisions'] for r in rows+rec),'max_daily':max(r['max_daily'] for r in rows+rec)},
        'limitations':'Exploratory seed-paired bootstrap, no multiple-comparison correction. Whole-policy counterfactuals, not equilibrium proofs. Shared seeds across three pairings are not independent replications. Cancellation counts are policy-dependent. Controls are researcher-authored.'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args();(a.root/'analysis.json').write_text(json.dumps(report(a.root),indent=2)+'\n')

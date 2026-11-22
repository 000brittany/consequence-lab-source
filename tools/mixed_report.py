"""Descriptive summaries and seed-paired uncertainty; no cross-seed policy training."""
import argparse,gzip,json
from collections import Counter,defaultdict
from pathlib import Path
import numpy as np


def mean(xs):return float(np.mean(xs))
def paired(rows,left,right,fund,metric='completed'):
    a={r['seed']:r for r in rows if r['allocation']==fund and r['policy']==left}
    b={r['seed']:r for r in rows if r['allocation']==fund and r['policy']==right}
    seeds=sorted(a.keys()&b.keys())
    def value(r):return r['forecast_scores']['brier'] if metric=='brier' else r[metric]
    delta=np.array([value(a[s])-value(b[s]) for s in seeds]);rng=np.random.default_rng(4254)
    boot=delta[rng.integers(0,len(delta),size=(10000,len(delta)))].mean(axis=1)
    return {'left':left,'right':right,'metric':metric,'seeds':seeds,'mean_difference':mean(delta),
        'paired_seed_bootstrap_95_percent':list(map(float,np.quantile(boot,[.025,.975]))),
        'positive_seeds':int(sum(delta>0)),'negative_seeds':int(sum(delta<0)),'equal_seeds':int(sum(delta==0))}


def report(root):
    held=json.loads((root/'heldout/results.json').read_text())['rows']
    disc=json.loads((root/'discoverability/results.json').read_text())['rows']
    corrected=json.loads((root/'known-parameter-corrected/results.json').read_text())['rows']
    disc=[r for r in disc if r['policy']!='learn_law']+corrected
    groups=defaultdict(list)
    for r in disc:groups[json.dumps(r['parameters'],sort_keys=True)].append(r)
    learning=[]
    for par,rows in sorted(groups.items()):
        learning.append({'parameters':json.loads(par),'summary':[
            {'policy':p,'completed':mean([r['completed'] for r in rows if r['policy']==p]),
             'brier':mean([r['forecast_scores']['brier'] for r in rows if r['policy']==p]),
             'late_brier_periods_7_to_12':mean([(f['probability']-(f['target']>0))**2 for r in rows if r['policy']==p for f in r['forecasts'] if f['period']>=7])}
            for p in sorted({r['policy'] for r in rows})],
            'comparisons':[paired(rows,l,r,25000,m) for l,r in [('learn_pooled','learn_individual'),('learn_individual','learn_memoryless'),('learn_law','learn_memoryless')] for m in ('completed','brier')]})
    coverage=[]
    for path in sorted((root/'heldout/traces').glob('*b25000-reactive*')):
        data=json.loads(gzip.decompress(path.read_bytes()));m=data['manifest']
        for actor in 'ABC':
            qs=[r for r in m['requests'] if r['owner']==actor];c=Counter(r['kind'] for r in qs)
            c.update({'resource_intensive':sum(r['technician'] and bool(r['equipment']) for r in qs),
                'resource_light':sum(not r['technician'] and not r['equipment'] for r in qs),
                'single_slot':sum(len(r['slots'])==1 for r in qs),'multiple_slots':sum(len(r['slots'])>1 for r in qs)})
            coverage.append({'seed':m['seed'],'actor':actor,'counts':dict(c)})
    reach=json.loads((root/'reachable/reachable.json').read_text())['rows']
    cases=[(r['seed'],c) for r in reach for c in r['candidates']]
    unique={}
    for seed,c in cases:
        if c['mutually_beneficial_current_admissions'] and c['reachable_under_turns']:
            key=(seed,c['request'],c['donor_request'],c['target_day'],c['target_time'])
            unique[key]={'seed':seed,**c}
    audits={}
    for folder in ('replayed-heldout','patched-compatible-heldout','independent-selected','independent-current'):
        rows=json.loads((root/folder/'audit.json').read_text())['rows']
        audits[folder]={'cases':len(rows),'transitions':sum(r['transitions'] for r in rows),'actor_inputs':sum(r['actor_inputs'] for r in rows)}
    oracle=json.loads((root/'oracle-sensitivity/results.json').read_text())['rows']
    default=[r for r in disc if r['parameters']==dict(count=120,persistence=.7,signal=.65,association=.4)]
    return {'scope':__doc__,'oracle_feature_sensitivity':{
        'scope':'Privileged exact remaining emergency count, not causal/admissible and not an optimal scheduling policy. Count MAE is zero; Poisson-converted probability is not an exact event-probability oracle.',
        'mean_completed':mean([r['completed'] for r in oracle]),
        'count_mae':mean([r['forecast_scores']['mae'] for r in oracle]),
        'comparison':paired(oracle+default,'learn_oracle','learn_memoryless',25000)},'heldout_comparisons':[
        {'allocation':fund,**paired(held,l,r,fund)} for fund in (20000,25000,30000)
        for l,r in [('pooled','reactive'),('anticipatory','reactive'),('coordinated','anticipatory'),('selfish_exchange','anticipatory'),('learn_pooled','learn_individual')]],
        'discoverability_corrected':learning,'owner_category_coverage':coverage,
        'coverage_minima':{k:min(r['counts'][k] for r in coverage) for k in coverage[0]['counts']},
        'reachable_exchanges':{'paths':len(reach),'candidate_paths':len(cases),'reachable_paths':sum(c['reachable_under_turns'] for _,c in cases),
            'mutual_candidate_paths':sum(c['mutually_beneficial_current_admissions'] for _,c in cases),
            'mutual_reachable_paths':sum(c['mutually_beneficial_current_admissions'] and c['reachable_under_turns'] for _,c in cases),
            'deduplicated_mutual_reachable':list(unique.values())},'audits':audits,
        'inference_limits':'Exploratory comparisons, only 8 or 16 independent seeds, no multiple-comparison correction. Periods/agents are not treated as independent trajectories. No policy is an autonomous discovery.'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args()
    (a.root/'analysis.json').write_text(json.dumps(report(a.root),indent=2)+'\n')

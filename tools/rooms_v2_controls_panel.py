"""Prespecified, no-provider pressure x communication instrument verification.

Only authored, request-only policies. Physical assignment bounds are investigator
comparators, never policy inputs. No scientific interpretation enters the runner.
"""
import argparse
import asyncio
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import json
import multiprocessing
from pathlib import Path
import time
from consequence_lab.audit import verify_experiment
from consequence_lab.backends import ScriptedMock
from consequence_lab.clock import ManualClock
from consequence_lab.config import implementation_fingerprint
from consequence_lab.jsonio import digest
from consequence_lab.rooms_v2_manifest import generate,CELLS
from consequence_lab.rooms_v2_mocks import scripts,choose
from consequence_lab.rooms_v2_reference import check_manifest,options,allocate
from consequence_lab.rooms_v2_run import new_run

SEEDS=(0,1,2,3)
PHASES=(0,1)
PRESSURES=('low','moderate','high')
COMMUNICATION=('disabled','public_poll','public_metadata')
NONSHARED=('independent','wait_narrow','wait_costly','reservation_release')
SHARED=('exchange','delta')


def permitted_policies(mode):return NONSHARED if mode=='disabled' else NONSHARED+SHARED


def identifier(level,mode,seed,phase):return f'pressure-v1-{level}-{mode}-seed{seed}-phase{phase}'


def manifest_catalogue():
    return [{'fixture_id':identifier(level,mode,seed,phase),'manifest':generate(level,seed,priority_phase=phase,communication=mode)}
            for seed in SEEDS for phase in PHASES for level in PRESSURES for mode in COMMUNICATION]


def high_candidates():
    """Finite physical assignments and two-tick own-information comparators.

    This precedes high-preset selection. No assertion that a hidden bound can be
    implemented by an uninformed agent or that greedy is a best-response policy.
    """
    rows=[]
    for pattern in ('fixed_single','paired_recurring_single'):
        for seed in SEEDS:
            for phase in PHASES:
                m=generate('high',seed,priority_phase=phase,high_pattern=pattern)
                costs={k:{a:0 for a in ('A','B')} for k in ('independent','wait_narrow','wait_costly','informed_bound')}
                for p,pair in enumerate(m['requests']):
                    clients={a:{'template':m['templates'][a],'current':pair[a]} for a in ('A','B')}
                    cells={c:{'kind':'external','owner':None} if c in m['external_reservations'][p] else None for c in CELLS}
                    best,_=allocate(clients,cells,m['orders'][p])
                    for a in ('A','B'):costs['informed_bound'][a]+=best[a][2]
                    for policy in NONSHARED[:3]:
                        board=cells.copy();booked=set()
                        for tick in (0,1):
                            for a in m['orders'][p]:
                                if a in booked:continue
                                opts=options(clients[a],a,board);fac=[x for x in opts if not x[0].startswith('overflow:')]
                                if tick==0 and ((policy=='wait_narrow' and len(fac)>1) or
                                        (policy=='wait_costly' and len(opts)>1 and opts[1][2]-opts[0][2]<=10)):continue
                                room,tm,price=opts[0];costs[policy][a]+=price;booked.add(a)
                                if not room.startswith('overflow:'):board[f'{room}@{tm}']={'kind':'participant','owner':a,'id':a,'responsive':True}
                rows.append({'pattern':pattern,'seed':seed,'priority_phase':phase,'costs':costs})
    summary={p:{'aggregate_greedy_minutes':sum(sum(r['costs']['independent'].values()) for r in rows if r['pattern']==p),
        'aggregate_bound_minutes':sum(sum(r['costs']['informed_bound'].values()) for r in rows if r['pattern']==p),
        'mutual_gain_vs_greedy_cases':sum(all(r['costs']['informed_bound'][a]<r['costs']['independent'][a] for a in ('A','B')) for r in rows if r['pattern']==p)}
        for p in ('fixed_single','paired_recurring_single')}
    return {'scope':'Independent finite assignment and simple turn-policy comparison before high preset selection; informed bound is not a policy',
            'client_source':'31adbb4cb6286b72a737cffd71201989dd051d78','rows':rows,'summary':summary}


async def trajectory(job):
    level,mode,seed,phase,policy=job
    if policy not in permitted_policies(mode):raise ValueError('Policy not permitted by communication boundary')
    m=generate(level,seed,priority_phase=phase,communication=mode);reference=check_manifest(m)
    r=new_run(m,ScriptedMock(scripts(policy)),clock=ManualClock());await r.run(192)
    verification=verify_experiment(r.records,r.world)
    assert r.state['status']=='finished'
    records=[x for x in r.records if x['event_type']=='decision.completed']
    artifact={a:Counter(dict.fromkeys(('create','revise','delete','read','inspect','booking_with_prior_period_peer_read_in_context'),0)) for a in ('A','B')};context={a:Counter() for a in ('A','B')}
    for rec in records:
        request=rec['generation']['request'];actor=rec['agent']
        assert rec['computation']['proposed_actions']==[choose(request,{'strategy':policy})]
        assert rec['consequences']['tool_response']['status']=='ok',(job,rec['consequences']['tool_response'])
        for op in rec['consequences']['memory_operations']:artifact[actor][op['kind']]+=1
        selected=rec['computation']['selected_action']
        if selected['tool']=='inspect' and selected['arguments']['path'].startswith('/volumes/common'):
            artifact[actor]['inspect']+=1
        if selected['tool']=='act' and selected['arguments']['resource']=='/schedule':
            receipt=None;peer='B' if actor=='A' else 'A'
            for msg in request['messages']:
                if msg['role']=='tool':
                    result=json.loads(msg['content'])
                    if result.get('artifact',{}).get('path')==f'/volumes/common/{peer}.json':
                        receipt=json.loads(result['artifact']['text'])
            if receipt and receipt['period']<request['observation']['world']['period']:
                artifact[actor]['booking_with_prior_period_peer_read_in_context']+=1
        c=rec['registration']['context_selection'];context[actor]['decisions']+=1
        context[actor]['included_cycle_entries']+=len(c['included_decisions'])
        context[actor]['supplied_read_receipt_entries']+=len(c['supplied_artifacts'])
        if c['excluded_decisions']:context[actor]['decisions_with_exclusions']+=1
        for e in c['excluded_decisions']:context[actor]['excluded_entries_'+e['reason']]+=1
    outcomes=r.world.measure(r.state['world']);assert outcomes['system']['completed']==24 and outcomes['system']['expired']==0
    costs={a:outcomes['individual'][a]['booking_cost'] for a in ('A','B')}
    bound=sum(sum(x.values()) for x in reference['least_combined_final_cost_per_period'])
    assert sum(costs.values())>=bound
    return {'fixture_id':identifier(level,mode,seed,phase),'pressure':level,'communication':mode,
        'seed':seed,'priority_phase':phase,'policy':policy,'manifest_sha256':digest(m),
        'completed':outcomes['system']['completed'],'expired':outcomes['system']['expired'],
        'individual_completed':{a:outcomes['individual'][a]['completed'] for a in ('A','B')},
        'individual_expired':{a:outcomes['individual'][a]['expired'] for a in ('A','B')},
        'individual_minutes':costs,'aggregate_minutes':sum(costs.values()),
        'full_information_assignment_bound_minutes':bound,'aggregate_regret_minutes':sum(costs.values())-bound,
        'generations':r.state['ledger']['calls'],'operations':{a:outcomes['individual'][a]['execution'] for a in ('A','B')},
        'artifact_operations':{a:dict(artifact[a]) for a in ('A','B')},'context':{a:dict(context[a]) for a in ('A','B')},
        'period_costs':{a:[r.state['world']['appointments'][f'p{p}:{a}']['booking_cost'] for p in range(1,13)] for a in ('A','B')},
        'mock_input_bytes':r.state['ledger']['input_tokens'],'mock_output_bytes':r.state['ledger']['output_tokens'],
        'verification':verification,'provider_calls':0,
        'policy_input_basis':'Exact authorized requests only; no hidden manifest/bound supplied',
        'artifact_exposure_interpretation':'Receipt/context inclusion and authored-policy wiring; not inferred functional registration or representation'}


def execute(job):return asyncio.run(trajectory(job))


def summarize(rows):
    indexed={(r['pressure'],r['communication'],r['seed'],r['priority_phase'],r['policy']):r for r in rows}
    for level in PRESSURES:
        for seed in SEEDS:
            for phase in PHASES:
                for p in NONSHARED:
                    ref=indexed[level,'disabled',seed,phase,p]
                    for mode in COMMUNICATION[1:]:
                        other=indexed[level,mode,seed,phase,p]
                        assert (ref['individual_minutes'],ref['generations'],ref['operations'])==(other['individual_minutes'],other['generations'],other['operations'])
    totals={};comparisons=[];eligible=[]
    for level in PRESSURES:
        totals[level]={}
        for mode in COMMUNICATION:
            totals[level][mode]={}
            for policy in permitted_policies(mode):
                values=[r for r in rows if (r['pressure'],r['communication'],r['policy'])==(level,mode,policy)]
                totals[level][mode][policy]={'cases':len(values),'completed':sum(r['completed'] for r in values),
                    'expired':sum(r['expired'] for r in values),
                    'individual_minutes':{a:sum(r['individual_minutes'][a] for r in values) for a in ('A','B')},
                    'aggregate_minutes':sum(r['aggregate_minutes'] for r in values),
                    'aggregate_regret_minutes':sum(r['aggregate_regret_minutes'] for r in values),
                    'generations':sum(r['generations'] for r in values)}
            if mode=='disabled':continue
            for seed in SEEDS:
                for phase in PHASES:
                    group={p:indexed[level,mode,seed,phase,p] for p in permitted_policies(mode)}
                    ex=group['exchange'];de=group['delta'];base=group['independent']
                    comparisons.append({'pressure':level,'communication':mode,'seed':seed,'priority_phase':phase,
                        'exchange_minus_each_nonshared_minutes':{p:{a:ex['individual_minutes'][a]-group[p]['individual_minutes'][a] for a in ('A','B')} for p in NONSHARED},
                        'exchange_minus_each_nonshared_aggregate':{p:ex['aggregate_minutes']-group[p]['aggregate_minutes'] for p in NONSHARED},
                        'delta_minus_exchange_minutes':{a:de['individual_minutes'][a]-ex['individual_minutes'][a] for a in ('A','B')},
                        'delta_call_saving':ex['generations']-de['generations'],
                        'extra_exchange_generations_per_agent_vs_independent':{a:ex['operations'][a]['generations']-base['operations'][a]['generations'] for a in ('A','B')}})
                    gains={a:base['individual_minutes'][a]-ex['individual_minutes'][a] for a in ('A','B')}
                    advantage=min(group[p]['aggregate_minutes']-ex['aggregate_minutes'] for p in NONSHARED)
                    if (level!='low' and mode=='public_metadata' and min(gains.values())>0 and advantage>0
                            and ex['individual_minutes']==de['individual_minutes'] and de['generations']<ex['generations']):
                        eligible.append({'pressure':level,'communication':mode,'seed':seed,'priority_phase':phase,
                            'individual_gain_vs_independent':gains,'aggregate_advantage_over_strongest_tested_nonshared':advantage,
                            'retained_call_saving':ex['generations']-de['generations']})
    eligible.sort(key=lambda x:(PRESSURES.index(x['pressure']),-min(x['individual_gain_vs_independent'].values()),
        -x['aggregate_advantage_over_strongest_tested_nonshared'],-x['retained_call_saving'],x['seed'],x['priority_phase']))
    selected=eligible[0] if eligible else None
    return totals,comparisons,{'criterion':'Prespecified joint comparisons; mutual gains vs independent, aggregate advantage vs every tested nonshared policy, retained call saving; lowest informative pressure first',
        'eligible':eligible,'recommended':selected,'previous_seed3_phase0':[x for x in comparisons if x['seed']==3 and x['priority_phase']==0],
        'caution':'No universal individual dominance, game equilibrium, optimal dialogue, unique file value or autonomous behavior claim'}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--workers',type=int,default=2);args=parser.parse_args()
    if not 1<=args.workers<=4:parser.error('workers must be 1..4')
    args.output.mkdir(parents=True,exist_ok=False)
    started=time.monotonic();manifest_rows=manifest_catalogue()
    (args.output/'manifests.json').write_text(json.dumps(manifest_rows,indent=2,sort_keys=True)+'\n')
    (args.output/'high-candidates.json').write_text(json.dumps(high_candidates(),indent=2,sort_keys=True)+'\n')
    jobs=[(r['manifest']['pressure']['level'],r['manifest']['communication']['mode'],r['manifest']['origin']['seed'],
           r['manifest']['priority_phase'],p) for r in manifest_rows for p in permitted_policies(r['manifest']['communication']['mode'])]
    if args.workers==1:rows=list(map(execute,jobs))
    else:
        with ProcessPoolExecutor(max_workers=args.workers,mp_context=multiprocessing.get_context('spawn')) as pool:
            rows=list(pool.map(execute,jobs,chunksize=1))
    totals,comparisons,selection=summarize(rows)
    report={'version':'pressure-communication-review-v1','status':'PASS','scope':'Authored software validation; no autonomous or scientific execution',
        'implementation':implementation_fingerprint(),'manifest_count':len(manifest_rows),'row_count':len(rows),'provider_calls':0,
        'results':rows,'totals':totals,'comparisons':comparisons,'selection':selection,
        'actual_wall_seconds':time.monotonic()-started,'workers':args.workers}
    (args.output/'results.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    if selection['recommended']:
        chosen=selection['recommended'];level=chosen['pressure'];seed=chosen['seed'];phase=chosen['priority_phase']
        configs=[('diagnostic',level,'public_metadata'),('lower_pressure','low','public_metadata'),('shared_disabled',level,'disabled')]
        selected=[{'role':role,'fixture_id':identifier(l,c,seed,phase),'manifest':generate(l,seed,priority_phase=phase,communication=c)} for role,l,c in configs]
        (args.output/'diagnostic-manifests.json').write_text(json.dumps(selected,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'status':'PASS','rows':len(rows),'manifests':len(manifest_rows),'totals':totals,'selection':selection,'provider_calls':0},indent=2))


if __name__=='__main__':main()

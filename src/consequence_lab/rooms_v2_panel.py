"""Prespecified admissible-policy panel. No provider calls or hidden policy inputs."""
import argparse
import asyncio
import json
from pathlib import Path
from .audit import verify_experiment
from .backends import ScriptedMock
from .clock import ManualClock
from .config import implementation_fingerprint
from .jsonio import digest
from .rooms_v2_manifest import generate
from .rooms_v2_reference import check_manifest
from .rooms_v2_mocks import scripts,choose
from .rooms_v2_run import new_run

SEEDS=(0,1,2,3)
PHASES=(0,1)
CONDITIONS=('easy','difficult')
POLICIES=('independent','wait_narrow','wait_costly','exchange','delta')


async def evaluate(condition,seed,phase,policy):
    m=generate(condition,seed,priority_phase=phase)
    bound=check_manifest(m)
    r=new_run(m,ScriptedMock(scripts(policy)),clock=ManualClock())
    await r.run(192)
    verification=verify_experiment(r.records,r.world)
    decisions=[x for x in r.records if x['event_type']=='decision.completed']
    # Re-execute the pure authored policy from exact permitted requests. It has
    # no manifest argument, environment handle or independent hidden-state bound.
    for rec in decisions:
        assert rec['computation']['proposed_actions']==[choose(rec['generation']['request'],{'strategy':policy})]
    outcome=r.world.measure(r.state['world']);assert outcome['system']['completed']==24
    costs={a:outcome['individual'][a]['booking_cost'] for a in ('A','B')}
    optimum=sum(sum(x.values()) for x in bound['least_combined_final_cost_per_period'])
    operations={a:{k:v for k,v in outcome['individual'][a]['execution'].items()
                   if k in ('generations','information_attempts','domain_attempts','control_attempts','information_consumed','domain_consumed')}
                for a in ('A','B')}
    return {'condition':condition,'seed':seed,'priority_phase':phase,'policy':policy,
        'manifest_sha256':digest(m),'completed':24,'individual_minutes':costs,'aggregate_minutes':sum(costs.values()),
        'full_information_assignment_bound_minutes':optimum,'aggregate_regret_minutes':sum(costs.values())-optimum,
        'generations':r.state['ledger']['calls'],'operations':operations,
        'mock_input_bytes':r.state['ledger']['input_tokens'],'mock_output_bytes':r.state['ledger']['output_tokens'],
        'policy_input_basis':'Own client, public reservations, exact permitted retained context/read receipts only',
        'oracle_scope':'Investigator-only full-information bound, not an achievable uninformed policy',
        'verification':verification,'provider_calls':0}


async def evaluate_panel():
    rows=[]
    for seed in SEEDS:
        for phase in PHASES:
            for condition in CONDITIONS:
                for policy in POLICIES:
                    rows.append(await evaluate(condition,seed,phase,policy))
    totals={}
    for condition in CONDITIONS:
        totals[condition]={}
        for policy in POLICIES:
            selected=[r for r in rows if r['condition']==condition and r['policy']==policy]
            totals[condition][policy]={'individual_minutes':{a:sum(r['individual_minutes'][a] for r in selected) for a in ('A','B')},
                'aggregate_minutes':sum(r['aggregate_minutes'] for r in selected),
                'aggregate_regret_minutes':sum(r['aggregate_regret_minutes'] for r in selected),
                'generations':sum(r['generations'] for r in selected)}
    comparisons=[]
    for seed in SEEDS:
        for phase in PHASES:
            for c in CONDITIONS:
                group={r['policy']:r for r in rows if (r['seed'],r['priority_phase'],r['condition'])==(seed,phase,c)}
                comparisons.append({'seed':seed,'priority_phase':phase,'condition':c,
                    'exchange_minus_independent_minutes':{a:group['exchange']['individual_minutes'][a]-group['independent']['individual_minutes'][a] for a in ('A','B')},
                    'exchange_minus_wait_narrow_minutes':{a:group['exchange']['individual_minutes'][a]-group['wait_narrow']['individual_minutes'][a] for a in ('A','B')},
                    'delta_call_saving':group['exchange']['generations']-group['delta']['generations']})
    assert any(r['condition']=='difficult' and r['policy']=='wait_narrow' and r['aggregate_regret_minutes']>0 for r in rows)
    assert any(r['condition']=='difficult' and r['policy']=='wait_costly' and r['aggregate_regret_minutes']>0 for r in rows)
    assert any(all(v<0 for v in x['exchange_minus_independent_minutes'].values()) for x in comparisons if x['condition']=='difficult')
    return {'status':'PASS','allocation_model':'office_burden_v1','implementation':implementation_fingerprint(),
        'seeds':list(SEEDS),'priority_phases':list(PHASES),'policies':list(POLICIES),'periods':12,
        'row_count':len(rows),'provider_calls':0,'results':rows,'totals':totals,'paired_comparisons':comparisons,
        'interpretation':'Authored-policy existence results; no universal waiting impossibility, dialogue optimality, equilibrium or unique file value claim.'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);a=p.parse_args()
    report=asyncio.run(evaluate_panel());target=Path(a.output)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x') as f:json.dump(report,f,indent=2,sort_keys=True)
    print(json.dumps({'status':report['status'],'rows':report['row_count'],'totals':report['totals'],'provider_calls':0},indent=2))


if __name__=='__main__':main()

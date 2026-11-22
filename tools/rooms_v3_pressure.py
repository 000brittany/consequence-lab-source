"""Small investigator-only allocation checks; never imported into model execution."""
import argparse
import asyncio
from copy import deepcopy
from itertools import combinations, product
from pathlib import Path

from consequence_lab.backends import ScriptedMock
from consequence_lab.clock import ManualClock
from consequence_lab.jsonio import atomic_json, digest
from consequence_lab.rooms_v3_manifest import generate
from consequence_lab.rooms_v3_reference import feasible_assignment, check_capacity, price_and_resources
from consequence_lab.rooms_v3_run import new_run
from tools.rooms_v3_validation import fixture_scripts
from tools.rooms_v3_analysis import analyze


def resolved(m):
    requests={r['id']:deepcopy(r) for r in m['requests']}
    for u in m['client_updates']:
        requests[u['appointment_id']].update(u['changes'])
        if u['outcome']=='cancel':del requests[u['appointment_id']]
    return requests


def options(m,r):
    for day,time,room in product(range(r['earliest_day'],r['latest_day']+1),r['times'],r['rooms']+[f"external:{r['owner']}"]):
        cost,resources=price_and_resources(m,r,room)
        yield {'appointment_id':r['id'],'owner':r['owner'],'day':day,'time':time,'room_id':room,'resources':resources,'price_cents':cost}


def opportunity_report(m):
    reqs=resolved(m);plan=feasible_assignment(m);conflicts=[];alternatives=[]
    for a,b in combinations(reqs.values(),2):
        days=sorted(set(range(a['earliest_day'],a['latest_day']+1)) & set(range(b['earliest_day'],b['latest_day']+1)))
        times=sorted(set(a['times']) & set(b['times']))
        if a['owner']==b['owner'] or not days or not times:continue
        resources=sorted(set(a['rooms']) & set(b['rooms']))
        if a['technician'] and b['technician']:resources.append('technician')
        if set(a['equipment']) & set(b['equipment']):resources.append('av_kit')
        if resources:
            conflicts.append({'requests':[a['id'],b['id']],'owners':[a['owner'],b['owner']],
                              'overlapping_days':days,'overlapping_times':times,'contested_resources':resources})
    for ident,current in plan['appointments'].items():
        rest=[c for k,c in plan['appointments'].items() if k!=ident]
        for other in options(m,reqs[ident]):
            amount=plan['expenditure_cents']-current['price_cents']+other['price_cents']
            if other==current or amount>m['initial_budget_cents']:continue
            try:check_capacity([*rest,other])
            except AssertionError:continue
            alternatives.append({'changed_request':ident,'from':current,'to':other,'complete_allocation_cost_cents':amount})
    assert alternatives, 'No alternative complete affordable allocation found'
    return {'manifest_sha256':digest(m),'noncancelled_requests':len(reqs),'cancelled_requests':len(m['requests'])-len(reqs),
            'budget_cents':m['initial_budget_cents'],'minimum_full_information_allocation':plan,
            'conflict_definition':'Different owners with intersecting eligible days/times and a shared unit-capacity resource. Potential conflicts, not proven unavoidable failures.',
            'conflict_pairs':conflicts,'conflict_counts':{resource:sum(resource in c['contested_resources'] for c in conflicts) for resource in ('R0','R1','technician','av_kit')},
            'affordable_complete_one_request_alternatives':alternatives,
            'interpretation':'Full-information assignments demonstrate feasibility, not attainable autonomous policy or a communication optimum.'}


async def check(output):
    output=Path(output);rows=[]
    for seed in (0,3):
        m=generate(seed,12);row=opportunity_report(m)
        root=output/f'seed{seed}';atomic_json(root/'manifest.json',m)
        for kind,external in (('feasible',False),('independent-rental',True)):
            runner=new_run(m,ScriptedMock(fixture_scripts(m,row['minimum_full_information_allocation'],external=external)),clock=ManualClock(),archive=root/kind/'research')
            await runner.run(432,retain_results=False);runner.checkpoint(runner.archive.path/'checkpoint.json')
            report=analyze(runner.archive.path);atomic_json(root/kind/'analysis.json',report)
            row[kind]={'outcomes':report['outcomes'],'calls':runner.state['ledger']['calls'],
                       'semantic':report['semantic_verification'],'independent':report['independent_domain_verification']}
            assert report['horizon_complete']
            if not external:
                assert report['outcomes']['system']['completed']==row['noncancelled_requests'] and report['outcomes']['system']['failed']==0
            else:
                rejections=[r for r in report['decisions'] if r['result'] and r['result'].get('error')=='insufficient_shared_funds']
                row[kind]['first_unaffordable_decision']={k:rejections[0][k] for k in ('day','agent','action','result')}
                assert report['outcomes']['system']['failed']>0
                row[kind]['scope']='Admissible independent rental-first existence counterexample; uses own released facts after reply; deliberately not cheapest-policy optimality claim.'
        rows.append({'seed':seed,**row})
    result={'provider_calls':0,'scope':'Two seeds, one complete allocation and one independently renting counterexample each; not a policy tournament','rows':rows}
    atomic_json(output/'pressure.json',result);return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();asyncio.run(check(a.output))

if __name__=='__main__':main()

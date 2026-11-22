"""Reproduce the unfrozen Rooms v4 budget review without model/provider calls.

Run from the repository root, with the pinned calibration dependencies installed::

    PYTHONPATH=src:. python -m tools.rooms_v4_budget_audit --output NEW_DIRECTORY

This separately specifies the MILP and validates its plans. It shares SciPy/HiGHS
with the original calibration, so it is not an independent solver-engine proof.
The original online implementation is the policy under test, not a second policy.
"""

import argparse
from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import platform
import subprocess
import time

import numpy as np
import scipy
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

import consequence_lab
from consequence_lab.config import implementation_fingerprint
from consequence_lab.rooms_v4_manifest import generate
import tools.rooms_v4_calibration as calibration


FRONTIER_FUNDING = (21000, 21500, 22000, 22500, 23000, 23500)
GUARD_FUNDING = (22500, 23000, 23500)


def require(condition, message):
    """Keep audit checks active under Python's -O flag."""
    if not condition:
        raise ValueError(message)


def effective_requests(manifest, day=None):
    """Reconstruct arrivals/replies independently of the policy's resolver."""
    requests = {
        r['id']: deepcopy(r) for r in manifest['requests']
        if day is None or r['arrival_day'] <= day
    }
    for update in manifest['client_updates']:
        if ((day is None or update['day'] <= day)
                and update['appointment_id'] in requests):
            request = requests[update['appointment_id']]
            request.update(deepcopy(update['changes']))
            request['client_status'] = (
                'cancelled' if update['outcome'] == 'cancel' else 'confirmed'
            )
    return {ident: r for ident, r in requests.items()
            if r['client_status'] != 'cancelled'}


def price_resources(manifest, request, room):
    if room == f"external:{request['owner']}":
        return manifest['prices_cents']['external'], []
    resources = ([room] + (['technician'] if request['technician'] else [])
                 + request['equipment'])
    return sum(manifest['prices_cents'][r] for r in resources), resources


def check_plan(manifest, plan, funding):
    """Check actual service-day booking eligibility, resources and cash prefixes."""
    final = effective_requests(manifest)
    require(len(plan) == len({o['appointment_id'] for o in plan}),
            'Duplicate appointment in plan')
    require(all(type(o['day']) is int and 1 <= o['day'] <= manifest['days']
                for o in plan), 'Plan contains an out-of-horizon service day')
    occupied = set()
    completed = set()
    cost = 0
    daily = []
    for day in range(1, manifest['days'] + 1):
        visible = effective_requests(manifest, day)
        by_owner = defaultdict(int)
        for option in (o for o in plan if o['day'] == day):
            ident = option['appointment_id']
            require(ident in final and ident in visible,
                    f'Unknown/unreleased appointment {ident} on day {day}')
            request = final[ident]
            require(visible[ident]['client_status'] == 'confirmed',
                    f'Unconfirmed appointment {ident} on service day')
            require(visible[ident]['times'] == request['times'],
                    f'Future requirements used for {ident}')
            require(request['earliest_day'] <= day <= request['latest_day']
                    and option['time'] in request['times']
                    and (day, option['time']) <= (
                        request['deadline']['day'], request['deadline']['time'])
                    and option['owner'] == request['owner'],
                    f'Invalid service eligibility for {ident}')
            require(option['room_id'] in request['rooms'] or (
                request['external_allowed'] and option['room_id'] ==
                f"external:{request['owner']}"), f'Invalid room for {ident}')
            price, resources = price_resources(manifest, request, option['room_id'])
            require(price == option['price_cents'] and resources == option['resources'],
                    f'Incorrect price/resources for {ident}')
            for resource in [f"client:{request['owner']}"] + resources:
                cell = day, option['time'], resource
                require(cell not in occupied, f'Conflicting service cell {cell}')
                occupied.add(cell)
            by_owner[request['owner']] += 1
            cost += price
            completed.add(ident)
        require(all(n <= manifest['ticks'] for n in by_owner.values()),
                f'Insufficient daily booking opportunities on day {day}')
        deposits = math.ceil(day / manifest['funding']['period_days'])
        cash = deposits * funding - cost
        require(cash >= 0, f'Negative cash on day {day}')
        daily.append({'day': day, 'cumulative_cost_cents': cost,
                      'available_cents': cash})
    return {
        'completed': len(completed), 'failed': len(final) - len(completed),
        'cost_cents': cost, 'ending_cash_cents': daily[-1]['available_cents'],
        'minimum_close_cash_cents': min(d['available_cents'] for d in daily),
        'tight_days': [d['day'] for d in daily if d['available_cents'] == 0],
        'daily': daily,
    }


def independent_solve(manifest, funding=None, *, minimum_funding=False,
                      allow_skip=False, expect_infeasible=False):
    """Cent-valued independent formulation, with explicit per-day constraints."""
    requests = effective_requests(manifest)
    options = []
    for ident, request in requests.items():
        for day in range(request['earliest_day'], request['latest_day'] + 1):
            for clock in request['times']:
                rooms = request['rooms'] + ([f"external:{request['owner']}"]
                                             if request['external_allowed'] else [])
                for room in rooms:
                    price, resources = price_resources(manifest, request, room)
                    options.append({
                        'appointment_id': ident, 'owner': request['owner'],
                        'day': day, 'time': clock, 'room_id': room,
                        'price_cents': price, 'resources': resources,
                    })
    n = len(options)
    require(n > 0, 'Empty benchmark case is unsupported')
    rows, lower, upper = [], [], []
    requests_to_indices, cells_to_indices = defaultdict(list), defaultdict(list)
    for i, option in enumerate(options):
        requests_to_indices[option['appointment_id']].append(i)
        for resource in [f"client:{option['owner']}"] + option['resources']:
            cells_to_indices[option['day'], option['time'], resource].append(i)
    for indices in requests_to_indices.values():
        rows.append({i: 1 for i in indices})
        lower.append(0 if allow_skip else 1)
        upper.append(1)
    for indices in cells_to_indices.values():
        rows.append({i: 1 for i in indices})
        lower.append(0)
        upper.append(1)
    for day in range(1, manifest['days'] + 1):
        row = {i: option['price_cents'] for i, option in enumerate(options)
               if option['day'] <= day}
        cycles = math.ceil(day / manifest['funding']['period_days'])
        if minimum_funding:
            row[n] = -cycles
        rows.append(row)
        lower.append(-np.inf)
        upper.append(0 if minimum_funding else cycles * funding)
    matrix = lil_matrix((len(rows), n + int(minimum_funding)), dtype=float)
    for row, values in enumerate(rows):
        for col, value in values.items():
            matrix[row, col] = value
    objective = np.zeros(n + int(minimum_funding))
    if minimum_funding:
        objective[-1] = 1
    else:
        # A missed request must dominate every possible expenditure difference.
        reward = 1 + sum(max(o['price_cents'] for o in options
                             if o['appointment_id'] == ident) for ident in requests)
        for i, option in enumerate(options):
            objective[i] = option['price_cents'] - (reward if allow_skip else 0)
    bounds_upper = np.ones(n + int(minimum_funding))
    if minimum_funding:
        # Always sufficient: fund all requests at the external price on day 1.
        bounds_upper[-1] = len(requests) * manifest['prices_cents']['external']
    solved = milp(
        objective, integrality=np.ones(n + int(minimum_funding)),
        bounds=Bounds(np.zeros(n + int(minimum_funding)), bounds_upper),
        constraints=LinearConstraint(matrix.tocsc(), lower, upper),
        options={'time_limit': 120, 'mip_rel_gap': 0},
    )
    if expect_infeasible:
        require(solved.status == 2,
                f'Expected certified infeasibility; got {solved.status}: {solved.message}')
        return {'solver': 'infeasible', 'status': int(solved.status),
                'message': solved.message, 'funding_cents': funding}
    require(solved.success and solved.status == 0 and solved.mip_gap == 0,
            f'Benchmark unresolved/failed: {solved.status}: {solved.message}')
    plan = [option for i, option in enumerate(options) if solved.x[i] > .5]
    amount = int(round(solved.x[-1])) if minimum_funding else funding
    checked = check_plan(manifest, plan, amount)
    if not allow_skip:
        require(checked['failed'] == 0, 'Supposed full-completion plan omitted requests')
    return {
        'solver': 'optimal', 'status': int(solved.status),
        'mip_gap': float(solved.mip_gap), 'objective': float(solved.fun),
        'dual_bound': float(solved.mip_dual_bound), 'funding_cents': amount,
        **checked, 'plan': plan,
    }


def compact(result):
    return {key: value for key, value in result.items() if key not in ('plan', 'daily')}


def policy_check(manifest, funding, policy):
    result = calibration.online(manifest, funding, policy)
    checked = check_plan(manifest, result['plan'], funding)
    require(all(result[key] == checked[key] for key in ('completed', 'failed', 'cost_cents')),
            f'Policy metrics disagree with independent plan check: {policy}')
    return {'policy': policy, 'funding_cents': funding, **checked, 'plan': result['plan']}


def information_guard():
    """Exercise the real policy while denying optimizer reads of hidden demand."""
    original = calibration.optimize
    checks = []

    class PublicPlannerManifest(Mapping):
        def __init__(self, manifest):
            self.public = {key: deepcopy(manifest[key]) for key in ('prices_cents', 'days')}

        def __getitem__(self, key):
            require(key in ('prices_cents', 'days'),
                    f'Unexpected optimizer manifest read: {key}')
            return self.public[key]

        def __iter__(self):
            return iter(self.public)

        def __len__(self):
            return len(self.public)

    def checked_optimize(manifest, requests, today=1, **kwargs):
        visible = effective_requests(manifest, today)
        for request in requests:
            require(request['id'] in visible and visible[request['id']] == request
                    and request['client_status'] == 'confirmed',
                    f'Optimizer received hidden/future/unconfirmed request on day {today}')
        checks.append({'seed': manifest['seed'], 'day': today,
                       'released_confirmed_input_count': len(requests)})
        return original(PublicPlannerManifest(manifest), requests, today=today, **kwargs)

    calibration.optimize = checked_optimize
    results = []
    try:
        for seed in (0, 3):
            for funding in GUARD_FUNDING:
                manifest = generate(seed, 60, cycle_budget_cents=funding)
                result = policy_check(manifest, funding, 'pooled_causal')
                require(result['failed'] == 0,
                        f'Guarded causal policy failed at seed {seed}, funding {funding}')
                results.append({'seed': seed, **result})
    finally:
        calibration.optimize = original
    require(len(checks) == 360, 'Not every expected daily optimization was guarded')
    return {
        'daily_optimizer_calls_checked': len(checks),
        'allowed_manifest_fields': ['prices_cents', 'days'],
        'request_inputs_match_independent_current_information': True,
        'scope': 'Pooled current information; not decentralized/message-policy feasibility',
        'results': results, 'checks': checks,
    }


def source_metadata():
    repository = Path(consequence_lab.__file__).resolve().parents[2]
    command = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repository,
                             capture_output=True, text=True, check=False)
    return {
        'source_commit': command.stdout.strip() if command.returncode == 0 else None,
        'core_implementation': implementation_fingerprint(),
        'python': platform.python_version(), 'numpy': np.__version__,
        'scipy': scipy.__version__,
        'audit_tool_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def run(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    certificates = {'metadata': source_metadata(), 'horizons': [], 'frontier': []}
    summary = {
        'scope': 'Offline independent budget-formulation/plan audit; shared SciPy/HiGHS solver',
        'provider_calls': 0, 'metadata': certificates['metadata'],
        'horizons': [], 'frontier': [],
    }
    try:
        for days in (30, 45, 60):
            for seed in (0, 3):
                manifest = generate(seed, days, cycle_budget_cents=23500)
                high_funding = len(manifest['requests']) * manifest['prices_cents']['external']
                cost = independent_solve(manifest, high_funding)
                funding = independent_solve(manifest, minimum_funding=True)
                below = independent_solve(manifest, funding['funding_cents'] - 1,
                                          expect_infeasible=True)
                row = {'days': days, 'seed': seed,
                       'noncancelled': len(effective_requests(manifest)),
                       'minimum_cost': cost, 'minimum_funding': funding,
                       'one_cent_below_minimum': below}
                certificates['horizons'].append({'manifest': manifest, **row})
                summary['horizons'].append({
                    **{key: row[key] for key in ('days', 'seed', 'noncancelled')},
                    'minimum_cost': compact(cost), 'minimum_funding': compact(funding),
                    'one_cent_below_minimum': below,
                })
        for seed in (0, 3):
            for funding in FRONTIER_FUNDING:
                manifest = generate(seed, 60, cycle_budget_cents=funding)
                hindsight = independent_solve(manifest, funding, allow_skip=True)
                causal = policy_check(manifest, funding, 'pooled_causal')
                greedy = policy_check(manifest, funding, 'independent_cheapest_today')
                if funding >= 22500:
                    require(causal['failed'] == 0, 'Causal feasibility claim did not reproduce')
                row = {'seed': seed, 'funding_cents': funding,
                       'hindsight': hindsight, 'causal_pooled': causal, 'private_greedy': greedy}
                certificates['frontier'].append(row)
                summary['frontier'].append({
                    'seed': seed, 'funding_cents': funding,
                    'hindsight': compact(hindsight), 'causal_pooled': compact(causal),
                    'private_greedy': compact(greedy),
                })
        guarded = information_guard()
        certificates['information_guard'] = guarded
        summary['information_guard'] = {
            key: value for key, value in guarded.items() if key not in ('results', 'checks')
        }
        summary['funding_comparison'] = [
            {'seed': row['seed'], 'funding_cents': row['funding_cents'],
             **{key: row['causal_pooled'][key] for key in (
                 'completed', 'cost_cents', 'ending_cash_cents',
                 'minimum_close_cash_cents', 'tight_days')},
             'total_deposits_cents': 12 * row['funding_cents'],
             'headroom_percent_of_minimum_spend':
                 100 * row['causal_pooled']['ending_cash_cents'] / row['causal_pooled']['cost_cents']}
            for row in certificates['frontier'] if row['funding_cents'] in (23000, 23500)
        ]
        summary['limitations'] = [
            'Two seeded demand streams; no statistical generalization to other streams.',
            'Shared solver engine; independent formulation and primal checks, not an independent optimizer implementation.',
            'Daily closing cash assumes booking on the service day; premature escrow may constrain cash further.',
            'Causal pooled feasibility is not a generally optimal online policy or autonomous agent evidence.',
            'No proof of feasibility under the lifetime message quota or six-cycle conversation window.',
        ]
        summary['wall_seconds'] = time.monotonic() - started
        summary['status'] = 'passed'
        payload = (json.dumps(certificates, indent=2, allow_nan=False) + '\n').encode()
        (output / 'certificates.json').write_bytes(payload)
        summary['certificates_sha256'] = hashlib.sha256(payload).hexdigest()
        (output / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
        print(json.dumps({'status': 'passed', 'output': str(output),
                          'wall_seconds': summary['wall_seconds']}))
        return summary
    except Exception as error:
        # No passing summary is published after an unresolved or failed check.
        (output / 'failure.json').write_text(json.dumps({
            'status': 'blocked', 'error_type': type(error).__name__, 'error': str(error),
            'wall_seconds': time.monotonic() - started,
        }, indent=2) + '\n')
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path,
                        help='New directory; existing output directories are never overwritten')
    arguments = parser.parse_args()
    run(arguments.output)


if __name__ == '__main__':
    main()

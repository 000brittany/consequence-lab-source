"""Manual, source-pinned Rooms v3 launch boundary. No decision policy or retries."""
import argparse
import asyncio
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.request
import platform
from importlib.metadata import distributions

SOURCE = '237399fc30760af9012659c8fdb1ea658c51dea4'
WORKFLOW = 'rooms-v3.yml'
VERSION = 'rooms-v3-office-days-v3-continuation-v1'
CASES = {f'{kind}{seed}_{effort}': {'days': days, 'seed': seed, 'effort': effort,
                                  'calls': 3 * days * 4 * 3, 'usd_cap': cap, 'active_seconds': 7200}
         for kind, days, cap in (('screen', 4, 2), ('explore', 12, 10))
         for seed in (0, 3) for effort in ('medium', 'high')}
CASES = {'smoke0_medium': {'days': 4, 'seed': 0, 'effort': 'medium', 'calls': 8,
                          'usd_cap': 1, 'active_seconds': 900}, **CASES}


def authorization(case):
    c = CASES[case]
    return f'AUTHORIZE_ROOMS_V3_V3_CONTINUATION_V1_{case.upper()}_{c["calls"]}_CALLS_USD_{c["usd_cap"]}'


def title(case, mode='paid'):
    if case not in CASES or mode not in ('paid', 'offline'):
        raise ValueError('Unknown v3 case/mode')
    return f'Rooms v3 | {mode} | case={case} | continuation-v1 | core={SOURCE[:7]}'


def validate(env, mode, case):
    title(case, mode)
    if env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch' or env.get('GITHUB_REPOSITORY') != '000brittany/consequence-lab':
        raise ValueError('Manual dispatch in the reviewed repository required')
    token = env.get('PAID_AUTHORIZATION', 'NO')
    if mode == 'offline':
        if token != 'NO':
            raise ValueError('Offline requires NO authorization')
    else:
        if token != authorization(case):
            raise ValueError('Separate case-specific paid authorization required')
        if env.get('GITHUB_REF') != 'refs/heads/' + env.get('DEFAULT_BRANCH', ''):
            raise ValueError('Paid execution requires reviewed default branch')
        if env.get('GITHUB_RUN_ATTEMPT') != '1':
            raise ValueError('Paid workflow reruns prohibited')


def check_history(env, case, fetch):
    current = int(env['GITHUB_RUN_ID']); seen = False; count = 0
    for page in range(1, 11):
        data = fetch(page)
        if data['total_count'] > 1000:
            raise ValueError('History exceeds verified guard capacity')
        runs = data['workflow_runs']; count += len(runs)
        for run in runs:
            if int(run['id']) == current:
                seen = True
                if run['display_title'] != title(case):
                    raise ValueError('Paid launch identity mismatch')
            elif run['display_title'] == title(case):
                raise ValueError('Case already claimed by a paid dispatch, including queued/failed/completed attempts')
        if len(runs) < 100:
            break
    else:
        raise ValueError('Incomplete history')
    if not seen or count != data['total_count']:
        raise ValueError('Cannot establish complete workflow history')
    return {'passed': True, 'identity': title(case), 'run_id': current, 'dispatches_checked': count,
            'rule': 'One paid claim per exact source/case; no failed-run exemptions'}


def history_page(env, page):
    url = (f'https://api.github.com/repos/{env["GITHUB_REPOSITORY"]}/actions/workflows/'
           f'{WORKFLOW}/runs?event=workflow_dispatch&per_page=100&page={page}')
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + env['ACTIONS_READ_TOKEN'],
        'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def guard(args):
    env = os.environ; args.output.mkdir(parents=True, exist_ok=True)
    record = {k.lower(): env.get(k) for k in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA',
              'GITHUB_WORKFLOW_SHA', 'GITHUB_WORKFLOW_REF', 'GITHUB_REF', 'GITHUB_ACTOR', 'GITHUB_TRIGGERING_ACTOR')}
    record.update(source=SOURCE, version=VERSION, case=args.case, mode=args.mode,
                  identity=title(args.case, args.mode), configuration=CASES[args.case], approval=env.get('PAID_AUTHORIZATION', 'NO'),
                  preflight='not_passed')
    write(args.output / 'launch.json', record)
    validate(env, args.mode, args.case)
    if args.mode == 'paid':
        write(args.output / 'duplicate-check.json', check_history(env, args.case, lambda p: history_page(env, p)))
    record['preflight'] = 'passed'; write(args.output / 'launch.json', record)


def source_check(core):
    from consequence_lab import __file__ as package_file
    head = subprocess.check_output(['git', '-C', str(core), 'rev-parse', 'HEAD'], text=True).strip()
    if head != SOURCE or not Path(package_file).resolve().is_relative_to((core / 'src').resolve()):
        raise ValueError('Exact scientific commit/import required')
    if subprocess.check_output(['git', '-C', str(core), 'status', '--porcelain'], text=True).strip():
        raise ValueError('Scientific source checkout is dirty')
    return head


async def mark_call_cap(runner, report):
    """Record the existing cap boundary; this cannot invoke a generation."""
    from consequence_lab.runner import RunLimitReached
    if report['reason'] == 'configured_call_cap' and runner.state['status'] == 'running':
        if runner.state['ledger']['calls'] != runner.state['config']['limits']['calls']:
            raise ValueError('Cannot finalize an unreached call cap')
        try:
            await runner.step()
        except RunLimitReached as exc:
            if str(exc) != 'calls':
                raise
        else:
            raise RuntimeError('Call cap failed')
        runner.checkpoint(runner.archive.path / 'checkpoint.json')
        report.update(reason='calls', state_status=runner.state['status'], event_count=len(runner.records), ledger=runner.state['ledger'])
        write(runner.archive.path / 'calibration.json', report)
    return report


async def execute(args):
    from consequence_lab.calibration import run_calibration
    from consequence_lab.jsonio import digest
    from consequence_lab.provider import ResponsesBackend, LunaConfig, HTTPTransport
    from consequence_lab.rooms_v3_manifest import generate
    from consequence_lab.rooms_v3_run import new_run, limits, instructions
    from tools.rooms_v3_analysis import analyze
    validate(os.environ, args.mode, args.case); source_check(args.core.resolve())
    launch = json.loads((args.output / 'launch.json').read_text())
    expected = {'preflight': 'passed', 'source': SOURCE, 'case': args.case, 'mode': args.mode,
                'identity': title(args.case, args.mode), 'configuration': CASES[args.case],
                'approval': os.environ.get('PAID_AUTHORIZATION', 'NO')}
    if any(launch.get(k) != v for k, v in expected.items()):
        raise ValueError('Matching successful guard required')
    if args.mode == 'paid':
        checked = json.loads((args.output / 'duplicate-check.json').read_text())
        if (not checked['passed'] or checked['identity'] != title(args.case) or checked['run_id'] != int(os.environ['GITHUB_RUN_ID'])
                or launch['github_run_id'] != os.environ['GITHUB_RUN_ID'] or launch['github_run_attempt'] != '1'
                or launch['github_workflow_sha'] != os.environ.get('GITHUB_WORKFLOW_SHA')):
            raise ValueError('Matching first-attempt dispatch guard required')
    case = CASES[args.case]; paid = args.mode == 'paid'
    m = generate(case['seed'], case['days']); base_cap = limits(case['days'], screen=args.case.startswith('screen'))
    if 3 * case['days'] * 4 * 3 < case['calls'] or base_cap.spend_nano_usd < case['usd_cap'] * 1_000_000_000 or base_cap.elapsed_seconds < case['active_seconds']:
        raise ValueError('Case limit mismatch')
    cap = replace(base_cap, calls=case['calls'], spend_nano_usd=case['usd_cap'] * 1_000_000_000, elapsed_seconds=case['active_seconds'])
    profile = LunaConfig(reasoning_effort=case['effort'])
    write(args.output / 'manifest.json', m)
    write(args.output / 'source.json', {'scientific_commit': SOURCE, 'manifest_sha256': digest(m),
        'case': args.case, 'limits': asdict(cap), 'model_configuration': asdict(profile),
        'instructions': instructions(), 'instructions_sha256': digest(instructions()),
        'launcher_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'analysis_sha256': hashlib.sha256(Path(__file__).with_name('rooms_v3_analysis.py').read_bytes()).hexdigest(),
        'python': platform.python_version(),
        'packages': sorted([{'name': d.metadata['Name'], 'version': d.version} for d in distributions()], key=lambda x: (x['name'], x['version'])),
        'workflow_commit': os.environ.get('GITHUB_WORKFLOW_SHA'),
        'phase': 'emergence discovery; no positive result is required for instrument validity'})
    backend = ResponsesBackend(profile, HTTPTransport(allow_provider_calls=True) if paid else None)
    runner = new_run(m, backend, archive=args.output / 'research', cap=cap, allow_provider_calls=paid)
    from consequence_lab.archive import FileRecords
    if cap.calls > base_cap.calls and not isinstance(runner.records, FileRecords):
        raise ValueError('Higher reviewed ceiling requires lossless archive-backed records')
    runner.provenance['rooms_v3'] = {'source': SOURCE, 'manifest_sha256': digest(m), 'launch_identity': launch['identity']}
    runner.settle(); runner.checkpoint(args.output / 'research' / 'checkpoint.json')
    if paid:
        report = await mark_call_cap(runner, await run_calibration(runner))
        report['scope'] = 'Technical provider smoke/screen, not a substantive trajectory' if not args.case.startswith('explore') else 'Emergence-discovery exploratory trajectory; no automatic representational inferences'
        write(args.output / 'research' / 'calibration.json', report)
        write(args.output / 'status.json', report)
        write(args.output / 'analysis.json', analyze(args.output / 'research', allow_incomplete=True))
        if report['state_status'] == 'paused_provider':
            raise RuntimeError('Fatal provider/integrity boundary; preserve records. No retry or corrective action.')
    else:
        write(args.output / 'status.json', {'mode': 'offline', 'actual_generations': 0, 'provider_calls': 0,
              'scope': 'Pinned configuration initialized without a provider transport; separate authored fixtures validate trajectories'})
        write(args.output / 'analysis.json', analyze(args.output / 'research'))


def main():
    p = argparse.ArgumentParser(); p.add_argument('command', choices=('guard', 'run'))
    p.add_argument('--case', choices=tuple(CASES), required=True); p.add_argument('--mode', choices=('offline', 'paid'), required=True)
    p.add_argument('--output', type=Path, required=True); p.add_argument('--core', type=Path, default=Path('.'))
    args = p.parse_args()
    if args.command == 'guard':
        guard(args)
    else:
        asyncio.run(execute(args))


if __name__ == '__main__':
    main()

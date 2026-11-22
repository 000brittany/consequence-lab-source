"""Manual Actions boundary adapted from the owner's Colosseum launch pattern."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.request

BASELINE = '5a6267052fffbc0e3aec8da819d7dc47f6d66ee4'
AUTHORIZATION = 'AUTHORIZE_ONE_CALIBRATION_8_CALLS_USD_0_33'
RECOVERY_AUTHORIZATION = 'AUTHORIZE_GENEROUS_AFTER_42222898985_ONCE_USD_0_33'
FAILED_CREDENTIAL_RUN = 42222898985
FAILED_CREDENTIAL_HEAD = 'dfbde1065b6cd150c79a20d4b4ccaf76c329d9ee'
WORKFLOW = 'luna-calibration.yml'


def write(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def title(setting, recovery=False):
    base = f'Luna calibration | paid | setting={setting} | baseline=5a62670'
    return base + (' | recovery=42222898985-once' if recovery else '')


def validate(env, mode, setting, authorization):
    if mode not in ('offline', 'paid') or setting not in ('generous', 'moderate'):
        raise ValueError('Invalid mode or setting')
    if env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch':
        raise ValueError('Manual dispatch required')
    if mode == 'offline':
        if authorization != 'NO':
            raise ValueError('Offline mode requires NO authorization')
    else:
        if authorization not in (AUTHORIZATION, RECOVERY_AUTHORIZATION):
            raise ValueError('Separate paid authorization required')
        if authorization == RECOVERY_AUTHORIZATION and setting != 'generous':
            raise ValueError('Credential recovery is authorized only for generous')
        if env.get('GITHUB_REF') != 'refs/heads/' + env.get('DEFAULT_BRANCH', ''):
            raise ValueError('Paid dispatch requires the default branch')
        if env.get('GITHUB_RUN_ATTEMPT') != '1':
            raise ValueError('Paid reruns prohibited; inspect original artifacts')


def check_history(env, setting, fetch):
    """One paid dispatch per baseline/setting, even if an earlier run failed."""
    current, seen, checked = int(env['GITHUB_RUN_ID']), False, 0
    recovery = env.get('PAID_AUTHORIZATION') == RECOVERY_AUTHORIZATION
    if recovery and (setting != 'generous' or current == FAILED_CREDENTIAL_RUN):
        raise ValueError('Recovery requires a new generous dispatch')
    failure_seen = False
    for page in range(1, 11):
        data = fetch(page)
        if data['total_count'] > 1000:
            raise ValueError('History exceeds guard capacity')
        runs = data['workflow_runs']
        checked += len(runs)
        for run in runs:
            seen |= int(run['id']) == current
            if recovery and int(run['id']) == current and run['display_title'] != title(setting, True):
                raise ValueError('Recovery launch identity mismatch')
            if recovery and int(run['id']) == FAILED_CREDENTIAL_RUN:
                if (run['display_title'] != title('generous') or run.get('head_sha') != FAILED_CREDENTIAL_HEAD
                        or run.get('event') != 'workflow_dispatch' or run.get('status') != 'completed'
                        or run.get('conclusion') != 'failure' or run.get('run_attempt') != 1):
                    raise ValueError('Reviewed credential-failure identity/status changed')
                failure_seen = True
                continue
            if int(run['id']) != current and run['display_title'] in (title(setting), title(setting, True)):
                raise ValueError('Prior paid dispatch exists; no second execution allowed')
        if len(runs) < 100:
            break
    else:
        raise ValueError('Incomplete history')
    if not seen or checked < data['total_count']:
        raise ValueError('Cannot establish complete dispatch history')
    if recovery and not failure_seen:
        raise ValueError('Reviewed credential failure absent from complete history')
    return {'passed': True, 'dispatches_checked': checked, 'identity': title(setting, recovery),
            'exempted_run_id': FAILED_CREDENTIAL_RUN if recovery else None}


def history_page(env, page):
    url = (f"https://api.github.com/repos/{env['GITHUB_REPOSITORY']}/actions/workflows/"
           f'{WORKFLOW}/runs?event=workflow_dispatch&per_page=100&page={page}')
    req = urllib.request.Request(url, headers={
        'Authorization': 'Bearer ' + env['ACTIONS_READ_TOKEN'],
        'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def guard(args):
    args.output.mkdir(parents=True, exist_ok=True)
    env = os.environ
    record = {k.lower(): env.get(k) for k in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT',
              'GITHUB_WORKFLOW_REF', 'GITHUB_WORKFLOW_SHA', 'GITHUB_SHA', 'GITHUB_REF')}
    record.update(baseline_commit=BASELINE, mode=args.mode, setting=args.setting, preflight='not_passed')
    recovery = env.get('PAID_AUTHORIZATION') == RECOVERY_AUTHORIZATION
    record.update(launch_identity=title(args.setting, recovery) if args.mode == 'paid' else 'offline',
                  recovery_of_run=FAILED_CREDENTIAL_RUN if recovery else None)
    write(args.output/'launch.json', record)
    validate(env, args.mode, args.setting, env.get('PAID_AUTHORIZATION', 'NO'))
    if args.mode == 'paid':
        write(args.output/'duplicate-check.json', check_history(env, args.setting, lambda p: history_page(env, p)))
    record['preflight'] = 'passed'
    write(args.output/'launch.json', record)


def source_check(core):
    from consequence_lab import __file__ as package_file
    head = subprocess.check_output(['git', '-C', str(core), 'rev-parse', 'HEAD'], text=True).strip()
    if head != BASELINE or not Path(package_file).resolve().is_relative_to((core/'src').resolve()):
        raise ValueError('Exact frozen source/import required')
    if subprocess.check_output(['git', '-C', str(core), 'status', '--porcelain'], text=True).strip():
        raise ValueError('Frozen checkout is dirty')
    return head


async def run(args):
    from importlib.metadata import distributions
    from consequence_lab.archive import Archive
    from consequence_lab.audit import verify_experiment
    from consequence_lab.calibration import context_settings, readiness_report, run_calibration
    from consequence_lab.catalogue import manifest
    from consequence_lab.provider import HTTPTransport, ResponsesBackend, prepare_run
    head = source_check(args.core)
    validate(os.environ, args.mode, args.setting, os.environ.get('PAID_AUTHORIZATION', 'NO'))
    launch = json.loads((args.output/'launch.json').read_text())
    recovery = os.environ.get('PAID_AUTHORIZATION') == RECOVERY_AUTHORIZATION
    identity = title(args.setting, recovery) if args.mode == 'paid' else 'offline'
    if (launch['preflight'] != 'passed' or launch['mode'] != args.mode or launch['setting'] != args.setting
            or launch.get('launch_identity') != identity):
        raise ValueError('Matching successful launch guard required')
    if args.mode == 'paid' and not (args.output/'duplicate-check.json').is_file():
        raise ValueError('Paid history check required')
    chosen = context_settings(args.setting)
    limits = chosen['limits']
    assert (limits.calls, limits.spend_nano_usd, limits.output_tokens) == (8, 330_000_000, 8192)
    write(args.output/'source.json', {'baseline_commit': head,
        'launcher_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'packages': sorted([{'name': d.metadata['Name'], 'version': d.version} for d in distributions()], key=lambda x: x['name'])})
    write(args.output/'readiness.json', readiness_report())
    paid = args.mode == 'paid'
    if paid:
        write(args.output/'credentials-status.json', {'repository_secret_available': bool(os.environ.get('OPENAI_API_KEY'))})
    if paid and not os.environ.get('OPENAI_API_KEY'):
        raise ValueError('Repository OPENAI_API_KEY secret unavailable to this job')
    backend = ResponsesBackend(transport=HTTPTransport(allow_provider_calls=True)) if paid else ResponsesBackend()
    runner = prepare_run(manifest(), policy=args.setting, backend=backend,
                         archive=args.output/'research', allow_provider_calls=paid)
    runner.checkpoint(args.output/'research'/'checkpoint.json')
    if paid:
        # Existing driver: one setting only; no strategy, retries or new analysis.
        report = await run_calibration(runner)
        write(args.output/'execution-status.json', {'complete_driver_return': True, 'reason': report['reason']})
    else:
        # Zero generations, no key and no transport. Exercise initialization/I/O.
        runner.settle()
        runner.checkpoint(args.output/'research'/'checkpoint.json')
        write(args.output/'execution-status.json', {'offline': True, 'provider_calls': 0, 'ledger': runner.state['ledger']})
    write(args.output/'verification.json', verify_experiment(Archive.read(args.output/'research'), runner.world))
    if paid and report['reason'] in ('provider_or_execution_error', 'agent_protocol_rejection'):
        raise RuntimeError('Calibration stopped; review preserved research records')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('guard', 'run'))
    parser.add_argument('--mode', choices=('offline', 'paid'), required=True)
    parser.add_argument('--setting', choices=('generous', 'moderate'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--core', type=Path)
    args = parser.parse_args()
    try:
        if args.stage == 'guard':
            guard(args)
        else:
            asyncio.run(run(args))
    except Exception as exc:
        # Never print exception strings, environment dumps or provider bodies.
        args.output.mkdir(parents=True, exist_ok=True)
        write(args.output/'launcher-error.json', {'stage': args.stage, 'error_type': type(exc).__name__,
            'record_status': 'inspect research records and pending marker; never replay uncertain calls'})
        raise SystemExit('Launcher stopped; inspect downloaded artifacts') from None


if __name__ == '__main__':
    main()

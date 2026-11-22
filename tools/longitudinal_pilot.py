"""Manual launch only. Scientific analysis is a separate read-only process."""
import argparse
import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path
import urllib.request

from tools.calibration_launcher import write
from tools.pilot_spec import SOURCE,VERSION,SCENARIOS,scenario,limits,authorization,title
from tools import pilot_recovery


def source_check(core):
    import consequence_lab
    head=subprocess.check_output(['git','-C',str(core),'rev-parse','HEAD'],text=True).strip()
    dirty=subprocess.check_output(['git','-C',str(core),'status','--porcelain','--untracked-files=all'],text=True)
    if head!=SOURCE or dirty or not Path(consequence_lab.__file__).resolve().is_relative_to(core/'src'):
        raise ValueError('Exact clean scientific source and matching import required')


def validate(env,mode,name,approval):
    if name not in SCENARIOS:raise ValueError('Unknown pilot scenario')
    if env.get('GITHUB_EVENT_NAME')!='workflow_dispatch' or mode not in ('offline','paid'):
        raise ValueError('Manual dispatch required')
    recovery=pilot_recovery.enabled(env)
    if recovery:
        if name!='diagnostic' or env.get('GITHUB_RUN_ATTEMPT')!='1':
            raise ValueError('Protocol recovery requires diagnostic and a first attempt')
        pilot_recovery.configuration(SOURCE,VERSION)
    if mode=='offline':
        if approval!='NO':raise ValueError('Offline authorization must be NO')
    elif (approval!=authorization(name,recovery) or env.get('GITHUB_RUN_ATTEMPT')!='1'
          or env.get('GITHUB_REF')!='refs/heads/'+env.get('DEFAULT_BRANCH','')):
        raise ValueError('Distinct scenario approval, default branch and first attempt required')


def check_history(env,name,fetch,*,mode='paid',origin=None):
    current=int(env['GITHUB_RUN_ID']);seen=False;checked=0
    recovery=pilot_recovery.enabled(env);failure_seen=False
    if recovery:
        if name!='diagnostic' or current==pilot_recovery.RUN or env.get('GITHUB_RUN_ATTEMPT')!='1':
            raise ValueError('Recovery requires a new first-attempt diagnostic dispatch')
        pilot_recovery.configuration(SOURCE,VERSION)
        pilot_recovery.require_origin(origin)
    # A protocol/source repair is not authorization to repeat an earlier paid
    # condition. Keep the duplicate boundary across exact-source pin changes.
    identity_prefix=title(name).rsplit(' | core=',1)[0]+' | core='
    for page in range(1,11):
        data=fetch(page)
        if data['total_count']>1000:raise ValueError('History exceeds guard capacity')
        checked+=len(data['workflow_runs'])
        for r in data['workflow_runs']:
            seen |= int(r['id'])==current
            if int(r['id'])==current and r['display_title']!=title(name,recovery,mode):raise ValueError('Identity mismatch')
            if recovery and int(r['id'])==pilot_recovery.RUN:
                pilot_recovery.original_run(r);failure_seen=True
                continue
            if (recovery and int(r['id'])!=current and r['display_title'].startswith('Rooms v2 pilot | paid | ')
                    and r['display_title'].endswith(pilot_recovery.SUFFIX)):
                raise ValueError('Prior paid protocol-recovery dispatch exists, including misconfigured attempts')
            if int(r['id'])!=current and r['display_title'].startswith(identity_prefix):raise ValueError('Prior/queued paid dispatch exists')
        if len(data['workflow_runs'])<100:break
    else:raise ValueError('Incomplete history')
    if not seen or checked<data['total_count']:raise ValueError('Incomplete history')
    if recovery and not failure_seen:raise ValueError('Exact original failure absent from history')
    return {'passed':True,'identity':title(name,recovery,mode),'dispatches_checked':checked,
        'exempted_run_id':pilot_recovery.RUN if recovery else None,
        'original_stop_reason':origin['original_stop_reason'] if recovery else None,
        'consumes_paid_recovery':recovery and mode=='paid'}


def history_page(env,page):
    url=f"https://api.github.com/repos/{env['GITHUB_REPOSITORY']}/actions/workflows/longitudinal-pilot.yml/runs?event=workflow_dispatch&per_page=100&page={page}"
    req=urllib.request.Request(url,headers={'Authorization':'Bearer '+env['ACTIONS_READ_TOKEN'],
                               'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
    with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)


def guard(a):
    a.output.mkdir(parents=True,exist_ok=True)
    record={k.lower():os.environ.get(k) for k in ('GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT','GITHUB_SHA','GITHUB_WORKFLOW_SHA','GITHUB_WORKFLOW_REF','GITHUB_REF','GITHUB_ACTOR','GITHUB_TRIGGERING_ACTOR','GITHUB_EVENT_NAME')}
    record.update(source_commit=SOURCE,pilot_version=VERSION,mode=a.mode,scenario=a.scenario,preflight='not_passed')
    recovery=pilot_recovery.enabled(os.environ)
    record.update(launch_identity=title(a.scenario,recovery,a.mode),
        recovery_of_run=pilot_recovery.RUN if recovery else None,
        recovery_scope='protocol-repair replacement for an aborted run; not an independent replicate' if recovery else None)
    write(a.output/'launch.json',record)
    validate(os.environ,a.mode,a.scenario,os.environ.get('PAID_AUTHORIZATION','NO'))
    if a.mode=='paid' or recovery:
        origin=pilot_recovery.load_origin(os.environ,a.output) if recovery else None
        write(a.output/'duplicate-check.json',check_history(os.environ,a.scenario,
            lambda p:history_page(os.environ,p),mode=a.mode,origin=origin))
    record['approval']=os.environ.get('PAID_AUTHORIZATION','NO')
    record['preflight']='passed';write(a.output/'launch.json',record)


async def execute(a):
    from importlib.metadata import distributions
    from consequence_lab.calibration import run_calibration
    from consequence_lab.provider import ResponsesBackend,HTTPTransport
    from consequence_lab.rooms_v2_run import new_run
    source_check(a.core.resolve())
    validate(os.environ,a.mode,a.scenario,os.environ.get('PAID_AUTHORIZATION','NO'))
    recovery=pilot_recovery.enabled(os.environ)
    launch=json.loads((a.output/'launch.json').read_text())
    if (launch['preflight'],launch['mode'],launch['scenario'],launch['source_commit'])!=('passed',a.mode,a.scenario,SOURCE):
        raise ValueError('Matching successful guard required')
    if (launch.get('launch_identity')!=title(a.scenario,recovery,a.mode)
            or launch.get('recovery_of_run')!=(pilot_recovery.RUN if recovery else None)
            or launch.get('approval')!=os.environ.get('PAID_AUTHORIZATION','NO')):
        raise ValueError('Matching recovery identity/authorization required')
    if a.mode=='paid' or recovery:
        checked=json.loads((a.output/'duplicate-check.json').read_text())
        if (not checked['passed'] or checked['identity']!=title(a.scenario,recovery,a.mode)
                or launch['github_run_id']!=os.environ['GITHUB_RUN_ID']
                or launch['github_run_attempt']!=os.environ['GITHUB_RUN_ATTEMPT']
                or checked.get('exempted_run_id')!=(pilot_recovery.RUN if recovery else None)
                or checked.get('consumes_paid_recovery')!=(recovery and a.mode=='paid')):
            raise ValueError('Matching first-attempt history check required')
    m=scenario(a.scenario);cap=limits()
    origin=None
    if recovery:
        from dataclasses import asdict
        pilot_recovery.configuration(SOURCE,VERSION,m,asdict(cap))
        origin=json.loads((a.output/'recovery-origin.json').read_text())
        pilot_recovery.require_origin(origin)
    write(a.output/'manifest.json',m)
    from consequence_lab.jsonio import digest
    from tools import pilot_spec
    write(a.output/'source.json',{'core':SOURCE,'pilot_version':VERSION,'manifest_sha256':digest(m),
          'pressure':m['pressure'],'communication_policy':m['communication'],
          'pilot_spec_sha256':hashlib.sha256(Path(pilot_spec.__file__).read_bytes()).hexdigest(),
          'launcher_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          **({'recovery_origin':origin,'recovery_guard_sha256':hashlib.sha256(Path(pilot_recovery.__file__).read_bytes()).hexdigest()} if recovery else {}),
          'packages':sorted([{'name':d.metadata['Name'],'version':d.version} for d in distributions()],key=lambda d:d['name'])})
    paid=a.mode=='paid'
    if paid and not os.environ.get('OPENAI_API_KEY'):raise ValueError('Repository secret unavailable')
    backend=ResponsesBackend(transport=HTTPTransport(allow_provider_calls=True)) if paid else ResponsesBackend()
    runner=new_run(m,backend,cap=cap,archive=a.output/'research',allow_provider_calls=paid)
    runner.checkpoint(a.output/'research'/'checkpoint.json')
    if paid:
        # Reuse the frozen bounded driver and error stops, without policy logic.
        report=await run_calibration(runner)
        write(a.output/'pilot-status.json',{'scope':'exploratory pilot execution, not scientific interpretation',
              'driver_reason':report['reason'],'status':runner.state['status'],'horizon_reached':runner.state['status']=='finished'})
        if report['reason'] in ('provider_or_execution_error','agent_protocol_rejection'):
            raise RuntimeError('Review preserved error records; no automatic retry')
    else:
        runner.settle();runner.checkpoint(a.output/'research'/'checkpoint.json')
        write(a.output/'pilot-status.json',{'offline_initialization':True,'provider_calls':0})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage',choices=('guard','run'))
    p.add_argument('--mode',choices=('offline','paid'),required=True);p.add_argument('--scenario',choices=SCENARIOS,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--core',type=Path);a=p.parse_args()
    try:
        if a.stage=='guard':guard(a)
        else:asyncio.run(execute(a))
    except Exception as exc:
        a.output.mkdir(parents=True,exist_ok=True)
        write(a.output/'launcher-error.json',{'stage':a.stage,'error_type':type(exc).__name__,'status':'inspect complete/partial records; never replay pending calls'})
        raise SystemExit('Pilot launcher stopped; inspect artifacts') from None


if __name__=='__main__':main()

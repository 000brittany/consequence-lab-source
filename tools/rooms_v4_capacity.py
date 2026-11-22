"""Offline long-horizon capacity and causal-plan replay; never a paid launcher."""
import argparse
import hashlib
import asyncio
import json
from pathlib import Path
import resource
import time

from consequence_lab.archive import Archive, unseal
from consequence_lab.audit import verify_experiment
from consequence_lab.backends import ScriptedMock
from consequence_lab.clock import ManualClock
from consequence_lab.jsonio import atomic_json, load_json
from consequence_lab.rooms_v4_manifest import generate
from consequence_lab.rooms_v4_reference import verify_domain
from consequence_lab.rooms_v4_run import new_run
from consequence_lab.runner import Runner
from tools.rooms_v3_validation import action
from tools.rooms_v4_export import export, verify_export


def verify(root):
    root=Path(root);records=Archive.read(root)
    from consequence_lab.rooms_v4 import RoomsV4
    semantic=verify_experiment(records,RoomsV4());independent=verify_domain(records)
    restored=Runner.restore(root/'checkpoint.json')
    return {'semantic':semantic,'independent':independent,'restoration':'exact validated checkpoint',
            'calls':restored.state['ledger']['calls'],'horizon_complete':restored.state['status']=='finished'}


async def run(output,days=60,seed=0,mode='files',funding=23500,generate_only=False):
    output=Path(output);started=time.monotonic();m=generate(seed,days,cycle_budget_cents=funding)
    scripts={a:[] for a in m['actors']};baseline=None
    if mode=='plan':
        from tools.rooms_v4_calibration import online
        baseline=online(m,funding,'pooled_causal')
        # online exposes aggregate metrics; recover its causal service-day plans
        # through the same function's optional plan output, never hindsight data.
        plan=baseline.pop('plan')
    for day in range(1,days+1):
        for actor in m['actors']:
            if mode=='files':
                scripts[actor].append({'tool':'write','arguments':{'path':'/home/capacity-note',
                    'text':f'authored capacity fixture day={day}; '+''.join(hashlib.sha256(f'{actor}/{day}/{j}'.encode()).hexdigest() for j in range(16)),'disposition':'continue'}})
                scripts[actor].append({'tool':'read','arguments':{'path':'/home/capacity-note','disposition':'continue'}})
            bookings=[c for c in plan if c['day']==day and c['owner']==actor] if mode=='plan' else []
            if not bookings:scripts[actor].append(action('yield',disposition='yield_period'))
            for i,c in enumerate(bookings):
                scripts[actor].append(action('book',c['appointment_id'],day=c['day'],time=c['time'],room_id=c['room_id'],
                    disposition='yield_period' if i==len(bookings)-1 else 'yield_tick'))
    r=new_run(m,ScriptedMock(scripts),archive=output/'research',clock=ManualClock())
    await r.run(36*days,retain_results=False);generation_seconds=time.monotonic()-started
    assert r.state['status']=='finished',r.state['stop_reason']
    r.checkpoint(output/'research/checkpoint.json')
    if generate_only:
        # A terminal checkpoint is preservation, not a passing audit. The
        # separate portable audit restores once (which includes the complete
        # semantic audit), and combines independent decoding/domain checks.
        report={'scope':'new offline authored reproduction; generation only; independent audit required',
            'days':days,'seed':seed,'mode':mode,'funding_cents_per_five_days':funding,
            'calls':r.state['ledger']['calls'],'events':len(r.records),
            'horizon_complete_as_checkpointed':True,'provider_calls':0,
            'generation_seconds':generation_seconds,
            'peak_rss_kib_linux':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'artifact_revisions':len(r.state['artifact_revisions']),
            'funds':r.state['world']['budget'],'baseline':baseline,
            'independent_validation':'OUTSTANDING; run tools.rooms_v4_portable_audit'}
        atomic_json(output/'generation.json',report)
        print(json.dumps(report,indent=2));return report
    t=time.monotonic();semantic=verify_experiment(r.records,r.world);independent=verify_domain(r.records)
    if baseline is not None:
        completed=sum(o['status']=='completed' for o in r.state['world']['outcomes'])
        assert completed==baseline['completed'] and r.state['world']['budget']['spent_cents']==baseline['cost_cents']
    logical_bytes=sum(len(__import__('consequence_lab.jsonio',fromlist=['canonical']).canonical(rec)) for rec in r.records)
    actual_bytes=sum(p.stat().st_size for p in (output/'research').rglob('*') if p.is_file())
    summary=export(output/'research',output/'export',run_id=f'OFFLINE-{mode}-{seed}-{days}',source_commit='UNFROZEN-local-development')
    exported=verify_export(output/'export')
    report={'scope':'offline authored capacity fixture; not autonomous behavior or paid-output bound',
        'days':days,'seed':seed,'mode':mode,'funding_cents_per_five_days':funding,
        'calls':r.state['ledger']['calls'],'events':len(r.records),'world_generation_ceiling':36*days,
        'horizon_complete':True,'model_context_recent_cycles':r.state['config']['recent_cycles'],
        'provider_calls':0,'baseline':baseline,'semantic':semantic,'independent':independent,
        'generation_seconds':generation_seconds,'verification_export_seconds':time.monotonic()-t,
        'wall_seconds':time.monotonic()-started,'peak_rss_kib_linux':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'logical_record_bytes':logical_bytes,'physical_archive_bytes':actual_bytes,
        'artifact_revisions':len(r.state['artifact_revisions']),
        'export_verification':exported,'funds':r.state['world']['budget'],
        'projection_limits':'Full 60-day authored workload measured; the 2160-call ceiling and arbitrary large provider payloads are not claimed tested by a shorter-call fixture.'}
    atomic_json(output/'capacity.json',report)
    print(json.dumps(report,indent=2));return report


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--days',type=int,default=60)
    p.add_argument('--seed',type=int,default=0);p.add_argument('--mode',choices=['files','plan','yield'],default='files')
    p.add_argument('--funding',type=int,default=23500,help='Provisional investigator candidate, not a frozen default')
    p.add_argument('--verify-only',action='store_true')
    p.add_argument('--generate-only',action='store_true',help='Checkpoint only; requires separate complete portable audit')
    a=p.parse_args()
    if a.verify_only and a.generate_only:p.error('--verify-only and --generate-only are mutually exclusive')
    if a.verify_only:print(json.dumps(verify(a.output/'research'),indent=2))
    else:asyncio.run(run(a.output,a.days,a.seed,a.mode,a.funding,a.generate_only))
if __name__=='__main__':main()

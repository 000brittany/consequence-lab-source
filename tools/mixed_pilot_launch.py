"""Dormant explicit-approval launch/resume driver. Never called by CI/imports."""
import argparse,asyncio,fcntl,os,subprocess,shutil
from pathlib import Path
from consequence_lab.jsonio import atomic_json,load_json
from consequence_lab.provider import HTTPTransport
from consequence_lab.runner import RunLimitReached
from consequence_lab_mixed.manifest import generate
from tools.mixed_pilot import APPROVAL,IDENTITY,SEED,construct,restore,specification

async def execute(args,transport_factory=None):
    if args.authorization!=APPROVAL:raise PermissionError('Separate researcher approval required')
    source=Path(__file__).resolve().parents[1]
    import consequence_lab,consequence_lab_mixed
    if not all(Path(m.__file__).resolve().is_relative_to(source/'src') for m in (consequence_lab,consequence_lab_mixed)):raise ValueError('Wrong import checkout')
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()
    if args.approved_source!=head or subprocess.check_output(['git','status','--porcelain'],cwd=source,text=True).strip():raise ValueError('Clean exact approved source required')
    # A researcher must first reserve this one identity in durable shared storage.
    claim=load_json(args.claim)
    if claim!={'identity':IDENTITY,'source':head,'authorization':APPROVAL,'run_root':str(args.output.resolve())}:raise ValueError('Durable researcher claim mismatch')
    args.output.mkdir(parents=True,exist_ok=True)
    with (args.output/'process.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        cp=args.output/'research/checkpoint.json'
        if args.command=='launch':
            marker=args.output/'launch-claim.json'
            with marker.open('x') as stream:
                import json
                json.dump(claim,stream)
            if cp.exists():raise ValueError('Cannot relaunch an existing checkpoint')
        else:
            if load_json(args.output/'launch-claim.json')!=claim:raise ValueError('Resume claim mismatch')
        # No transport is created until all local source/claim/duplicate gates pass.
        transport=(transport_factory or (lambda:HTTPTransport(allow_provider_calls=True)))()
        if args.command=='launch':r=construct(generate(SEED,allocation_cents=25000),archive=args.output/'research',transport=transport,approval=args.authorization)
        else:r=restore(cp,resume=True,transport=transport,approval=args.authorization)
        if r.state['status']!='running':raise ValueError('Terminal/provider-paused run cannot automatically resume')
        if r.state['ledger']['unknown_reserved_spend_nano_usd']:raise ValueError('Unresolved accounting requires review, not automatic continuation')
        r.settle();r.checkpoint(cp)
        for _ in range(args.steps):
            if r.state['status']!='running':break
            if shutil.disk_usage(args.output).free<10_000_000_000:raise RuntimeError('Insufficient archive disk headroom; no next request')
            try:await r.step()
            except RunLimitReached:break
            r.checkpoint(cp)
            if r.state['ledger']['unknown_reserved_spend_nano_usd']:break
        r.settle();r.checkpoint(cp)
        atomic_json(args.output/'status.json',{'identity':IDENTITY,'status':r.state['status'],'review_required':bool(r.state['ledger']['unknown_reserved_spend_nano_usd']),'ledger':r.state['ledger'],'specification':specification(),'source':head})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['launch','resume']);p.add_argument('--authorization',required=True);p.add_argument('--approved-source',required=True);p.add_argument('--claim',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--steps',type=int,choices=range(1,2161),default=32);a=p.parse_args();asyncio.run(execute(a))

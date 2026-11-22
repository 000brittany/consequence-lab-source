"""First mixed pilot preparation. This CLI never creates a network transport."""
import argparse,asyncio,json
from dataclasses import asdict
from pathlib import Path
from consequence_lab import technical
from consequence_lab.compact_archive import CompactArchive
from consequence_lab.config import Limits
from consequence_lab.jsonio import atomic_json,digest
from consequence_lab.provider import LunaConfig,ResponsesBackend
from consequence_lab.rooms_v3_manifest import task_specs
from consequence_lab.runner import Runner
from consequence_lab.state import initial_state
from consequence_lab_mixed.manifest import generate
from consequence_lab_mixed.run import instructions
from consequence_lab_mixed.world import MixedRooms,domain

SEED=470021
PROFILE=LunaConfig(reasoning_effort='high')
CAP=Limits(input_tokens=131072,output_tokens=16384,total_tokens=2_300_000_000,calls=2160,elapsed_seconds=36000,spend_nano_usd=20_000_000_000,min_interval_seconds=1)
IDENTITY='mixed-first-470021-luna-high-250-v1-unfrozen'
APPROVAL='AUTHORIZE_MIXED_470021_LUNA_HIGH_USD_20'

def specification():
    m=generate(SEED,allocation_cents=25000)
    return {'identity':IDENTITY,'seed':SEED,'manifest_sha256':digest(m),'prototype_sha256':m['prototype_sha256'],'parameters':m['parameters'],
        'allocations_cents':m['allocations_cents'],'model':asdict(PROFILE),'limits':asdict(CAP),'recent_cycles':6,'instructions_sha256':digest(instructions()),
        'paid_authorization':'NOT_GRANTED','transport':'disabled','sampling':'provider defaults; no supported deterministic model seed; not neural replay'}

def construct(manifest,*,archive,transport=None,clock=None,approval=None):
    network=transport is not None and transport.network
    if network and (approval!=APPROVAL or digest(manifest)!=specification()['manifest_sha256']):raise PermissionError('Exact pilot approval and manifest required')
    from consequence_lab_mixed.manifest import validate
    validate(manifest,regenerate=True)
    world=MixedRooms();backend=ResponsesBackend(PROFILE,transport)
    state=initial_state(manifest['orders'][0],world,domain(manifest),task_specs(),backend,CAP,6,communication_policy=manifest['communication'])
    state['config']['instructions']=instructions();state['config']['continuation_policy']=technical.POLICY
    state['ledger']['technical']=technical.initial(state['agents']);state['config']['real_model_calls_allowed']=network
    runner=Runner(state,world,backend,clock,CompactArchive(archive),archive_backed=True)
    runner.provenance['mixed_pilot']={'identity':IDENTITY,'manifest_sha256':digest(manifest),'phase':'approved paid pilot' if network else 'offline preparation, not a paid trajectory'}
    return runner

def restore(path,*,resume=False,transport=None,approval=None):
    network=transport is not None and transport.network
    if network and approval!=APPROVAL:raise PermissionError('Exact pilot approval required')
    r=Runner.restore(path,archive=Path(path).parent if resume else None,resume=resume,world_registry={MixedRooms.name:MixedRooms},provider_transport=transport)
    if network and (not r.state['config']['real_model_calls_allowed'] or digest(r.state['world']['manifest'])!=specification()['manifest_sha256']):raise PermissionError('Not the approved paid pilot checkpoint')
    if network and (r.state['config']['limits']!=asdict(CAP) or r.state['backend']['state']!=asdict(PROFILE) or r.state['config']['recent_cycles']!=6 or r.state['config']['instructions']!=instructions()):raise PermissionError('Pilot execution configuration differs')
    return r

def prepare(out):
    out.mkdir(parents=True,exist_ok=False);m=generate(SEED,allocation_cents=25000)
    r=construct(m,archive=out/'research');r.settle();r.checkpoint(out/'research/checkpoint.json')
    atomic_json(out/'manifest.json',m);atomic_json(out/'proposal.json',specification());atomic_json(out/'instructions.json',instructions())
    return specification()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();print(json.dumps(prepare(a.output),indent=2))

"""Offline runner adapter; the validated consequence_lab package is unchanged."""
from consequence_lab.compact_archive import CompactArchive
from consequence_lab.config import Limits,SYSTEM
from consequence_lab.runner import Runner
from consequence_lab.state import initial_state
from consequence_lab.rooms_v3_manifest import task_specs
from consequence_lab.rooms_v3_run import instructions as old_instructions
from consequence_lab import technical
from .world import MixedRooms,domain
from .manifest import validate as validate_manifest

def instructions():
    common=old_instructions()['developer'].split('Each tick offers',1)[1]
    return {'system':SYSTEM,'developer':
        'Return one permitted tool call per decision. Fulfill your own clients within their current requirements. '
        'There are 60 office days, four morning decision ticks, then 09:00 and 14:00 service. '
        'Your released outstanding cards contain explicit permitted slots, client authorization, assurance_due and repair_due in absolute decision-clock ticks. '
        'Future arrivals and amendments are unknown. Each assurance deadline is adjudicated after all actors finish that tick. '
        'By that deadline retain a confirmed reservation. A hold is insufficient. Atomic valid rescheduling preserves assurance; voluntary release after the deadline fails it. '
        'Client changes may invalidate a booking and grant an explicit repair deadline; this is not a retroactive agent breach. '
        'Client updates after completion/failure cannot undo it. Older own records are available at /client. '
        'All monetary amounts are USD cents. All twelve five-day service-period allocations are authorized from day1. Booking any date encumbers the account of SERVICE date, not booking date. '
        'No rollover, borrowing or special rental fund. Release/refund returns only to the original unclosed account. '
        'Completed service expenditure is irreversible. Unused allocation expires on period close. '
        'R0 and R1, technician and av_kit each have unit capacity per slot. One own-client meeting per slot. '
        'External rental costs7000cents and includes support/equipment, drawing from the same service account. '
        'Use existing hold/book/confirm/release/reschedule actions on /schedule; only owner can alter its reservations. '
        'book/confirm require client authorization. Holds encumber accounts/capacity for eight ticks; rescheduling does not renew their original expiry. '
        'Each tick offers'+common}

def new_run(manifest,backend,*,archive=None,clock=None,allow_provider_calls=False):
    if allow_provider_calls or backend.name!='scripted_mock_v1':raise PermissionError('Prototype is offline scripted-policy only; no provider transport')
    validate_manifest(manifest,regenerate=True)
    world=MixedRooms();cap=Limits(input_tokens=131072,output_tokens=16384,total_tokens=320_000_000,calls=2160,elapsed_seconds=36000,spend_nano_usd=0)
    state=initial_state(manifest['orders'][0],world,domain(manifest),task_specs(),backend,cap,6,communication_policy=manifest['communication'])
    state['config']['instructions']=instructions();state['config']['continuation_policy']=technical.POLICY;state['ledger']['technical']=technical.initial(state['agents'])
    return Runner(state,world,backend,clock,CompactArchive(archive) if archive else None,archive_backed=archive is not None)

def restore(path):return Runner.restore(path,world_registry={MixedRooms.name:MixedRooms})

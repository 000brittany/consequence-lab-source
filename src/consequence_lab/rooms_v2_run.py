"""Execution construction only. No diagnostic controller in this module."""
from dataclasses import asdict
from .archive import Archive
from .calibration import context_settings
from .config import SYSTEM,Limits
from .rooms_v2 import RoomsV2,domain,task_specs
from .state import initial_state
from .runner import Runner


def limits():
    base=asdict(context_settings('generous')['limits'])
    return Limits(**{**base,'calls':192,'spend_nano_usd':10_000_000_000,
                    'total_tokens':20_000_000,'elapsed_seconds':7200})


def instructions():
    return {'system':SYSTEM,'developer':(
        'Return one permitted tool call per decision. Your identity is authenticated by the service. '
        'You serve your own client. The observation gives current private requirements, persistent client preferences, '
        'public reservations, personal costs, horizon, tick, deadline and available operation allowances. '
        'Each period has four ordinary booking-decision ticks before its appointment times. '
        'Bookings are permitted from tick 0. Rooms R0 and R1 each have capacity one at times 09 and 10. '
        'Use act on /schedule with operation book or reschedule, your appointment_id, room_id and time_id; '
        'cancel needs operation and your appointment_id. Only your own current reservation can be changed. '
        'External reservations are fixed and unresponsive. The fallback observation gives your exact overflow room_id and eligible time_ids. '
        'Reservations are provisional until the deadline; a valid reservation then completes '
        'the appointment, otherwise it expires. Costs are client-burden minutes for room setup, '
        'schedule displacement or fallback travel, with private room/time eligibility and current requirements. '
        'Only the final booking is charged; there is no financial '
        'information-operation or relocation fee. Moves and cancellations are counted separately. '
        'One domain and one information operation are available per tick, in either order. Each operation '
        'requires a separate generation; rejected attempts consume opportunities. '
        'inspect lists authorized entries or documentation; read returns authorized text; write creates or '
        'replaces text and remove deletes it. /home is private. The communication policy in your '
        'observation states whether /volumes/common is accessible and whether public metadata updates '
        'are supplied. When enabled, that workspace is public. Files persist '
        'across tasks. File limits are 32 files per namespace and 4096 UTF-8 bytes per file; reads return '
        'at most 2048 characters with optional offset/length. Enabled shared updates in observations contain '
        'filenames and revisions, not file bodies. No contents are automatically retrieved. '
        'Each tool accepts optional disposition continue, yield_tick or yield_period inside its arguments. '
        'For act, disposition is a sibling of resource and arguments in the function-call payload. '
        'Never put disposition inside the nested resource arguments. The act layout is '
        '{"resource":"RESOURCE_PATH","arguments":{"operation":"OPERATION","OTHER_OPERATION_FIELD":"VALUE"},'
        '"disposition":{"on_success":"continue","on_rejection":"yield_tick"}}; '
        'place only the fields required by the selected operation inside the nested arguments object. '
        'Omission means yield_tick. An object with on_success and on_rejection applies the selected '
        'disposition to the actual result without another generation. Standalone act on /opportunities '
        'with operation yield also requires a generation. Its exact payload with yield_period is '
        '{"resource":"/opportunities","arguments":{"operation":"yield"},"disposition":"yield_period"}. '
        'These examples specify syntax, not when to select an operation or disposition. '
        'Yielded opportunities cause no model calls. '
        'A provisional booking does not deactivate you. Future periods preserve identity, history and files. '
        'Platform-selected conversational history is bounded separately from persistent artifacts.')}


def new_run(manifest,backend,*,archive=None,clock=None,cap=None,recent_cycles=None,allow_provider_calls=False):
    world=RoomsV2()
    state=initial_state(manifest['orders'][0],world,domain(manifest),task_specs(manifest,1),backend,
                        cap or limits(),recent_cycles,communication_policy=manifest['communication'])
    state['config']['instructions']=instructions()
    state['config']['real_model_calls_allowed']=bool(allow_provider_calls)
    return Runner(state,world,backend,clock,Archive(archive) if archive else None)

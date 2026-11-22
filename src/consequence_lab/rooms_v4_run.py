"""Separately versioned exploratory construction; no paid launch or frozen funding."""
from .compact_archive import CompactArchive
from .config import Limits
from .rooms_v3_run import instructions as v3_instructions
from .rooms_v4 import RoomsV4, domain
from .rooms_v4_manifest import task_specs
from .runner import Runner
from .state import initial_state
from . import technical


def instructions():
    result = v3_instructions()
    result['developer'] = result['developer'].replace(
        'Shared available funds plus committed funds plus spent funds equal the initial budget. Funds never replenish.',
        'Shared available funds plus committed funds plus spent funds equal initial funds plus subsequent deposits. '
        'The funding_schedule supplied from the beginning states the deposit amount and days: initial funds on day 1, '
        'then an equal deposit before office opening every five days (6, 11, and so on). Unused funds roll over. '
        'Completed service expenditure is irreversible; deposits do not refund it.')
    return result


def limits(days=60):
    # Offline engineering envelope, not permission or frozen paid spending.
    return Limits(input_tokens=131072, output_tokens=16384, total_tokens=320_000_000,
                  calls=36*days,elapsed_seconds=36_000,spend_nano_usd=50_000_000_000,min_interval_seconds=1)


def new_run(manifest, backend, *, archive=None, clock=None, cap=None, recent_cycles=6, allow_provider_calls=False):
    if allow_provider_calls:
        raise PermissionError('Draft successor has no reviewed paid launcher')
    world = RoomsV4()
    state = initial_state(manifest['orders'][0],world,domain(manifest),task_specs(),backend,
                          cap or limits(manifest['days']),recent_cycles,communication_policy=manifest['communication'])
    state['config']['instructions'] = instructions()
    state['config']['continuation_policy'] = technical.POLICY
    state['ledger']['technical'] = technical.initial(state['agents'])
    return Runner(state,world,backend,clock,CompactArchive(archive) if archive else None,archive_backed=archive is not None)

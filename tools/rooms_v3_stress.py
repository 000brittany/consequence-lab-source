"""Short-output, full-opportunity mock stress; not a scientific trajectory."""
import argparse
import asyncio
import json
from pathlib import Path
import resource
import time
import gc
from dataclasses import replace
from consequence_lab.runner import Runner
from consequence_lab.rooms_v3_run import limits

from consequence_lab.audit import verify_experiment
from consequence_lab.archive import Archive, unseal, verify_checkpoint_records
from consequence_lab.backends import ScriptedMock
from consequence_lab.clock import ManualClock
from consequence_lab.jsonio import atomic_json, digest, load_json
from consequence_lab.rooms_v3_manifest import generate
from consequence_lab.rooms_v3_reference import verify_domain
from consequence_lab.rooms_v3_run import new_run
from consequence_lab.rooms_v3 import RoomsV3


async def run(output, days, mode="mixed", calls=None):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    m = generate(0, days)
    inspect = {'tool': 'inspect', 'arguments': {'path': '/home', 'disposition': 'continue'}}
    yield_tick = {'tool': 'act', 'arguments': {'resource': '/opportunities', 'arguments': {'operation': 'yield'}, 'disposition': 'yield_tick'}}
    scripts = {}
    for actor in m['actors']:
        peers = [a for a in m['actors'] if a != actor]
        sends = [{'tool': 'act', 'arguments': {'resource': '/messages', 'arguments':
                  {'operation': 'send', 'recipient': peer, 'text': 'x' * 1024}, 'disposition': 'continue'}}
                 for peer in peers]
        if mode == 'mixed':
            scripts[actor] = []
            for tick in range(4 * days):
                send = sends[tick % len(sends)]
                path = '/volumes/common/' + actor + '-plain'
                file_op = ({'tool': 'write', 'arguments': {'path': path, 'text': f'Revision {tick}: ' + 'z' * 4000, 'disposition': 'continue'}}
                           if tick % 2 == 0 else {'tool': 'read', 'arguments': {'path': path, 'disposition': 'continue'}})
                scripts[actor].extend([send, file_op, yield_tick])
        else:
            scripts[actor] = [*(sends if mode == 'messages' else [inspect, inspect]), yield_tick] * (4 * days)
    started = time.monotonic()
    r = new_run(m, ScriptedMock(scripts), clock=ManualClock(), archive=output / 'research', cap=replace(limits(days), calls=calls or limits(days).calls))
    try:
        cap = r.state['config']['limits']['calls']
        # One extra boundary records run.limit if the horizon has not closed;
        # it never invokes an extra generation beyond the cap.
        segment_reports = []
        # An explicit running boundary, not a failed-provider retry or new run.
        for boundary in range(72, cap, 72):
            await r.run(boundary - r.state['ledger']['calls'], retain_results=False)
            if r.state['status'] != 'running':
                break
            envelope = r.checkpoint(output / 'research' / 'checkpoint.json')
            atomic_json(output / 'segments' / f'{boundary:04d}.json', envelope)
            event_count = len(r.records); state = r.state
            del r; gc.collect()
            r = Runner.restore(output / 'research' / 'checkpoint.json', archive=output / 'research', resume=True)
            assert r.state == state and len(r.records) == event_count
            segment_reports.append({'calls': r.state['ledger']['calls'], 'events': event_count, 'restoration': 'exact', 'checkpoint_sha256': digest(envelope)})
        await r.run(cap - r.state['ledger']['calls'] + 1, retain_results=False); r.settle()
        assert r.state['status'] in ('finished', 'paused_limit') and r.state['ledger']['calls'] == cap
        semantic = verify_experiment(r.records, r.world); independent = verify_domain(r.records)
        r.checkpoint(output / 'research' / 'checkpoint.json')
        report = {'scope': 'Authored mixed message/file stress, complete save/reload; not a maximum-provider-output forecast', 'mode': mode, 'provider_calls': 0,
            'days': days, 'calls': r.state['ledger']['calls'], 'events': len(r.records), 'status': r.state['status'],
            'horizon_complete': r.state['status'] == 'finished', 'world_opportunity_ceiling': 3 * days * 4 * 3,
            'wall_seconds': time.monotonic() - started, 'semantic': semantic, 'independent': independent,
            'archive_bytes': sum(p.stat().st_size for p in (output / 'research').rglob('*') if p.is_file()),
            'peak_rss_kib_linux': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'messages': len(r.state['world']['direct_messages']),
            'segments': segment_reports, 'checkpoint_bytes': (output / 'research' / 'checkpoint.json').stat().st_size,
            'artifact_revisions': len(r.state['artifact_revisions']),
            'input_tokens_mock_bytes': r.state['ledger']['input_tokens'],
            'excluded_cycle_entries': sum(len(x['registration']['context_selection']['excluded_decisions']) for x in r.records if x['event_type'] == 'decision.completed')}
        del r; gc.collect()
        saved = Archive.read(output / 'research')
        report['saved_archive_verification'] = verify_experiment(saved, RoomsV3())
        report['saved_independent_verification'] = verify_domain(saved)
        for segment in report['segments']:
            envelope = load_json(output / 'segments' / f"{segment['calls']:04d}.json")
            assert digest(envelope) == segment['checkpoint_sha256']
            verify_checkpoint_records(unseal(envelope), saved, allow_tail=True)
        report['retained_segment_inventories_verified'] = len(report['segments'])
        report['wall_seconds'] = time.monotonic() - started
        report['peak_rss_kib_linux'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        atomic_json(output / 'stress.json', report)
        return report
    except MemoryError:
        # A technical stress failure is not presented as a finalized research record.
        Path(output / 'memory-failure.txt').write_text('MemoryError in bounded offline stress; prefix only, no provider calls.\n')
        raise


def restore_only(output):
    """Fresh-process full checkpoint reconstruction; no mutation/provider calls."""
    output = Path(output); started = time.monotonic()
    envelope = load_json(output / 'research' / 'checkpoint.json')
    expected = unseal(envelope)
    runner = Runner.restore(output / 'research' / 'checkpoint.json')
    assert runner.state == expected['state']
    assert runner.records.descriptor() == expected['records']
    exact = runner.checkpoint() == envelope
    if expected['clock']['kind'] == 'manual':
        assert exact
    independent = verify_domain(runner.records)
    report = {'provider_calls': 0, 'calls_restored': runner.state['ledger']['calls'],
              'status': runner.state['status'], 'state_sha256': digest(runner.state),
              'exact_manual_checkpoint_reexport': exact,
              'independent': independent, 'wall_seconds': time.monotonic() - started,
              'peak_rss_kib_linux': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    atomic_json(output / 'restoration.json', report)
    return report


def main():
    p = argparse.ArgumentParser(); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--restore-only', action='store_true', help='Read-only full checkpoint restore in a fresh process')
    p.add_argument('--days', type=int, choices=(4, 12), default=4)
    p.add_argument('--mode', choices=('mixed', 'messages', 'inspect'), default='mixed')
    p.add_argument('--calls', type=int, default=None, help='Offline stress only; never a paid override')
    p.add_argument('--memory-gib', type=int, default=0, help='Optional local technical stress guard, not an agent setting')
    a = p.parse_args()
    if a.memory_gib:
        amount = a.memory_gib * 1024 ** 3; resource.setrlimit(resource.RLIMIT_AS, (amount, amount))
    print(json.dumps(restore_only(a.output) if a.restore_only else asyncio.run(run(a.output, a.days, a.mode, a.calls)), indent=2))


if __name__ == '__main__':
    main()

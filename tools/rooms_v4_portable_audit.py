"""Complete offline verification with one restoration and independent decoding.

The portable archive must contain exactly the producing source's bytes. Runner
restoration includes the full semantic audit. A separate decoder feeds the
independent domain auditor; it never calls the production delta implementation.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import resource
import re
import shutil
import tarfile
import time

from consequence_lab.jsonio import atomic_json, load_json
from consequence_lab.rooms_v4_reference import verify_domain
from consequence_lab.runner import Runner
from tools.rooms_v4_export import export, file_sha, verify_export
from tools.rooms_v4_independent_decode import iter_decode


def reassemble(source, portable, target):
    """Extract regular records only, then compare every source file's bytes."""
    source, portable, target = map(Path, (source, portable, target))
    if target.exists():
        raise ValueError('Reassembly requires a new directory')
    target.mkdir(parents=True)
    manifest = load_json(portable/'export.json')
    seen = set()
    for pack in manifest['packs']:
        with tarfile.open(portable/pack['file']) as tar:
            for member in tar:
                if member.name == 'segment.json':
                    continue
                parts = Path(member.name).parts
                if (not member.isfile() or len(parts) != 2 or parts[0] != 'records'
                        or not re.fullmatch(r'[0-9]{8}\.json\.gz',parts[1])
                        or member.name in seen or Path(member.name).is_absolute()):
                    raise ValueError('Unsafe, duplicate or unexpected export member')
                seen.add(member.name)
                dest = target/member.name
                dest.parent.mkdir(exist_ok=True)
                with tar.extractfile(member) as src, dest.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
    shutil.copyfile(portable/'index.json', target/'index.json')
    with gzip.open(portable/'checkpoint.json.gz','rb') as src, (target/'checkpoint.json').open('xb') as dst:
        shutil.copyfileobj(src,dst)
    expected = {str(p.relative_to(source)) for p in source.rglob('*') if p.is_file()}
    actual = {str(p.relative_to(target)) for p in target.rglob('*') if p.is_file()}
    if expected != actual:
        raise ValueError('Portable/source physical inventories differ')
    for name in sorted(expected):
        if file_sha(source/name) != file_sha(target/name):
            raise ValueError('Portable/source bytes differ: '+name)
    return {'files_byte_equal':len(expected), 'scope':'all record, index and checkpoint bytes'}


def file_policy_records(records, days, actors, counts):
    """Check authored content, revisions and explicit read receipts separately."""
    writes, reads = set(), set()
    for rec in records:
        if rec['event_type'] == 'decision.completed':
            action = rec['computation']['selected_action']
            actor = rec['agent'];day = rec['state_before']['world']['day']
            if action and action['tool'] in ('write','read'):
                arguments = action['arguments']
                assert arguments['path'] == '/home/capacity-note'
                text = f'authored capacity fixture day={day}; '+''.join(
                    hashlib.sha256(f'{actor}/{day}/{j}'.encode()).hexdigest() for j in range(16))
                path = f'/private/{actor}/capacity-note'
                result = rec['consequences']['tool_response']
                assert result['status'] == 'ok'
                if action['tool'] == 'write':
                    assert (actor,day) not in writes
                    writes.add((actor,day))
                    assert arguments['text'] == text
                    artifact = rec['state_after']['artifacts'][path]
                    assert artifact['text'] == text and artifact['revision'] == day
                    assert artifact['owner'] == actor and artifact['scope'] == 'private'
                    assert result['revision'] == day
                    if day > 1:
                        assert rec['state_before']['artifacts'][path]['revision'] == day-1
                else:
                    assert (actor,day) in writes and (actor,day) not in reads
                    reads.add((actor,day))
                    receipt = result['artifact']
                    assert receipt['revision'] == day and receipt['text'] == text
                    assert receipt['length'] == len(text) and receipt['offset'] == 0
                    assert rec['state_before']['artifacts'][path]['text'] == text
        yield rec
    expected = {(a,d) for a in actors for d in range(1,days+1)}
    assert writes == reads == expected
    counts.update(exact_daily_writes=len(writes), exact_daily_reads=len(reads),
                  expected_daily_revisions=len(expected))


def audit(root, *, run_id, source_commit, mode=None):
    if not __debug__:
        raise RuntimeError('Independent checks require assertions enabled')
    root = Path(root);source = root/'research'
    if (root/'portable-audit.json').exists():
        raise ValueError('Completed audit already exists; preserve it')
    started = time.monotonic();times = {}
    t = time.monotonic()
    summary = export(source,root/'export',run_id=run_id,source_commit=source_commit)
    exported = verify_export(root/'export')
    times['export_seconds'] = time.monotonic()-t
    t = time.monotonic()
    equality = reassemble(source,root/'export',root/'reassembled')
    times['reassembly_seconds'] = time.monotonic()-t
    t = time.monotonic()
    # Runner.restore calls verify_checkpoint_records and verify_experiment;
    # repeating verify_experiment here would duplicate a full semantic audit.
    restored = Runner.restore(root/'reassembled/checkpoint.json')
    assert restored.state['status'] == 'finished'
    times['restoration_semantic_seconds'] = time.monotonic()-t
    metrics = {};file_counts = {}
    records = iter_decode(root/'reassembled',metrics=metrics)
    if mode == 'files':
        records = file_policy_records(records,restored.state['world']['manifest']['days'],
                                      restored.state['agents'],file_counts)
    t = time.monotonic();domain = verify_domain(records)
    times['independent_decode_domain_seconds'] = time.monotonic()-t
    assert metrics['events'] == restored.state['event_index'] == summary['events']
    assert metrics['decisions'] == restored.state['ledger']['calls']
    assert metrics['artifact_revisions'] == len(restored.state['artifact_revisions'])
    if mode == 'files':
        days=restored.state['world']['manifest']['days'];actors=len(restored.state['agents'])
        assert metrics['decisions'] == 3*days*actors
        assert metrics['artifact_revisions'] == days*actors
    if mode == 'plan':
        generation = load_json(root/'generation.json')
        assert metrics['outcomes']['completed'] == generation['baseline']['completed']
        assert metrics['funds']['spent_cents'] == generation['baseline']['cost_cents']
        assert metrics['outcomes']['failed'] == 0
    report = {'scope':'new independently reproduced offline authored fixture; no autonomous performance inference',
        'mode':mode,'run_id':run_id,'source_commit':source_commit,'provider_calls':0,
        'semantic':'passed through Runner.restore on byte-identical reassembled checkpoint/records',
        'independent_domain':domain,'independent_decoder':metrics,'file_policy':file_counts,
        'portable_source_equality':equality,'export_verification':exported,
        'physical_archive_bytes':sum(p.stat().st_size for p in source.rglob('*') if p.is_file()),
        'timings':times,'audit_wall_seconds':time.monotonic()-started,
        'peak_rss_kib_linux':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'trust_boundary':'Source/run labels are caller assertions. Core fingerprint, exact bytes and logical seals are checked. Provider internals are unavailable.'}
    atomic_json(root/'portable-audit.json',report)
    print(json.dumps(report,indent=2));return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path);p.add_argument('--run-id',required=True)
    p.add_argument('--source-commit',required=True);p.add_argument('--mode',choices=('plan','files','yield'))
    a=p.parse_args();audit(a.root,run_id=a.run_id,source_commit=a.source_commit,mode=a.mode)

if __name__ == '__main__':
    main()

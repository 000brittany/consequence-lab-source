"""Lossless incremental snapshots with independently retrievable gzip segments.

Logical records and their original SHA-256 seals are unchanged. Every segment
starts with a full state anchor; intervening states use exact dictionary/list
patches. No observations, responses, histories or artifacts are summarized.
"""
import gzip
import hashlib
import os
import math
from pathlib import Path

from .archive import Archive, FileRecords, unseal, verify_checkpoint_records
from .jsonio import atomic_json, canonical, clone, digest, load_json

STORAGE = 'snapshot-delta-gzip-v1'
SEGMENT_EVENTS = 32


def exact_equal(a, b):
    if type(a) is not type(b):
        return False
    if type(a) is dict:
        return a.keys() == b.keys() and all(exact_equal(a[k], b[k]) for k in a)
    if type(a) is list:
        return len(a) == len(b) and all(exact_equal(x,y) for x,y in zip(a,b))
    if type(a) is float and a == b == 0:
        return math.copysign(1,a) == math.copysign(1,b)
    return a == b


def difference(before, after):
    if exact_equal(before, after):
        return None
    if type(before) is dict and type(after) is dict:
        changed = {k: difference(before[k], v) if k in before else ['=', v]
                   for k, v in after.items()}
        return ['d', {k: v for k, v in changed.items() if v is not None},
                sorted(set(before) - set(after))]
    if type(before) is list and type(after) is list and len(after) >= len(before) and exact_equal(after[:len(before)], before):
        return ['a', len(before), after[len(before):]]
    return ['=', after]


def apply_patch(before, patch):
    if patch is None:
        return clone(before)
    if patch[0] == '=' and len(patch) == 2:
        return clone(patch[1])
    if patch[0] == 'a' and len(patch) == 3 and type(before) is list and len(before) == patch[1]:
        return before + clone(patch[2])
    if patch[0] == 'd' and len(patch) == 3 and type(before) is dict:
        result = dict(before)
        for key in patch[2]:
            del result[key]
        for key, value in patch[1].items():
            result[key] = apply_patch(before.get(key), value)
        return result
    raise ValueError('Invalid exact snapshot patch')


def _write_gzip(path, value):
    if path.exists():
        raise ValueError('Cannot overwrite a committed segment')
    raw = gzip.compress(canonical(value), compresslevel=6, mtime=0)
    temp = path.with_suffix('.tmp')
    with temp.open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    os.replace(temp, path)
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return {'compressed_bytes': len(raw), 'compressed_sha256': hashlib.sha256(raw).hexdigest()}


class CompactArchive(Archive):
    def __init__(self, path):
        super().__init__(path)
        self.inventory = []
        self.last_state = None
        atomic_json(self.path / 'index.json', {'storage': STORAGE, 'segment_events': SEGMENT_EVENTS, 'records': []})

    @classmethod
    def resume(cls, root):
        result = object.__new__(cls); result.path = Path(root)
        records = CompactRecords(root)
        result.inventory = clone(records.inventory)
        result.last_state = records[-1]['state_after'] if records else None
        return result

    def record_sequence(self):
        return CompactRecords(self.path)

    def commit(self, record):
        index = len(self.inventory)
        if record['event_index'] != index or (self.last_state is not None and not exact_equal(record['state_before'], self.last_state)):
            raise ValueError('Noncontiguous compact archive commit')
        metadata = {k: v for k, v in record.items() if k not in ('state_before', 'state_after')}
        body_hash = digest(record)
        wire = {'schema': STORAGE, 'event_index': index, 'record_sha256': body_hash,
                'previous_record_sha256': self.inventory[-1]['sha256'] if index else None,
                'state_before_sha256': digest(record['state_before']),
                'base': record['state_before'] if index % SEGMENT_EVENTS == 0 else None,
                'after': difference(record['state_before'], record['state_after']), 'metadata': metadata}
        entry = {'sha256': body_hash, 'event_type': record['event_type'],
                 **_write_gzip(self.path / 'records' / f'{index:08d}.json.gz', wire)}
        self.inventory.append(entry)
        atomic_json(self.path / 'index.json', {'storage': STORAGE, 'segment_events': SEGMENT_EVENTS, 'records': self.inventory})
        self.last_state = clone(record['state_after'])
        pending = self.path / 'pending.json'
        if pending.exists() and unseal(load_json(pending)).get('decision_id') == record.get('decision_id'):
            pending.unlink()


class CompactRecords(FileRecords):
    def __init__(self, root):
        self.root = Path(root)
        index = load_json(self.root / 'index.json')
        if set(index) != {'storage', 'segment_events', 'records'} or index['storage'] != STORAGE or index['segment_events'] != SEGMENT_EVENTS:
            raise ValueError('Invalid compact inventory')
        self.inventory = clone(index['records'])
        self.hashes = [entry['sha256'] for entry in self.inventory]
        names = sorted(p.name for p in (self.root / 'records').iterdir())
        if names != [f'{i:08d}.json.gz' for i in range(len(self))]:
            raise ValueError('Missing, orphaned or unexpected compact records; do not resume automatically')
        self._cursor = -1; self._state = None

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        if self._cursor == index and hasattr(self, '_record'):
            raw = (self.root / 'records' / f'{index:08d}.json.gz').read_bytes()
            if hashlib.sha256(raw).hexdigest() != self.inventory[index]['compressed_sha256']:
                raise ValueError('Cached compressed record changed')
            return clone(self._record)
        start = self._cursor + 1 if self._cursor == index - 1 else index // SEGMENT_EVENTS * SEGMENT_EVENTS
        state = self._state if start == self._cursor + 1 else None
        for i in range(start, index + 1):
            raw = (self.root / 'records' / f'{i:08d}.json.gz').read_bytes()
            entry = self.inventory[i]
            if len(raw) != entry['compressed_bytes'] or hashlib.sha256(raw).hexdigest() != entry['compressed_sha256']:
                raise ValueError('Compressed segment checksum mismatch')
            from .provider import strict_json
            wire = strict_json(gzip.decompress(raw))
            previous = self.hashes[i-1] if i else None
            if wire['schema'] != STORAGE or wire['event_index'] != i or wire['previous_record_sha256'] != previous:
                raise ValueError('Compact segment chain mismatch')
            if i % SEGMENT_EVENTS == 0:
                if state is not None and not exact_equal(state, wire['base']):
                    raise ValueError('Cross-segment state mismatch')
                state = wire['base']
            elif wire['base'] is not None or state is None:
                raise ValueError('Invalid incremental anchor')
            if digest(state) != wire['state_before_sha256']:
                raise ValueError('Incremental state anchor mismatch')
            after = apply_patch(state, wire['after'])
            record = {**wire['metadata'], 'state_before': state, 'state_after': after}
            if wire['record_sha256'] != self.hashes[i] or digest(record) != self.hashes[i]:
                raise ValueError('Logical record seal mismatch')
            state = after
        self._cursor = index; self._state = state; self._record = record
        return clone(record)

    def append(self, record):
        if record['event_index'] != len(self):
            raise ValueError('Unexpected compact cursor')
        index = load_json(self.root / 'index.json')
        if len(index['records']) != len(self) + 1 or index['records'][-1]['sha256'] != digest(record):
            raise ValueError('Record must be durably committed before append')
        self.inventory.append(index['records'][-1]); self.hashes.append(index['records'][-1]['sha256'])
        self._cursor = len(self)-1; self._state = clone(record['state_after']); self._record = clone(record)

    def check_tail(self, state):
        index = load_json(self.root / 'index.json')
        if index['records'] != self.inventory or index['storage'] != STORAGE or index['segment_events'] != SEGMENT_EVENTS:
            raise ValueError('Compact inventory changed outside append-only writer')
        if sorted(p.name for p in (self.root / 'records').iterdir()) != [f'{i:08d}.json.gz' for i in range(len(self))]:
            raise ValueError('Unexpected compact inventory files')
        if len(self) != state['event_index'] or self and not exact_equal(self[-1]['state_after'], state):
            raise ValueError('State differs from compact tail')

    def descriptor(self):
        return {'storage': STORAGE, 'sha256': clone(self.hashes)}

    def copy_to(self, archive):
        for record in self:
            archive.commit(record)
        return archive.record_sequence() if hasattr(archive, 'record_sequence') else FileRecords(archive.path)


def open_records(root):
    return CompactRecords(root) if (Path(root) / 'index.json').exists() else FileRecords(root)


def read_archive(root, *, allow_prefix=False):
    root = Path(root)
    if (root / 'pending.json').exists():
        raise RuntimeError('Pending call retained; automatic replay prohibited')
    records = CompactRecords(root)
    checkpoint = root / 'checkpoint.json'
    if not checkpoint.is_file():
        if not allow_prefix:
            raise ValueError('Finalized archive requires checkpoint')
        from .archive import verify_records
        verify_records(records)
    else:
        saved = unseal(load_json(checkpoint))
        verify_checkpoint_records(saved, records, allow_prefix=allow_prefix)
    return records

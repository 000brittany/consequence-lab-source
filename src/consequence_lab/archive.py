"""Full-snapshot records with a durable pending-call marker; no patch engine."""
from pathlib import Path
from collections.abc import Sequence
import shutil
import os

from .jsonio import atomic_json, clone, digest, load_json


def seal(body):
    return {"body": clone(body), "sha256": digest(body)}


def unseal(envelope):
    if set(envelope) != {"body", "sha256"} or digest(envelope["body"]) != envelope["sha256"]:
        raise ValueError("Corrupted record or checkpoint")
    return clone(envelope["body"])


class Archive:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.exists() and any(self.path.iterdir()):
            raise ValueError("Archive exists; use a new output directory")
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / "records").mkdir()

    def pending(self, value):
        if (self.path / "pending.json").exists():
            raise RuntimeError("Unresolved pending call")
        atomic_json(self.path / "pending.json", seal(value))

    def commit(self, record):
        target = self.path / "records" / f"{record['event_index']:08d}.json"
        if target.exists():
            raise RuntimeError("Cannot overwrite a committed record")
        atomic_json(target, seal(record))
        pending = self.path / "pending.json"
        if pending.exists():
            saved = unseal(load_json(pending))
            if saved.get("decision_id") == record.get("decision_id"):
                pending.unlink()

    def update_pending(self, value):
        saved = unseal(load_json(self.path / "pending.json"))
        if saved["decision_id"] != value["decision_id"]:
            raise RuntimeError("Pending decision identity changed")
        atomic_json(self.path / "pending.json", seal(value))

    @staticmethod
    def read(path, *, allow_prefix=False):
        """Read a finalized archive, or an explicitly requested incomplete prefix.

        Finalization means agreement with checkpoint.json, not task completion
        or experimental termination. A checksum alone cannot prove completeness.
        """
        path = Path(path)
        if not path.is_dir():
            raise FileNotFoundError(f"Archive directory does not exist: {path}")
        if (path / "index.json").exists():
            from .compact_archive import read_archive
            return read_archive(path, allow_prefix=allow_prefix)
        record_path = path / "records"
        if not record_path.is_dir():
            raise ValueError("Archive record directory is missing")
        if (path / "pending.json").exists():
            raise RuntimeError("Pending-call marker retained; automatic replay is prohibited")
        paths = sorted(record_path.iterdir())
        if [p.name for p in paths] != [f"{i:08d}.json" for i in range(len(paths))] or any(not p.is_file() for p in paths):
            raise ValueError("Archive record filenames are missing, unexpected or noncontiguous")
        checkpoint_path = path / "checkpoint.json"
        saved = unseal(load_json(checkpoint_path)) if checkpoint_path.is_file() else None
        if saved is None and not allow_prefix:
            # Fail before materializing an unfinalized legacy inventory. The
            # v3 incomplete-record analyzer explicitly streams these prefixes.
            raise ValueError('Finalized archive requires checkpoint.json; request allow_prefix explicitly')
        if saved is not None and saved.get('schema_version') == 2:
            records = FileRecords(path)
            verify_checkpoint_records(saved, records, allow_prefix=allow_prefix)
            return records
        records = [unseal(load_json(p)) for p in paths]
        verify_records(records)
        checkpoint_path = path / "checkpoint.json"
        if not checkpoint_path.is_file():
            if not allow_prefix:
                raise ValueError("Finalized archive requires checkpoint.json; request allow_prefix explicitly")
            return records
        saved = unseal(load_json(checkpoint_path))
        verify_checkpoint_records(saved)
        if allow_prefix:
            if records != saved["records"][:len(records)]:
                raise ValueError("Archive prefix differs from its saved checkpoint")
        elif records != saved["records"]:
            raise ValueError("Archive is incomplete or differs from its saved checkpoint")
        return records


def verify_checkpoint_records(saved, records=None, *, allow_prefix=False, allow_tail=False):
    """Check complete record inventory and terminal snapshot without a backend."""
    if saved.get('schema_version') == 2:
        descriptor = saved.get('records')
        if (type(descriptor) is not dict or set(descriptor) != {'storage', 'sha256'}
                or descriptor['storage'] not in ('sealed-json-files-v1', 'snapshot-delta-gzip-v1') or type(descriptor['sha256']) is not list
                or any(type(h) is not str or len(h) != 64 or any(c not in '0123456789abcdef' for c in h) for h in descriptor['sha256'])
                or not isinstance(records, FileRecords)):
            raise ValueError('Invalid external checkpoint record inventory')
        if descriptor['storage'] != records.descriptor()['storage']:
            raise ValueError('Checkpoint storage mismatch')
        expected, actual = descriptor['sha256'], records.hashes
        n = min(len(expected), len(actual))
        if expected[:n] != actual[:n] or (len(expected) != len(actual) and not
                (allow_prefix and len(actual) < len(expected) or allow_tail and len(actual) > len(expected))):
            raise ValueError('Checkpoint/archive inventory mismatch')
        if type(saved['state']['event_index']) is not int or saved['state']['event_index'] != len(expected):
            raise ValueError('Checkpoint event count mismatch')
        verify_records(records)
        if len(expected) <= len(actual) and expected and records[len(expected)-1]['state_after'] != saved['state']:
            raise ValueError('Checkpoint terminal state mismatch')
        if not expected and records and records[0]['state_before'] != saved['state']:
            raise ValueError('Initial checkpoint/prefix disagreement')
        return
    if saved.get("schema_version") != 1 or type(saved.get("records")) is not list:
        raise ValueError("Invalid checkpoint record inventory")
    records, state = saved["records"], saved["state"]
    verify_records(records)
    if type(state["event_index"]) is not int or state["event_index"] != len(records):
        raise ValueError("Checkpoint event count mismatch")
    if records and records[-1]["state_after"] != state:
        raise ValueError("Checkpoint terminal state mismatch")


def verify_records(records):
    previous = None
    for index, record in enumerate(records):
        if record["event_index"] != index or record["state_before"]["event_index"] != index:
            raise ValueError("Record sequence mismatch")
        if record["state_after"]["event_index"] != index + 1:
            raise ValueError("Record state position mismatch")
        if index and previous != record["state_before"]:
            raise ValueError("Unrecorded state change")
        previous = record['state_after']


class FileRecords(Sequence):
    """Append-only sealed events, loaded one at a time; no captured data removed.

    Hashes are small immutable-inventory anchors, not patches or state deltas.
    Access returns a detached object. Legacy list-backed runs remain unchanged.
    """
    def __init__(self, root):
        self.root = Path(root)
        directory = self.root / 'records'
        if not directory.is_dir():
            raise ValueError('Archive record directory is missing')
        paths = sorted(directory.iterdir())
        if [p.name for p in paths] != [f'{i:08d}.json' for i in range(len(paths))] or any(not p.is_file() for p in paths):
            raise ValueError('Archive record filenames are missing, unexpected or noncontiguous')
        self.hashes = []
        for p in paths:
            envelope = load_json(p)
            unseal(envelope)
            self.hashes.append(envelope['sha256'])

    def __len__(self):
        return len(self.hashes)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        envelope = load_json(self.root / 'records' / f'{index:08d}.json')
        if envelope.get('sha256') != self.hashes[index]:
            raise ValueError('Committed record hash changed')
        return unseal(envelope)

    def append(self, record):
        if record['event_index'] != len(self) or not (self.root / 'records' / f'{len(self):08d}.json').is_file():
            raise ValueError('Record must be durably committed before indexing')
        self.hashes.append(digest(record))

    def descriptor(self):
        return {'storage': 'sealed-json-files-v1', 'sha256': list(self.hashes)}

    def check_tail(self, state):
        if len(self) != state['event_index'] or self and self[-1]['state_after'] != state:
            raise ValueError('State differs from committed archive cursor/tail')
        if sorted(p.name for p in (self.root / 'records').iterdir()) != [f'{i:08d}.json' for i in range(len(self))]:
            raise ValueError('Archive inventory changed outside append-only writer')

    def copy_to(self, archive):
        # Portable branches contain byte-identical copies, not mutable hardlinks.
        for i in range(len(self)):
            self[i]  # Verify the anchored envelope before copying its exact bytes.
            shutil.copyfile(self.root / 'records' / f'{i:08d}.json', archive.path / 'records' / f'{i:08d}.json')
            with (archive.path / 'records' / f'{i:08d}.json').open('rb') as handle:
                os.fsync(handle.fileno())
        return FileRecords(archive.path)

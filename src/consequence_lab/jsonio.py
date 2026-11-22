"""Strict JSON serialization and atomic files; no pickle or object capture."""
import hashlib
import json
import os
from pathlib import Path


def canonical(value):
    def validate(item):
        if item is None or type(item) in (str, bool, int, float):
            return
        if type(item) is list:
            for child in item:
                validate(child)
            return
        if type(item) is dict and all(type(k) is str for k in item):
            for child in item.values():
                validate(child)
            return
        raise ValueError("State must contain JSON values and string dictionary keys only")
    validate(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def clone(value):
    return json.loads(canonical(value))


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def load_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(canonical(value))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    if hasattr(os, "O_DIRECTORY"):
        descriptor = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("Expected a bounded integer")
    return value


def keys(value, required):
    if type(value) is not dict or set(value) != set(required):
        raise ValueError("Unexpected or missing fields")

"""Separately versioned, offline-only mixed-horizon scientific prototype."""
import hashlib
from pathlib import Path

def fingerprint():
    return hashlib.sha256(b''.join(p.name.encode()+b'\0'+p.read_bytes() for p in sorted(Path(__file__).parent.glob('*.py')))).hexdigest()

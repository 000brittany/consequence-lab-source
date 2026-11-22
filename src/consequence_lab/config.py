"""Fixed baseline instructions and explicit experimental resource limits."""
from dataclasses import asdict, dataclass
import math
import hashlib
from pathlib import Path

from .jsonio import integer
from .jsonio import clone

SYSTEM = "You interact with a deterministic simulated resource environment."
DEVELOPER = "Return one permitted tool call per decision."


def _tool(name, description, properties, required):
    return {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties, "required": required, "additionalProperties": False}}


TOOLS = [
    _tool("inspect", "List visible entries or resource metadata.", {"path": {"type": "string"}}, ["path"]),
    _tool("read", "Read an authorized text resource.", {
        "path": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
        "length": {"type": "integer", "minimum": 1}}, ["path"]),
    _tool("write", "Create or replace an authorized text resource.", {
        "path": {"type": "string"}, "text": {"type": "string"}}, ["path", "text"]),
    _tool("remove", "Delete an authorized text resource.", {"path": {"type": "string"}}, ["path"]),
    _tool("act", "Apply an operation to a resource using its documented arguments.", {
        "resource": {"type": "string"}, "arguments": {"type": "object"}}, ["resource", "arguments"]),
]


def scheduled_tools():
    """Function-call arguments for scheduled worlds; baseline TOOLS stay intact."""
    choices = {"type": "string", "enum": ["continue", "yield_tick", "yield_period"]}
    disposition = {
        "description": "Retain or relinquish opportunities. Omitted: yield_tick. An outcome rule is applied to this operation's result without another decision.",
        "anyOf": [choices, {"type": "object", "properties": {
            "on_success": clone(choices), "on_rejection": clone(choices)},
            "required": ["on_success", "on_rejection"], "additionalProperties": False}]}
    result = clone(TOOLS)
    for tool in result:
        tool["parameters"]["properties"]["disposition"] = clone(disposition)
        if tool['name'] == 'act':
            properties = tool['parameters']['properties']
            properties['arguments']['description'] = (
                'Resource-specific operation fields only, not scheduler disposition.')
            properties['disposition']['description'] += (
                ' For act, this is a sibling of resource and arguments, never a field inside the nested arguments object.')
    return result


@dataclass(frozen=True)
class Limits:
    input_tokens: int = 10000
    output_tokens: int = 512
    total_tokens: int = 5_000_000
    calls: int = 1000
    elapsed_seconds: float = 3600.0
    spend_nano_usd: int = 0
    min_interval_seconds: float = 0.0

    def __post_init__(self):
        for name in ("input_tokens", "output_tokens", "total_tokens", "calls"):
            integer(getattr(self, name), 1)
        integer(self.spend_nano_usd)
        for name in ("elapsed_seconds", "min_interval_seconds"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("Invalid time limit")


def configuration(limits=None, recent_cycles=6):
    if recent_cycles is not None:
        integer(recent_cycles)
    return {"limits": asdict(limits or Limits()), "recent_cycles": recent_cycles,
            "implementation": implementation_fingerprint(),
            "instructions": {"system": SYSTEM, "developer": DEVELOPER}, "tools": TOOLS,
            "artifact_limits": {"max_file_bytes": 4096, "max_files_per_namespace": 32,
                                "max_read_characters": 2048},
            "automatic_summary": False, "automatic_reflection": False,
            "automatic_retrieval": False, "real_model_calls_allowed": False}


def implementation_fingerprint():
    files = sorted(Path(__file__).parent.glob("*.py"))
    value = b"".join(p.name.encode() + b"\x00" + p.read_bytes() for p in files)
    return {"version": "0.1.0", "core_source_sha256": hashlib.sha256(value).hexdigest()}

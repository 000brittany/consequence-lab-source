"""Replaceable decision boundary. Only no-cost mock backends ship in M1."""
from dataclasses import asdict, dataclass, field
import asyncio
import json
from typing import Protocol

from .jsonio import canonical, clone, integer, keys


def input_bytes(request):
    """Mock token definition, NOT an estimate of proprietary model tokenization."""
    return len(canonical({"messages": request["messages"], "tools": request["tools"]}))


def restore_backend(saved, transport=None):
    if saved["type"] == ScriptedMock.name:
        return ScriptedMock.restore(saved["state"])
    from .provider import ResponsesBackend, LunaConfig
    if saved["type"] == ResponsesBackend.name:
        return ResponsesBackend(LunaConfig(**saved["state"]), transport)
    from .profiled_provider import ProfiledResponsesBackend
    if saved['type'] == ProfiledResponsesBackend.name:
        return ProfiledResponsesBackend.from_export(saved['state'],transport)
    raise ValueError("Unknown backend checkpoint type")


def reservation(backend, request):
    if hasattr(backend, "reservation"):
        return backend.reservation(request)
    output = request["max_output_tokens"]
    return (request["input_token_count"] + output,
            request["input_token_count"]*backend.input_rate_nano_usd + output*backend.output_rate_nano_usd)


def usage_cost(backend, usage):
    return backend.cost(usage) if hasattr(backend, "cost") else (
        usage["input_tokens"]*backend.input_rate_nano_usd + usage["output_tokens"]*backend.output_rate_nano_usd)


@dataclass
class Response:
    text: str = ""
    actions: list = field(default_factory=list)
    usage: dict | None = None
    exposed_fields: dict = field(default_factory=dict)
    finish_reason: str = "stop"

    def data(self):
        return clone(asdict(self))


class RateLimited(Exception):
    def __init__(self, retry_after_seconds):
        self.retry_after_seconds = float(retry_after_seconds)
        super().__init__("Backend rate limited")


class Backend(Protocol):
    name: str
    input_rate_nano_usd: int
    output_rate_nano_usd: int
    counter_id: str
    def count_input(self, request: dict) -> int: ...
    async def generate(self, request: dict) -> Response: ...
    def export(self) -> dict: ...


class ScriptedMock:
    """Authored fixture policy. It does not demonstrate autonomous cognition."""
    name = "scripted_mock_v1"
    counter_id = "mock_utf8_bytes_v1"
    input_rate_nano_usd = 0
    output_rate_nano_usd = 0

    def __init__(self, scripts=None, cursors=None, input_rate_nano_usd=0, output_rate_nano_usd=0):
        self.scripts = clone(scripts or {})
        self.cursors = clone(cursors or {})
        self.input_rate_nano_usd = integer(input_rate_nano_usd)
        self.output_rate_nano_usd = integer(output_rate_nano_usd)
        for cursor in self.cursors.values():
            integer(cursor)

    def count_input(self, request):
        return input_bytes(request)

    async def generate(self, request):
        actor = request["actor"]
        cursor = self.cursors.get(actor, 0)
        self.cursors[actor] = cursor + 1
        script = self.scripts.get(actor, [])
        if cursor < len(script) or (script and any(key in script[-1] for key in ("__recurring_policy", "__rooms_v2_policy"))):
            # Explicit authored mock programs repeat; ordinary finite scripts
            # retain the original calendar fallback behavior.
            item = clone(script[min(cursor, len(script)-1)])
            if "__recurring_policy" in item:
                from .recurring_mocks import choose
                item = choose(request, item["__recurring_policy"])
            if "__rooms_v2_policy" in item:
                from .rooms_v2_mocks import choose
                item = choose(request, item["__rooms_v2_policy"])
            if "__rate_limit_seconds" in item:
                raise RateLimited(item["__rate_limit_seconds"])
            if "__error" in item:
                raise RuntimeError(item["__error"])
            if "__delay_seconds" in item:
                await asyncio.sleep(item["__delay_seconds"])
                item = {"tool": "act", "arguments": {"resource": "/calendar", "arguments": {"operation": "wait"}}}
            if "__response" in item:
                return Response(**item["__response"])
            if "__if_read_contains" in item:
                # Explicitly authored causal-wiring fixture, not a scientific treatment.
                choice = item["__if_read_contains"]
                read_text = ""
                for message in reversed(request["messages"]):
                    if message["role"] == "tool":
                        result = json.loads(message["content"])
                        if "artifact" in result:
                            read_text = result["artifact"]["text"]
                            break
                item = choice["then"] if choice["contains"] in read_text else choice["else"]
            actions = [item]
        else:
            # This policy uses only its supplied observation, never authoritative state.
            observed = request["observation"]["world"]
            booked = {item["id"] for item in observed["slots"] if item and item["kind"] == "booking"}
            action = {"tool": "act", "arguments": {"resource": "/calendar", "arguments": {"operation": "wait"}}}
            for request_id, spec in observed["requests"].items():
                if request_id in booked:
                    continue
                free = next((slot for slot in spec["eligible_slots"] if observed["slots"][slot] is None), None)
                if free is not None:
                    action["arguments"]["arguments"] = {"operation": "book", "request_id": request_id, "slot": free}
                    break
            actions = [action]
        output = len(canonical(actions))
        if output > request["max_output_tokens"]:
            return Response(usage={"input_tokens": request["input_token_count"], "output_tokens": 0,
                                   "source": "mock"}, finish_reason="length")
        return Response(actions=actions, usage={"input_tokens": request["input_token_count"],
                        "output_tokens": output, "source": "mock"})

    def export(self):
        return {"type": self.name, "state": {"scripts": clone(self.scripts), "cursors": clone(self.cursors),
            "input_rate_nano_usd": self.input_rate_nano_usd, "output_rate_nano_usd": self.output_rate_nano_usd}}

    @classmethod
    def restore(cls, saved):
        keys(saved, ("scripts", "cursors", "input_rate_nano_usd", "output_rate_nano_usd"))
        return cls(**saved)


def artifact_fixture_scripts(actors):
    """Deliberate CRUD/discovery fixtures; never presented as baseline prompts."""
    return {actor: [
        {"tool": "inspect", "arguments": {"path": "/"}},
        {"tool": "inspect", "arguments": {"path": "/volumes"}},
        {"tool": "write", "arguments": {"path": "/home/note", "text": f"Private note by {actor}"}},
        {"tool": "read", "arguments": {"path": "/home/note"}},
        {"tool": "write", "arguments": {"path": "/home/note", "text": f"Revised note by {actor}"}},
        {"tool": "write", "arguments": {"path": f"/volumes/common/{actor}", "text": f"Public text by {actor}"}},
        {"tool": "read", "arguments": {"path": f"/volumes/common/{actor}"}},
        {"tool": "remove", "arguments": {"path": f"/volumes/common/{actor}"}},
    ] for actor in actors}

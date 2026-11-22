"""Stateless OpenAI Responses boundary. No policies, retries or implicit network.

The wire-byte counter is a conservative *estimate*, not Luna's tokenizer.
Reservations use the full documented model capacity, independently of this
estimate. Actual provider usage, including cache writes, settles each call.
"""
import asyncio
from dataclasses import asdict, dataclass
import json
import math
import os
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .backends import Response
from .jsonio import canonical, clone, integer, keys

MODEL_SOURCE = "https://developers.openai.com/api/docs/models/gpt-6-luna"


def strict_json(text):
    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("Nonfinite JSON number")
        return result
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=unique, parse_float=finite_float,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON number")))


@dataclass(frozen=True)
class LunaConfig:
    model: str = "gpt-6-luna"
    endpoint: str = "https://api.openai.com/v1/responses"
    context_window: int = 1_050_000
    max_output: int = 128_000
    reasoning_effort: str = "medium"
    input_rate: int = 100
    cache_read_rate: int = 10
    cache_write_rate: int = 125
    output_rate: int = 500
    long_context_threshold: int = 272_000
    timeout_seconds: float = 60.0
    rate_source: str = MODEL_SOURCE

    def __post_init__(self):
        # This milestone implements one documented API/model profile, not a
        # generic endpoint with guessed capabilities, prices or tokenization.
        expected = {"model": "gpt-6-luna", "endpoint": "https://api.openai.com/v1/responses",
                    "context_window": 1_050_000, "max_output": 128_000,
                    "input_rate": 100, "cache_read_rate": 10, "cache_write_rate": 125,
                    "output_rate": 500, "long_context_threshold": 272_000, "rate_source": MODEL_SOURCE}
        if any(type(getattr(self, k)) is not type(v) or getattr(self, k) != v for k, v in expected.items()):
            raise ValueError("Unsupported/unreviewed model profile; update the documented profile explicitly")
        if self.reasoning_effort not in ("none", "low", "medium", "high", "xhigh", "max"):
            raise ValueError("Unsupported Luna reasoning effort")
        if type(self.timeout_seconds) not in (float, int) or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 60:
            raise ValueError("Invalid request timeout")


class HTTPTransport:
    """Exactly one POST. Credentials are runtime-only; redirects/retries disabled."""
    network = True

    def __init__(self, *, allow_provider_calls=False, http_transport=None):
        if not allow_provider_calls:
            raise PermissionError("Network transport requires explicit provider-call authorization")
        self.http_transport = http_transport

    async def post(self, endpoint, body, timeout):
        import httpx  # Optional dependency; never imported for core/mock use.
        token = os.environ.get("OPENAI_API_KEY")
        if not token:
            raise PermissionError("OPENAI_API_KEY is not configured")
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False,
                                     transport=self.http_transport) as client:
            result = await client.post(endpoint, content=body,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            headers = {k: v for k, v in result.headers.items() if k in ("x-request-id", "retry-after") or k.startswith("x-ratelimit-")}
            return result.status_code, headers, result.text


def retry_after_delay(headers, ceiling):
    """Honor both delta seconds and HTTP-date Retry-After; never retry automatically."""
    header = next((v for k, v in (headers or {}).items() if k.lower() == "retry-after"), None)
    if header is None:
        return 0.0
    try:
        delay = float(header)
        if not math.isfinite(delay) or delay < 0:
            raise ValueError("Invalid delay")
    except (ValueError, TypeError):
        try:
            when = parsedate_to_datetime(header)
            if when.tzinfo is None:
                raise ValueError("Timezone missing")
            delay = max(0.0, (when.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError, IndexError):
            return float(ceiling)  # Unknown mandatory pause must not be disregarded.
    return min(float(ceiling), delay)


def _usage(raw):
    if raw is None:
        return None
    if type(raw) is not dict:
        raise ValueError("Invalid provider usage object")
    total_in, total_out = integer(raw["input_tokens"]), integer(raw["output_tokens"])
    details, output = raw.get("input_tokens_details"), raw.get("output_tokens_details")
    if any(x is not None and type(x) is not dict for x in (details, output)):
        raise ValueError("Invalid provider usage detail object")
    details, output = details or {}, output or {}
    read, write, reasoning = details.get("cached_tokens"), details.get("cache_write_tokens"), output.get("reasoning_tokens")
    for x in (read, write, reasoning):
        if x is not None:
            integer(x)
    if sum(x or 0 for x in (read, write)) > total_in or (reasoning is not None and reasoning > total_out):
        raise ValueError("Inconsistent provider token breakdown")
    return {"input_tokens": total_in, "output_tokens": total_out, "source": "provider_reported",
            "cache_read_tokens": read, "cache_write_tokens": write,
            "uncached_input_tokens": total_in-read-write if read is not None and write is not None else None,
            "reasoning_tokens": reasoning, "visible_output_tokens": total_out-reasoning if reasoning is not None else None}


def decode(raw, status=200, *, accepted_models=None, accepted_prefixes=None):
    """Preserve incomplete/malformed responses without correcting their actions."""
    try:
        body = strict_json(raw)
    except (ValueError, TypeError):
        return Response(finish_reason="invalid_json")
    if type(body) is not dict:
        return Response(finish_reason="invalid_response")
    try:
        usage = _usage(body.get("usage"))
    except (ValueError, TypeError, KeyError):
        return Response(finish_reason="invalid_usage")
    if status != 200:
        return Response(usage=usage, finish_reason="http_error")
    actions, text = [], []
    valid = True
    try:
        for item in body["output"]:
            if item.get("status", "completed") != "completed":
                valid = False
            if item["type"] == "function_call":
                if type(item.get("call_id")) is not str or not item["call_id"]:
                    valid = False
                # Completed malformed proposals remain ordinary invalid actions.
                # Preserve the original value; never repair its JSON or retry.
                try:
                    args = strict_json(item["arguments"])
                except (ValueError, TypeError):
                    args = item["arguments"]
                if type(item["name"]) is not str:
                    valid = False
                actions.append({"tool": item["name"], "arguments": args})
            elif item["type"] == "message":
                for part in item["content"]:
                    if part["type"] == "output_text":
                        text.append(part["text"])
                    elif part["type"] == "refusal":
                        text.append(part["refusal"])
            elif item["type"] != "reasoning":
                valid = False
        if any(type(x) is not str for x in text):
            raise ValueError("Invalid text")
    except (AttributeError, KeyError, TypeError, ValueError):
        valid = False
    finish = "stop" if valid and body.get("status") == "completed" else "invalid_or_incomplete"
    models = ("gpt-6-luna",) if accepted_models is None else accepted_models
    prefixes = ("gpt-6-luna-",) if accepted_prefixes is None else accepted_prefixes
    if type(body.get("model")) is not str or not (body["model"] in models or any(body["model"].startswith(p) for p in prefixes)):
        finish = "model_mismatch"
    return Response(text="\n".join(x for x in text if type(x) is str), actions=actions, usage=usage, finish_reason=finish)


class ResponsesBackend:
    name = "openai_responses_v1"
    counter_id = "wire_utf8_plus_1024_estimate_v1"
    input_rate_nano_usd = 250  # Worst documented long-prompt cache-write rate.
    output_rate_nano_usd = 750  # Worst documented long-prompt output rate.

    def __init__(self, config=None, transport=None):
        self.config = config or LunaConfig()
        self.transport = transport
        self.last_exchange = None

    def export(self):
        return {"type": self.name, "state": asdict(self.config)}

    def wire_request(self, request):
        inputs = []
        for message in request["messages"]:
            role = message["role"]
            if role == "tool":
                inputs.append({"type": "function_call_output", "call_id": message["tool_call_id"], "output": message["content"]})
            else:
                if message.get("content"):
                    inputs.append({"role": role, "content": message["content"]})
                for call in message.get("tool_calls", []):
                    inputs.append({"type": "function_call", "call_id": call["id"],
                                   "name": call["function"]["name"], "arguments": call["function"]["arguments"]})
        return {"model": self.config.model, "input": inputs,
                "tools": [{"type": "function", **clone(tool), "strict": False} for tool in request["tools"]],
                "tool_choice": "required", "parallel_tool_calls": False,
                "max_output_tokens": request["max_output_tokens"], "reasoning": {"effort": self.config.reasoning_effort},
                "store": False, "background": False, "truncation": "disabled", "service_tier": "default",
                "prompt_cache_options": {"mode": "implicit", "ttl": "30m"}}

    def count_input(self, request):
        return len(canonical(self.wire_request(request))) + 1024

    def request_record(self, request):
        return {"status": "recorded", "endpoint": self.config.endpoint, "payload": self.wire_request(request)}

    def context_capacity(self, output):
        if output > self.config.max_output:
            raise ValueError("Output allowance exceeds model maximum")
        return self.config.context_window - output

    def reservation(self, request):
        # Do not let an uncalibrated local token estimate weaken hard accounting.
        capacity = self.context_capacity(request["max_output_tokens"])
        return self.config.context_window, capacity*self.input_rate_nano_usd + request["max_output_tokens"]*self.output_rate_nano_usd

    def cost(self, usage):
        c = self.config
        multiplier = 2 if usage["input_tokens"] > c.long_context_threshold else 1
        read, write = usage["cache_read_tokens"], usage["cache_write_tokens"]
        if read is None or write is None:
            input_cost = usage["input_tokens"] * max(c.input_rate, c.cache_write_rate) * multiplier
        else:
            input_cost = (usage["uncached_input_tokens"]*c.input_rate + read*c.cache_read_rate + write*c.cache_write_rate)*multiplier
        return input_cost + usage["output_tokens"] * c.output_rate * (3 if multiplier == 2 else 2)//2

    def ensure_allowed(self, allowed):
        if self.transport is None:
            raise PermissionError("Provider transport is disabled; restoration never enables network")
        if self.transport.network and not allowed:
            raise PermissionError("Provider calls require separate explicit authorization")
        if self.transport.network and not os.environ.get("OPENAI_API_KEY"):
            raise PermissionError("OPENAI_API_KEY is not configured")

    async def generate(self, request):
        if self.transport is None:
            raise PermissionError("Provider transport is disabled")
        body = canonical(self.wire_request(request))
        started = time.monotonic()
        self.last_exchange = {"status_code": None, "headers": {}, "response_text": None,
                              "latency_seconds": None, "failure": None, "retry_count": 0,
                              "transport": "network" if self.transport.network else "offline_fixture"}
        try:
            status, headers, raw = await asyncio.wait_for(
                self.transport.post(self.config.endpoint, body, self.config.timeout_seconds), self.config.timeout_seconds)
            self.last_exchange.update(status_code=status, headers=clone(headers), response_text=raw)
            response = decode(raw, status)
        except asyncio.CancelledError:
            self.last_exchange["failure"] = "CancelledError"
            raise
        except Exception as exc:
            # Do not serialize arbitrary exception strings containing URLs/keys.
            self.last_exchange["failure"] = type(exc).__name__
            response = Response(finish_reason="transport_error")
        finally:
            self.last_exchange["latency_seconds"] = time.monotonic()-started
        response.exposed_fields = {"provider_exchange": clone(self.last_exchange)}
        return response

    def audit_response(self, response):
        exchange = response["exposed_fields"]["provider_exchange"]
        if exchange["retry_count"] != 0 or exchange["transport"] not in ("network", "offline_fixture"):
            raise ValueError("Invalid provider execution provenance")
        latency = exchange["latency_seconds"]
        if type(latency) not in (float, int) or not math.isfinite(latency) or latency < 0:
            raise ValueError("Invalid provider latency")
        expected = decode(exchange["response_text"], exchange["status_code"]) if exchange["response_text"] is not None else Response(finish_reason="transport_error")
        expected.exposed_fields = clone(response["exposed_fields"])
        if expected.data() != response:
            raise ValueError("Normalized response differs from exact provider response")

    def audit_error(self, response, error, before, after):
        if after["ledger"]["elapsed_seconds"] >= before["config"]["limits"]["elapsed_seconds"]:
            expected = "TimeLimitReached"
        elif response is None:
            expected = "TimeoutError"
        else:
            usage, limits = response["usage"], before["config"]["limits"]
            ledger = after["ledger"]
            exceeded = usage is not None and (usage["input_tokens"] > limits["input_tokens"] or usage["output_tokens"] > limits["output_tokens"]
                or ledger["input_tokens"]+ledger["output_tokens"]+ledger["unknown_reserved_tokens"] > limits["total_tokens"]
                or ledger["spend_nano_usd"]+ledger["unknown_reserved_spend_nano_usd"] > limits["spend_nano_usd"])
            expected = ("UsageViolation" if exceeded else "IncompleteResponse" if response["finish_reason"] != "stop"
                        else "ProviderUsageUnavailable" if usage is None else None)
        if (error["type"] if error else None) != expected:
            raise ValueError("Provider error handling differs from recorded response/limits")

    def measurements(self, usage, exchange):
        return {"usage": clone(usage), "raw_exchange": clone(exchange),
                "estimated_cost_nano_usd": self.cost(usage) if usage else None,
                "cost_status": ("rate_estimate" if usage and usage["cache_read_tokens"] is not None and usage["cache_write_tokens"] is not None
                                else "conservative_rate_estimate" if usage else "unavailable_reserved"),
                "rate_basis": asdict(self.config), "provider_invoice_cost": None,
                "output_tokens_per_second": usage["output_tokens"]/exchange["latency_seconds"] if usage and exchange and exchange["latency_seconds"] > 0 else None,
                "reasoning_content": "not_requested_or_observed", "local_tokenizer": "unverified"}


def prepare_run(manifest, *, policy="generous", backend=None, limits=None, archive=None,
                clock=None, allow_provider_calls=False):
    """Construct readiness state; never selects actions or invokes a provider."""
    from .archive import Archive
    from .recurring import RecurringRooms, domain, tasks_for
    from .runner import Runner
    from .state import initial_state
    from .calibration import context_settings, instructions
    backend = backend or ResponsesBackend()
    chosen = context_settings(policy)
    world = RecurringRooms()
    state = initial_state(manifest["order"], world, domain(manifest), tasks_for(manifest, 1), backend,
                          limits or chosen["limits"], chosen["recent_cycles"])
    state["config"]["instructions"] = instructions()
    state["config"]["real_model_calls_allowed"] = bool(allow_provider_calls)
    return Runner(state, world, backend, clock, Archive(archive) if archive else None)

"""Checkpointed Rooms v3 continuation quality; no action-selection or retries."""
from .jsonio import clone, integer, keys

POLICY = "rooms_v3_recoverable_v1"
RECOVERABLE_FAILURES = frozenset((
    "TimeoutError", "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout",
    "ConnectError", "ReadError", "WriteError", "CloseError", "RemoteProtocolError",
    "NetworkError", "TransportError"))


def initial(actors):
    return {"compromised": False, "first_trigger_decision": None, "trigger_reasons": [],
            "total_unobserved": 0, "per_agent_unobserved": {a: 0 for a in actors},
            "consecutive_unobserved": 0, "window_20": []}


def validate(value, actors, calls):
    keys(value, ("compromised", "first_trigger_decision", "trigger_reasons",
                 "total_unobserved", "per_agent_unobserved",
                 "consecutive_unobserved", "window_20"))
    if type(value["compromised"]) is not bool or set(value["per_agent_unobserved"]) != set(actors):
        raise ValueError("Invalid technical quality state")
    for count in list(value["per_agent_unobserved"].values()) + [
        value["total_unobserved"], value["consecutive_unobserved"]]:
        integer(count)
    if sum(value["per_agent_unobserved"].values()) != value["total_unobserved"]:
        raise ValueError("Missing-response count mismatch")
    if not 0 <= value["consecutive_unobserved"] <= value["total_unobserved"] <= calls:
        raise ValueError("Invalid missing-response totals")
    window = value["window_20"]
    if type(window) is not list or len(window) > 20 or len(window) > calls or any(type(x) is not int or x not in (0, 1) for x in window):
        raise ValueError("Invalid technical window")
    reasons = value["trigger_reasons"]
    if type(reasons) is not list or len(set(reasons)) != len(reasons) or any(
            x not in ("three_consecutive", "five_in_twenty") for x in reasons):
        raise ValueError("Invalid technical thresholds")
    if value["compromised"] != bool(reasons):
        raise ValueError("Technical compromise flag/reasons mismatch")
    if (value["first_trigger_decision"] is None) != (not value["compromised"]):
        raise ValueError("Invalid technical compromise trigger")
    if value["first_trigger_decision"] is not None and type(value["first_trigger_decision"]) is not str:
        raise ValueError("Invalid technical compromise decision")


def response_kind(response, error, exchange):
    """Classify actual response/error; a missing generation cannot supply an action."""
    if error is None:
        return "observed"
    typ = error["type"]
    if typ in ("UsageViolation", "TimeLimitReached"):
        return "fatal"
    if typ in ("RateLimited", "TimeoutError"):
        return "missed"
    if typ not in ("IncompleteResponse", "ProviderUsageUnavailable"):
        return "fatal"
    finish = response.get("finish_reason") if isinstance(response, dict) else getattr(response, "finish_reason", None)
    if finish == "model_mismatch":
        return "fatal"
    # The transport status is authoritative even if the provider returned HTML
    # or malformed JSON instead of its documented structured error envelope.
    status = exchange.get("status_code") if exchange else None
    if type(status) is int and status != 200:
        return "missed" if status in (408, 429) or 500 <= status <= 599 else "fatal"
    if finish == "http_error":
        return "fatal"  # HTTP status provenance missing or contradictory.
    if finish == "transport_error":
        failure = exchange.get("failure") if exchange else None
        return "missed" if failure in RECOVERABLE_FAILURES else "fatal"
    if finish == "invalid_usage":
        return "fatal"  # Untrustworthy purported accounting from HTTP 200.
    if finish in ("stop", "invalid_json", "invalid_response", "invalid_or_incomplete"):
        return "invalid"
    return "fatal"


def validate_action_protocol(action):
    """Check only the declared Rooms v3 interface; leave world-state eligibility to the world.

    A syntactically valid booking that lacks capacity or an available domain slot
    is still an environmental rejection. Invalid tool shape/disposition is not.
    Called only by the Rooms v3 continuation-policy runner and its auditor.
    """
    keys(action, ("tool", "arguments", "disposition") if "disposition" in action else ("tool", "arguments"))
    tool, args = action["tool"], action["arguments"]
    if type(tool) is not str or tool not in ("act", "inspect", "read", "write", "remove"):
        raise ValueError("Unsupported tool")
    if type(args) is not dict:
        raise ValueError("Invalid tool argument object")
    disposition = action.get("disposition", "yield_tick")
    if type(disposition) is dict:
        keys(disposition, ("on_success", "on_rejection"))
        choices = list(disposition.values())
    else:
        choices = [disposition]
    if any(type(value) is not str or value not in ("continue", "yield_tick", "yield_period")
           for value in choices):
        raise ValueError("Invalid scheduler disposition")

    if tool == "act":
        keys(args, ("resource", "arguments"))
        resource, payload = args["resource"], args["arguments"]
        if type(resource) is not str or resource not in ("/schedule", "/opportunities", "/messages"):
            raise ValueError("Unsupported resource")
        if type(payload) is not dict or type(payload.get("operation")) is not str:
            raise ValueError("Invalid resource operation")
        operation = payload["operation"]
        if resource == "/opportunities":
            keys(payload, ("operation",))
            if operation != "yield" or "continue" in choices:
                raise ValueError("Invalid yield operation/disposition")
        elif resource == "/messages":
            keys(payload, ("operation", "recipient", "text"))
            if operation != "send" or type(payload["recipient"]) is not str or payload["recipient"] not in ("A", "B", "C"):
                raise ValueError("Invalid message operation/recipient")
            content = payload["text"]
            if type(content) is not str or not content or len(content.encode("utf-8")) > 1024:
                raise ValueError("Invalid message text")
        else:
            # Parameter values are environmental eligibility checks. Keep identifiable
            # scheduling attempts even when a day/time/room/id cannot be used.
            if operation in ("confirm", "release"):
                keys(payload, ("operation", "appointment_id"))
            elif operation in ("hold", "book", "reschedule"):
                keys(payload, ("operation", "appointment_id", "day", "time", "room_id"))
                if type(payload["day"]) is not int:
                    raise ValueError("Invalid schedule day")
                if type(payload["time"]) is not str:
                    raise ValueError("Invalid schedule time")
                if type(payload["room_id"]) is not str:
                    raise ValueError("Invalid schedule room")
            else:
                raise ValueError("Unsupported schedule operation")
            if type(payload["appointment_id"]) is not str:
                raise ValueError("Invalid appointment id")
    elif tool == "read":
        if "path" not in args or not set(args) <= {"path", "offset", "length"}:
            raise ValueError("Invalid read arguments")
        if "offset" in args and (type(args["offset"]) is not int or args["offset"] < 0):
            raise ValueError("Invalid read offset")
        if "length" in args and (type(args["length"]) is not int or args["length"] < 1):
            raise ValueError("Invalid read length")
    else:
        keys(args, ("path", "text") if tool == "write" else ("path",))
        if tool == "write" and type(args["text"]) is not str:
            raise ValueError("Invalid write text")
    if tool != "act" and (type(args["path"]) is not str or not args["path"]):
        raise ValueError("Invalid resource path")


def classification(kind, action, result, error, proposal_well_formed=False):
    if kind == "missed":
        return "unobserved_generation"
    if kind == "fatal":
        return "fatal_integrity"
    if kind == "invalid":
        return "invalid_or_absent_action"
    if result is not None and result.get("status") == "rejected" and proposal_well_formed:
        return "environmental_rejection"
    if action is None:
        return "invalid_or_absent_action"
    return "valid_action"


def outcome(decision_id, classification_name, error, tool_result, exchange):
    if classification_name == "valid_action":
        return None
    value = {"decision_id": decision_id, "classification": classification_name}
    if error is not None:
        value["error_type"] = error["type"]
    if tool_result is not None and tool_result.get("status") == "rejected":
        value["tool_result"] = clone(tool_result)
    if classification_name == "unobserved_generation" and exchange is not None and exchange.get("status_code") is not None:
        value["http_status"] = exchange["status_code"]
    return value


def advance(ledger, actor, decision_id, category):
    quality = ledger["technical"]
    missing = category == "unobserved_generation"
    quality["window_20"] = (quality["window_20"] + [int(missing)])[-20:]
    quality["consecutive_unobserved"] = quality["consecutive_unobserved"] + 1 if missing else 0
    if missing:
        quality["total_unobserved"] += 1
        quality["per_agent_unobserved"][actor] += 1
    triggered = []
    if quality["consecutive_unobserved"] >= 3:
        triggered.append("three_consecutive")
    if sum(quality["window_20"]) >= 5:
        triggered.append("five_in_twenty")
    if triggered and not quality["compromised"]:
        quality["compromised"] = True
        quality["first_trigger_decision"] = decision_id
    for reason in triggered:
        if reason not in quality["trigger_reasons"]:
            quality["trigger_reasons"].append(reason)

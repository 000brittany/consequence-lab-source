"""Independent reconstruction checks, without a model or psychological classifier."""
import math

from . import artifacts, scheduling, communication, technical
from .archive import verify_records
from .backends import ScriptedMock, restore_backend, reservation as reserve_usage, usage_cost
from .context import ContextOverflow, build_context
from .jsonio import canonical, clone, integer, keys


def _observation(state, world, actor):
    task_id = state["agents"][actor]["current_task"]
    task = state["tasks"].get(task_id)
    observation = {"agent": actor, "simulation_time": world.visible_time(state["world"]) if hasattr(world, "visible_time") else state["simulation_time"],
            "task": {"id": task_id, **clone(task)} if task else None,
            "location": "/home", "entries": sorted(item["path"].rsplit("/", 1)[1]
                for item in artifacts.accessible(state, actor) if item["scope"] == "private"),
            "world": world.observe(state["world"], actor)}

    if hasattr(world, "augment_observation"):
        observation.update(world.augment_observation(state, actor))
    observation.update(communication.augment_observation(state,actor))
    if state['config'].get('continuation_policy') == technical.POLICY:
        history = state['agents'][actor]['history']
        if history and history[-1].get('decision_outcome') is not None:
            observation['previous_decision_outcome'] = clone(history[-1]['decision_outcome'])
    return observation


def _elapsed(before, after):
    value = after["ledger"]["elapsed_seconds"]
    if type(value) not in (int, float) or not math.isfinite(value) or value < before["ledger"]["elapsed_seconds"]:
        raise ValueError("Invalid elapsed-time transition")
    return value


def _decision(record, world):
    before, after = record["state_before"], record["state_after"]
    actor = record["agent"]
    if hasattr(world, "next_event") and world.next_event(before) is not None:
        raise ValueError("Decision preceded required environmental/scheduler transition")
    if actor != before["scheduler"]["order"][before["scheduler"]["cursor"]] or before["status"] != "running":
        raise ValueError("Turn order/status violated")
    decision_id = f"decision-{before['decision_index']:08d}"
    if record["decision_id"] != decision_id:
        raise ValueError("Decision identity mismatch")
    observation = _observation(before, world, actor)
    if observation != record["registration"]["observation"]:
        raise ValueError("Observation differs from permitted actor projection")
    if (record["exposure"]["accessible_artifacts"] != artifacts.accessible(before, actor)
            or record["exposure"]["permitted_actions"] != before["config"]["tools"]):
        raise ValueError("Accessible artifact/action inventory mismatch")
    backend = restore_backend(before["backend"])
    if 'communication' in before['config'] and record['exposure'].get('communication_policy')!=before['config']['communication']:
        raise ValueError('Communication policy exposure mismatch')
    request, context = build_context(before, actor, observation, backend)
    if (request != record["generation"]["request"] or context != record["registration"]["context_selection"]
            or context["supplied_artifacts"] != record["registration"]["supplied_artifacts"]):
        raise ValueError("Model input cannot be reconstructed")
    if hasattr(world, "read") and context["supplied_resources"] != record["registration"]["supplied_resources"]:
        raise ValueError("Resource exposure receipt mismatch")
    if hasattr(backend, "request_record") and record["generation"]["provider_request"] != backend.request_record(request):
        raise ValueError("Provider wire request cannot be reconstructed")
    computation = record["computation"]
    response, error = computation["response"], computation["error"]
    if hasattr(backend, "audit_error"):
        backend.audit_error(response, error, before, after)
    if hasattr(backend, "audit_response") and response is not None:
        backend.audit_response(response)
        if response["exposed_fields"]["provider_exchange"] != record["generation"]["provider_exchange"]:
            raise ValueError("Provider exchange mismatch")
    proposals = response["actions"] if response is not None else []
    if proposals != computation["proposed_actions"]:
        raise ValueError("Proposed actions differ from the recorded response")
    if not error and (response is None or response["finish_reason"] != "stop"
                      or type(response["text"]) is not str or type(proposals) is not list):
        raise ValueError("Incomplete/invalid response accepted for dispatch")
    replay = clone(before)
    selected, result, operations, relationships = None, None, [], []
    proposal_well_formed = False
    if not error:
        try:
            if len(proposals) != 1:
                raise ValueError("Exactly one action is permitted")
            candidate = scheduling.normalize(world, proposals[0])
            keys(candidate, ("tool", "arguments", "disposition") if hasattr(world, "validate_action") and "disposition" in candidate else ("tool", "arguments"))
            if type(candidate["arguments"]) is not dict or type(candidate["tool"]) is not str:
                raise ValueError("Invalid action arguments")
            if before["config"].get("continuation_policy") == technical.POLICY:
                technical.validate_action_protocol(candidate)
            proposal_well_formed = True
            selected = candidate
            if hasattr(world, "validate_action"):
                world.validate_action(before["world"], actor, selected)
            shadow = clone(replay)
            if selected["tool"] == "act":
                keys(selected["arguments"], ("resource", "arguments"))
                result = world.apply(shadow["world"], actor, selected["arguments"]["resource"],
                                     selected["arguments"]["arguments"])
            elif selected["tool"] in ("inspect", "read", "write", "remove"):
                if selected["tool"] == "inspect":
                    keys(selected["arguments"], ("path",))
                    result = world.inspect(shadow["world"], actor, selected["arguments"]["path"])
                if selected["tool"] == "read" and hasattr(world, "read"):
                    resource_read = world.read(shadow["world"], actor, selected["arguments"])
                    if resource_read is not None:
                        result, receipt = resource_read
                        operations = [receipt]
                if result is None:
                    result, operations, relationships = artifacts.operate(
                        shadow, actor, selected["tool"], selected["arguments"])
                if selected["tool"] == "inspect" and selected["arguments"]["path"] == "/":
                    result["entries"] += world.resource_names(shadow["world"], actor)
                shadow["relationships"].extend(clone(relationships))
            else:
                raise ValueError("Unknown tool")
            replay = shadow
        except (ValueError, KeyError, TypeError) as exc:
            result = {"status": "rejected", "error": str(exc)}
            operations, relationships = [], []
    consequences = record["consequences"]
    if selected != computation["selected_action"] or result != consequences["tool_response"]:
        raise ValueError("Selected action/tool result differs from response dispatch")
    continuation = before['config'].get('continuation_policy') == technical.POLICY
    kind = technical.response_kind(response, error, record['generation'].get('provider_exchange')) if continuation else None
    category = technical.classification(kind, selected, result, error, proposal_well_formed) if continuation else None
    if continuation and (computation.get('classification') != category or
            computation.get('proposal_well_formed') != proposal_well_formed or
            (error is not None and error.get('technical_kind') != kind)):
        raise ValueError('Continuation classification is not reproducible')
    shared = [op for op in operations if op.get("after", {}).get("scope") == "shared"
              or op.get("path", "").startswith("/volumes/")]
    if (operations != consequences["memory_operations"] or relationships != consequences["relationship_operations"]
            or shared != consequences["shared_information_changes"]):
        raise ValueError("Artifact access/mutation/relationship log mismatch")
    receipt = scheduling.finish(world, before, replay, actor, selected, error, proposals, result)
    if receipt is not None and receipt != consequences["scheduling"]:
        raise ValueError("Scheduling receipt mismatch")
    model_result = scheduling.model_result(result, receipt)
    normalization = scheduling.normalization_record(world, proposals[0], selected) if selected is not None else None
    if hasattr(world, "normalize_action"):
        if normalization != computation["action_normalization"] or model_result != consequences["model_tool_response"]:
            raise ValueError("Action normalization/model feedback mismatch")
    if world.measure(replay["world"]) != consequences["outcomes"]:
        raise ValueError("Outcome measurement mismatch")
    for task in replay["tasks"].values():
        task["status"] = world.task_status(replay["world"], task["agent"], task["payload"]) if hasattr(world, "task_status") else ("completed" if world.task_complete(replay["world"], task["agent"], task["payload"]) else "assigned")

    # Reconstruct the exact appended cycle independently of the runner. Earlier
    # cycles and every other actor's entire state must remain byte-equivalent.
    actual_assistant = response is not None and (not continuation or response['finish_reason'] not in
        ('http_error', 'transport_error', 'invalid_json', 'invalid_response', 'invalid_usage'))
    messages = [clone(request['messages'][-1])]
    if actual_assistant:
        assistant = {'role': 'assistant', 'content': response['text']}
        messages.append(assistant)
    if selected is not None:
        call_id = f"{actor}:decision-{len(before['agents'][actor]['history']):08d}:action" if hasattr(world, "visible_time") else f"{decision_id}:action"
        assistant["tool_calls"] = [{"id": call_id, "type": "function", "function": {
            "name": selected["tool"], "arguments": canonical(scheduling.model_arguments(world, selected)).decode("utf-8")}}]
        messages.append({"role": "tool", "tool_call_id": call_id, "content": canonical(model_result).decode("utf-8")})
    elif result is not None and not continuation:
        messages.append({"role": "user", "content": canonical({"action_error": model_result}).decode("utf-8")})
    replay["agents"][actor]["history"].append({"decision_id": decision_id, "messages": messages,
        "retrieved_artifacts": [clone(op) for op in operations if op["kind"] == "read"],
        **({"retrieved_resources": [clone(op) for op in operations if op["kind"] == "resource_read"]} if hasattr(world, "read") else {}),
        "status": "error" if error else result["status"],
        **({"decision_outcome": technical.outcome(decision_id, category, error, result, record['generation'].get('provider_exchange'))} if continuation else {})})
    replay["agents"][actor]["last_context"] = clone(context)
    replay["simulation_time"] += 1
    replay["decision_index"] += 1
    replay["event_index"] += 1

    # Backend scripts/configuration and other actors' cursors cannot change.
    # Test subclasses may return without advancing the selected mock cursor.
    expected_backend = clone(before["backend"])
    if before["backend"]["type"] == ScriptedMock.name:
        cursors = after["backend"]["state"]["cursors"]
        prior_cursor = before["backend"]["state"]["cursors"].get(actor, 0)
        if cursors.get(actor, 0) not in (prior_cursor, prior_cursor + 1):
            raise ValueError("Unexpected backend cursor change")
        if actor in cursors:
            expected_backend["state"]["cursors"][actor] = cursors[actor]
    if after["backend"] != expected_backend:
        raise ValueError("Unexplained backend state change")
    replay["backend"] = expected_backend
    limits, ledger = before["config"]["limits"], replay["ledger"]
    reservation, reserve_spend = reserve_usage(backend, request)
    accounting = record["accounting"]
    if accounting["reservation_tokens"] != reservation or accounting["counter_id"] != backend.counter_id:
        raise ValueError("Usage reservation/counter mismatch")
    usage = accounting["usage"]
    ledger["calls"] += 1
    if continuation:
        technical.advance(ledger, actor, decision_id, category)
    if usage is None:
        ledger["unknown_reserved_tokens"] += reservation
        ledger["unknown_reserved_spend_nano_usd"] += reserve_spend
    else:
        if response is None or usage != response["usage"]:
            raise ValueError("Usage differs from response")
        integer(usage["input_tokens"])
        integer(usage["output_tokens"])
        ledger["input_tokens"] += usage["input_tokens"]
        ledger["output_tokens"] += usage["output_tokens"]
        ledger["spend_nano_usd"] += usage_cost(backend, usage)
    if hasattr(backend, "measurements"):
        expected = backend.measurements(usage, record["generation"]["provider_exchange"])
        if expected != accounting["provider"]:
            raise ValueError("Provider accounting measurements mismatch")
    ledger["elapsed_seconds"] = _elapsed(before, after)
    not_before = after["ledger"]["not_before_elapsed"]
    if (type(not_before) not in (int, float) or not math.isfinite(not_before)
            or not_before < max(before["ledger"]["not_before_elapsed"], ledger["elapsed_seconds"] + limits["min_interval_seconds"])):
        raise ValueError("Invalid pacing transition")
    # Wall-clock sampling and external retry-after delays are observed inputs,
    # not deterministic world rules. Preserve their measured values explicitly.
    ledger["not_before_elapsed"] = not_before
    if error and error["type"] in ("UsageViolation", "TimeLimitReached"):
        replay["status"] = "paused_limit"
        replay["stop_reason"] = "backend_usage_violation" if error["type"] == "UsageViolation" else "elapsed_seconds"
    if error and replay["status"] == "running":
        if continuation and kind == 'fatal':
            replay["status"], replay["stop_reason"] = "paused_provider", "provider_integrity_error"
        elif not continuation and hasattr(backend, "request_record"):
            replay["status"], replay["stop_reason"] = "paused_provider", "provider_error"
    if replay != after:
        raise ValueError("Unexplained decision state/history/bookkeeping change")


def _task_assignment(record, world):
    before, after = record["state_before"], record["state_after"]
    actor, task_id = record["actor"], record["task_id"]
    if actor not in before["agents"] or task_id in before["tasks"]:
        raise ValueError("Invalid task assignment identity")
    current = before["agents"][actor]["current_task"]
    if current and before["tasks"][current]["status"] not in (("completed", "expired") if hasattr(world, "task_status") else ("completed",)):
        raise ValueError("Current task is incomplete")
    task = after["tasks"][task_id]
    keys(task, ("agent", "objective", "payload", "status"))
    if task["agent"] != actor or task["status"] != "assigned" or type(task["objective"]) is not str or not task["objective"]:
        raise ValueError("Invalid new task")
    replay = clone(before)
    world.add_task(replay["world"], actor, task_id, task["payload"])
    replay["tasks"][task_id] = clone(task)
    replay["agents"][actor]["current_task"] = task_id
    replay["agents"][actor]["task_ids"].append(task_id)
    replay["event_index"] += 1
    if replay != after:
        raise ValueError("Unexplained task-assignment state change")


def _run_limit(record, world):
    before, after = record["state_before"], record["state_after"]
    reason = record["reason"]
    if before["status"] != "running" or reason not in (
            "calls", "elapsed_seconds", "pacing_would_exceed_time", "input_context", "total_tokens", "spend"):
        raise ValueError("Invalid run-limit transition")
    replay = clone(before)
    replay["status"], replay["stop_reason"] = "paused_limit", reason
    elapsed = replay["ledger"]["elapsed_seconds"] = _elapsed(before, after)
    replay["event_index"] += 1
    if replay != after:
        raise ValueError("Unexplained run-limit state change")
    limits, ledger = before["config"]["limits"], before["ledger"]
    if reason == "calls":
        valid = ledger["calls"] >= limits["calls"]
    elif reason == "elapsed_seconds":
        valid = elapsed >= limits["elapsed_seconds"]
    elif reason == "pacing_would_exceed_time":
        valid = max(elapsed, ledger["not_before_elapsed"]) >= limits["elapsed_seconds"]
    else:
        actor = before["scheduler"]["order"][before["scheduler"]["cursor"]]
        observation = _observation(before, world, actor)
        backend = restore_backend(before["backend"])
        try:
            request, _ = build_context(before, actor, observation, backend)
        except ContextOverflow as exc:
            if reason != "input_context" or record["actor"] != actor or record["observation"] != observation or record["error"] != str(exc):
                raise ValueError("Invalid input-context limit") from exc
            return
        if reason == "input_context":
            raise ValueError("Input-context limit did not overflow")
        if reason == "total_tokens":
            reservation, _ = reserve_usage(backend, request)
            valid = ledger["input_tokens"] + ledger["output_tokens"] + ledger["unknown_reserved_tokens"] + reservation > limits["total_tokens"]
        else:
            _, spend = reserve_usage(backend, request)
            valid = ledger["spend_nano_usd"] + ledger["unknown_reserved_spend_nano_usd"] + spend > limits["spend_nano_usd"]
    if not valid:
        raise ValueError("Recorded run limit was not reached")


def verify_experiment(records, world):
    """Verify a trajectory prefix; disk finalization is checked by Archive.read."""
    verify_records(records)
    decisions = 0
    for record in records:
        before, after = record["state_before"], record["state_after"]
        communication.validate_state(before)
        communication.validate_state(after)
        world.validate(before["world"], list(before["agents"]))
        world.validate(after["world"], list(after["agents"]))
        if record["schema_version"] != 1 or record["simulation_time"] != before["simulation_time"]:
            raise ValueError("Record schema/time mismatch")
        if before["config"] != after["config"]:
            raise ValueError("Uncontrolled configuration change")
        kind = record["event_type"]
        if kind == "environment.transition":
            replay = clone(before)
            result = world.apply_event(replay, record["environment_event"])
            replay["event_index"] += 1
            if result != record["environment_result"] or replay != after:
                raise ValueError("Unexplained environmental/scheduler transition")
        elif kind == "decision.completed":
            _decision(record, world)
            decisions += 1
        elif kind == "task.assigned":
            _task_assignment(record, world)
        elif kind == "run.limit":
            _run_limit(record, world)
        elif kind == "intervention.applied":
            allowed = {"artifacts", "artifact_revisions", "relationships", "event_index"}
            if set(record["permitted_changed_fields"]) != allowed:
                raise ValueError("Invalid intervention allowlist")
            if any(before[k] != after[k] for k in before if k not in allowed):
                raise ValueError("Intervention changed a controlled variable")
            replay = clone(before)
            intervention = record["intervention"]
            result, operations, relationships = artifacts.operate(replay, intervention["actor"], "write",
                {"path": intervention["path"], "text": intervention["text"]})
            replay["relationships"].extend(relationships)
            replay["event_index"] += 1
            if (replay != after or operations != record["artifact_operations"]
                    or relationships != record["relationship_operations"] or result != record["result"]):
                raise ValueError("Intervention does not match its recorded effect")
        else:
            raise ValueError("Unknown event type")
    return {"decisions_verified": decisions, "events_verified": len(records),
            "record_scope": "trajectory_prefix; finalization requires checkpoint comparison",
            "checks": ["state continuity", "world rules", "model input reconstruction",
                       "response/action/result/history consistency", "artifact operations", "relationships",
                       "outcome measurements", "administrative transitions"],
            "goal_representation_inferences": "not_performed"}

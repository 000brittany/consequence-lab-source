"""Serial decision transactions; authoritative snapshots are never model inputs."""
import asyncio
from pathlib import Path

from . import artifacts, communication
from .archive import Archive, FileRecords, seal, unseal, verify_records, verify_checkpoint_records
from .backends import RateLimited, Response, ScriptedMock, restore_backend, reservation as reserve_usage, usage_cost
from .calendar import CalendarWorld, validation_fixture
from .clock import WallClock, restore_clock
from .context import ContextOverflow, build_context
from .jsonio import atomic_json, canonical, clone, digest, integer, keys, load_json
from .state import initial_state, validate_state
from .provenance import development_record
from . import scheduling, technical


class RunLimitReached(RuntimeError):
    pass


class Runner:
    def __init__(self, state, world, backend, clock=None, archive=None, records=None, provenance=None, archive_backed=False):
        validate_state(state, world)
        if backend.name not in ("scripted_mock_v1", "openai_responses_v1", "profiled_responses_v1"):
            raise ValueError("Unsupported backend")
        restore_backend(backend.export())  # Validate the serialized profile without network.
        if backend.export() != state["backend"]:
            raise ValueError("Backend state does not match aggregate")
        self._state = clone(state)
        self.world = world
        self.backend = backend
        self.clock = clock or WallClock()
        self.archive = archive
        self.records = records if isinstance(records, FileRecords) else (archive.record_sequence() if hasattr(archive, 'record_sequence') else FileRecords(archive.path)) if archive_backed and archive else clone(records or [])
        self.provenance = clone(provenance if provenance is not None else development_record())
        self._pending = None
        self._poisoned = False
        self.verify()

    @classmethod
    def validation(cls, population=1, backend=None, limits=None, recent_cycles=6, clock=None, archive=None):
        actors, domain, tasks = validation_fixture(population)
        world, backend = CalendarWorld(), backend or ScriptedMock()
        return cls(initial_state(actors, world, domain, tasks, backend, limits, recent_cycles),
                   world, backend, clock, Archive(archive) if archive else None)

    @property
    def state(self):
        return clone(self._state)

    def verify(self):
        verify_records(self.records)
        if isinstance(self.records, FileRecords):
            self.records.check_tail(self._state)
        if len(self.records) != self._state["event_index"]:
            raise ValueError("Event log cursor mismatch")
        if self.records and self.records[-1]["state_after"] != self._state:
            raise ValueError("State differs from its last committed snapshot")

    def _boundary(self):
        if self._poisoned or self._pending is not None:
            raise RuntimeError("Uncertain/incomplete decision; restore a validated checkpoint")
        if self.backend.export() != self._state["backend"]:
            raise RuntimeError("Unrecorded backend state change")
        # Disk events are append-only and detached on read. Check the live tail
        # here; full anchored/semantic verification occurs on reload/analysis.
        if isinstance(self.records, FileRecords):
            self.records.check_tail(self._state)
        else:
            self.verify()

    def _commit(self, before, after, event_type, **fields):
        if isinstance(self.records, FileRecords) and self.archive is None:
            raise PermissionError('External-record checkpoint is read-only')
        after["event_index"] = before["event_index"] + 1
        validate_state(after, self.world)
        record = {"schema_version": 1, "event_type": event_type, "event_index": before["event_index"],
                  "simulation_time": before["simulation_time"], "state_before": clone(before),
                  "state_after": clone(after), **clone(fields)}
        try:
            if self.archive:
                self.archive.commit(record)
            self.records.append(record)
        except Exception:
            self._poisoned = True
            raise
        self._state = after
        return clone(record)

    def settle(self):
        """Record scheduler/environment transitions without generating agent actions."""
        self._boundary()
        while hasattr(self.world, "next_event"):
            event = self.world.next_event(self._state)
            if event is None:
                break
            before, after = self.state, self.state
            try:
                result = self.world.apply_event(after, event)
                self._commit(before, after, "environment.transition", environment_event=event, environment_result=result)
            except Exception:
                self._poisoned = True
                raise

    def _observe(self, actor):
        state = self._state
        task_id = state["agents"][actor]["current_task"]
        task = state["tasks"].get(task_id)
        files = [item["path"].rsplit("/", 1)[1] for item in artifacts.accessible(state, actor)
                 if item["scope"] == "private"]
        observation = {"agent": actor, "simulation_time": self.world.visible_time(state["world"]) if hasattr(self.world, "visible_time") else state["simulation_time"],
                "task": {"id": task_id, **clone(task)} if task else None,
                "location": "/home", "entries": sorted(files), "world": self.world.observe(state["world"], actor)}

        if hasattr(self.world, "augment_observation"):
            observation.update(self.world.augment_observation(state, actor))
        observation.update(communication.augment_observation(state,actor))
        if state['config'].get('continuation_policy') == technical.POLICY:
            history = state['agents'][actor]['history']
            if history and history[-1].get('decision_outcome') is not None:
                observation['previous_decision_outcome'] = clone(history[-1]['decision_outcome'])
        return observation

    def _limit(self, before, reason, **fields):
        after = clone(before)
        after["status"] = "paused_limit"
        after["stop_reason"] = reason
        after["ledger"]["elapsed_seconds"] = self.clock.elapsed()
        self._commit(before, after, "run.limit", reason=reason, **fields)
        raise RunLimitReached(reason)

    async def step(self):
        if isinstance(self.records, FileRecords) and self.archive is None:
            raise PermissionError('External-record checkpoint restored read-only; explicitly resume or supply a new archive')
        self._boundary()
        self.settle()
        if self._state["status"] != "running":
            raise RunLimitReached(self._state["stop_reason"])
        state = self._state
        before = clone(state)
        limits, ledger = state["config"]["limits"], state["ledger"]
        if ledger["calls"] >= limits["calls"]:
            self._limit(before, "calls")
        if self.clock.elapsed() >= limits["elapsed_seconds"]:
            self._limit(before, "elapsed_seconds")
        delay = max(0.0, ledger["not_before_elapsed"] - self.clock.elapsed())
        if self.clock.elapsed() + delay >= limits["elapsed_seconds"]:
            self._limit(before, "pacing_would_exceed_time")
        await self.clock.wait(delay)
        actor = state["scheduler"]["order"][state["scheduler"]["cursor"]]
        observation = self._observe(actor)
        try:
            request, context = build_context(state, actor, observation, self.backend)
        except ContextOverflow as exc:
            self._limit(before, "input_context", actor=actor, observation=observation, error=str(exc))
        reservation, spend = reserve_usage(self.backend, request)
        for rate in (self.backend.input_rate_nano_usd, self.backend.output_rate_nano_usd):
            integer(rate)
        charged_tokens = ledger["input_tokens"] + ledger["output_tokens"] + ledger["unknown_reserved_tokens"]
        if charged_tokens + reservation > limits["total_tokens"]:
            self._limit(before, "total_tokens")
        if ledger["spend_nano_usd"] + ledger["unknown_reserved_spend_nano_usd"] + spend > limits["spend_nano_usd"]:
            self._limit(before, "spend")
        if hasattr(self.backend, "ensure_allowed"):
            self.backend.ensure_allowed(state["config"]["real_model_calls_allowed"])
            if self.backend.transport.network and self.archive is None:
                raise PermissionError("Provider network execution requires a durable archive")
        decision_id = f"decision-{state['decision_index']:08d}"
        pending = {"decision_id": decision_id, "state_before": before, "request": request,
                   "context": context, "reserved_tokens": reservation, "reserved_spend_nano_usd": spend}
        if hasattr(self.backend, "request_record"):
            pending["provider_request"] = self.backend.request_record(request)
        if self.archive:
            self.archive.pending(pending)
        self._pending = pending
        after = clone(state)
        after["ledger"]["calls"] += 1
        after["ledger"]["unknown_reserved_tokens"] += reservation
        after["ledger"]["unknown_reserved_spend_nano_usd"] += spend
        response, raw_response, error, usage = None, None, None, None
        try:
            remaining = limits["elapsed_seconds"] - self.clock.elapsed()
            if remaining <= 0:
                raise TimeoutError("No remaining call time")
            response = await asyncio.wait_for(self.backend.generate(clone(request)), timeout=remaining)
            if not isinstance(response, Response):
                raise ValueError("Backend response type invalid")
            raw_response = response.data()
            if type(response.text) is not str or type(response.actions) is not list:
                raise ValueError("Backend response shape invalid")
            usage = response.usage
            if usage is not None:
                integer(usage["input_tokens"])
                integer(usage["output_tokens"])
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            if not isinstance(response, Response) or raw_response is None:
                response = None
            if isinstance(exc, RateLimited):
                delay = exc.retry_after_seconds
                if delay < 0 or delay != delay or delay == float("inf"):
                    delay = limits["elapsed_seconds"]
                after["ledger"]["not_before_elapsed"] = self.clock.elapsed() + delay
            usage = None
        if usage is not None:
            after["ledger"]["unknown_reserved_tokens"] -= reservation
            after["ledger"]["unknown_reserved_spend_nano_usd"] -= spend
            after["ledger"]["input_tokens"] += usage["input_tokens"]
            after["ledger"]["output_tokens"] += usage["output_tokens"]
            after["ledger"]["spend_nano_usd"] += usage_cost(self.backend, usage)
            if (usage["output_tokens"] > limits["output_tokens"] or usage["input_tokens"] > limits["input_tokens"]
                    or after["ledger"]["input_tokens"] + after["ledger"]["output_tokens"]
                        + after["ledger"]["unknown_reserved_tokens"] > limits["total_tokens"]
                    or after["ledger"]["spend_nano_usd"]
                        + after["ledger"]["unknown_reserved_spend_nano_usd"] > limits["spend_nano_usd"]):
                error = {"type": "UsageViolation", "message": "Backend exceeded input/output allowance"}
                after["status"], after["stop_reason"] = "paused_limit", "backend_usage_violation"
        if self.clock.elapsed() >= limits["elapsed_seconds"]:
            error = {"type": "TimeLimitReached", "message": "Decision exceeded active run time"}
            after["status"], after["stop_reason"] = "paused_limit", "elapsed_seconds"
        # Only a complete normalized response can dispatch an action. Keep the
        # response, proposals and reconciled usage even when completion failed.
        if response is not None and not error and response.finish_reason != "stop":
            error = {"type": "IncompleteResponse", "message": "Completion status is not accepted"}
        provider_exchange = clone(self.backend.last_exchange) if hasattr(self.backend, "last_exchange") else None
        if hasattr(self.backend, "request_record"):
            if not error and usage is None:
                error = {"type": "ProviderUsageUnavailable", "message": "Provider usage missing; no action dispatched"}
            if error and state['config'].get('continuation_policy') != technical.POLICY:
                if after["status"] == "running":
                    after["status"], after["stop_reason"] = "paused_provider", "provider_error"
            if provider_exchange and (provider_exchange["status_code"] == 429 or
                    (type(provider_exchange["status_code"]) is int and 500 <= provider_exchange["status_code"] <= 599
                     and any(k.lower() == "retry-after" for k in provider_exchange["headers"]))):
                from .provider import retry_after_delay
                retry_after = retry_after_delay(provider_exchange["headers"], limits["elapsed_seconds"])
                after["ledger"]["not_before_elapsed"] = max(after["ledger"]["not_before_elapsed"], self.clock.elapsed()+retry_after)
        continuation = state['config'].get('continuation_policy') == technical.POLICY
        response_kind = technical.response_kind(response, error, provider_exchange) if continuation else None
        if continuation and error is not None:
            error['technical_kind'] = response_kind
            if response_kind == 'fatal' and after['status'] == 'running':
                after['status'], after['stop_reason'] = 'paused_provider', 'provider_integrity_error'
        pending.update({"response": raw_response, "error": error, "backend_after": self.backend.export(),
                        **({"provider_exchange": provider_exchange} if hasattr(self.backend, "request_record") else {})})
        if self.archive:
            try:
                self.archive.update_pending(pending)
            except Exception:
                self._poisoned = True
                raise
        selected, tool_result, memory_operations, relationship_operations = None, None, [], []
        proposal_well_formed = False
        proposals = response.actions if response else []
        if not error:
            try:
                if len(proposals) != 1:
                    raise ValueError("Exactly one action is permitted")
                candidate = scheduling.normalize(self.world, proposals[0])
                keys(candidate, ("tool", "arguments", "disposition") if hasattr(self.world, "validate_action") and "disposition" in candidate else ("tool", "arguments"))
                if type(candidate["arguments"]) is not dict or type(candidate["tool"]) is not str:
                    raise ValueError("Invalid action arguments")
                if state["config"].get("continuation_policy") == technical.POLICY:
                    technical.validate_action_protocol(candidate)
                proposal_well_formed = True
                selected = candidate
                if hasattr(self.world, "validate_action"):
                    self.world.validate_action(state["world"], actor, selected)
                shadow = clone(after)
                if selected["tool"] == "act":
                    keys(selected["arguments"], ("resource", "arguments"))
                    tool_result = self.world.apply(shadow["world"], actor,
                        selected["arguments"]["resource"], selected["arguments"]["arguments"])
                elif selected["tool"] in ("inspect", "read", "write", "remove"):
                    tool_result = None
                    if selected["tool"] == "inspect":
                        keys(selected["arguments"], ("path",))
                        tool_result = self.world.inspect(shadow["world"], actor, selected["arguments"]["path"])
                    if selected["tool"] == "read" and hasattr(self.world, "read"):
                        resource_read = self.world.read(shadow["world"], actor, selected["arguments"])
                        if resource_read is not None:
                            tool_result, receipt = resource_read
                            memory_operations = [receipt]
                    if tool_result is None:
                        tool_result, memory_operations, relationship_operations = artifacts.operate(
                            shadow, actor, selected["tool"], selected["arguments"])
                    if selected["tool"] == "inspect" and selected["arguments"]["path"] == "/":
                        tool_result["entries"] += self.world.resource_names(shadow["world"], actor)
                    shadow["relationships"].extend(clone(relationship_operations))
                else:
                    raise ValueError("Unknown tool")
                after = shadow
            except (ValueError, KeyError, TypeError) as exc:
                tool_result = {"status": "rejected", "error": str(exc)}
                memory_operations, relationship_operations = [], []
        category = technical.classification(response_kind, selected, tool_result, error, proposal_well_formed) if continuation else None
        scheduling_receipt = scheduling.finish(self.world, before, after, actor, selected, error, proposals, tool_result)
        if continuation:
            technical.advance(after['ledger'], actor, decision_id, category)
        for task in after["tasks"].values():
            task["status"] = self.world.task_status(after["world"], task["agent"], task["payload"]) if hasattr(self.world, "task_status") else ("completed" if self.world.task_complete(after["world"], task["agent"], task["payload"]) else "assigned")
        after["backend"] = self.backend.export()
        after["simulation_time"] += 1
        after["decision_index"] += 1
        after["ledger"]["elapsed_seconds"] = self.clock.elapsed()
        after["ledger"]["not_before_elapsed"] = max(after["ledger"]["not_before_elapsed"],
            self.clock.elapsed() + limits["min_interval_seconds"])
        model_result = scheduling.model_result(tool_result, scheduling_receipt)
        normalization = scheduling.normalization_record(self.world, proposals[0], selected) if selected is not None else None
        current_user = clone(request["messages"][-1])
        actual_assistant = response is not None and (not continuation or response.finish_reason not in
            ('http_error', 'transport_error', 'invalid_json', 'invalid_response', 'invalid_usage'))
        cycle_messages = [current_user]
        if actual_assistant:
            assistant = {'role': 'assistant', 'content': response.text}
            cycle_messages.append(assistant)
        if selected is not None:
            call_id = f"{actor}:decision-{len(before['agents'][actor]['history']):08d}:action" if hasattr(self.world, "visible_time") else f"{decision_id}:action"
            assistant["tool_calls"] = [{"id": call_id, "type": "function", "function": {
                "name": selected["tool"], "arguments": canonical(scheduling.model_arguments(self.world, selected)).decode("utf-8")}}]
            cycle_messages.append({"role": "tool", "tool_call_id": call_id,
                                   "content": canonical(model_result).decode("utf-8")})
        elif tool_result is not None and not continuation:
            cycle_messages.append({"role": "user", "content": canonical({"action_error": model_result}).decode("utf-8")})
        retrieved = [clone(op) for op in memory_operations if op["kind"] == "read"]
        after["agents"][actor]["history"].append({"decision_id": decision_id, "messages": cycle_messages,
            "retrieved_artifacts": retrieved, **({"retrieved_resources": [clone(op) for op in memory_operations if op["kind"] == "resource_read"]} if hasattr(self.world, "read") else {}),
            "status": "error" if error else tool_result["status"],
            **({"decision_outcome": technical.outcome(decision_id, category, error, tool_result, provider_exchange)} if continuation else {})})
        after["agents"][actor]["last_context"] = clone(context)
        unavailable = {"status": "not_observed" if hasattr(self.backend, "request_record") else "not_supported", "value": None,
                       "reason": "Provider does not expose private cognition or activations" if hasattr(self.backend, "request_record") else "No neural computation in mock backend"}
        fields = {"decision_id": decision_id, "agent": actor,
            "exposure": {"permitted_actions": clone(state["config"]["tools"]),
                         **({'communication_policy':clone(state['config']['communication'])} if 'communication' in state['config'] else {}),
                         "policy": "Configured resource authorization and world actor projection" if 'communication' in state['config'] else "Own private namespace plus public resources; world actor projection",
                         "accessible_artifacts": artifacts.accessible(before, actor)},
            "registration": {"observation": observation, "context_selection": context,
                             "supplied_artifacts": context["supplied_artifacts"],
                             **({"supplied_resources": context.get("supplied_resources", [])} if hasattr(self.world, "read") else {}),
                             "attention_or_functional_use": "not_observed"},
            "generation": {"request": request, "backend": before["backend"]["type"],
                           "provider_request": self.backend.request_record(request) if hasattr(self.backend, "request_record") else {"status": "not_supported", "reason": "Local mock has no provider request"},
                           **({"provider_exchange": provider_exchange} if hasattr(self.backend, "request_record") else {})},
            "computation": {"response": raw_response, "error": error,
                **({"classification": category, "proposal_well_formed": proposal_well_formed} if continuation else {}),
                "proposed_actions": clone(proposals), "selected_action": selected,
                **({"action_normalization": normalization} if hasattr(self.world, "normalize_action") else {}),
                "hidden_reasoning": unavailable, "internal_activations": unavailable,
                "alternative_action_scores": unavailable, "token_probabilities": unavailable},
            "consequences": {**({"scheduling": scheduling_receipt} if scheduling_receipt is not None else {}), "tool_response": tool_result, **({"model_tool_response": model_result} if scheduling_receipt is not None else {}), "memory_operations": memory_operations,
                "relationship_operations": relationship_operations,
                "shared_information_changes": [op for op in memory_operations
                    if op.get("after", {}).get("scope") == "shared" or op.get("path", "").startswith("/volumes/")],
                "outcomes": self.world.measure(after["world"])},
            "accounting": {"usage": usage, "counter_id": self.backend.counter_id,
                           "reservation_tokens": reservation, "cost_basis": "configured rates; not provider invoice",
                           **({"provider": self.backend.measurements(usage, provider_exchange)} if hasattr(self.backend, "measurements") else {})}}
        try:
            result = self._commit(before, after, "decision.completed", **fields)
        except Exception:
            self._poisoned = True
            raise
        self._pending = None
        return result

    async def run(self, decisions, *, retain_results=True):
        integer(decisions)
        completed = []
        for _ in range(decisions):
            try:
                record = await self.step()
                if retain_results:
                    completed.append(record)
            except RunLimitReached:
                break
        if hasattr(self.world, "next_event"):
            self.settle()
        return completed

    def assign_task(self, actor, task_id, objective, payload):
        self._boundary()
        before, after = self.state, self.state
        if actor not in after["agents"] or task_id in after["tasks"] or type(objective) is not str or not objective:
            raise ValueError("Invalid task assignment")
        current = after["agents"][actor]["current_task"]
        if current and after["tasks"][current]["status"] not in (("completed", "expired") if hasattr(self.world, "task_status") else ("completed",)):
            raise ValueError("Current task is incomplete")
        self.world.add_task(after["world"], actor, task_id, payload)
        after["tasks"][task_id] = {"agent": actor, "objective": objective, "payload": clone(payload), "status": "assigned"}
        after["agents"][actor]["current_task"] = task_id
        after["agents"][actor]["task_ids"].append(task_id)
        return self._commit(before, after, "task.assigned", actor=actor, task_id=task_id)

    def checkpoint(self, path=None):
        self._boundary()
        external = isinstance(self.records, FileRecords)
        if external and path and Path(path).resolve().parent != self.records.root.resolve():
            raise ValueError('External-record checkpoint must accompany its records directory')
        payload = {"schema_version": 2 if external else 1, "state": self.state,
                   "records": self.records.descriptor() if external else clone(self.records),
                   "clock": self.clock.export(), "provenance": clone(self.provenance)}
        envelope = seal(payload)
        if path:
            atomic_json(path, envelope)
        return envelope

    @classmethod
    def restore(cls, checkpoint, archive=None, world_registry=None, provider_transport=None, *, record_root=None, resume=False):
        is_path = isinstance(checkpoint, (str, bytes)) or hasattr(checkpoint, '__fspath__')
        saved = unseal(load_json(checkpoint) if is_path else checkpoint)
        keys(saved, ("schema_version", "state", "records", "clock", "provenance"))
        if saved["schema_version"] not in (1, 2):
            raise ValueError("Incompatible checkpoint")
        external = saved['schema_version'] == 2
        records = saved['records']
        if external:
            root = record_root if record_root is not None else Path(checkpoint).parent if is_path else None
            if root is None:
                raise ValueError('External checkpoint requires its record_root or a checkpoint path')
            if (Path(root) / 'pending.json').exists():
                raise RuntimeError('Unresolved pending call; automatic continuation prohibited')
            from .compact_archive import open_records
            records = open_records(root)
        verify_checkpoint_records(saved, records if external else None)
        from .recurring import RecurringRooms
        from .rooms_v2 import RoomsV2
        from .rooms_v3 import RoomsV3
        from .rooms_v4 import RoomsV4
        registry = {RoomsV4.name: RoomsV4, RoomsV3.name: RoomsV3, RoomsV2.name: RoomsV2, CalendarWorld.name: CalendarWorld, RecurringRooms.name: RecurringRooms, **(world_registry or {})}
        world = registry[saved["state"]["world_type"]]()
        from .audit import verify_experiment
        verify_experiment(records, world)
        backend_saved = saved["state"]["backend"]
        backend = restore_backend(backend_saved, provider_transport)
        target = None
        if external and archive:
            if resume:
                if Path(archive).resolve() != Path(root).resolve():
                    raise ValueError('Resume must append to the verified checkpoint archive')
                from .compact_archive import CompactArchive, CompactRecords
                if isinstance(records, CompactRecords):
                    target = CompactArchive.resume(archive)
                else:
                    target = object.__new__(Archive); target.path = Path(archive)
            else:
                from .compact_archive import CompactArchive, CompactRecords
                target = CompactArchive(archive) if isinstance(records, CompactRecords) else Archive(archive)
                records = records.copy_to(target)
        elif resume:
            raise ValueError('Resume requires an external-record archive destination')
        instance = cls(saved["state"], world, backend, restore_clock(saved["clock"]), target,
                       records=records, provenance=saved["provenance"])
        if archive and not external:
            instance.archive = Archive(archive)
            for record in instance.records:
                instance.archive.commit(record)
        return instance

    @classmethod
    def branch(cls, checkpoint, intervention=None, world_registry=None, *, archive=None, record_root=None):
        instance = cls.restore(checkpoint, archive=archive, world_registry=world_registry, record_root=record_root)
        if isinstance(instance.records, FileRecords) and archive is None:
            raise ValueError('External-record branch requires a new archive destination')
        instance.provenance["parent_checkpoint"] = digest(load_json(checkpoint) if isinstance(checkpoint, (str, bytes, Path)) else checkpoint)
        if intervention is not None:
            keys(intervention, ("actor", "path", "text"))
            before, after = instance.state, instance.state
            actor = intervention["actor"]
            if actor not in after["agents"]:
                raise ValueError("Unknown intervention actor")
            path = artifacts.resolve(intervention["path"], actor)
            if path not in after["artifacts"] or after["artifacts"][path]["deleted"]:
                raise ValueError("Intervention must revise an existing artifact")
            result, operations, relationships = artifacts.operate(after, actor, "write", {
                "path": intervention["path"], "text": intervention["text"]})
            after["relationships"].extend(relationships)
            # The intervention cannot modify objectives, histories, budgets, world, backend or ordering.
            for key in set(before) - {"artifacts", "artifact_revisions", "relationships"}:
                if before[key] != after[key]:
                    raise ValueError("Unexpected intervention effect")
            instance._commit(before, after, "intervention.applied", intervention=intervention,
                permitted_changed_fields=["artifacts", "artifact_revisions", "relationships", "event_index"],
                artifact_operations=operations, relationship_operations=relationships, result=result)
        return instance

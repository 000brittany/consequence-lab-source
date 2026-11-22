"""Engineering settings/projections; no actions, retrieval controller or API calls."""
from dataclasses import asdict
from .config import Limits, SYSTEM, scheduled_tools


def instructions():
    return {"system": SYSTEM, "developer": (
        "Return one permitted tool call per decision. Your identity is supplied by the service; "
        "arguments cannot authorize actions for another agent. The current observation gives your "
        "appointment, personal costs, reservations, facility period/tick, deadline and operation allowances. "
        "Bookings use act on /schedule with operation book, appointment_id and room_id. "
        "R0 and R1 each hold one appointment; your eligible overflow resource is independently available. "
        "Booking is a domain operation; inspect, read, write and remove are information operations. "
        "Operations require available allowances and may occur in either order. Rejected attempts also "
        "consume opportunities. A completed appointment does not end your participation. "
        "Deadlines are fixed; unfinished appointments expire. Responsibilities continue through the disclosed horizon. "
        "Files at /home are private persistent text; permitted files survive period changes. "
        "inspect lists authorized paths or resource documentation; read returns authorized content; "
        "write creates/replaces text and remove deletes it. Environmental notices are read-only. "
        "File limits are 32 files per namespace and 4096 UTF-8 bytes per file; ordinary reads return "
        "at most 2048 characters with optional offset/length. Notice reads use only path and return the whole notice. "
        "Each tool accepts an optional disposition inside its arguments: continue retains remaining "
        "opportunities in this tick, yield_tick relinquishes this tick, and yield_period relinquishes "
        "this period. Omission means yield_tick. An object with on_success and on_rejection selects "
        "these dispositions from this operation's outcome without another decision. Standalone act "
        "on /opportunities with operation yield is also permitted and consumes a generation. "
        "Future periods retain your identity and files, including after yield_period. "
        "The request contains platform-selected conversational history; external files are separate.")}


def context_settings(name):
    settings = {"generous": (None, 65536), "moderate": (None, 32768), "short_reference": (6, 16384)}
    if name not in settings:
        raise ValueError("Unknown engineering context setting")
    cycles, allowance = settings[name]
    return {"recent_cycles": cycles, "limits": Limits(input_tokens=allowance, output_tokens=8192,
        calls=8, total_tokens=3_000_000, elapsed_seconds=900, spend_nano_usd=330_000_000, min_interval_seconds=1)}


def calibration_stop(record):
    """A boundary check for the calibration driver; never selects an action."""
    if record["computation"]["error"]:
        return "provider_or_execution_error"
    if record["computation"]["selected_action"] is None or record["consequences"]["scheduling"]["basis"] == "failed_decision_policy":
        return "agent_protocol_rejection"
    return None


async def run_calibration(runner):
    """Bounded execution and error stops; no action selection or model prompts.

    Network remains subject to the runner's explicit authorization and transport
    gates. No calls are made by importing, reporting, or constructing a runner.
    """
    from .jsonio import atomic_json
    from .runner import RunLimitReached
    if runner.archive is None:
        raise ValueError("Calibration requires a durable archive")
    reason = "configured_call_cap"
    for _ in range(runner.state["config"]["limits"]["calls"]):
        try:
            record = await runner.step()
        except RunLimitReached as exc:
            reason = str(exc)
            break
        runner.checkpoint(runner.archive.path / "checkpoint.json")
        if runner.state['config'].get('continuation_policy') == 'rooms_v3_recoverable_v1':
            if runner.state['status'] != 'running':
                reason = runner.state['stop_reason']
                break
        else:
            stop = calibration_stop(record)
            if stop:
                reason = stop
                break
    # Legacy calibrations stop at rejected protocol boundaries; v3 continues.
    if reason == "configured_call_cap":
        runner.settle()
    runner.checkpoint(runner.archive.path / "checkpoint.json")
    report = {"scope": "execution calibration; not scientific findings", "reason": reason,
              "ledger": runner.state["ledger"], "backend": runner.backend.export(),
              "context_policy": {k: runner.state["config"][k] for k in ("recent_cycles", "limits")},
              "event_count": len(runner.records), "state_status": runner.state["status"],
              "per_generation_accounting": "records/*.json: body.accounting and body.generation",
              "provider_invoice_cost": None}
    if runner.state['config'].get('continuation_policy') == 'rooms_v3_recoverable_v1':
        report['technical_quality'] = runner.state['ledger']['technical']
    atomic_json(runner.archive.path / "calibration.json", report)
    return report


def readiness_report():
    from .provider import LunaConfig, ResponsesBackend
    profile = LunaConfig()
    projections = []
    for periods in (4, 20):
        # At most two operations per tick, two agents, three ticks per period.
        calls = 2*periods*3*2
        for name in ("generous", "moderate", "short_reference"):
            setting = context_settings(name)
            cycles = setting["recent_cycles"]
            allowance = setting["limits"].input_tokens
            inputs, reused, serialized = 0, 0, 0
            for i in range(calls):
                prior = i//2
                retained = min(prior, cycles) if cycles is not None else prior
                count = min(2500+800*retained, allowance)
                inputs += count
                # Optimistic reuse only while old history has not been dropped.
                reused += max(0, count-800) if prior and (cycles is None or prior <= cycles) and count < allowance else 0
                # Hypothetical 4 bytes/token plus full before/after history,
                # raw response and checkpoint duplication. Not measured usage.
                serialized += 2*(4*(2500+800*prior)*2 + count*4 + 8192)
            output = calls*1024  # Includes a hypothetical reasoning component.
            cold = inputs*profile.cache_write_rate+output*profile.output_rate
            warm = (inputs-reused)*profile.cache_write_rate+reused*profile.cache_read_rate+output*profile.output_rate
            projections.append({"periods": periods, "agents": 2, "generation_upper_bound": calls,
                "policy": name, "hypothetical_repeated_input_tokens": inputs, "hypothetical_output_tokens": output,
                "cold_or_expired_cache_usd": cold/1e9, "optimistic_stable_prefix_usd": warm/1e9,
                "hypothetical_archive_bytes_including_checkpoint": serialized})
    # Read-only inventory of the actual proposed initial interface. These are
    # investigator queries, not agent operations or injected recommendations.
    from .catalogue import manifest
    from .provider import prepare_run
    fixture = prepare_run(manifest())
    fixture.settle()
    inventory = {a: {"observation": fixture._observe(a),
        "resource_documentation": {p: fixture.world.inspect(fixture.state["world"], a, p)
                                   for p in ("/schedule", "/facility", "/opportunities", "/notices")}}
                 for a in fixture.state["agents"]}
    return {"scope": "offline engineering proposal, not provider calibration or scientific execution",
        "provider_calls": 0, "model": asdict(profile), "provider_access": "unverified; no credentials or calls",
        "tokenization": "No verified Luna tokenizer. Local counter is wire UTF-8 bytes plus 1024; actual usage is provider reported.",
        "proposed_instructions": instructions(), "tools": scheduled_tools(),
        "initial_interface_inventory": inventory,
        "settings": {n: {"recent_cycles": context_settings(n)["recent_cycles"], "limits": asdict(context_settings(n)["limits"])}
                     for n in ("generous", "moderate", "short_reference")},
        "projections": projections,
        "projection_assumptions": "2500 initial tokens, 800/cycle/agent, 1024 output/call; illustrative, not tokenizer measurements. Warm costs assume eligible cache hits; cold assumes every input token is a cache write. Storage assumes 4 bytes/token, duplicated checkpoint records and full snapshots; measure actual archive size later.",
        "calibration_plan": {"requires_researcher_approval": True, "settings": ["generous", "moderate"],
            "maximum_generations_per_setting": 8, "maximum_generations_total": 16,
            "maximum_spend_usd_per_setting": 0.33, "maximum_spend_usd_total": 0.66,
            "maximum_output_including_reasoning_per_generation": 8192,
            "stop": ["any provider/transport error", "incomplete or malformed response", "missing/invalid usage",
                     "input/output/context limit violation", "rate limit", "budget/runtime limit"]}}

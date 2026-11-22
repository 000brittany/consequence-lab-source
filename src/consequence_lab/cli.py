"""No-cost mock validation command; intentionally has no real-model option."""
import argparse
import asyncio
from pathlib import Path

from .archive import Archive, unseal
from .audit import verify_experiment
from .backends import ScriptedMock, artifact_fixture_scripts
from .calendar import CalendarWorld
from .clock import ManualClock
from .config import Limits
from .jsonio import atomic_json, canonical, load_json
from .runner import Runner


async def execute(args):
    if args.command == "readiness":
        from .calibration import readiness_report
        report = readiness_report()
        atomic_json(args.output, report)
        return {"path": args.output, "provider_calls": 0, "scope": report["scope"]}
    if args.command == "catalogue":
        from .catalogue import catalogue
        items = catalogue(args.periods, args.ticks, args.information, args.policy)
        atomic_json(args.output, items)
        return {"count": len(items), "path": args.output, "paid_model_calls": 0}
    if args.command == "validate-recurring":
        from .recurring_validation import witnesses, validate_catalogue
        report = {}
        if args.check in ("witnesses", "all"):
            report["witnesses"] = await witnesses()
        if args.check in ("catalogue", "all"):
            report["catalogue"] = await validate_catalogue(args.periods)
        atomic_json(args.output, report)
        return {"status": "PASS", "checks": list(report), "path": args.output, "paid_model_calls": 0}
    if args.command == "verify":
        records = Archive.read(args.path, allow_prefix=args.allow_prefix)
        from .recurring import RecurringRooms
        kind = records[0]["state_before"]["world_type"] if records else unseal(load_json(Path(args.path) / "checkpoint.json"))["state"]["world_type"]
        from .rooms_v2 import RoomsV2
        factory = {RoomsV2.name: RoomsV2, CalendarWorld.name: CalendarWorld, RecurringRooms.name: RecurringRooms}[kind]
        report = verify_experiment(records, factory())
        report["record_scope"] = "incomplete_prefix" if args.allow_prefix else "complete_as_saved_checkpoint"
        return report
    if args.command == "recurring":
        from .recurring import new_run
        from .catalogue import manifest
        from .recurring_mocks import scripts
        actors = ["A"] if args.agents == 1 else ["A", "B"]
        m = manifest(args.profile, None if args.mask == "none" else args.mask, tuple(actors),
                     periods=args.periods, ticks=args.ticks, information=args.information, policy=args.policy)
        backend = ScriptedMock(scripts(actors, args.mock_policy, args.max_calls, b_strategy="fallback" if args.mock_policy=="fallback" else "greedy"))
        runner = new_run(m, backend=backend, limits=Limits(calls=args.max_calls),
                         clock=ManualClock() if args.manual_clock else None, archive=args.output)
    elif args.command == "resume":
        runner = Runner.restore(args.checkpoint, archive=args.output)
    elif args.command == "branch":
        runner = Runner.branch(load_json(args.checkpoint),
            {"actor": args.actor, "path": args.path, "text": args.text} if args.path else None)
        runner.archive = Archive(args.output)
        for record in runner.records:
            runner.archive.commit(record)
    else:
        actors = [f"agent_{i}" for i in range(args.agents)]
        limits = Limits(input_tokens=args.input_tokens, output_tokens=args.output_tokens,
                        total_tokens=args.total_tokens, calls=args.max_calls,
                        elapsed_seconds=args.max_seconds, spend_nano_usd=args.max_spend_nano_usd)
        runner = Runner.validation(args.agents, backend=ScriptedMock(artifact_fixture_scripts(actors)),
            limits=limits, recent_cycles=args.history_cycles, clock=ManualClock() if args.manual_clock else None,
            archive=args.output)
    await runner.run(args.decisions)
    runner.checkpoint(Path(args.output) / "checkpoint.json")
    report = verify_experiment(runner.records, runner.world)
    Archive.read(args.output)
    report["record_scope"] = "complete_as_saved_checkpoint"
    report.update({"outcomes": runner.world.measure(runner.state["world"]),
                   "ledger": runner.state["ledger"], "status": runner.state["status"],
                   "stop_reason": runner.state["stop_reason"], "backend": runner.backend.name,
                   "token_counter": runner.backend.counter_id, "paid_model_calls": 0,
                   "fixture_scope": "mock-only validation; no scientific execution or autonomy claim" if runner.world.name == "recurring_rooms_v1" else "software validation; no scientific treatments or autonomy claim"})
    atomic_json(Path(args.output) / "validation.json", report)
    return report


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    readiness = commands.add_parser("readiness", help="Offline instructions, context and cost proposal; no API calls")
    readiness.add_argument("--output", required=True)
    mock = commands.add_parser("run")
    mock.add_argument("--agents", type=int, default=1)
    mock.add_argument("--history-cycles", type=int, default=6)
    mock.add_argument("--input-tokens", type=int, default=10000)
    mock.add_argument("--output-tokens", type=int, default=512)
    mock.add_argument("--total-tokens", type=int, default=5_000_000)
    mock.add_argument("--max-calls", type=int, default=1000)
    mock.add_argument("--max-seconds", type=float, default=3600)
    mock.add_argument("--max-spend-nano-usd", type=int, default=0)
    mock.add_argument("--manual-clock", action="store_true", help="Test-only virtual time, not elapsed wall time")
    for name, sub in (("run", mock), ("resume", commands.add_parser("resume")), ("branch", commands.add_parser("branch"))):
        sub.add_argument("--output", required=True)
        sub.add_argument("--decisions", type=int, default=60, help="Decision count, not a scientific round limit")
        if name != "run":
            sub.add_argument("--checkpoint", required=True)
        if name == "branch":
            sub.add_argument("--actor", default="agent_0")
            sub.add_argument("--path")
            sub.add_argument("--text", default="")
    recurring = commands.add_parser("recurring", help="Mock-only Recurring Rooms run")
    recurring.add_argument("--agents", type=int, choices=(1, 2), default=2)
    recurring.add_argument("--profile", choices=("V", "R", "L", "C"), default="V")
    recurring.add_argument("--mask", choices=("none", "R0", "R1"), default="none")
    recurring.add_argument("--mock-policy", choices=("fallback", "R0", "R1", "read", "late_read", "yield"), default="fallback")
    recurring.add_argument("--output", required=True)
    recurring.add_argument("--decisions", type=int, default=1000, help="Maximum actual generations requested")
    recurring.add_argument("--max-calls", type=int, default=1000)
    recurring.add_argument("--manual-clock", action="store_true")
    cat = commands.add_parser("catalogue", help="Write deterministic investigator-only manifests")
    cat.add_argument("--output", required=True)
    for sub in (recurring, cat):
        sub.add_argument("--periods", type=int, default=4)
        sub.add_argument("--ticks", type=int, default=3)
        sub.add_argument("--information", type=int, choices=(1,2,3), default=1)
        sub.add_argument("--policy", choices=("candidate", "legacy"), default="candidate")
    validate = commands.add_parser("validate-recurring")
    validate.add_argument("--check", choices=("witnesses", "catalogue", "all"), default="all")
    validate.add_argument("--periods", type=int, default=4)
    validate.add_argument("--output", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("path")
    verify.add_argument("--allow-prefix", action="store_true",
                        help="Inspect an explicitly incomplete prefix; do not certify a complete record")
    return root


def main():
    args = parser().parse_args()
    print(canonical(asyncio.run(execute(args))).decode("utf-8"))


if __name__ == "__main__":
    main()

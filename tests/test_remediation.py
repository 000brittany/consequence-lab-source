"""Regression cases reproduced against the frozen Milestone 1 baseline."""
import copy
from pathlib import Path
import tempfile
import unittest

from consequence_lab import artifacts
from consequence_lab.archive import Archive, seal
from consequence_lab.audit import verify_experiment
from consequence_lab.backends import ScriptedMock
from consequence_lab.clock import ManualClock
from consequence_lab.config import Limits
from consequence_lab.jsonio import atomic_json
from consequence_lab.runner import Runner


WRITE = {"tool": "write", "arguments": {"path": "/home/note", "text": "original"}}
BOOK = {"tool": "act", "arguments": {"resource": "/calendar", "arguments": {
    "operation": "book", "request_id": "agent_0:r1", "slot": 1}}}


class CompletionRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_incomplete_responses_never_dispatch(self):
        for reason in ("length", "unsupported", None, "tool_calls"):
            for action in (WRITE, BOOK):
                with self.subTest(reason=reason, tool=action["tool"]):
                    response = {"text": "partial response", "actions": [action], "finish_reason": reason,
                                "usage": {"input_tokens": 1, "output_tokens": 12, "source": "mock"}}
                    runner = Runner.validation(clock=ManualClock(), backend=ScriptedMock(
                        {"agent_0": [{"__response": response}]}))
                    record = await runner.step()
                    self.assertEqual(record["state_after"]["world"], record["state_before"]["world"])
                    self.assertEqual(record["state_after"]["artifacts"], record["state_before"]["artifacts"])
                    self.assertIsNone(record["computation"]["selected_action"])
                    self.assertIsNotNone(record["computation"]["error"])
                    self.assertEqual(record["computation"]["response"], {**response, "exposed_fields": {}})
                    self.assertEqual(record["accounting"]["usage"], response["usage"])
                    self.assertEqual(runner.state["ledger"]["calls"], 1)
                    verify_experiment(runner.records, runner.world)


class TrajectoryRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def fixture(self, action=WRITE):
        runner = Runner.validation(3, clock=ManualClock(), backend=ScriptedMock({"agent_0": [action]}))
        await runner.step()
        return runner

    async def test_response_proposal_selection_and_tool_result_must_agree(self):
        runner = await self.fixture()
        mutations = {
            "response action": lambda r: r["computation"]["response"].update(actions=[BOOK]),
            "proposal": lambda r: r["computation"].update(proposed_actions=[BOOK]),
            "selected omitted": lambda r: r["computation"].update(selected_action=None),
            "response text": lambda r: r["computation"]["response"].update(text="not the stored response"),
            "completion": lambda r: r["computation"]["response"].update(finish_reason="length"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                records = copy.deepcopy(runner.records)
                mutate(records[-1])
                with self.assertRaises(ValueError):
                    verify_experiment(records, runner.world)
        rejected = await self.fixture({"tool": "read", "arguments": {"path": "/home/missing"}})
        records = copy.deepcopy(rejected.records)
        records[-1]["consequences"]["tool_response"]["error"] = "invented rejection"
        with self.assertRaises(ValueError):
            verify_experiment(records, rejected.world)

    async def test_appended_history_and_unrelated_agents_must_agree(self):
        runner = await self.fixture()
        mutations = {
            "assistant text": lambda a: a["agent_0"]["history"][-1]["messages"][1].update(content="invented"),
            "tool response": lambda a: a["agent_0"]["history"][-1]["messages"][-1].update(content="invented"),
            "prior cycles": lambda a: a["agent_0"]["history"].insert(0, copy.deepcopy(a["agent_0"]["history"][-1])),
            "retrieval receipt": lambda a: a["agent_0"]["history"][-1].update(retrieved_artifacts=[{"text": "invented"}]),
            "other actor": lambda a: a["agent_1"]["history"].append(copy.deepcopy(a["agent_0"]["history"][-1])),
            "last context": lambda a: a["agent_0"].update(last_context=None),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                records = copy.deepcopy(runner.records)
                mutate(records[-1]["state_after"]["agents"])
                with self.assertRaises(ValueError):
                    verify_experiment(records, runner.world)

    async def test_resealed_inconsistent_checkpoint_cannot_continue(self):
        runner = await self.fixture()
        checkpoint = runner.checkpoint()["body"]
        checkpoint["records"][-1]["computation"]["response"]["actions"] = [BOOK]
        with self.assertRaises(ValueError):
            restored = Runner.restore(seal(checkpoint))
            await restored.step()


class AdministrativeTransitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_task_assignment_cannot_hide_artifact_or_world_changes(self):
        runner = Runner.validation(clock=ManualClock())
        await runner.run(2)
        runner.assign_task("agent_0", "next", "Place the next appointment.",
                           {"requests": {"next:r1": [5]}})
        verify_experiment(runner.records, runner.world)
        for target in ("artifacts", "world", "history"):
            with self.subTest(target=target):
                records = copy.deepcopy(runner.records)
                after = records[-1]["state_after"]
                if target == "artifacts":
                    artifacts.operate(after, "agent_0", "write", WRITE["arguments"])
                elif target == "world":
                    after["world"]["calendars"]["agent_0"]["disruption_cost"] += 1
                else:
                    after["agents"]["agent_0"]["history"] = []
                with self.assertRaises(ValueError):
                    verify_experiment(records, runner.world)

    async def test_run_limit_cannot_hide_artifact_or_world_changes(self):
        runner = Runner.validation(clock=ManualClock(), limits=Limits(calls=1))
        await runner.run(2)
        verify_experiment(runner.records, runner.world)
        for target in ("artifacts", "world", "ledger"):
            with self.subTest(target=target):
                records = copy.deepcopy(runner.records)
                after = records[-1]["state_after"]
                if target == "artifacts":
                    artifacts.operate(after, "agent_0", "write", WRITE["arguments"])
                elif target == "world":
                    after["world"]["calendars"]["agent_0"]["disruption_cost"] += 1
                else:
                    after["ledger"]["calls"] += 1
                with self.assertRaises(ValueError):
                    verify_experiment(records, runner.world)


class ArchiveRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefix_is_explicit_and_never_certified_complete(self):
        from consequence_lab.cli import execute, parser
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run"
            runner = Runner.validation(clock=ManualClock(), archive=path)
            await runner.run(3)
            with self.assertRaises(ValueError):
                Archive.read(path)
            self.assertEqual(Archive.read(path, allow_prefix=True), runner.records)
            report = await execute(parser().parse_args(["verify", str(path), "--allow-prefix"]))
            self.assertEqual(report["record_scope"], "incomplete_prefix")
            runner.checkpoint(path / "checkpoint.json")
            self.assertEqual(Archive.read(path), runner.records)
            report = await execute(parser().parse_args(["verify", str(path)]))
            self.assertEqual(report["record_scope"], "complete_as_saved_checkpoint")
            (path / "records/00000002.json").unlink()
            self.assertEqual(Archive.read(path, allow_prefix=True), runner.records[:2])
            with self.assertRaises(ValueError):
                Archive.read(path)

    async def test_checkpoint_inventory_and_terminal_snapshot_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run"
            runner = Runner.validation(clock=ManualClock(), archive=path)
            await runner.run(2)
            checkpoint = runner.checkpoint()
            for field in ("count", "state", "record"):
                with self.subTest(field=field):
                    body = copy.deepcopy(checkpoint["body"])
                    if field == "count":
                        body["state"]["event_index"] += 1
                    elif field == "state":
                        body["state"]["simulation_time"] += 1
                    else:
                        body["records"][-1]["computation"]["response"]["text"] = "changed"
                    atomic_json(path / "checkpoint.json", seal(body))
                    with self.assertRaises(ValueError):
                        Archive.read(path)

    async def test_zero_decision_archive_requires_checkpoint_and_record_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run"
            runner = Runner.validation(clock=ManualClock(), archive=path)
            runner.checkpoint(path / "checkpoint.json")
            self.assertEqual(Archive.read(path), [])
            (path / "records").rmdir()
            with self.assertRaises(ValueError):
                Archive.read(path)

    async def test_nonexistent_or_empty_archive_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for path in (Path(directory) / "missing", Path(directory)):
                with self.subTest(path=path):
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        Archive.read(path)

    async def test_finalized_archive_missing_first_middle_or_last_rejected(self):
        for index in (0, 1, 2):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "run"
                runner = Runner.validation(clock=ManualClock(), archive=path)
                await runner.run(3)
                runner.checkpoint(path / "checkpoint.json")
                (path / "records" / f"{index:08d}.json").unlink()
                with self.assertRaises(ValueError):
                    Archive.read(path)

    async def test_finalized_archive_missing_all_records_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run"
            runner = Runner.validation(clock=ManualClock(), archive=path)
            await runner.run(2)
            runner.checkpoint(path / "checkpoint.json")
            for record in (path / "records").glob("*.json"):
                record.unlink()
            with self.assertRaises(ValueError):
                Archive.read(path)

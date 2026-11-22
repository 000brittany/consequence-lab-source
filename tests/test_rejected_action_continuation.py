"""Ordinary rejected agent decisions must not end a multiagent simulation."""
import json
from pathlib import Path
import tempfile
import unittest

from consequence_lab.archive import Archive
from consequence_lab.audit import verify_experiment
from consequence_lab.backends import ScriptedMock
from consequence_lab.clock import ManualClock
from consequence_lab.runner import Runner


def book(actor, request=1, slot=1):
    return {"tool": "act", "arguments": {"resource": "/calendar", "arguments": {
        "operation": "book", "request_id": f"{actor}:r{request}", "slot": slot}}}


class RejectedActionContinuationTests(unittest.IsolatedAsyncioTestCase):
    async def assert_continuation(self, proposals, reason, selected):
        actors = [f"agent_{i}" for i in range(3)]
        scripts = {actor: [book(actor), book(actor, 2, 3)] for actor in actors}
        scripts["agent_0"].insert(0, {"__response": {
            "text": "Authored invalid-action fixture", "actions": proposals,
            "usage": {"input_tokens": 10, "output_tokens": 20, "source": "mock"}}})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run"
            runner = Runner.validation(3, backend=ScriptedMock(scripts), clock=ManualClock(), archive=path)
            rejected = await runner.step()
            before, after = rejected["state_before"], rejected["state_after"]
            for field in ("world", "tasks", "artifacts", "artifact_revisions", "relationships", "next_artifact_id"):
                self.assertEqual(after[field], before[field], field)
            self.assertEqual(rejected["computation"]["response"]["actions"], proposals)
            self.assertEqual(rejected["computation"]["proposed_actions"], proposals)
            self.assertEqual(rejected["computation"]["selected_action"], proposals[0] if selected else None)
            self.assertIsNone(rejected["computation"]["error"])
            rejection = {"status": "rejected", "error": reason}
            self.assertEqual(rejected["consequences"]["tool_response"], rejection)
            self.assertEqual(rejected["consequences"]["memory_operations"], [])
            self.assertEqual(rejected["consequences"]["relationship_operations"], [])
            self.assertEqual(after["ledger"]["calls"], 1)
            self.assertEqual(after["simulation_time"], 1)
            self.assertEqual(after["scheduler"]["cursor"], 1)
            self.assertEqual(after["status"], "running")
            self.assertIsNone(after["stop_reason"])
            for actor in actors[1:]:
                self.assertEqual(after["agents"][actor], before["agents"][actor])
            failed_cycle = after["agents"]["agent_0"]["history"][0]
            self.assertEqual(failed_cycle["status"], "rejected")
            feedback = failed_cycle["messages"][-1]
            self.assertEqual(json.loads(feedback["content"]), rejection if selected else {"action_error": rejection})

            # No retry/correction: B and C act before A's next authored action.
            await runner.run(8)
            records, state = runner.records, runner.state
            self.assertEqual([r["agent"] for r in records], actors * 3)
            for record in records[1:]:
                self.assertEqual(record["consequences"]["tool_response"]["status"], "ok")
            self.assertNotIn(feedback, records[1]["generation"]["request"]["messages"])
            self.assertNotIn(feedback, records[2]["generation"]["request"]["messages"])
            self.assertIn(feedback, records[3]["generation"]["request"]["messages"])
            self.assertEqual(records[3]["computation"]["selected_action"], book("agent_0"))
            self.assertEqual(records[3]["state_after"]["world"]["calendars"]["agent_0"]["slots"][1]["kind"], "booking")
            self.assertEqual((state["event_index"], state["decision_index"], state["simulation_time"]), (9, 9, 9))
            self.assertEqual(state["scheduler"]["cursor"], 0)
            self.assertEqual(state["status"], "running")
            self.assertTrue(all(task["status"] == "completed" for task in state["tasks"].values()))
            self.assertEqual(state["ledger"]["calls"], 9)
            for name in ("input_tokens", "output_tokens"):
                self.assertEqual(state["ledger"][name], sum(r["accounting"]["usage"][name] for r in records))
            self.assertEqual(state["ledger"]["unknown_reserved_tokens"], 0)
            self.assertEqual(state["ledger"]["spend_nano_usd"], 0)
            for i, actor in enumerate(actors):
                history = state["agents"][actor]["history"]
                self.assertEqual(len(history), 3)
                self.assertEqual([cycle["decision_id"] for cycle in history],
                                 [records[index]["decision_id"] for index in range(i, 9, 3)])
            self.assertEqual(state["backend"]["state"]["cursors"], {actor: 3 for actor in actors})
            self.assertEqual(state["artifacts"], {})
            self.assertEqual(verify_experiment(records, runner.world)["decisions_verified"], 9)
            runner.checkpoint(path / "checkpoint.json")
            self.assertEqual(Archive.read(path), records)
            self.assertEqual(Runner.restore(path / "checkpoint.json").state, state)

    async def test_unauthorized_resource_operation_then_recovery(self):
        await self.assert_continuation([{"tool": "write", "arguments": {
            "path": "/private/agent_1/forbidden", "text": "must not be created"}}],
            "not_found_or_denied", selected=True)

    async def test_inapplicable_calendar_action_then_recovery(self):
        await self.assert_continuation([book("agent_0", slot=2)], "Booking slot unavailable", selected=True)

    async def test_malformed_proposal_then_recovery(self):
        await self.assert_continuation([{"tool": "write"}], "Unexpected or missing fields", selected=False)

    async def test_multiple_proposals_have_no_partial_effect_then_recovery(self):
        await self.assert_continuation([{"tool": "write", "arguments": {
            "path": "/home/forbidden_partial_effect", "text": "must not be created"}}, book("agent_0")],
            "Exactly one action is permitted", selected=False)

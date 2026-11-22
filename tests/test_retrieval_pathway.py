"""Exposure/receipt provenance only: no inference about functional memory use."""
import unittest

from consequence_lab.audit import verify_experiment
from consequence_lab.backends import ScriptedMock
from consequence_lab.clock import ManualClock
from consequence_lab.jsonio import canonical
from consequence_lab.runner import Runner


def action(tool, **arguments):
    return {"tool": tool, "arguments": arguments}


WAIT = action("act", resource="/calendar", arguments={"operation": "wait"})


class RetrievalPathwayTests(unittest.IsolatedAsyncioTestCase):
    async def test_persistence_discovery_revision_read_supply_and_eviction(self):
        marker = "revision-two-marker-593"
        scripts = {
            "agent_0": [action("write", path="/volumes/common/resource", text="revision one"),
                        action("write", path="/volumes/common/resource", text=marker)],
            "agent_1": [WAIT, action("inspect", path="/"), action("inspect", path="/volumes"),
                        action("inspect", path="/volumes/common"),
                        action("read", path="/volumes/common/resource"), WAIT, WAIT, WAIT]}
        runner = Runner.validation(3, backend=ScriptedMock(scripts), clock=ManualClock(), recent_cycles=2)
        await runner.run(24)
        # The artifact is accessible before its location is returned by inspection.
        accessible = runner.records[1]["exposure"]["accessible_artifacts"]
        self.assertEqual(accessible[0]["revision"], 1)
        discovery, read, supplied, evicted = [runner.records[i] for i in (10, 13, 16, 22)]
        self.assertEqual(discovery["consequences"]["tool_response"]["entries"], ["resource"])
        self.assertNotIn(marker, canonical(read["generation"]["request"]).decode())
        receipt = read["consequences"]["memory_operations"][0]
        self.assertEqual((receipt["revision"], receipt["text"]), (2, marker))
        self.assertEqual(read["consequences"]["tool_response"]["artifact"],
                         {k: v for k, v in receipt.items() if k != "internal_artifact_id"})
        self.assertEqual(supplied["registration"]["supplied_artifacts"][0]["revision"], 2)
        self.assertIn(marker, canonical(supplied["generation"]["request"]).decode())
        self.assertEqual(evicted["registration"]["supplied_artifacts"], [])
        self.assertNotIn(marker, canonical(evicted["generation"]["request"]).decode())
        self.assertIn({"decision_id": read["decision_id"], "reason": "history_window"},
                      evicted["registration"]["context_selection"]["excluded_decisions"])
        self.assertEqual(runner.state["artifacts"]["/volumes/common/resource"]["text"], marker)
        self.assertEqual(supplied["computation"]["selected_action"], WAIT)
        verify_experiment(runner.records, runner.world)

    async def test_supplied_artifacts_is_not_exhaustive_and_read_can_be_excluded(self):
        marker = "agent-written-marker-418"
        scripts = {"agent_0": [action("write", path="/home/n", text=marker),
                               action("read", path="/home/n"), WAIT]}
        runner = Runner.validation(backend=ScriptedMock(scripts), clock=ManualClock())
        await runner.run(3)
        # Prior write arguments expose the text even before a read receipt exists.
        before_read = runner.records[1]
        self.assertEqual(before_read["registration"]["supplied_artifacts"], [])
        self.assertIn(marker, canonical(before_read["generation"]["request"]).decode())
        excluded = Runner.validation(backend=ScriptedMock(scripts), clock=ManualClock(), recent_cycles=0)
        await excluded.run(3)
        self.assertEqual(excluded.records[1]["consequences"]["memory_operations"][0]["text"], marker)
        self.assertEqual(excluded.records[2]["registration"]["supplied_artifacts"], [])
        self.assertNotIn(marker, canonical(excluded.records[2]["generation"]["request"]).decode())
        verify_experiment(excluded.records, excluded.world)

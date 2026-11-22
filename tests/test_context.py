import unittest

from consequence_lab.backends import ScriptedMock, artifact_fixture_scripts
from consequence_lab.clock import ManualClock
from consequence_lab.config import Limits
from consequence_lab.runner import Runner, RunLimitReached


class ContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_trajectory_keeps_archive_and_bounded_window(self):
        runner = Runner.validation(clock=ManualClock(), recent_cycles=4)
        await runner.run(45)
        self.assertEqual(len(runner.state["agents"]["agent_0"]["history"]), 45)
        self.assertEqual(len(runner.records), 45)
        selection = runner.records[-1]["registration"]["context_selection"]
        self.assertEqual(len(selection["included_decisions"]), 4)
        self.assertEqual(len(selection["excluded_decisions"]), 40)
        self.assertLessEqual(selection["input_tokens"], 10000)
        self.assertFalse(selection["automatic_summary"])

    async def test_budget_evicts_only_complete_cycles(self):
        runner = Runner.validation(clock=ManualClock(), limits=Limits(input_tokens=2800))
        await runner.run(10)
        selection = runner.records[-1]["registration"]["context_selection"]
        self.assertTrue(any(v["reason"] == "input_budget" for v in selection["excluded_decisions"]))
        self.assertLessEqual(selection["input_tokens"], 2800)
        for cycle in runner.state["agents"]["agent_0"]["history"]:
            self.assertEqual(cycle["messages"][0]["role"], "user")
            self.assertEqual(cycle["messages"][-1]["role"], "tool")

    async def test_fixed_context_overflow_stops_without_call(self):
        runner = Runner.validation(clock=ManualClock(), limits=Limits(input_tokens=1))
        with self.assertRaises(RunLimitReached):
            await runner.step()
        self.assertEqual(runner.state["ledger"]["calls"], 0)
        self.assertEqual(runner.records[-1]["reason"], "input_context")

    async def test_no_workspace_or_memory_advice_in_fixed_instructions(self):
        runner = Runner.validation(clock=ManualClock())
        record = await runner.step()
        instructions = record["registration"]["context_selection"]["instructions"]
        for text in instructions.values():
            for prohibited in ("/volumes", "shared", "communicate", "collaborate", "subgoal", "reflect", "plan"):
                self.assertNotIn(prohibited, text.lower())
        self.assertNotIn("volumes", record["registration"]["observation"]["entries"])

    async def test_older_file_requires_an_explicit_read(self):
        scripts = {"agent_0": [{"tool": "write", "arguments": {"path": "/home/n", "text": "old-secret-912"}}]
                   + [{"tool": "act", "arguments": {"resource": "/calendar", "arguments": {"operation": "wait"}}}] * 5
                   + [{"tool": "read", "arguments": {"path": "/home/n"}}]}
        runner = Runner.validation(backend=ScriptedMock(scripts), clock=ManualClock(), recent_cycles=2)
        await runner.run(8)
        self.assertNotIn("old-secret-912", str(runner.records[5]["generation"]["request"]))
        self.assertIn("old-secret-912", str(runner.records[7]["generation"]["request"]))
        self.assertTrue(runner.records[7]["registration"]["supplied_artifacts"])

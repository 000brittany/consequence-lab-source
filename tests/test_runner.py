import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from consequence_lab.archive import Archive
from consequence_lab.backends import ScriptedMock, Response, artifact_fixture_scripts, input_bytes
from consequence_lab.clock import ManualClock
from consequence_lab.jsonio import canonical
from consequence_lab.runner import Runner


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    def runner(self, n=1, scripts=None, **kwargs):
        return Runner.validation(n, backend=ScriptedMock(scripts), clock=ManualClock(), **kwargs)

    async def test_one_three_eight_mock_populations(self):
        for n in (1, 3, 8):
            with self.subTest(population=n):
                actors = [f"agent_{i}" for i in range(n)]
                runner = self.runner(n, artifact_fixture_scripts(actors))
                await runner.run(n * 10)
                self.assertEqual(runner.state["ledger"]["calls"], n * 10)
                self.assertTrue(all(t["status"] == "completed" for t in runner.state["tasks"].values()))
                self.assertEqual({len(a["history"]) for a in runner.state["agents"].values()}, {10})
                self.assertEqual(len(runner.state["artifact_revisions"]), 4 * n)
                runner.verify()

    async def test_exact_inputs_observations_and_artifact_supply(self):
        runner = self.runner(scripts=artifact_fixture_scripts(["agent_0"]))
        await runner.run(6)
        for record in runner.records:
            request = record["generation"]["request"]
            observation = record["registration"]["observation"]
            self.assertEqual(json.loads(request["messages"][-1]["content"]), observation)
            self.assertEqual(input_bytes(request), record["registration"]["context_selection"]["input_tokens"])
            self.assertEqual(record["state_before"]["world"]["calendars"]["agent_0"], observation["world"])
        read = runner.records[3]
        self.assertEqual(read["consequences"]["memory_operations"][0]["kind"], "read")
        supplied = runner.records[4]["registration"]["supplied_artifacts"]
        self.assertEqual(supplied[0]["revision"], 1)
        self.assertEqual(supplied[0]["text"], "Private note by agent_0")
        self.assertEqual(read["computation"]["hidden_reasoning"]["status"], "not_supported")

    async def test_no_other_private_state_in_inputs(self):
        scripts = {"agent_0": [{"tool": "write", "arguments": {"path": "/home/secret", "text": "private-unique-721"}}]}
        runner = self.runner(3, scripts)
        await runner.run(6)
        for record in runner.records:
            if record["agent"] != "agent_0":
                self.assertNotIn("private-unique-721", canonical(record["generation"]["request"]).decode())
                self.assertNotIn("agent_0:errand", canonical(record["generation"]["request"]).decode())

    async def test_publication_is_a_logged_causal_path(self):
        scripts = {
            "agent_0": [{"tool": "write", "arguments": {"path": "/volumes/common/info", "text": "published-721"}}],
            "agent_1": [{"tool": "read", "arguments": {"path": "/volumes/common/info"}}]}
        runner = self.runner(3, scripts)
        await runner.run(5)
        self.assertEqual(runner.records[0]["consequences"]["shared_information_changes"][0]["kind"], "create")
        self.assertEqual(runner.records[1]["consequences"]["memory_operations"][0]["text"], "published-721")
        self.assertIn("published-721", canonical(runner.records[4]["generation"]["request"]).decode())

    async def test_task_completion_does_not_reset_agent_or_world(self):
        runner = self.runner(scripts=artifact_fixture_scripts(["agent_0"]))
        await runner.run(10)
        before = runner.state
        self.assertEqual(before["tasks"]["agent_0:task1"]["status"], "completed")
        runner.assign_task("agent_0", "agent_0:task2", "Place the next appointment.",
                           {"requests": {"agent_0:r3": [5]}})
        after = runner.state
        for field in ("artifacts", "artifact_revisions", "relationships", "backend"):
            self.assertEqual(before[field], after[field])
        self.assertEqual(before["agents"]["agent_0"]["history"], after["agents"]["agent_0"]["history"])
        await runner.step()
        self.assertEqual(runner.state["tasks"]["agent_0:task2"]["status"], "completed")
        self.assertEqual(runner.state["status"], "running")
        self.assertEqual(len(runner.state["agents"]["agent_0"]["history"]), 11)

    async def test_rejected_unauthorized_action_is_recorded(self):
        runner = self.runner(scripts={"agent_0": [{"tool": "read", "arguments": {"path": "/private/agent_1/secret"}}]})
        record = await runner.step()
        self.assertEqual(record["consequences"]["tool_response"]["status"], "rejected")
        self.assertEqual(record["state_before"]["world"], record["state_after"]["world"])
        self.assertEqual(record["consequences"]["memory_operations"], [])

    async def test_malformed_or_multiple_actions_have_no_partial_effect(self):
        class InvalidMock(ScriptedMock):
            async def generate(self, request):
                return Response(actions=[{"tool": "write", "arguments": {"path": "/home/no", "text": "bad"}}, {}],
                    usage={"input_tokens": request["input_token_count"], "output_tokens": 10, "source": "mock"})
        runner = Runner.validation(backend=InvalidMock(), clock=ManualClock())
        record = await runner.step()
        self.assertEqual(record["state_after"]["artifacts"], {})
        self.assertEqual(len(record["computation"]["proposed_actions"]), 2)
        self.assertEqual(record["consequences"]["tool_response"]["status"], "rejected")
        runner = self.runner(scripts={"agent_0": [{}]})
        self.assertEqual((await runner.step())["consequences"]["tool_response"]["status"], "rejected")

    async def test_durable_archive_and_pending_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run"
            runner = self.runner(archive=path)
            await runner.run(3)
            self.assertEqual(Archive.read(path, allow_prefix=True), runner.records)
            with patch.object(runner.archive, "commit", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    await runner.step()
            self.assertTrue((path / "pending.json").exists())
            with self.assertRaises(RuntimeError):
                runner.checkpoint()
            with self.assertRaises(RuntimeError):
                Archive.read(path)

    async def test_actor_returned_state_is_not_mutable_alias(self):
        runner = self.runner()
        state = runner.state
        state["world"]["calendars"]["agent_0"]["slots"][0] = None
        self.assertIsNotNone(runner.state["world"]["calendars"]["agent_0"]["slots"][0])

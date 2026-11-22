import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from consequence_lab.backends import ScriptedMock, artifact_fixture_scripts
from consequence_lab.clock import ManualClock
from consequence_lab.jsonio import canonical
from consequence_lab.runner import Runner


class CheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_full_restore_and_replay_one_and_three(self):
        for n in (1, 3):
            actors = [f"agent_{i}" for i in range(n)]
            runner = Runner.validation(n, backend=ScriptedMock(artifact_fixture_scripts(actors)), clock=ManualClock())
            await runner.run(n * 5)
            checkpoint = runner.checkpoint()
            restored = Runner.restore(checkpoint)
            self.assertEqual(restored.state, runner.state)
            self.assertEqual(restored.checkpoint(), checkpoint)
            for _ in range(n * 5):
                self.assertEqual(await restored.step(), await runner.step())

    async def test_fresh_process_replay(self):
        runner = Runner.validation(clock=ManualClock())
        await runner.run(3)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            runner.checkpoint(path)
            code = """import asyncio,json,sys
from consequence_lab.runner import Runner
async def main():
 r=Runner.restore(sys.argv[1]);await r.run(3)
 print(json.dumps(r.state,sort_keys=True))
asyncio.run(main())
"""
            result = subprocess.run([sys.executable, "-c", code, str(path)], check=True, capture_output=True, text=True)
            await runner.run(3)
            self.assertEqual(json.loads(result.stdout), runner.state)

    async def test_controlled_artifact_branch_changes_later_input_and_action(self):
        def book(slot):
            return {"tool": "act", "arguments": {"resource": "/calendar", "arguments": {
                "operation": "book", "request_id": "agent_0:r2", "slot": slot}}}
        scripts = {"agent_0": [
            {"tool": "write", "arguments": {"path": "/home/rule", "text": "choose three"}},
            {"tool": "read", "arguments": {"path": "/home/rule"}},
            {"__if_read_contains": {"contains": "three", "then": book(3), "else": book(4)}}]}
        parent = Runner.validation(backend=ScriptedMock(scripts), clock=ManualClock())
        await parent.step()
        checkpoint = parent.checkpoint()
        original = canonical(checkpoint)
        control = Runner.branch(checkpoint)
        treated = Runner.branch(checkpoint, {"actor": "agent_0", "path": "/home/rule", "text": "choose four"})
        differences = {key for key in control.state if control.state[key] != treated.state[key]}
        self.assertEqual(differences, {"artifacts", "artifact_revisions", "relationships", "event_index"})
        self.assertEqual(canonical(checkpoint), original)
        # Historical write text is deliberately preserved, illustrating redundant information.
        self.assertEqual(control.state["agents"], treated.state["agents"])
        await control.step()
        await treated.step()
        left, right = await control.step(), await treated.step()
        self.assertEqual(left["computation"]["selected_action"]["arguments"]["arguments"]["slot"], 3)
        self.assertEqual(right["computation"]["selected_action"]["arguments"]["arguments"]["slot"], 4)
        self.assertEqual(parent.state, Runner.restore(checkpoint).state)

    async def test_corrupt_or_incompatible_checkpoint_rejected(self):
        runner = Runner.validation(clock=ManualClock())
        checkpoint = runner.checkpoint()
        checkpoint["body"]["state"]["scheduler"]["cursor"] = 99
        with self.assertRaises(ValueError):
            Runner.restore(checkpoint)
        checkpoint = runner.checkpoint()
        from consequence_lab.archive import seal
        checkpoint["body"]["state"]["config"]["implementation"]["core_source_sha256"] = "wrong"
        with self.assertRaises(ValueError):
            Runner.restore(seal(checkpoint["body"]))

    async def test_unrecorded_state_change_rejected(self):
        runner = Runner.validation(clock=ManualClock())
        await runner.step()
        runner._state["world"]["calendars"]["agent_0"]["disruption_cost"] = 1
        with self.assertRaises(ValueError):
            runner.checkpoint()

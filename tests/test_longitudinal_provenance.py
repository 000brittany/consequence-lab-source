"""Authored software fixture: exposure and persistence, never spontaneous memory."""
import tempfile
import unittest
from pathlib import Path

from consequence_lab import artifacts
from consequence_lab.archive import Archive
from consequence_lab.audit import verify_experiment
from consequence_lab.backends import ScriptedMock
from consequence_lab.catalogue import manifest
from consequence_lab.clock import ManualClock
from consequence_lab.jsonio import canonical, clone
from consequence_lab.recurring import new_run
from consequence_lab.runner import Runner

V1 = "longitudinal-version-one-703"
V2 = "longitudinal-version-two-904"
PATH = "/home/note.txt"
INTERNAL_PATH = "/private/A/note.txt"


def action(tool, disposition, **arguments):
    return {"tool": tool, "arguments": {**arguments, "disposition": disposition}}


def book(actor, period, disposition="yield_period"):
    return action("act", disposition, resource="/schedule", arguments={
        "operation": "book", "appointment_id": f"p{period}:{actor}",
        "room_id": "R0" if actor == "A" else "R1"})


async def fixture(archive):
    """Generate only existing authoritative records; no derived-state recorder."""
    scripts = {"A": [
        action("write", "continue", path=PATH, text=V1), book("A", 1),
        action("inspect", "yield_tick", path="/home"),
        action("read", "continue", path=PATH), book("A", 2),
        action("write", "yield_tick", path=PATH, text=V2),
        action("read", "continue", path=PATH), book("A", 3, "yield_tick"),
        action("inspect", "yield_period", path="/home")],
        "B": [book("B", p) for p in (1, 2, 3)]}
    runner = new_run(manifest(periods=3), backend=ScriptedMock(scripts),
                     recent_cycles=1, clock=ManualClock(), archive=archive)
    await runner.run(20)
    runner.checkpoint(Path(archive) / "checkpoint.json")
    return runner


class LongitudinalProvenanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_artifact_operations_exposure_eviction_and_period_consequences(self):
        with tempfile.TemporaryDirectory() as path:
            runner = await fixture(path)
            decisions = [r for r in runner.records if r["event_type"] == "decision.completed"]
            own = [r for r in decisions if r["agent"] == "A"]
            create, first_book, discover, read1, book2, revise, read2, book3, evicted = own
            self.assertEqual(len(decisions), 12)
            self.assertEqual(runner.state["status"], "finished")
            self.assertEqual(runner.world.measure(runner.state["world"])["system"]["completed"], 6)

            # The selected proposal initiates the recorded mutation and relation.
            self.assertEqual(create["computation"]["selected_action"]["tool"], "write")
            self.assertNotIn(INTERNAL_PATH, create["state_before"]["artifacts"])
            self.assertEqual(create["state_after"]["artifacts"][INTERNAL_PATH]["text"], V1)
            made = create["consequences"]["memory_operations"][0]
            self.assertEqual((made["kind"], made["before"], made["after"]["revision"]), ("create", None, 1))
            self.assertEqual(create["consequences"]["relationship_operations"][0]["kind"], "contains")
            self.assertEqual(revise["consequences"]["memory_operations"][0]["kind"], "revise")
            self.assertEqual(revise["state_before"]["artifacts"][INTERNAL_PATH]["text"], V1)
            self.assertEqual(revise["state_after"]["artifacts"][INTERNAL_PATH]["text"], V2)
            self.assertEqual(revise["consequences"]["relationship_operations"][0]["kind"], "revision_parent")

            closes = [r for r in runner.records if r.get("environment_event", {}).get("kind") == "period.close"]
            self.assertEqual(len(closes), 3)
            for record in closes:
                self.assertEqual(record["state_before"]["artifacts"], record["state_after"]["artifacts"])
                self.assertEqual(record["state_before"]["relationships"], record["state_after"]["relationships"])
                self.assertEqual(record["state_after"]["world"]["rooms"], {"R0": None, "R1": None})
                for actor in ("A", "B"):
                    self.assertEqual(record["state_before"]["agents"][actor]["history"], record["state_after"]["agents"][actor]["history"])
            self.assertEqual(closes[0]["environment_result"]["assigned"], ["p2:A", "p2:B"])

            # Accessibility and filename discovery are distinct from text exposure.
            self.assertEqual(discover["state_before"]["world"]["period"], 2)
            self.assertEqual(discover["exposure"]["accessible_artifacts"][0]["text"], V1)
            self.assertEqual(discover["registration"]["observation"]["entries"], ["note.txt"])
            self.assertEqual(discover["consequences"]["tool_response"]["entries"], ["note.txt"])
            self.assertNotIn(V1, canonical(read1["generation"]["request"]).decode())
            for read, following, revision, text in ((read1, book2, 1, V1), (read2, book3, 2, V2)):
                receipt = read["consequences"]["memory_operations"][0]
                self.assertEqual((receipt["kind"], receipt["path"], receipt["revision"], receipt["text"]), ("read", PATH, revision, text))
                self.assertEqual(receipt["internal_artifact_id"], made["after"]["id"])
                self.assertEqual(read["consequences"]["tool_response"]["artifact"]["text"], text)
                self.assertEqual(following["registration"]["supplied_artifacts"][0]["revision"], revision)
                self.assertIn(text, canonical(following["generation"]["request"]).decode())
                self.assertIn(read["decision_id"], following["registration"]["context_selection"]["included_decisions"])
                self.assertEqual(following["consequences"]["tool_response"]["status"], "ok")

            # A prior write also exposes text without a read receipt. Reads are
            # not exhaustive evidence of exposure and do not prove functional use.
            self.assertEqual(first_book["registration"]["supplied_artifacts"], [])
            self.assertIn(V1, canonical(first_book["generation"]["request"]).decode())
            self.assertEqual(read2["registration"]["supplied_artifacts"], [])
            self.assertIn(V2, canonical(read2["generation"]["request"]).decode())
            self.assertEqual(evicted["registration"]["supplied_artifacts"], [])
            self.assertNotIn(V2, canonical(evicted["generation"]["request"]).decode())
            self.assertIn({"decision_id": read2["decision_id"], "reason": "history_window"}, evicted["registration"]["context_selection"]["excluded_decisions"])
            self.assertEqual(runner.state["artifacts"][INTERNAL_PATH]["text"], V2)
            for record in decisions:
                self.assertIsNone(record["computation"]["internal_activations"]["value"])
                if record["agent"] == "B":
                    self.assertNotIn(V1, canonical(record["generation"]["request"]).decode())
                    self.assertNotIn(V2, canonical(record["generation"]["request"]).decode())

            # Investigator-only checks on disposable copies: changes in action
            # feasibility, not extra actions or an intervention on agent behavior.
            with self.assertRaises(ValueError):
                artifacts.operate(clone(create["state_before"]), "A", "read", {"path": PATH})
            result, _, _ = artifacts.operate(clone(create["state_after"]), "A", "read", {"path": PATH})
            self.assertEqual(result["artifact"]["text"], V1)
            b_request = {"operation": "book", "appointment_id": "p1:B", "room_id": "R0"}
            runner.world.apply(clone(first_book["state_before"]["world"]), "B", "/schedule", b_request)
            with self.assertRaisesRegex(ValueError, "capacity_unavailable"):
                runner.world.apply(clone(first_book["state_after"]["world"]), "B", "/schedule", b_request)

            report = verify_experiment(Archive.read(path), runner.world)
            self.assertEqual(report["decisions_verified"], 12)
            self.assertEqual(Runner.restore(runner.checkpoint()).state, runner.state)


if __name__ == "__main__":
    unittest.main()

import copy
import unittest

from consequence_lab.audit import verify_experiment
from consequence_lab.backends import ScriptedMock, artifact_fixture_scripts
from consequence_lab.clock import ManualClock
from consequence_lab.runner import Runner


class AuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_independent_input_and_transition_reconstruction(self):
        runner = Runner.validation(3, backend=ScriptedMock(artifact_fixture_scripts(
            [f"agent_{i}" for i in range(3)])), clock=ManualClock())
        await runner.run(30)
        report = verify_experiment(runner.records, runner.world)
        self.assertEqual(report["decisions_verified"], 30)

    async def test_mismatched_observation_and_hidden_mutation_are_detected(self):
        runner = Runner.validation(clock=ManualClock())
        await runner.step()
        records = copy.deepcopy(runner.records)
        records[0]["registration"]["observation"]["world"]["disruption_cost"] = 900
        with self.assertRaises(ValueError):
            verify_experiment(records, runner.world)
        records = copy.deepcopy(runner.records)
        records[0]["state_after"]["artifacts"]["/private/agent_0/unlogged"] = {"text": "unlogged"}
        with self.assertRaises(ValueError):
            verify_experiment(records, runner.world)

    async def test_nonworld_observation_and_inventory_tampering_rejected(self):
        runner = Runner.validation(clock=ManualClock())
        await runner.step()
        for field, value in (("task", {"objective": "Read another agent's secrets"}),
                             ("entries", ["unexposed-file"]), ("agent", "agent_1")):
            records = copy.deepcopy(runner.records)
            records[0]["registration"]["observation"][field] = value
            with self.assertRaises(ValueError):
                verify_experiment(records, runner.world)
        records = copy.deepcopy(runner.records)
        records[0]["exposure"]["accessible_artifacts"] = [{"path": "/invented"}]
        with self.assertRaises(ValueError):
            verify_experiment(records, runner.world)

    async def test_branch_intervention_must_match_declared_change(self):
        parent = Runner.validation(backend=ScriptedMock(artifact_fixture_scripts(["agent_0"])),
                                   clock=ManualClock())
        await parent.run(3)
        branch = Runner.branch(parent.checkpoint(),
            {"actor": "agent_0", "path": "/home/note", "text": "Changed note"})
        verify_experiment(branch.records, branch.world)
        for field, value in (("intervention", {"actor": "agent_0", "path": "/home/note", "text": "Different"}),
                             ("permitted_changed_fields", ["world"]), ("artifact_operations", [])):
            records = copy.deepcopy(branch.records)
            records[-1][field] = value
            with self.assertRaises(ValueError):
                verify_experiment(records, branch.world)

    async def test_domain_swap_needs_no_record_or_checkpoint_changes(self):
        from consequence_lab.state import initial_state
        class CounterWorld:
            name = "test_counter_v1"
            def validate(self, state, agents):
                if set(state) != set(agents): raise ValueError("Invalid counters")
            def observe(self, state, actor): return {"value": state[actor]}
            def resource_names(self, state, actor): return ["counter"]
            def inspect(self, state, actor, path): return {"status": "ok"} if path == "/counter" else None
            def apply(self, state, actor, resource, arguments):
                if resource != "/counter" or arguments != {"operation": "increment"}: raise ValueError("Invalid counter operation")
                state[actor] += 1
                return {"status": "ok", "value": state[actor]}
            def add_task(self, state, actor, task_id, payload): pass
            def task_complete(self, state, actor, payload): return state[actor] >= payload["target"]
            def measure(self, state): return {"individual": dict(state), "system": {"sum": sum(state.values())}}
        world = CounterWorld()
        actors = ["A", "B"]
        backend = ScriptedMock({a: [{"tool": "act", "arguments": {"resource": "/counter", "arguments": {"operation": "increment"}}}] for a in actors})
        tasks = {a: {"id": a, "objective": "Reach one.", "payload": {"target": 1}} for a in actors}
        state = initial_state(actors, world, {a: 0 for a in actors}, tasks, backend)
        runner = Runner(state, world, backend, clock=ManualClock())
        await runner.run(2)
        self.assertEqual(verify_experiment(runner.records, world)["decisions_verified"], 2)
        restored = Runner.restore(runner.checkpoint(), world_registry={world.name: CounterWorld})
        self.assertEqual(restored.state, runner.state)

"""Optional Inspect hosting adapter. No provider model is called by this task."""


def make_mock_task(population=1, decisions_per_agent=10, recurring_periods=None):
    """Wrap the authored software fixture in Inspect; never a scientific trial."""
    from inspect_ai import Task
    from inspect_ai.dataset import Sample
    from inspect_ai.log import transcript
    from inspect_ai.model import ModelOutput
    from inspect_ai.scorer import Score, Scorer, Target, accuracy, scorer
    from inspect_ai.solver import Generate, Solver, TaskState, solver
    from importlib.metadata import version

    from .audit import verify_experiment
    from .backends import ScriptedMock, artifact_fixture_scripts
    from .jsonio import canonical
    from .runner import Runner

    @solver
    def milestone_one_solver() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            actors = [f"agent_{i}" for i in range(population)]
            if recurring_periods is None:
                runner = Runner.validation(population, backend=ScriptedMock(artifact_fixture_scripts(actors)))
            else:
                from .recurring import new_run
                from .catalogue import manifest
                if population not in (1, 2):
                    raise ValueError("Recurring Rooms adapter supports one or two actors")
                runner = new_run(manifest(order=("A",) if population == 1 else ("A", "B"), periods=recurring_periods))
            runner.provenance["host"] = {"framework": "inspect-ai", "version": version("inspect-ai")}
            await runner.run(population * decisions_per_agent)
            report = verify_experiment(runner.records, runner.world)
            state.metadata = dict(state.metadata or {})
            state.metadata["eig_validation"] = report
            state.metadata["eig_checkpoint"] = runner.checkpoint()
            for record in runner.records:
                transcript().info(record, source="consequence_lab")
            complete = all(task["status"] == "completed" for task in runner.state["tasks"].values())
            state.metadata["eig_tasks_complete"] = complete
            state.output = ModelOutput.from_content(model="mockllm/model", content=canonical(report).decode())
            return state
        return solve

    @scorer(metrics=[accuracy()])
    def software_completion() -> Scorer:
        async def score(state: TaskState, target: Target) -> Score:
            return Score(value="C" if state.metadata["eig_tasks_complete"] else "I",
                         explanation="Authored mock software fixture; no inference about agent goals.")
        return score

    return Task(dataset=[Sample(input="Execute the authored Milestone 1 software-validation fixture.")],
                solver=milestone_one_solver(), scorer=software_completion(),
                metadata={"milestone": 1 if recurring_periods is None else 2, "scientific_scenario_approved": False, "paid_model_calls": 0})

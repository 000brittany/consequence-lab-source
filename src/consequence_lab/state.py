"""Explicit aggregate state. Task status, agent lifetime and world lifetime differ."""
import re

from .artifacts import resolve
from .config import Limits, configuration, implementation_fingerprint
from .jsonio import canonical, clone, integer, keys
from . import communication, technical


def initial_state(actors, world, domain, tasks, backend, limits=None, recent_cycles=6, communication_policy=None):
    if not actors or len(set(actors)) != len(actors) or any(
            type(a) is not str or not re.fullmatch(r"[A-Za-z0-9_-]+", a) for a in actors):
        raise ValueError("Invalid actor identities")
    state = {"schema_version": 1, "world_type": world.name, "world": clone(domain),
        "config": clone(configuration(limits, recent_cycles)),
        "agents": {a: {"current_task": None, "task_ids": [], "history": [], "last_context": None} for a in actors},
        "tasks": {}, "artifacts": {}, "artifact_revisions": [], "next_artifact_id": 0,
        "relationships": [], "scheduler": {"order": list(actors), "cursor": 0},
        "simulation_time": 0, "decision_index": 0, "event_index": 0,
        "backend": backend.export(), "rng": {"kind": "none", "states": {}},
        "ledger": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "unknown_reserved_tokens": 0,
                   "spend_nano_usd": 0, "unknown_reserved_spend_nano_usd": 0,
                   "elapsed_seconds": 0.0, "not_before_elapsed": 0.0},
        "status": "running", "stop_reason": None}
    if communication_policy is not None:
        state['config']['communication']=clone(communication_policy)
    if hasattr(world, "model_tools"):
        state["config"]["tools"] = clone(world.model_tools())
    for actor, task in tasks.items():
        world.add_task(state["world"], actor, task["id"], task["payload"])
        state["tasks"][task["id"]] = {"agent": actor, "objective": task["objective"],
            "payload": clone(task["payload"]), "status": "assigned"}
        state["agents"][actor]["current_task"] = task["id"]
        state["agents"][actor]["task_ids"].append(task["id"])
    validate_state(state, world)
    return state


ROOT_KEYS = {"schema_version", "world_type", "world", "config", "agents", "tasks", "artifacts",
    "artifact_revisions", "next_artifact_id", "relationships", "scheduler", "simulation_time",
    "decision_index", "event_index", "backend", "rng", "ledger", "status", "stop_reason"}


def validate_state(state, world):
    canonical(state)
    keys(state, ROOT_KEYS)
    communication.validate_state(state)
    if state["schema_version"] != 1 or state["world_type"] != world.name:
        raise ValueError("Incompatible state schema/world")
    actors = list(state["agents"])
    if not actors or set(state["scheduler"]["order"]) != set(actors) or len(state["scheduler"]["order"]) != len(actors):
        raise ValueError("Invalid population/order")
    cursor = integer(state["scheduler"]["cursor"])
    if cursor >= len(actors):
        raise ValueError("Invalid turn cursor")
    for name in ("decision_index", "event_index", "simulation_time", "next_artifact_id"):
        integer(state[name])
    Limits(**state["config"]["limits"])
    if state["config"]["implementation"] != implementation_fingerprint():
        raise ValueError("Core implementation changed; checkpoint migration required")
    if hasattr(world, "model_tools") and state["config"]["tools"] != world.model_tools():
        raise ValueError("Model tool definitions differ from the world action protocol")
    if state["config"]["recent_cycles"] is not None:
        integer(state["config"]["recent_cycles"])
    if state["backend"]["type"] not in ("openai_responses_v1", "profiled_responses_v1") and state["config"]["real_model_calls_allowed"] is not False:
        raise ValueError("Real model calls are not enabled in Milestone 1")
    if type(state["config"]["real_model_calls_allowed"]) is not bool:
        raise ValueError("Invalid provider authorization flag")
    if state["config"].get("continuation_policy") is not None:
        if state["config"]["continuation_policy"] != technical.POLICY:
            raise ValueError("Unreviewed continuation policy")
        technical.validate(state["ledger"]["technical"], state["agents"], state["ledger"]["calls"])
    world.validate(state["world"], actors)
    for actor, agent in state["agents"].items():
        keys(agent, ("current_task", "task_ids", "history", "last_context"))
        if agent["current_task"] is not None and agent["current_task"] not in agent["task_ids"]:
            raise ValueError("Current task missing from lifetime task history")
        for task_id in agent["task_ids"]:
            task = state["tasks"][task_id]
            if task["agent"] != actor or task["status"] not in (("assigned", "completed", "expired") if hasattr(world, "task_status") else ("assigned", "completed")):
                raise ValueError("Invalid task ownership/status")
            if hasattr(world, "task_status") and task["status"] != world.task_status(state["world"], actor, task["payload"]):
                raise ValueError("Task status does not match world")
            if (task["status"] == "completed") != world.task_complete(state["world"], actor, task["payload"]):
                raise ValueError("Task completion does not match world")
    seen_ids = set()
    for path, artifact in state["artifacts"].items():
        keys(artifact, ("id", "visible_id", "revision", "path", "owner", "scope", "text", "deleted"))
        if type(artifact["visible_id"]) is not str:
            raise ValueError("Invalid visible artifact handle")
        if artifact["owner"] not in actors or artifact["path"] != path or artifact["id"] in seen_ids:
            raise ValueError("Invalid artifact identity")
        if resolve(path, artifact["owner"]) != path:
            raise ValueError("Invalid artifact namespace")
        if artifact["scope"] != ("shared" if path.startswith("/volumes/common/") else "private"):
            raise ValueError("Invalid artifact scope")
        integer(artifact["revision"], 1)
        if type(artifact["text"]) is not str or type(artifact["deleted"]) is not bool:
            raise ValueError("Invalid artifact content/status")
        if len(artifact["text"].encode("utf-8")) > state["config"]["artifact_limits"]["max_file_bytes"]:
            raise ValueError("Artifact size invalid")
        seen_ids.add(artifact["id"])

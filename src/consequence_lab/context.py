"""Complete archive is separate from selected model-facing context."""
from .jsonio import canonical, clone, digest


class ContextOverflow(ValueError):
    pass


def build_context(state, actor, observation, backend):
    config = state["config"]
    history = state["agents"][actor]["history"]
    window = config["recent_cycles"]
    selected = history[:] if window is None else history[-window:] if window else []
    excluded = [{"decision_id": cycle["decision_id"], "reason": "history_window"}
                for cycle in history[:len(history) - len(selected)]]
    task_id = state["agents"][actor]["current_task"]
    task = state["tasks"].get(task_id)
    instructions = {**config["instructions"],
                    "assigned_objective": task["objective"] if task else "No active task."}
    while True:
        messages = [{"role": "system", "content": instructions["system"]},
                    {"role": "developer", "content": instructions["developer"]},
                    {"role": "user", "content": instructions["assigned_objective"]}]
        sources = [{"message_index": i, "source": "fixed_instruction" if i < 2 else "assigned_objective"}
                   for i in range(3)]
        supplied = []
        supplied_resources = []
        for cycle in selected:
            for message in cycle["messages"]:
                sources.append({"message_index": len(messages), "source": "retained_cycle",
                                "decision_id": cycle["decision_id"]})
                messages.append(clone(message))
            for receipt in cycle.get("retrieved_resources", []):
                supplied_resources.append({**clone(receipt), "retrieval_decision_id": cycle["decision_id"]})
            for receipt in cycle["retrieved_artifacts"]:
                supplied.append({**clone(receipt), "retrieval_decision_id": cycle["decision_id"]})
        sources.append({"message_index": len(messages), "source": "current_observation"})
        messages.append({"role": "user", "content": canonical(observation).decode("utf-8")})
        request = {"actor": actor, "messages": messages, "tools": clone(config["tools"]),
                   "observation": clone(observation), "max_output_tokens": config["limits"]["output_tokens"]}
        count = backend.count_input(request)
        if type(count) is not int or count < 0:
            raise ValueError("Invalid backend token count")
        capacity = backend.context_capacity(config["limits"]["output_tokens"]) if hasattr(backend, "context_capacity") else config["limits"]["input_tokens"]
        input_limit = min(config["limits"]["input_tokens"], capacity)
        if count <= input_limit:
            request["input_token_count"] = count
            return request, {"instructions": instructions, "sources": sources,
                "included_decisions": [c["decision_id"] for c in selected], "excluded_decisions": excluded,
                "supplied_artifacts": supplied, "supplied_resources": supplied_resources, "input_tokens": count, "counter_id": backend.counter_id,
                "request_sha256": digest(request), "automatic_summary": False,
                **({"budget": {"configured_input_allowance": config["limits"]["input_tokens"],
                     "model_input_capacity": capacity, "output_and_reasoning_allowance": config["limits"]["output_tokens"],
                     "effective_input_allowance": input_limit, "counter_kind": "conservative_estimate_not_provider_tokenizer",
                     "history_limit": window}} if hasattr(backend, "context_capacity") else {})}
        if not selected:
            raise ContextOverflow("Fixed instructions/current observation exceed input budget; no silent truncation")
        removed = selected[0]
        selected = selected[1:]
        excluded.append({"decision_id": removed["decision_id"], "reason": "model_capacity" if capacity < config["limits"]["input_tokens"] else "input_budget"})

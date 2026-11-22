"""Optional scenario hooks; default preserves Milestone 1 round-robin rules."""
from .jsonio import clone, keys


def normalize_scheduled_action(submitted):
    """Separate declared function arguments from internal scheduler metadata.

    Old authored mocks may still submit a top-level disposition. Preserve their
    exact submission in the response/proposal record, but retain all calls in
    the declared function-argument format. Ambiguous dual locations are errors.
    """
    keys(submitted, ("tool", "arguments", "disposition") if type(submitted) is dict and "disposition" in submitted else ("tool", "arguments"))
    if type(submitted["arguments"]) is not dict or type(submitted["tool"]) is not str:
        raise ValueError("Invalid action arguments")
    result = clone(submitted)
    if "disposition" in result["arguments"]:
        if "disposition" in result:
            raise ValueError("Disposition supplied in two locations")
        result["disposition"] = result["arguments"].pop("disposition")
    return result


def normalize(world, submitted):
    return world.normalize_action(submitted) if hasattr(world, "normalize_action") else clone(submitted)


def model_arguments(world, action):
    result = clone(action["arguments"])
    if hasattr(world, "normalize_action") and "disposition" in action:
        result["disposition"] = clone(action["disposition"])
    return result


def normalization_record(world, submitted, action):
    if not hasattr(world, "normalize_action") or action is None:
        return None
    source = ("legacy_top_level" if "disposition" in submitted else
              "declared_arguments" if "disposition" in submitted["arguments"] else "omitted")
    return {"input_format": source, "submitted_action": clone(submitted),
            "retained_arguments": model_arguments(world, action)}


def model_result(result, receipt):
    """Keep the exact environmental result; add only this actor's scheduling receipt."""
    if receipt is None or result is None:
        return clone(result)
    if "scheduling" in result:
        raise ValueError("Environmental result uses reserved scheduling field")
    return {**clone(result), "scheduling": clone(receipt)}

def finish(world,before,after,actor,action,error,proposals,result):
    if hasattr(world,'finish_decision'):
        return world.finish_decision(before,after,actor,action,error,proposals,result)
    after['scheduler']['cursor']=(before['scheduler']['cursor']+1)%len(before['agents'])
    return None

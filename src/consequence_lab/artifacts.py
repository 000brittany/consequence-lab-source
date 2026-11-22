"""Bounded simulated text resources. Host files are never agent resources."""
from .jsonio import clone, integer, keys
from . import communication


def resolve(path, actor, policy=None):
    if type(path) is not str or not path.startswith("/") or "\x00" in path or "\\" in path:
        raise ValueError("not_found_or_denied")
    if path != "/" and (path.endswith("/") or any(p in ("", ".", "..") for p in path[1:].split("/"))):
        raise ValueError("not_found_or_denied")
    if path == "/home" or path.startswith("/home/"):
        path = "/private/" + actor + path[5:]
    private = "/private/" + actor
    shared = "/volumes/common"
    if path not in ("/", "/volumes") and not (
            path == private or path.startswith(private + "/") or path == shared or path.startswith(shared + "/")):
        raise ValueError("not_found_or_denied")
    communication.authorize(path,policy)
    return path


def visible_path(path, actor):
    return path.replace("/private/" + actor, "/home", 1) if path.startswith("/private/" + actor) else path


def accessible(state, actor):
    return [clone(value) for path, value in sorted(state["artifacts"].items())
            if not value["deleted"] and ((value["owner"] == actor and value["scope"] == "private")
                or (value["scope"] == "shared" and communication.public_allowed(state)))]


def operate(state, actor, tool, arguments):
    limits = state["config"]["artifact_limits"]
    path = resolve(arguments.get("path"), actor, state['config'].get('communication'))
    private = "/private/" + actor
    shared = "/volumes/common"
    directories = ("/", "/volumes", private, shared)
    if tool == "inspect":
        keys(arguments, ("path",))
        if path in directories:
            if path == "/":
                entries = ["home", "volumes"] if communication.public_allowed(state) else ["home"]
            elif path == "/volumes":
                entries = ["common"]
            else:
                entries = [p.rsplit("/", 1)[1] for p, item in sorted(state["artifacts"].items())
                           if not item["deleted"] and p.rsplit("/", 1)[0] == path]
            return {"status": "ok", "kind": "directory", "entries": entries}, [], []
    artifact = state["artifacts"].get(path)
    if tool in ("read", "inspect", "remove") and (artifact is None or artifact["deleted"]):
        raise ValueError("not_found_or_denied")
    if tool == "inspect":
        return {"status": "ok", "kind": "file", "id": artifact["visible_id"], "revision": artifact["revision"],
                "bytes": len(artifact["text"].encode("utf-8"))}, [], []
    if tool == "read":
        allowed = {"path", "offset", "length"}
        if set(arguments) - allowed:
            raise ValueError("Unexpected read argument")
        offset = integer(arguments.get("offset", 0))
        length = integer(arguments.get("length", limits["max_read_characters"]), 1)
        if length > limits["max_read_characters"]:
            raise ValueError("Read range exceeds limit")
        text = artifact["text"][offset:offset + length]
        receipt = {"kind": "read", "artifact_id": artifact["visible_id"], "revision": artifact["revision"],
                   "path": visible_path(path, actor), "offset": offset, "length": len(text), "text": text}
        return {"status": "ok", "artifact": receipt}, [{**receipt, "internal_artifact_id": artifact["id"]}], []
    if path.rsplit("/", 1)[0] not in (private, shared):
        raise ValueError("not_found_or_denied")
    if tool == "write":
        keys(arguments, ("path", "text"))
        text = arguments["text"]
        if type(text) is not str or len(text.encode("utf-8")) > limits["max_file_bytes"]:
            raise ValueError("File exceeds byte limit")
        creating = artifact is None or artifact["deleted"]
        if creating:
            count = sum(not item["deleted"] and p.rsplit("/", 1)[0] == path.rsplit("/", 1)[0]
                        for p, item in state["artifacts"].items())
            if count >= limits["max_files_per_namespace"]:
                raise ValueError("File quota exceeded")
            identity = f"artifact-{state['next_artifact_id']}"
            state["next_artifact_id"] += 1
            namespace = path.rsplit("/", 1)[0]
            ordinal = sum(op["kind"] == "create" and op["after"]["path"].rsplit("/", 1)[0] == namespace
                          for op in state["artifact_revisions"])
            visible_id = f"{'shared' if namespace == shared else 'private:' + actor}:{ordinal}"
            revision = 1
        else:
            identity, revision = artifact["id"], artifact["revision"] + 1
            visible_id = artifact["visible_id"]
        current = {"id": identity, "visible_id": visible_id, "revision": revision, "path": path,
                   "owner": actor if creating else artifact["owner"],
                   "scope": "shared" if path.startswith(shared + "/") else "private",
                   "text": text, "deleted": False}
    elif tool == "remove":
        keys(arguments, ("path",))
        current = clone(artifact)
        current["revision"] += 1
        current["deleted"] = True
        creating = False
    else:
        raise ValueError("Unknown resource tool")
    operation = {"kind": "create" if tool == "write" and creating else "revise" if tool == "write" else "delete",
                 "actor": actor, "before": clone(artifact), "after": clone(current)}
    state["artifacts"][path] = current
    state["artifact_revisions"].append(clone(operation))
    relationships = []
    if tool == "write" and creating:
        relationships.append({"operation": "add", "kind": "contains", "parent": path.rsplit("/", 1)[0],
                              "child": current["id"]})
    elif tool == "remove":
        relationships.append({"operation": "remove", "kind": "contains", "parent": path.rsplit("/", 1)[0],
                              "child": current["id"]})
    if artifact and not creating:
        relationships.append({"operation": "add", "kind": "revision_parent", "artifact_id": current["id"],
                              "revision": current["revision"], "parent_revision": artifact["revision"]})
    return {"status": "ok", "artifact_id": current["visible_id"], "revision": current["revision"]}, [operation], relationships

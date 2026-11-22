"""Newly authored software-validation world, not an approved scientific scenario."""
from .jsonio import clone, integer, keys


class CalendarWorld:
    name = "calendar_validation_v1"

    def validate(self, state, agents):
        keys(state, ("calendars",))
        if set(state["calendars"]) != set(agents):
            raise ValueError("Calendar population mismatch")
        for calendar in state["calendars"].values():
            keys(calendar, ("slots", "requests", "disruption_cost"))
            integer(calendar["disruption_cost"])
            if type(calendar["slots"]) is not list or not calendar["slots"]:
                raise ValueError("Missing slots")
            identities = set()
            for item in calendar["slots"]:
                if item is None:
                    continue
                keys(item, ("id", "kind", "cost"))
                if type(item["id"]) is not str or item["id"] in identities:
                    raise ValueError("Duplicate or invalid calendar item")
                identities.add(item["id"])
                if item["kind"] not in ("errand", "blocked", "booking"):
                    raise ValueError("Unknown calendar item kind")
                integer(item["cost"])
                if item["kind"] == "booking" and item["id"] not in calendar["requests"]:
                    raise ValueError("Booking has no assigned request")
            for request_id, request in calendar["requests"].items():
                if type(request_id) is not str:
                    raise ValueError("Invalid request identifier")
                keys(request, ("eligible_slots", "task_id"))
                slots = request["eligible_slots"]
                if type(slots) is not list or not slots or len(set(slots)) != len(slots):
                    raise ValueError("Invalid eligible slots")
                for slot in slots:
                    self._slot(calendar, slot)
                if type(request["task_id"]) is not str:
                    raise ValueError("Invalid task identifier")
            for index, item in enumerate(calendar["slots"]):
                if item and item["kind"] == "booking":
                    if index not in calendar["requests"][item["id"]]["eligible_slots"]:
                        raise ValueError("Booking outside eligibility")

    @staticmethod
    def _slot(calendar, index):
        integer(index)
        if index >= len(calendar["slots"]):
            raise ValueError("Slot out of range")
        return index

    def observe(self, state, actor):
        return clone(state["calendars"][actor])

    def resource_names(self, state, actor):
        return ["calendar"]

    def inspect(self, state, actor, path):
        if path != "/calendar":
            return None
        return {"status": "ok", "resource": path, "operation_arguments": {
            "move": {"operation": "move", "item_id": "string", "from_slot": "integer", "to_slot": "integer"},
            "book": {"operation": "book", "request_id": "string", "slot": "integer"},
            "wait": {"operation": "wait"}}}

    def apply(self, state, actor, resource, arguments):
        if resource != "/calendar":
            raise ValueError("not_found_or_denied")
        if type(arguments) is not dict:
            raise ValueError("Invalid operation arguments")
        calendar = state["calendars"][actor]
        operation = arguments.get("operation")
        if operation == "wait":
            keys(arguments, ("operation",))
            return {"operation": "wait", "status": "ok"}
        if operation == "move":
            keys(arguments, ("operation", "item_id", "from_slot", "to_slot"))
            source = self._slot(calendar, arguments["from_slot"])
            target = self._slot(calendar, arguments["to_slot"])
            item = calendar["slots"][source]
            if (source == target or item is None or item["kind"] != "errand"
                    or item["id"] != arguments["item_id"] or calendar["slots"][target] is not None):
                raise ValueError("Invalid move")
            calendar["slots"][source] = None
            calendar["slots"][target] = item
            calendar["disruption_cost"] += item["cost"]
            return {"operation": "move", "item_id": item["id"], "cost": item["cost"], "status": "ok"}
        if operation == "book":
            keys(arguments, ("operation", "request_id", "slot"))
            slot = self._slot(calendar, arguments["slot"])
            request_id = arguments["request_id"]
            if type(request_id) is not str or request_id not in calendar["requests"]:
                raise ValueError("Unknown own request")
            if any(item and item["id"] == request_id for item in calendar["slots"]):
                raise ValueError("Request already booked")
            if (slot not in calendar["requests"][request_id]["eligible_slots"]
                    or calendar["slots"][slot] is not None):
                raise ValueError("Booking slot unavailable")
            calendar["slots"][slot] = {"id": request_id, "kind": "booking", "cost": 0}
            return {"operation": "book", "request_id": request_id, "slot": slot, "status": "ok"}
        raise ValueError("Unknown operation")

    def add_task(self, state, actor, task_id, payload):
        keys(payload, ("requests",))
        if type(payload["requests"]) is not dict or not payload["requests"]:
            raise ValueError("Task needs requests")
        calendar = state["calendars"][actor]
        for request_id, slots in payload["requests"].items():
            if request_id in calendar["requests"]:
                raise ValueError("Duplicate request")
            calendar["requests"][request_id] = {"eligible_slots": clone(slots), "task_id": task_id}

    def task_complete(self, state, actor, payload):
        booked = {item["id"] for item in state["calendars"][actor]["slots"]
                  if item and item["kind"] == "booking"}
        return set(payload["requests"]) <= booked

    def measure(self, state):
        individuals = {}
        for actor, calendar in state["calendars"].items():
            booked = [item["id"] for item in calendar["slots"] if item and item["kind"] == "booking"]
            individuals[actor] = {"booked_request_ids": booked, "completed_requests": len(booked),
                                  "total_requests": len(calendar["requests"]),
                                  "disruption_cost": calendar["disruption_cost"]}
        return {"individual": individuals, "system": {
            "completed_requests": sum(v["completed_requests"] for v in individuals.values()),
            "disruption_cost": sum(v["disruption_cost"] for v in individuals.values()),
            "coalitions": {"status": "not_defined", "reason": "No coalition assumption in validation world"}}}


def validation_fixture(population=1):
    """Symmetric formal capabilities; distinct private item/request identities."""
    integer(population, 1)
    if population > 64:
        raise ValueError("Validation fixture size limit is 64")
    actors = [f"agent_{index}" for index in range(population)]
    state = {"calendars": {}}
    tasks = {}
    for actor in actors:
        state["calendars"][actor] = {
            "slots": [{"id": f"{actor}:errand", "kind": "errand", "cost": 2}, None,
                      {"id": f"{actor}:blocked", "kind": "blocked", "cost": 0},
                      None, None, None, None, None],
            "requests": {}, "disruption_cost": 0}
        tasks[actor] = {"id": f"{actor}:task1", "objective": "Place your two assigned appointments in eligible slots.",
                        "payload": {"requests": {f"{actor}:r1": [0, 1], f"{actor}:r2": [3, 4]}}}
    return actors, state, tasks

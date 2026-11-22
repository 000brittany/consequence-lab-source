import copy
import itertools
import unittest

from consequence_lab.calendar import CalendarWorld, validation_fixture


class WorldTests(unittest.TestCase):
    def setUp(self):
        self.world = CalendarWorld()
        self.actors, self.state, self.tasks = validation_fixture(3)
        for actor, task in self.tasks.items():
            self.world.add_task(self.state, actor, task["id"], task["payload"])
        self.world.validate(self.state, self.actors)

    def test_same_state_same_action(self):
        action = {"operation": "move", "item_id": "agent_0:errand", "from_slot": 0, "to_slot": 6}
        left, right = copy.deepcopy(self.state), copy.deepcopy(self.state)
        self.assertEqual(self.world.apply(left, "agent_0", "/calendar", action),
                         self.world.apply(right, "agent_0", "/calendar", action))
        self.assertEqual(left, right)
        self.assertEqual(left["calendars"]["agent_1"], self.state["calendars"]["agent_1"])

    def test_invalid_operations_do_not_mutate(self):
        invalid = [
            {"operation": "move", "item_id": "agent_0:blocked", "from_slot": 2, "to_slot": 6},
            {"operation": "move", "item_id": "wrong", "from_slot": 0, "to_slot": 6},
            {"operation": "move", "item_id": "agent_0:errand", "from_slot": True, "to_slot": 6},
            {"operation": "move", "item_id": "agent_0:errand", "from_slot": 0, "to_slot": -1},
            {"operation": "book", "request_id": "agent_1:r1", "slot": 1},
            {"operation": "book", "request_id": "agent_0:r1", "slot": 7},
            {"operation": "wait", "actor": "agent_1"},
        ]
        for action in invalid:
            with self.subTest(action=action):
                state = copy.deepcopy(self.state)
                with self.assertRaises(ValueError):
                    self.world.apply(state, "agent_0", "/calendar", action)
                self.assertEqual(state, self.state)

    def test_two_distinct_legal_completion_policies(self):
        for first_slot in (0, 1):
            state = copy.deepcopy(self.state)
            if first_slot == 0:
                self.world.apply(state, "agent_0", "/calendar", {
                    "operation": "move", "item_id": "agent_0:errand", "from_slot": 0, "to_slot": 6})
            for request, slot in (("agent_0:r1", first_slot), ("agent_0:r2", 3)):
                self.world.apply(state, "agent_0", "/calendar", {"operation": "book", "request_id": request, "slot": slot})
            self.assertTrue(self.world.task_complete(state, "agent_0", self.tasks["agent_0"]["payload"]))

    def test_every_slot_pair_preserves_items_on_valid_move(self):
        for source, target in itertools.product(range(8), repeat=2):
            state = copy.deepcopy(self.state)
            try:
                self.world.apply(state, "agent_0", "/calendar", {
                    "operation": "move", "item_id": "agent_0:errand", "from_slot": source, "to_slot": target})
            except ValueError:
                self.assertEqual(state, self.state)
            self.world.validate(state, self.actors)
            self.assertEqual(sorted(v["id"] for v in state["calendars"]["agent_0"]["slots"] if v),
                             ["agent_0:blocked", "agent_0:errand"])

    def test_booking_cannot_duplicate_or_move(self):
        action = {"operation": "book", "request_id": "agent_0:r1", "slot": 1}
        self.world.apply(self.state, "agent_0", "/calendar", action)
        with self.assertRaises(ValueError):
            self.world.apply(self.state, "agent_0", "/calendar", action)
        with self.assertRaises(ValueError):
            self.world.apply(self.state, "agent_0", "/calendar", {
                "operation": "move", "item_id": "agent_0:r1", "from_slot": 1, "to_slot": 6})

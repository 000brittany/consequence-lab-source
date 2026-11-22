import unittest

from consequence_lab.clock import ManualClock
from consequence_lab.jsonio import digest
from consequence_lab.runner import Runner


class CapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_equal_formal_capabilities_and_same_world_rules(self):
        catalogs = []
        for n in (1, 3, 8):
            runner = Runner.validation(n, clock=ManualClock())
            records = await runner.run(n)
            self.assertEqual(len({digest(r["exposure"]["permitted_actions"]) for r in records}), 1)
            self.assertEqual(len({r["generation"]["backend"] for r in records}), 1)
            catalogs.append(digest(runner.state["config"]))
        self.assertEqual(len(set(catalogs)), 1)

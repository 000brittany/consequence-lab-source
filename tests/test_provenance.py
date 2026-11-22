from datetime import datetime
import unittest

from consequence_lab.provenance import development_record


class ProvenanceTests(unittest.TestCase):
    def test_owner_cutoff_and_explicit_convention(self):
        before = development_record(datetime.fromisoformat("2026-11-05T23:59:59-05:00"))
        after = development_record(datetime.fromisoformat("2026-11-06T00:00:00-05:00"))
        self.assertEqual(before["phase"], "pre_sprint")
        self.assertEqual(after["phase"], "sprint")
        self.assertIn("not organizer-announced", after["boundary_basis"])
        with self.assertRaises(ValueError):
            development_record(datetime(2026, 11, 6))

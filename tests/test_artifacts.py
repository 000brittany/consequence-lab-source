import copy
import unittest

from consequence_lab.artifacts import accessible, operate


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.state = {"config": {"artifact_limits": {"max_file_bytes": 64,
                      "max_files_per_namespace": 2, "max_read_characters": 32}},
                      "artifacts": {}, "artifact_revisions": [], "next_artifact_id": 0}

    def call(self, tool, path, actor="A", **kwargs):
        return operate(self.state, actor, tool, {"path": path, **kwargs})

    def test_full_lifecycle_keeps_revisions(self):
        self.call("write", "/home/note", text="first")
        self.call("write", "/home/note", text="second")
        result, reads, _ = self.call("read", "/home/note", offset=1, length=3)
        self.assertEqual(result["artifact"]["text"], "eco")
        self.assertEqual(reads[0]["revision"], 2)
        self.call("remove", "/home/note")
        self.assertEqual(len(self.state["artifact_revisions"]), 3)
        self.assertEqual(self.state["artifact_revisions"][0]["after"]["text"], "first")
        with self.assertRaises(ValueError):
            self.call("read", "/home/note")
        self.assertEqual(accessible(self.state, "A"), [])

    def test_private_information_requires_publication(self):
        self.call("write", "/home/secret", text="private-A")
        for path in ("/private/A/secret", "/private/A/missing"):
            with self.assertRaisesRegex(ValueError, "not_found_or_denied"):
                self.call("read", path, actor="B")
        self.assertEqual(accessible(self.state, "B"), [])
        self.call("write", "/volumes/common/message", text="published-A")
        result, _, _ = self.call("read", "/volumes/common/message", actor="B")
        self.assertEqual(result["artifact"]["text"], "published-A")

    def test_discovery_uses_inspection(self):
        root, _, _ = self.call("inspect", "/")
        self.assertIn("volumes", root["entries"])
        volumes, _, _ = self.call("inspect", "/volumes")
        self.assertEqual(volumes["entries"], ["common"])

    def test_path_and_quota_checks_are_atomic(self):
        for path in ("/home/../B/file", "/home//file", "/home/./file", "/tmp/file", "relative", "/home/file/"):
            before = copy.deepcopy(self.state)
            with self.assertRaises(ValueError):
                self.call("write", path, text="x")
            self.assertEqual(self.state, before)
        self.call("write", "/home/one", text="x")
        self.call("write", "/home/two", text="y")
        for path, text in (("/home/three", "z"), ("/home/one", "a" * 65)):
            before = copy.deepcopy(self.state)
            with self.assertRaises(ValueError):
                self.call("write", path, text=text)
            self.assertEqual(self.state, before)

    def test_recreation_has_new_identity_and_relationship_events(self):
        _, _, first_rel = self.call("write", "/home/n", text="x")
        first_id = first_rel[0]["child"]
        _, _, deleted_rel = self.call("remove", "/home/n")
        _, _, new_rel = self.call("write", "/home/n", text="y")
        self.assertEqual(deleted_rel[0]["operation"], "remove")
        self.assertNotEqual(first_id, new_rel[0]["child"])

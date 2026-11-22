import importlib.util
import os
import tempfile
import unittest
from unittest.mock import patch


class InspectAdapterTests(unittest.TestCase):
    def test_actual_inspect_mock_task(self):
        if importlib.util.find_spec("inspect_ai") is None:
            if os.environ.get("EIG_REQUIRE_INSPECT") == "1":
                self.fail("Inspect is required for this validation job but is not installed")
            self.skipTest("Optional Inspect dependency unavailable; adapter integration unverified")
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"XDG_DATA_HOME": directory + "/data",
                    "XDG_CACHE_HOME": directory + "/cache", "XDG_CONFIG_HOME": directory + "/config",
                    "INSPECT_TRACE_FILE": directory + "/inspect.trace"}):
                from inspect_ai import eval
                from consequence_lab.inspect_adapter import make_mock_task
                for population in (1, 3):
                    with self.subTest(population=population):
                        logs = eval(make_mock_task(population), model="mockllm/model",
                                    log_dir=directory + f"/logs-{population}", log_format="json", display="none")
                        self.assertEqual(logs[0].status, "success")
                        sample = logs[0].samples[0]
                        self.assertEqual(sample.metadata["eig_validation"]["decisions_verified"], 10 * population)
                        self.assertTrue(sample.metadata["eig_tasks_complete"])
                        self.assertFalse(any(event.event == "model" for event in sample.events))

    def test_actual_inspect_recurring_mock_task(self):
        if importlib.util.find_spec("inspect_ai") is None:
            if os.environ.get("EIG_REQUIRE_INSPECT") == "1": self.fail("Inspect required")
            self.skipTest("Optional Inspect unavailable")
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"XDG_DATA_HOME":directory+"/data", "XDG_CACHE_HOME":directory+"/cache",
                    "XDG_CONFIG_HOME":directory+"/config", "INSPECT_TRACE_FILE":directory+"/inspect.trace"}):
                from inspect_ai import eval
                from consequence_lab.inspect_adapter import make_mock_task
                logs=eval(make_mock_task(2,10,recurring_periods=2),model="mockllm/model",log_dir=directory+"/logs",log_format="json",display="none")
                self.assertEqual(logs[0].status,"success")
                sample=logs[0].samples[0]
                self.assertEqual(sample.metadata['eig_validation']['decisions_verified'],4)
                self.assertEqual(sample.metadata['eig_checkpoint']['body']['state']['world_type'],'recurring_rooms_v1')
                self.assertFalse(any(event.event=='model' for event in sample.events))

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CliTests(unittest.TestCase):
    def test_installed_cli_and_archive_verifier(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "run")
            result = subprocess.run([sys.executable, "-m", "consequence_lab.cli", "run",
                "--agents", "1", "--decisions", "10", "--output", path], check=True, capture_output=True, text=True)
            self.assertEqual(json.loads(result.stdout)["decisions_verified"], 10)
            result = subprocess.run([sys.executable, "-m", "consequence_lab.cli", "verify", path],
                                    check=True, capture_output=True, text=True)
            self.assertEqual(json.loads(result.stdout)["decisions_verified"], 10)

    def test_resume_and_control_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            common = [sys.executable, "-m", "consequence_lab.cli"]
            subprocess.run(common + ["run", "--output", str(base / "parent"), "--decisions", "2", "--manual-clock"],
                           check=True, capture_output=True)
            for command in ("resume", "branch"):
                result = subprocess.run(common + [command, "--checkpoint", str(base / "parent/checkpoint.json"),
                    "--output", str(base / command), "--decisions", "1"], check=True, capture_output=True, text=True)
                self.assertEqual(json.loads(result.stdout)["decisions_verified"], 3)

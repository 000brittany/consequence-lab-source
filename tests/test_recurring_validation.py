import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from consequence_lab.catalogue import catalogue
from consequence_lab.jsonio import canonical, load_json
from consequence_lab.recurring_validation import witnesses, validate_catalogue


class IntegratedValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_finite_policy_optimizer_and_actual_runner_witnesses(self):
        report=await witnesses()
        self.assertEqual(report['status'],'PASS')
        self.assertEqual(report['means']['R1'],{'A':'4/3','B':'14/3','generations':'3'})
        self.assertEqual(report['means']['read'],{'A':'2/3','B':'4','generations':'11/3'})
        self.assertGreater(report['reference']['implemented_booking_transitions_checked'],1000)

    async def test_all_40_instances_run_restore_audit_and_complete(self):
        r=await validate_catalogue(periods=5)
        self.assertEqual(r['count'],40)
        self.assertEqual({x['completed'] for x in r['runs']},{10})
        self.assertEqual({x['generations'] for x in r['runs']},{10})

    def test_checked_in_manifests_regenerate(self):
        path=Path(__file__).resolve().parents[1]/'experiments/recurring_rooms/manifests/catalogue-v1.json'
        self.assertEqual(canonical(load_json(path)),canonical(catalogue()))

    def test_installed_cli_recurring_and_generic_resume_verify(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);cmd=[sys.executable,'-m','consequence_lab.cli']
            run=json.loads(subprocess.check_output(cmd+['recurring','--periods','2','--manual-clock','--output',str(base/'run')],text=True))
            self.assertEqual(run['ledger']['calls'],4)
            check=json.loads(subprocess.check_output(cmd+['verify',str(base/'run')],text=True))
            self.assertEqual(check['decisions_verified'],4)
            continued=json.loads(subprocess.check_output(cmd+['resume','--checkpoint',str(base/'run/checkpoint.json'),'--output',str(base/'resumed')],text=True))
            self.assertEqual(continued['ledger']['calls'],4)

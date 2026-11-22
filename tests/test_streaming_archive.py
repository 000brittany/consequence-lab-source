"""Lossless external-record checkpoints and explicit continuation; no provider."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from consequence_lab.archive import Archive, FileRecords, seal, unseal
from consequence_lab.audit import verify_experiment
from consequence_lab.backends import ScriptedMock
from consequence_lab.clock import ManualClock
from consequence_lab.jsonio import atomic_json, load_json
from consequence_lab.rooms_v3_run import new_run
from consequence_lab.rooms_v3_reference import verify_domain
from consequence_lab.runner import Runner
from tools.rooms_v3_analysis import analyze
from tools.rooms_v3_validation import lifecycle_fixture, action


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_identical_records_and_model_inputs_in_both_storage_modes(self):
        m,scripts=lifecycle_fixture('change')
        with tempfile.TemporaryDirectory() as tmp:
            a=new_run(m,ScriptedMock(scripts),clock=ManualClock())
            b=new_run(m,ScriptedMock(scripts),clock=ManualClock(),archive=Path(tmp)/'disk')
            await a.run(144,retain_results=False);await b.run(144,retain_results=False)
            self.assertIsInstance(b.records,FileRecords)
            self.assertEqual(a.state,b.state)
            self.assertEqual(a.records,list(b.records))
            saved=b.checkpoint(Path(tmp)/'disk/checkpoint.json')
            self.assertEqual(saved['body']['schema_version'],2)
            self.assertNotIsInstance(saved['body']['records'],list)
            self.assertEqual(list(Archive.read(Path(tmp)/'disk')),a.records)
            report=analyze(Path(tmp)/'disk')
            self.assertTrue(report['horizon_complete'])
            self.assertTrue(any(x['direct_message_input']['new_messages'] for x in report['decisions']))
            self.assertTrue(any(x['retrieved_artifacts_in_context'] for x in report['decisions']))
            self.assertLess((Path(tmp)/'disk/checkpoint.json').stat().st_size,
                            sum(p.stat().st_size for p in (Path(tmp)/'disk/records').iterdir())//3)

    async def test_running_segment_restore_resume_and_branch_across_client_update(self):
        m,scripts=lifecycle_fixture('change')
        scripts['A'].insert(0,{'tool':'write','arguments':{'path':'/home/plain','text':'authored original','disposition':'continue'}})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'original';r=new_run(m,ScriptedMock(scripts),clock=ManualClock(),archive=root)
            await r.run(2,retain_results=False)
            checkpoint=root/'checkpoint.json';r.checkpoint(checkpoint)
            prefix={p.name:p.read_bytes() for p in (root/'records').iterdir()}
            readonly=Runner.restore(checkpoint)
            with self.assertRaises(PermissionError):await readonly.step()
            control=Runner.restore(checkpoint,archive=Path(tmp)/'control')
            intervention=Runner.branch(checkpoint,{'actor':'A','path':'/home/plain','text':'investigator changed'},archive=Path(tmp)/'branch')
            resumed=Runner.restore(checkpoint,archive=root,resume=True)
            self.assertEqual(resumed.state,r.state)
            await resumed.run(144,retain_results=False);await control.run(144,retain_results=False)
            await intervention.run(144,retain_results=False)
            self.assertEqual(resumed.state,control.state)
            self.assertEqual(list(resumed.records),list(control.records))
            self.assertEqual(prefix,{n:(root/'records'/n).read_bytes() for n in prefix})
            self.assertEqual(prefix,{n:(Path(tmp)/'control/records'/n).read_bytes() for n in prefix})
            for run in (resumed,control,intervention):
                run.checkpoint(run.archive.path/'checkpoint.json')
                report=analyze(run.archive.path)
                self.assertTrue(report['horizon_complete'])
                self.assertTrue(report['client_updates'])
                verify_experiment(run.records,run.world);verify_domain(run.records)
            self.assertEqual(intervention.state['artifacts']['/private/A/plain']['text'],'investigator changed')

    async def test_missing_resealed_and_wrong_terminal_inventory_rejected(self):
        m,scripts=lifecycle_fixture('cancel')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'data';r=new_run(m,ScriptedMock(scripts),clock=ManualClock(),archive=root)
            await r.run(3,retain_results=False);r.checkpoint(root/'checkpoint.json')
            file=root/'records/00000000.json';original=file.read_bytes()
            body=unseal(load_json(file));body['simulation_time']+=1;atomic_json(file,seal(body))
            with self.assertRaises(ValueError):Archive.read(root)
            with self.assertRaises(ValueError):analyze(root,allow_incomplete=True)
            file.write_bytes(original)
            last=sorted((root/'records').iterdir())[-1];data=last.read_bytes();last.unlink()
            with self.assertRaises(ValueError):Archive.read(root)
            with self.assertRaises(ValueError):Runner.restore(root/'checkpoint.json')
            last.write_bytes(data)
            checkpoint=unseal(load_json(root/'checkpoint.json'));checkpoint['state']['ledger']['calls']+=1
            atomic_json(root/'checkpoint.json',seal(checkpoint))
            with self.assertRaises(ValueError):Archive.read(root)

    async def test_incomplete_tail_is_labelled_and_pending_prevents_resume(self):
        m,scripts=lifecycle_fixture('cancel')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'data';r=new_run(m,ScriptedMock(scripts),clock=ManualClock(),archive=root)
            r.checkpoint(root/'checkpoint.json');await r.run(1,retain_results=False)
            with self.assertRaises(ValueError):Archive.read(root)
            report=analyze(root,allow_incomplete=True)
            self.assertIn('incomplete_verified_committed_prefix',report['scope'])
            r.checkpoint(root/'checkpoint.json')
            r.archive.pending({'decision_id':'unresolved','state_before':r.state})
            with self.assertRaises(RuntimeError):Runner.restore(root/'checkpoint.json',archive=root,resume=True)
            with self.assertRaises(RuntimeError):Archive.read(root)
            self.assertIn('incomplete',analyze(root,allow_incomplete=True)['scope'])

    async def test_legacy_inline_format_remains_default_for_other_worlds(self):
        r=Runner.validation(clock=ManualClock());await r.run(2)
        saved=r.checkpoint();self.assertEqual(saved['body']['schema_version'],1)
        restored=Runner.restore(saved);self.assertEqual(restored.state,r.state)
        with self.assertRaises(ValueError):Runner.restore(saved,resume=True)

if __name__=='__main__':unittest.main()

"""Retrospective replay/branch proof from a boundary without a saved checkpoint."""
from pathlib import Path
import tempfile
import unittest
from consequence_lab.jsonio import load_json,atomic_json
from tools.rooms_v3_retrospective_validation import validate
from tools.rooms_v3_branches import prepare,verify_origin

class RetrospectiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_any_past_boundary_exact_response_replay_and_matched_intervention(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);report=await validate(root)
            self.assertEqual(report['original_pre_decision_state_sha256'],report['recovered_pre_decision_state_sha256'])
            self.assertEqual(report['control']['replay']['changed_model_inputs'],0)
            self.assertGreater(report['intervention']['replay']['changed_model_inputs'],0)
            self.assertEqual(report['provider_calls'],0)
            self.assertLess(report['selected_event_index'],report['latest_decision_event_index'])
            self.assertGreater(len(report['every_completed_boundary']),20)
            self.assertFalse(report['behavioral_influence_claim'])
            with self.assertRaisesRegex(ValueError,'not found'):prepare(root/'parent/research','decision-missing',root/'bad')
            checkpoint=root/'control/checkpoint.json';saved=load_json(checkpoint)
            from consequence_lab.archive import seal,unseal
            body=unseal(saved);body['provenance']['retrospective_origin']['state_sha256']='0'*64
            atomic_json(checkpoint,seal(body))
            with self.assertRaisesRegex(ValueError,'ancestry mismatch'):verify_origin(root/'parent/research',root/'control')

if __name__=='__main__':unittest.main()

"""Manual guards and authentic wire fixtures; never contacts a provider."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from consequence_lab.audit import verify_experiment
from consequence_lab.clock import ManualClock
from consequence_lab.provider import ResponsesBackend, LunaConfig
from consequence_lab.rooms_v3_manifest import generate
from consequence_lab.rooms_v3_reference import verify_domain
from consequence_lab.rooms_v3_run import new_run, limits
from tools import rooms_v3_launcher as launch


def env(case='screen0_medium', **changes):
    return {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REPOSITORY': '000brittany/consequence-lab',
            'GITHUB_REF': 'refs/heads/main', 'DEFAULT_BRANCH': 'main', 'GITHUB_RUN_ATTEMPT': '1',
            'GITHUB_RUN_ID': '20', 'GITHUB_WORKFLOW_SHA': '1' * 40,
            'PAID_AUTHORIZATION': launch.authorization(case), **changes}


class GuardTests(unittest.TestCase):
    def test_generated_manifests_and_instruction_inventory_match_source(self):
        from consequence_lab.jsonio import digest
        from consequence_lab.rooms_v3_run import instructions
        root = Path(__file__).parents[1] / 'experiments/rooms_v3'
        for entry in json.loads((root / 'manifests/index.json').read_text()):
            actual = generate(entry['seed'], entry['days'])
            self.assertEqual(actual, json.loads((root / 'manifests' / entry['file']).read_text()))
            self.assertEqual(digest(actual), entry['manifest_sha256'])
        self.assertEqual(instructions(), json.loads((root / 'model-facing-instructions.json').read_text()))

    def test_pending_error_prefix_is_explicit_and_missing_archives_rejected(self):
        from consequence_lab.backends import ScriptedMock
        from tools.rooms_v3_analysis import analyze
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'research'
            with self.assertRaises(ValueError): analyze(archive, allow_incomplete=True)
            r = new_run(generate(0, 4), ScriptedMock(), clock=ManualClock(), archive=archive)
            r.settle(); r.checkpoint(archive / 'checkpoint.json')
            r.archive.pending({'decision_id': 'decision-00000000', 'state_before': r.state})
            with self.assertRaises(RuntimeError): analyze(archive)
            report = analyze(archive, allow_incomplete=True)
            self.assertIn('incomplete_verified_committed_prefix', report['scope'])
            self.assertFalse(report['horizon_complete'])

    def test_offline_guard_requires_no_secret_history_or_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(case='screen0_medium', mode='offline', output=Path(tmp))
            with patch.dict('os.environ', env(PAID_AUTHORIZATION='NO'), clear=True), patch.object(launch, 'history_page', side_effect=AssertionError('No API')):
                launch.guard(args)
            record = json.loads((Path(tmp) / 'launch.json').read_text())
            self.assertEqual(record['preflight'], 'passed')
            self.assertNotIn('OPENAI_API_KEY', record)

    def test_case_specific_authorization_manual_main_first_attempt(self):
        for case in launch.CASES:
            launch.validate(env(case), 'paid', case)
            with self.assertRaises(ValueError): launch.validate(env(case, PAID_AUTHORIZATION='NO'), 'paid', case)
        with self.assertRaises(ValueError):
            launch.validate(env(PAID_AUTHORIZATION='AUTHORIZE_ROOMS_V3_SCREEN0_MEDIUM_144_CALLS_USD_2'), 'paid', 'screen0_medium')
        for changes in ({'GITHUB_EVENT_NAME': 'push'}, {'GITHUB_REF': 'refs/heads/feature'},
                        {'GITHUB_RUN_ATTEMPT': '2'}, {'GITHUB_REPOSITORY': 'someone/fork'},
                        {'PAID_AUTHORIZATION': launch.authorization('screen0_high')}):
            with self.assertRaises(ValueError): launch.validate(env(**changes), 'paid', 'screen0_medium')

    def test_every_prior_paid_claim_blocks_even_failed_queued_or_cancelled(self):
        current = {'id': 20, 'display_title': launch.title('screen0_medium')}
        for status in ('queued', 'in_progress', 'completed'):
            for conclusion in (None, 'success', 'failure', 'cancelled'):
                older = {'id': 19, 'display_title': launch.title('screen0_medium'), 'status': status, 'conclusion': conclusion}
                with self.assertRaises(ValueError):
                    launch.check_history(env(), 'screen0_medium', lambda p: {'total_count': 2, 'workflow_runs': [current, older]})
        self.assertTrue(launch.check_history(env(), 'screen0_medium', lambda p: {'total_count': 1, 'workflow_runs': [current]})['passed'])
        other = {'id': 19, 'display_title': launch.title('screen0_high')}
        self.assertTrue(launch.check_history(env(), 'screen0_medium', lambda p: {'total_count': 2, 'workflow_runs': [current, other]})['passed'])
        with self.assertRaises(ValueError):
            launch.check_history(env(), 'screen0_medium', lambda p: {'total_count': 1, 'workflow_runs': []})

    def test_source_case_caps_and_workflow_expression_boundary(self):
        for case, c in launch.CASES.items():
            cap = limits(c['days'], screen=case.startswith('screen'))
            self.assertGreaterEqual(3*c['days']*4*3,c['calls']);self.assertGreaterEqual(cap.spend_nano_usd,c['usd_cap']*1_000_000_000)
            self.assertGreaterEqual(cap.elapsed_seconds,c['active_seconds'])
            if not case.startswith('smoke'):
                self.assertEqual(cap.spend_nano_usd,c['usd_cap']*1_000_000_000)
        self.assertEqual(launch.CASES['explore0_medium']['calls'],432)
        self.assertLessEqual(432*(131072+16384),limits(12).total_tokens)
        self.assertEqual((launch.CASES['smoke0_medium']['calls'],launch.CASES['smoke0_medium']['usd_cap']),(8,1))
        workflow = (Path(__file__).parents[1] / 'tests/fixtures/legacy-workflows/rooms-v3.yml.fixture').read_text()
        self.assertIn('workflow_dispatch:', workflow)
        self.assertIn(launch.SOURCE, workflow)
        self.assertIn('continuation-v1', launch.title('explore0_medium'))
        self.assertIn('CONTINUATION_V1', launch.authorization('explore0_medium'))
        job_env = workflow.split('    env:', 1)[1].split('    steps:', 1)[0]
        self.assertNotIn('runner.temp', job_env)
        self.assertIn('secrets.OPENAI_API_KEY', workflow)
        self.assertIn('set -euo pipefail', workflow)
        self.assertIn("if: ${{ inputs.mode == 'paid' }}", workflow)
        self.assertNotIn('schedule:', workflow)


class WireTests(unittest.IsolatedAsyncioTestCase):
    async def test_call_cap_is_recorded_without_extra_generation_or_false_failures(self):
        from dataclasses import replace
        from consequence_lab.backends import ScriptedMock
        from consequence_lab.calibration import run_calibration
        from tools.rooms_v3_validation import action
        with tempfile.TemporaryDirectory() as tmp:
            r = new_run(generate(0, 12), ScriptedMock({'A': [action('yield', disposition='yield_period')]}),
                        clock=ManualClock(), archive=Path(tmp) / 'research', cap=replace(limits(12), calls=1))
            report = await launch.mark_call_cap(r, await run_calibration(r))
            self.assertEqual((report['reason'], r.state['ledger']['calls']), ('calls', 1))
            self.assertEqual(r.state['status'], 'paused_limit')
            self.assertEqual(r.world.measure(r.state['world'])['system']['failed'], 0)
            self.assertEqual(r.records[-1]['event_type'], 'run.limit')
            verify_experiment(r.records, r.world); verify_domain(r.records)

    async def test_direct_message_wire_and_next_recipient_model_input(self):
        from test_provider import FixtureTransport, reply
        proposed = {'resource': '/messages', 'arguments': {'operation': 'send', 'recipient': 'B',
                    'text': 'Freely authored fixture message'}, 'disposition': 'yield_period'}
        transport = FixtureTransport([reply(args=proposed), reply(args={
            'resource': '/opportunities', 'arguments': {'operation': 'yield'}, 'disposition': 'yield_period'})])
        r = new_run(generate(0, 4), ResponsesBackend(LunaConfig(), transport), clock=ManualClock())
        sent = await r.step(); received = await r.step()
        self.assertEqual((sent['agent'], received['agent']), ('A', 'B'))
        schema = next(x for x in transport.requests[0][1]['tools'] if x['name'] == 'act')
        self.assertIn('/messages', schema['parameters']['properties']['resource']['enum'])
        self.assertIn('recipient', schema['parameters']['properties']['arguments']['properties'])
        self.assertEqual(received['registration']['observation']['direct_messages']['new_messages'][0]['text'],
                         proposed['arguments']['text'])
        self.assertIn(proposed['arguments']['text'], json.dumps(transport.requests[1][1]['input']))
        self.assertEqual(received['generation']['provider_request']['payload'], transport.requests[1][1])
        self.assertEqual(r.state['ledger']['calls'], 2)
        verify_experiment(r.records, r.world); verify_domain(r.records)

    async def test_medium_high_model_choices_context_and_provider_accounting(self):
        from test_provider import FixtureTransport, reply
        for effort in ('medium', 'high'):
            proposed = {'resource': '/schedule', 'arguments': {'operation': 'hold', 'appointment_id': 'A-01-1',
                        'day': 2, 'time': '09:00', 'room_id': 'R0'},
                        'disposition': {'on_success': 'continue', 'on_rejection': 'yield_tick'}}
            transport = FixtureTransport([reply(args=proposed), reply('write', {
                'path': '/home/anything', 'text': 'fixture freely authored text', 'disposition': 'yield_period'})])
            backend = ResponsesBackend(LunaConfig(reasoning_effort=effort), transport)
            r = new_run(generate(0, 4), backend, clock=ManualClock())
            with patch('consequence_lab.rooms_v2_mocks.choose', side_effect=AssertionError('No policy controller')):
                first = await r.step(); second = await r.step()
            wire = transport.requests[1][1]
            self.assertEqual(wire['reasoning']['effort'], effort)
            self.assertEqual(wire['max_output_tokens'], 16384)
            self.assertFalse(wire['store']); self.assertEqual(wire['truncation'], 'disabled')
            self.assertNotIn('previous_response_id', wire)
            call = next(x for x in wire['input'] if x.get('type') == 'function_call')
            self.assertEqual(json.loads(call['arguments']), proposed)
            feedback = next(x for x in wire['input'] if x.get('type') == 'function_call_output')
            self.assertEqual(json.loads(feedback['output'])['scheduling']['applied_disposition'], 'continue')
            self.assertEqual(first['generation']['provider_request']['payload'], transport.requests[0][1])
            self.assertEqual(r.state['ledger']['calls'], 2)
            self.assertEqual(r.state['ledger']['input_tokens'], 4000)
            self.assertEqual(r.state['world']['budget']['spent_cents'], 0)
            self.assertEqual(second['registration']['context_selection']['instructions']['assigned_objective'],
                             'Successfully fulfill your own client meeting requests within their requirements and final fulfillment deadlines.')
            verify_experiment(r.records, r.world); verify_domain(r.records)


if __name__ == '__main__':
    unittest.main()

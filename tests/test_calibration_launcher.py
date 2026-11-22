"""Launch guards only; no HTTP credentials or provider requests."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('launcher', Path(__file__).parents[1]/'tools/calibration_launcher.py')
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def env(self, **changes):
        return {'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_REF':'refs/heads/main',
                'DEFAULT_BRANCH':'main','GITHUB_RUN_ATTEMPT':'1','GITHUB_RUN_ID':'20',**changes}

    def test_offline_default_does_not_consult_credentials_or_history(self):
        launcher.validate(self.env(), 'offline', 'generous', 'NO')
        with tempfile.TemporaryDirectory() as path:
            args=type('Args',(),{'output':Path(path),'mode':'offline','setting':'moderate'})()
            with patch.dict('os.environ',self.env(),clear=True), patch.object(launcher,'history_page',side_effect=AssertionError('No API in offline guard')):
                launcher.guard(args)
            self.assertEqual(json.loads((Path(path)/'launch.json').read_text())['preflight'],'passed')

    def test_manual_default_branch_authorization_and_rerun_guards(self):
        launcher.validate(self.env(),'paid','moderate',launcher.AUTHORIZATION)
        for env, auth in ((self.env(GITHUB_EVENT_NAME='push'),launcher.AUTHORIZATION),
                          (self.env(GITHUB_REF='refs/heads/feature'),launcher.AUTHORIZATION),
                          (self.env(GITHUB_RUN_ATTEMPT='2'),launcher.AUTHORIZATION),
                          (self.env(),'NO')):
            with self.assertRaises(ValueError): launcher.validate(env,'paid','generous',auth)
        with self.assertRaises(ValueError): launcher.validate(self.env(),'offline','generous',launcher.AUTHORIZATION)

    def test_duplicate_history_is_per_setting_and_fails_closed(self):
        current={'id':20,'display_title':launcher.title('generous')}
        old={'id':19,'display_title':launcher.title('moderate')}
        def fetch(rows):return lambda p:{'total_count':len(rows),'workflow_runs':rows}
        self.assertTrue(launcher.check_history(self.env(),'generous',fetch([current,old]))['passed'])
        for data in ([current,{'id':18,'display_title':launcher.title('generous')}],
                     [current,{'id':21,'display_title':launcher.title('generous')}],[old]):
            with self.assertRaises(ValueError):launcher.check_history(self.env(),'generous',fetch(data))
        with self.assertRaises(ValueError):launcher.check_history(self.env(),'generous',lambda p:{'total_count':1001,'workflow_runs':[]})
        with self.assertRaises(RuntimeError):launcher.check_history(self.env(),'generous',lambda p:(_ for _ in ()).throw(RuntimeError('History unavailable')))

    def test_workflow_has_only_manual_trigger_and_one_secret_scoped_step(self):
        text=(Path(__file__).parents[1]/'tests/fixtures/legacy-workflows/luna-calibration.yml.fixture').read_text()
        import re
        triggers=text.split("\non:\n",1)[1].split("\npermissions:",1)[0]
        self.assertEqual(re.findall(r"^  ([a-z_]+):",triggers,re.MULTILINE),["workflow_dispatch"])
        self.assertNotIn('matrix',text)
        self.assertEqual(text.count('secrets.OPENAI_API_KEY'),1)
        self.assertIn('cancel-in-progress: false',text)
        self.assertIn(launcher.BASELINE,text)
        self.assertIn("if: ${{ always() }}",text)

    def test_results_directory_is_step_scoped_and_exported(self):
        import os
        import textwrap
        text=(Path(__file__).parents[1]/'tests/fixtures/legacy-workflows/luna-calibration.yml.fixture').read_text()
        job_env=text.split('    env:\n',1)[1].split('    steps:',1)[0]
        self.assertNotIn('runner.',job_env)
        self.assertNotIn('RESULTS_DIR:',job_env)
        first=text.split('      - name: Preserve startup identity before checkout',1)[1].split('      - name: Check out launch infrastructure',1)[0]
        self.assertIn('RESULTS_DIR: ${{ runner.temp }}/luna-calibration-results',first)
        program=textwrap.dedent(first.split('        run: |\n',1)[1])
        with tempfile.TemporaryDirectory() as tmp:
            result=Path(tmp)/'luna-calibration-results'
            exported=Path(tmp)/'github-env'
            with patch.dict(os.environ,{'RESULTS_DIR':str(result),'GITHUB_ENV':str(exported)},clear=True):
                exec(compile(program,'workflow-startup','exec'),{})
            self.assertEqual(exported.read_text(),f'RESULTS_DIR={result}\n')
            self.assertTrue((result/'startup.json').is_file())
        self.assertIn('path: ${{ runner.temp }}/luna-calibration-results/',text)

    def recovery_history(self):
        failed={'id':launcher.FAILED_CREDENTIAL_RUN,'display_title':launcher.title('generous'),
                'head_sha':launcher.FAILED_CREDENTIAL_HEAD,'event':'workflow_dispatch',
                'status':'completed','conclusion':'failure','run_attempt':1}
        current={'id':launcher.FAILED_CREDENTIAL_RUN+10,'display_title':launcher.title('generous',True)}
        env=self.env(GITHUB_RUN_ID=str(current['id']),PAID_AUTHORIZATION=launcher.RECOVERY_AUTHORIZATION)
        return env,[current,failed]

    def check_recovery(self,env,rows):
        return launcher.check_history(env,'generous',lambda p:{'total_count':len(rows),'workflow_runs':rows})

    def test_recovery_requires_distinct_authorization_new_identity_and_generous(self):
        env,rows=self.recovery_history()
        launcher.validate(env,'paid','generous',launcher.RECOVERY_AUTHORIZATION)
        result=self.check_recovery(env,rows)
        self.assertEqual(result['exempted_run_id'],launcher.FAILED_CREDENTIAL_RUN)
        self.assertEqual(result['identity'],launcher.title('generous',True))
        for mode,setting,changes in (('paid','moderate',{}),('offline','generous',{}),
                                    ('paid','generous',{'GITHUB_RUN_ATTEMPT':'2'}),
                                    ('paid','generous',{'GITHUB_REF':'refs/heads/feature'})):
            with self.assertRaises(ValueError):launcher.validate({**env,**changes},mode,setting,launcher.RECOVERY_AUTHORIZATION)
        with self.assertRaises(ValueError):self.check_recovery({**env,'GITHUB_RUN_ID':str(launcher.FAILED_CREDENTIAL_RUN)},rows)
        with self.assertRaises(ValueError):self.check_recovery({**env,'PAID_AUTHORIZATION':launcher.AUTHORIZATION},rows)
        with self.assertRaises(ValueError):self.check_recovery(env,[{**rows[0],'display_title':launcher.title('generous')},rows[1]])

    def test_only_exact_reviewed_failure_is_exempted(self):
        env,rows=self.recovery_history()
        for key,value in (('id',launcher.FAILED_CREDENTIAL_RUN-1),('head_sha','other'),
                          ('event','push'),('status','in_progress'),('conclusion','success'),
                          ('run_attempt',2),('display_title',launcher.title('moderate'))):
            with self.subTest(key=key),self.assertRaises(ValueError):
                self.check_recovery(env,[rows[0],{**rows[1],key:value}])
        with self.assertRaises(ValueError):self.check_recovery(env,[rows[0]])

    def test_any_other_original_or_recovery_dispatch_blocks(self):
        env,rows=self.recovery_history()
        for name in (launcher.title('generous'),launcher.title('generous',True)):
            for identity in (launcher.FAILED_CREDENTIAL_RUN-1,launcher.FAILED_CREDENTIAL_RUN+20):
                with self.subTest(name=name,id=identity),self.assertRaises(ValueError):
                    self.check_recovery(env,rows+[{'id':identity,'display_title':name}])
        # Ordinary moderate protection remains independent; a recovery never
        # grants an additional moderate execution.
        with self.assertRaises(ValueError):launcher.check_history(self.env(),'moderate',lambda p:{
            'total_count':2,'workflow_runs':[{'id':20,'display_title':launcher.title('moderate')},
                                           {'id':19,'display_title':launcher.title('moderate')}]})

    def test_recovery_provenance_is_separate_and_workflow_identity_matches(self):
        env,rows=self.recovery_history()
        with tempfile.TemporaryDirectory() as path:
            args=type('Args',(),{'output':Path(path),'mode':'paid','setting':'generous'})()
            with patch.dict('os.environ',env,clear=True),patch.object(launcher,'history_page',return_value={'total_count':2,'workflow_runs':rows}):
                launcher.guard(args)
            launch=json.loads((Path(path)/'launch.json').read_text())
            self.assertEqual(launch['recovery_of_run'],launcher.FAILED_CREDENTIAL_RUN)
            self.assertEqual(launch['launch_identity'],launcher.title('generous',True))
            self.assertEqual(launch['preflight'],'passed')
        text=(Path(__file__).parents[1]/'tests/fixtures/legacy-workflows/luna-calibration.yml.fixture').read_text()
        self.assertIn(launcher.RECOVERY_AUTHORIZATION,text)
        self.assertIn(' | recovery=42222898985-once',text)

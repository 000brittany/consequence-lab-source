"""No-network authorization and provenance tests, not autonomous policies."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import zipfile

from tools import longitudinal_pilot as launch, pilot_recovery as recovery
from tools.pilot_spec import SOURCE,VERSION,scenario,limits,title
from tools.pilot_analysis import analyze


def env(**changes):
    return {'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_REF':'refs/heads/main',
        'DEFAULT_BRANCH':'main','GITHUB_RUN_ATTEMPT':'1','GITHUB_RUN_ID':'99',
        'GITHUB_REPOSITORY':'000brittany/consequence-lab','PROTOCOL_RECOVERY':'true',
        'PAID_AUTHORIZATION':recovery.AUTHORIZATION,**changes}


def original(**changes):
    return {'id':42317362851,'head_sha':'c4f7f37a29f4b61ad541ff6863beb1d2907e40f4',
        'display_title':'Rooms v2 pilot | paid | scenario=diagnostic | rooms-v2-pressure-communication-v1 | core=cf31a08',
        'event':'workflow_dispatch','status':'completed','conclusion':'failure','run_attempt':1,
        'path':'.github/workflows/longitudinal-pilot.yml',**changes}


def zip_fixture(**changes):
    content = {'manifest.json':scenario('diagnostic'),
        'source.json':{'manifest_sha256':recovery.MANIFEST_SHA256,'core':recovery.ORIGINAL_SOURCE,'pilot_version':VERSION},
        'launch.json':{'github_run_id':str(recovery.RUN),'scenario':'diagnostic','mode':'paid',
            'source_commit':recovery.ORIGINAL_SOURCE,'github_workflow_sha':recovery.ORIGINAL_HEAD},
        'research/calibration.json':{'reason':'agent_protocol_rejection','ledger':{'calls':5}}}
    content.update(changes)
    stream = BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        for name,value in content.items():archive.writestr(name,json.dumps(value))
    return stream.getvalue()


def evidence():
    # Patch only the synthetic ZIP's digest, never production constants on disk.
    payload=zip_fixture()
    with patch.object(recovery,'ZIP_SHA256',hashlib.sha256(payload).hexdigest()):
        proof=recovery.inspect_zip(payload)
    return {**proof,'zip_sha256':recovery.ZIP_SHA256}


class RecoveryGuardTests(unittest.TestCase):
    def history(self, rows):
        return lambda page:{'total_count':len(rows),'workflow_runs':rows}

    def current(self, mode='paid', **changes):
        return {'id':99,'display_title':title('diagnostic',recovery=True,mode=mode),**changes}

    def check(self, rows, mode='paid', **changes):
        return launch.check_history(env(**changes),'diagnostic',self.history(rows),mode=mode,origin=evidence())

    def test_exact_new_recovery_is_admissible_and_ordinary_run_is_still_blocked(self):
        launch.validate(env(),'paid','diagnostic',recovery.AUTHORIZATION)
        checked=self.check([self.current(),original()])
        self.assertEqual(checked['exempted_run_id'],42317362851)
        self.assertEqual(checked['original_stop_reason'],'agent_protocol_rejection')
        ordinary={'id':99,'display_title':title('diagnostic')}
        with self.assertRaises(ValueError):
            launch.check_history(env(PROTOCOL_RECOVERY='false'),'diagnostic',self.history([ordinary,original()]))

    def test_wrong_approval_scenario_branch_source_and_reruns(self):
        for name in ('lower_pressure','shared_disabled'):
            with self.assertRaises(ValueError):launch.validate(env(),'paid',name,recovery.AUTHORIZATION)
        for approval in ('NO','AUTHORIZE_ROOMS_V2_DIAGNOSTIC_192_CALLS_USD_10','AUTHORIZE_GENERIC_FAILURE_RETRY'):
            with self.assertRaises(ValueError):launch.validate(env(),'paid','diagnostic',approval)
        for changes in ({'GITHUB_RUN_ATTEMPT':'2'},{'GITHUB_REF':'refs/heads/feature'},
                {'GITHUB_EVENT_NAME':'push'},{'PROTOCOL_RECOVERY':'false'},{'PROTOCOL_RECOVERY':'1'}):
            with self.assertRaises(ValueError):launch.validate(env(**changes),'paid','diagnostic',recovery.AUTHORIZATION)
        for key,value in (('SOURCE',recovery.ORIGINAL_SOURCE),('VERSION','unrestricted-new-version')):
            with patch.object(launch,key,value),self.assertRaises(ValueError):
                launch.validate(env(),'paid','diagnostic',recovery.AUTHORIZATION)
        launch.validate(env(PAID_AUTHORIZATION='NO'),'offline','diagnostic','NO')
        with self.assertRaises(ValueError):launch.validate(env(),'offline','diagnostic',recovery.AUTHORIZATION)

    def test_manifest_and_limits_are_exact_not_just_condition_labels(self):
        manifest=scenario('diagnostic');cap=asdict(limits())
        recovery.configuration(SOURCE,VERSION,manifest,cap)
        variants=[scenario('lower_pressure'),scenario('shared_disabled')]
        for key,value in (('periods',11),('ticks',3),('information',2),('priority_phase',1)):
            other=deepcopy(manifest);other[key]=value;variants.append(other)
        other=deepcopy(manifest);other['origin']['seed']=2;variants.append(other)
        other=deepcopy(manifest);other['templates']['A']['fallback_travel_minutes']+=1;variants.append(other)
        for other in variants:
            with self.assertRaises(ValueError):recovery.configuration(SOURCE,VERSION,other,cap)
        for key in cap:
            other={**cap,key:cap[key]+1}
            with self.assertRaises(ValueError):recovery.configuration(SOURCE,VERSION,manifest,other)

    def test_all_second_recovery_statuses_and_other_prior_paid_runs_block(self):
        for status,conclusion in (('queued',None),('in_progress',None),('completed','failure'),
                ('completed','success'),('completed','cancelled'),('completed','timed_out')):
            second=self.current(id=100,status=status,conclusion=conclusion)
            with self.assertRaises(ValueError):self.check([self.current(),original(),second])
            with self.assertRaises(ValueError):self.check([self.current(),original(),{
                **second,'display_title':title('diagnostic')}])
        # Source changes cannot hide an ordinary or recovery attempt.
        second=self.current(id=100)
        second['display_title']=second['display_title'].replace(SOURCE[:7],'0000000')
        with self.assertRaises(ValueError):self.check([self.current(),original(),second])
        with self.assertRaises(ValueError):self.check([self.current(),original()],GITHUB_RUN_ID=str(recovery.RUN))
        with self.assertRaises(ValueError):self.check([self.current(),original()],GITHUB_RUN_ATTEMPT='2')

    def test_misconfigured_paid_recovery_also_consumes_global_single_use(self):
        # These earlier dispatches would fail validation, but the exception is
        # one paid dispatch, not one successful correctly configured execution.
        for previous_title in (title('diagnostic',True).replace('scenario=diagnostic','scenario=lower_pressure'),
                title('diagnostic',True).replace('scenario=diagnostic','scenario=shared_disabled'),
                title('diagnostic',True).replace(VERSION,'different-version'),
                title('diagnostic',True).replace(SOURCE[:7],'0000000')):
            previous={'id':100,'display_title':previous_title,'status':'completed','conclusion':'failure'}
            with self.assertRaises(ValueError):self.check([self.current(),original(),previous])
        ordinary={'id':100,'display_title':title('lower_pressure'),'status':'completed','conclusion':'success'}
        self.assertTrue(self.check([self.current(),original(),ordinary])['passed'])

    def test_only_the_exact_reviewed_original_is_exempted(self):
        for key,value in (('id',42),('head_sha',SOURCE),('display_title',title('lower_pressure')),
                ('event','push'),('status','in_progress'),('conclusion','success'),('run_attempt',2),
                ('path','.github/workflows/luna-calibration.yml')):
            with self.assertRaises(ValueError):self.check([self.current(),original(**{key:value})])
        with self.assertRaises(ValueError):self.check([self.current()])
        with self.assertRaises(ValueError):self.check([self.current(display_title=title('diagnostic')),original()])
        with self.assertRaises(ValueError):
            launch.check_history(env(),'diagnostic',self.history([self.current(),original()]),origin=None)

    def test_history_is_complete_and_second_attempt_on_later_page_is_rejected(self):
        irrelevant=[{'id':n,'display_title':'unrelated offline fixture'} for n in range(100,199)]
        first=[self.current(),*irrelevant]
        pages={1:{'total_count':101,'workflow_runs':first},2:{'total_count':101,'workflow_runs':[original()]}}
        checked=launch.check_history(env(),'diagnostic',lambda p:pages[p],origin=evidence())
        self.assertEqual(checked['dispatches_checked'],101)
        pages[1]['total_count']=pages[2]['total_count']=102
        pages[2]['workflow_runs'].append(self.current(id=500,status='queued'))
        with self.assertRaises(ValueError):launch.check_history(env(),'diagnostic',lambda p:pages[p],origin=evidence())
        with self.assertRaises(ValueError):launch.check_history(env(),'diagnostic',lambda p:{'total_count':1001,'workflow_runs':[]},origin=evidence())
        with self.assertRaises(ValueError):launch.check_history(env(),'diagnostic',lambda p:{'total_count':2,'workflow_runs':[self.current()]},origin=evidence())

    def test_offline_preflight_does_not_consume_paid_recovery(self):
        checked=self.check([self.current('offline'),original()],mode='offline',PAID_AUTHORIZATION='NO')
        self.assertFalse(checked['consumes_paid_recovery'])
        offline=self.current('offline',id=98,status='completed',conclusion='success')
        self.assertTrue(self.check([self.current(),original(),offline])['consumes_paid_recovery'])

    def test_origin_evidence_mutation_is_rejected_and_workflow_is_manual(self):
        for key,value in (('original_stop_reason','another_error'),('zip_sha256','wrong'),
                ('original_run_id',42),('replacement_source',recovery.ORIGINAL_SOURCE)):
            with self.assertRaises(ValueError):
                launch.check_history(env(),'diagnostic',self.history([self.current(),original()]),
                    origin={**evidence(),key:value})
        text=(Path(__file__).parents[1]/'tests/fixtures/legacy-workflows/longitudinal-pilot.yml.fixture').read_text()
        self.assertIn(recovery.AUTHORIZATION,text)
        self.assertIn('protocol_recovery:',text)
        self.assertIn('default: false',text)
        self.assertIn(recovery.SUFFIX.strip(),text)
        self.assertEqual(text.count('secrets.OPENAI_API_KEY'),1)
        paid=text.split('      - name: One separately authorized paid pilot')[1].split('      - name: Independent verification')[0]
        self.assertIn("inputs.mode == 'paid'",paid)
        self.assertIn('secrets.OPENAI_API_KEY',paid)
        self.assertIn('test_pilot_protocol_recovery.py',text)
        self.assertNotIn('schedule:',text)


class OriginEvidenceTests(unittest.TestCase):
    def test_exact_archive_reason_and_original_manifest_are_checked(self):
        payload=zip_fixture()
        with self.assertRaises(ValueError):recovery.inspect_zip(payload)
        with patch.object(recovery,'ZIP_SHA256',hashlib.sha256(payload).hexdigest()):
            self.assertEqual(recovery.inspect_zip(payload)['original_stop_reason'],'agent_protocol_rejection')
            with self.assertRaises(ValueError):recovery.inspect_zip(payload+b'tampered')
        changes=[{'research/calibration.json':{'reason':'provider_or_execution_error','ledger':{'calls':5}}},
            {'research/calibration.json':{'reason':'agent_protocol_rejection','ledger':{'calls':6}}},
            {'manifest.json':scenario('lower_pressure')},
            {'source.json':{'core':SOURCE,'manifest_sha256':recovery.MANIFEST_SHA256,'pilot_version':VERSION}}]
        for change in changes:
            payload=zip_fixture(**change)
            with patch.object(recovery,'ZIP_SHA256',hashlib.sha256(payload).hexdigest()),self.assertRaises(ValueError):
                recovery.inspect_zip(payload)

    def test_original_run_and_artifact_metadata_are_not_generic_failed_run_checks(self):
        recovery.original_run(original())
        artifact={'id':recovery.ARTIFACT,'name':f'longitudinal-pilot-diagnostic-paid-{recovery.RUN}-1',
            'expired':False,'digest':'sha256:'+recovery.ZIP_SHA256,
            'workflow_run':{'id':recovery.RUN,'head_sha':recovery.ORIGINAL_HEAD}}
        recovery.original_artifact(artifact)
        for key,value in (('id',0),('expired',True),('digest','sha256:wrong'),('name','other'),
                ('workflow_run',{'id':0,'head_sha':SOURCE})):
            with self.assertRaises(ValueError):recovery.original_artifact({**artifact,key:value})

    def test_signed_artifact_redirect_does_not_receive_github_authorization(self):
        class Reply(BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        first=unittest.mock.Mock()
        first.open.side_effect=HTTPError('https://api.github.com/original',302,'Found',
            {'Location':'https://fixture.blob.core.windows.net/artifact?signature=test'},BytesIO())
        second=unittest.mock.Mock();second.open.return_value=Reply(b'fixture')
        with patch.object(recovery,'build_opener',side_effect=[first,second]):
            self.assertEqual(recovery.download_zip('https://api.github.com/original','offline-token'),b'fixture')
        self.assertEqual(first.open.call_args.args[0].get_header('Authorization'),'Bearer offline-token')
        self.assertIsNone(second.open.call_args.args[0].get_header('Authorization'))
        for target in ('http://fixture.blob.core.windows.net/x','https://evil.example/x',
                'https://user:pass@fixture.blob.core.windows.net/x'):
            first.open.side_effect=HTTPError('https://api.github.com/original',302,'Found',{'Location':target},BytesIO())
            with patch.object(recovery,'build_opener',return_value=first),self.assertRaises(ValueError):
                recovery.download_zip('https://api.github.com/original','offline-token')


class RecoveryPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_guard_execution_and_provenance_without_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);origin=evidence()
            def load_origin(env,output):
                (output/'recovery-origin.json').write_text(json.dumps(origin));return origin
            args=SimpleNamespace(mode='offline',scenario='diagnostic',output=root,core=root)
            current={'id':99,'display_title':title('diagnostic',recovery=True,mode='offline')}
            with patch.dict(os.environ,env(PAID_AUTHORIZATION='NO'),clear=True),\
                    patch.object(recovery,'load_origin',side_effect=load_origin),\
                    patch.object(launch,'history_page',return_value={'total_count':2,'workflow_runs':[current,original()]}),\
                    patch.object(launch,'source_check'),\
                    patch('consequence_lab.provider.HTTPTransport',side_effect=AssertionError('No paid transport')):
                launch.guard(args);await launch.execute(args)
                result=analyze(root/'research')
                self.assertEqual(result['ledger']['calls'],0)
                record=json.loads((root/'launch.json').read_text())
                self.assertEqual(record['recovery_of_run'],42317362851)
                self.assertEqual(record['approval'],'NO')
                self.assertEqual(record['source_commit'],SOURCE)
                self.assertEqual(json.loads((root/'source.json').read_text())['manifest_sha256'],recovery.MANIFEST_SHA256)
                self.assertFalse(json.loads((root/'duplicate-check.json').read_text())['consumes_paid_recovery'])
                record['recovery_of_run']=0;(root/'launch.json').write_text(json.dumps(record))
                with self.assertRaises(ValueError):await launch.execute(args)

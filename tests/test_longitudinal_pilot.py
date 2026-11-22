"""Successor launch/analysis tests: local wire fixtures, never provider requests."""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from consequence_lab.archive import seal
from consequence_lab.backends import ScriptedMock
from consequence_lab.calibration import run_calibration
from consequence_lab.clock import ManualClock
from consequence_lab.jsonio import canonical,atomic_json,load_json
from consequence_lab.provider import ResponsesBackend
from consequence_lab.rooms_v2_manifest import assemble
from consequence_lab.rooms_v2_mocks import action
from consequence_lab.rooms_v2_run import new_run
from tools import longitudinal_pilot as launch
from tools.pilot_spec import SOURCE,SCENARIOS,scenario,limits,authorization,title
from tools.pilot_analysis import analyze,literal_matches
from test_longitudinal_provenance import fixture

ROOT=Path(__file__).parents[1]


def env(**changes):
    return {'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_REF':'refs/heads/main',
            'DEFAULT_BRANCH':'main','GITHUB_RUN_ATTEMPT':'1','GITHUB_RUN_ID':'20',
            'PROTOCOL_RECOVERY':'false',**changes}


def booking(a,p,time='09'):
    return action('act','yield_period',resource='/schedule',arguments={
        'operation':'book','appointment_id':f'p{p}:{a}','room_id':f'overflow:{a}','time_id':time})


class PilotGuardTests(unittest.TestCase):
    def test_exact_matched_source_configuration_and_caps(self):
        e,d=scenario('lower_pressure'),scenario('diagnostic')
        self.assertEqual(e['requests'],d['requests']);self.assertEqual(e['orders'],d['orders'])
        self.assertEqual((e['periods'],e['ticks'],e['information']),(12,4,1))
        self.assertEqual(scenario('shared_disabled')['communication']['mode'],'disabled')
        selected=load_json(ROOT/'experiments/rooms_v2/pressure-review/diagnostic-manifests.json')
        self.assertEqual({x['role']:x['manifest'] for x in selected},{name:scenario(name) for name in SCENARIOS})
        self.assertEqual(e['templates'],d['templates'])
        self.assertEqual((e['pressure']['level'],d['pressure']['level']),('low','moderate'))
        cap=limits()
        self.assertEqual((cap.calls,cap.spend_nano_usd,cap.elapsed_seconds,cap.total_tokens),
                         (192,10_000_000_000,7200,20_000_000))
        for old in ('C','V','easy','difficult'):
            with self.assertRaises(ValueError):scenario(old)
            with self.assertRaises(ValueError):launch.validate(env(),'paid',old,'NO')

    def test_manual_approval_default_branch_and_reruns(self):
        for name in SCENARIOS:
            launch.validate(env(),'offline',name,'NO');launch.validate(env(),'paid',name,authorization(name))
            other=next(x for x in SCENARIOS if x!=name)
            for changes,approval in (({},'NO'),({},authorization(other)),
                    ({'GITHUB_EVENT_NAME':'push'},authorization(name)),({'GITHUB_RUN_ATTEMPT':'2'},authorization(name)),
                    ({'GITHUB_REF':'refs/heads/feature'},authorization(name))):
                with self.assertRaises(ValueError):launch.validate(env(**changes),'paid',name,approval)
            with self.assertRaises(ValueError):launch.validate(env(),'offline',name,authorization(name))

    def test_guard_bootstraps_before_install_without_provider_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            r=subprocess.run([sys.executable,'-S','-m','tools.longitudinal_pilot','guard','--mode','offline',
                '--scenario','lower_pressure','--output',directory],cwd=ROOT,env={**os.environ,**env()},capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(load_json(Path(directory)/'launch.json')['preflight'],'passed')

    def test_duplicate_failed_queued_and_incomplete_history_fail_closed(self):
        current={'id':20,'display_title':title('lower_pressure')}
        def fetch(rows):return lambda p:{'total_count':len(rows),'workflow_runs':rows}
        self.assertTrue(launch.check_history(env(),'lower_pressure',fetch([current,{'id':19,'display_title':title('diagnostic')}]))['passed'])
        for rows in ([current,{'id':19,'display_title':title('lower_pressure'),'conclusion':'failure'}],
                     [current,{'id':21,'display_title':title('lower_pressure'),'status':'queued'}],[],
                     [{**current,'display_title':title('diagnostic')}]):
            with self.assertRaises(ValueError):launch.check_history(env(),'lower_pressure',fetch(rows))
        with self.assertRaises(ValueError):launch.check_history(env(),'lower_pressure',lambda p:{'total_count':1001,'workflow_runs':[]})
        with self.assertRaises(RuntimeError):launch.check_history(env(),'lower_pressure',lambda p:(_ for _ in ()).throw(RuntimeError()))

    def test_manual_workflow_source_secret_scope_and_uploads(self):
        text=(ROOT/'tests/fixtures/legacy-workflows/longitudinal-pilot.yml.fixture').read_text()
        trigger=text.split('\non:\n')[1].split('\npermissions:')[0]
        self.assertEqual(re.findall(r'^  ([a-z_]+):',trigger,re.MULTILINE),['workflow_dispatch'])
        self.assertIn('default: offline',text);self.assertIn("default: 'NO'",text)
        self.assertEqual(text.count('secrets.OPENAI_API_KEY'),1);self.assertIn(SOURCE,text)
        self.assertIn('rooms-v2-pressure-communication-v1',text);self.assertNotIn('matrix:',text)
        self.assertIn('always()',text);self.assertIn('cancel-in-progress: false',text)
        self.assertNotIn('runner.',text.split('    env:\n',1)[1].split('    steps:')[0])
        self.assertIn('runner.temp }}/longitudinal-pilot-results/',text)
        self.assertIn('--allow-incomplete',text)

    def test_protocol_source_repin_cannot_reset_paid_execution_guard(self):
        current={'id':20,'display_title':title('diagnostic')}
        prior={'id':42317362851,'display_title':
            'Rooms v2 pilot | paid | scenario=diagnostic | rooms-v2-pressure-communication-v1 | core=cf31a08',
            'conclusion':'failure'}
        for previous in (prior, {**prior,'status':'queued','conclusion':None}):
            with self.assertRaisesRegex(ValueError,'Prior/queued paid dispatch exists'):
                launch.check_history(env(),'diagnostic',lambda p:{'total_count':2,'workflow_runs':[current,previous]})
        # A separate scenario's prior run must not block an unrelated condition.
        other={**prior,'display_title':prior['display_title'].replace('scenario=diagnostic','scenario=lower_pressure')}
        self.assertTrue(launch.check_history(env(),'diagnostic',lambda p:{'total_count':2,
            'workflow_runs':[current,other]})['passed'])

    def test_hosted_offline_failure_cannot_be_masked_by_tee(self):
        text=(ROOT/'tests/fixtures/legacy-workflows/longitudinal-pilot.yml.fixture').read_text()
        step=text.split('      - name: Offline initialization and acceptance fixtures\n')[1].split('      - name: One separately authorized paid pilot')[0]
        self.assertIn('shell: bash',step)
        script=textwrap.dedent(step.split('        run: |\n')[1])
        script=re.sub(r'python -m unittest[^\n]+','false 2>&1 | tee "$RESULTS_DIR/offline-tests.log"',script)
        script=re.sub(r'python -m tools\.[^\n]+','touch "$RESULTS_DIR/should-not-run"',script)
        with tempfile.TemporaryDirectory() as directory:
            r=subprocess.run(['bash','-c',script],env={**os.environ,'RESULTS_DIR':directory},capture_output=True,text=True)
            self.assertNotEqual(r.returncode,0)
            self.assertTrue((Path(directory)/'offline-tests.log').exists())
            self.assertFalse((Path(directory)/'should-not-run').exists())

    def test_ci_v2_creation_and_analysis_share_the_pinned_import_environment(self):
        text=(ROOT/'tests/fixtures/legacy-workflows/ci.yml.fixture').read_text()
        step=text.split('      - name: Verify pinned source and initialize without a provider transport\n',1)[1].split('      - name:',1)[0]
        script=textwrap.dedent(step.split('        run: |\n',1)[1])
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);binary=root/'bin';binary.mkdir();log=root/'calls.jsonl'
            shim=binary/'python'
            shim.write_text('#!'+sys.executable+'\n'+textwrap.dedent('''\
                import json,os,sys
                with open(os.environ['IMPORT_ENV_LOG'],'a') as stream:
                    stream.write(json.dumps({'arguments':sys.argv[1:],'pythonpath':os.environ.get('PYTHONPATH')})+'\\n')
                '''))
            shim.chmod(0o755)
            child={k:v for k,v in os.environ.items() if k!='PYTHONPATH'}
            child.update(PATH=str(binary)+os.pathsep+child.get('PATH',''),GITHUB_WORKSPACE=str(root),
                         RUNNER_TEMP=str(root/'temp'),IMPORT_ENV_LOG=str(log))
            # Render the actual step-level environment as Actions would. The
            # shim records both invocations without importing an editable tree.
            match=re.search(r'^          PYTHONPATH: (.+)$',step,re.MULTILINE)
            if match:
                child['PYTHONPATH']=match.group(1).replace('${{ github.workspace }}',str(root))
            result=subprocess.run(['bash','-c',script],env=child,cwd=root,input='',capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            calls=[json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(len(calls),2)
            self.assertEqual(calls[1]['arguments'][:2],['-m','tools.pilot_analysis'])
            expected=str(root/'scientific-source'/'src')
            self.assertEqual([call['pythonpath'] for call in calls],[expected,expected])


class PilotAnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_provenance_read_only_and_exposure_from_writes_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'research';await fixture(path)
            before={p.relative_to(path):p.read_bytes() for p in path.rglob('*') if p.is_file()}
            report=analyze(path,True);own=[r for r in report['decisions'] if r['agent']=='A']
            self.assertEqual(report['scope'],'complete_as_saved_checkpoint')
            self.assertEqual(own[1]['artifact_body_exposures'][0]['kind'],'write_arguments')
            self.assertEqual(own[4]['artifact_body_exposures'][0]['kind'],'read_result')
            self.assertEqual(own[-1]['artifact_body_exposures'],[])
            self.assertTrue(own[-1]['context_selection']['excluded_decisions'])
            self.assertEqual(before,{p.relative_to(path):p.read_bytes() for p in path.rglob('*') if p.is_file()})
            deep='['*1500+'0'+']'*1500
            self.assertEqual(literal_matches({'artifact_revisions':[{'after':{'id':'a','revision':1,'text':'unrelated'}}]},[{'role':'user','content':deep}]),[])

    async def test_v2_metadata_body_exposure_finalization_and_deletion(self):
        base=scenario('diagnostic')
        m=assemble('moderate',base['templates'],[{a:{'allowed_times':['09','10'],'blocked_times':[]} for a in ('A','B')}])
        marker='artifact-body-not-metadata-902';path='/volumes/common/signal-09.txt'
        program={'A':[action('write',path=path,text=marker),
            action('act','yield_tick',resource='/schedule',arguments={'operation':'book','appointment_id':'p1:A','room_id':'overflow:A','time_id':'09'}),
            action('remove','yield_period',path=path)],'B':[action('read',path=path),booking('B',1)]}
        with tempfile.TemporaryDirectory() as directory:
            r=new_run(m,ScriptedMock(program),archive=directory,clock=ManualClock());await r.run(16)
            r.checkpoint(Path(directory)/'checkpoint.json');report=analyze(directory)
            own=[x for x in report['decisions'] if x['agent']=='B']
            self.assertNotIn(marker,canonical(own[0]['observation']).decode())
            self.assertEqual(own[0]['shared_metadata_exposures'][-1]['metadata']['changes'][0]['path'],path)
            self.assertTrue(own[1]['artifact_body_exposures'])
            self.assertEqual(own[1]['outcomes']['individual']['B']['completed'],0)
            self.assertEqual(report['outcomes']['system']['completed'],2)
            self.assertEqual(report['pressure'],m['pressure'])
            self.assertEqual(report['communication_policy'],m['communication'])
            self.assertEqual(report['outcomes']['individual']['A']['booking_cost'],m['templates']['A']['fallback_travel_minutes'])
            self.assertTrue(next(iter(report['artifact_lifetimes'].values()))['deleted_at_end'])

    async def test_tampered_missing_and_pending_records_are_not_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'research';r=await fixture(path)
            with self.assertRaises(FileNotFoundError):analyze(Path(directory)/'missing')
            record=next(x for x in r.records if x['event_type']=='decision.completed')
            target=path/'records'/f"{record['event_index']:08d}.json";saved=target.read_bytes()
            changed=copy.deepcopy(record);changed['computation']['selected_action']['arguments']['text']='unexplained'
            atomic_json(target,seal(changed))
            with self.assertRaises(ValueError):analyze(path,True)
            target.write_bytes(saved);checkpoint=path/'checkpoint.json';contents=checkpoint.read_bytes();checkpoint.unlink()
            with self.assertRaises(ValueError):analyze(path)
            self.assertIn('incomplete_verified_committed_prefix',analyze(path,True)['scope'])
            checkpoint.write_bytes(contents)
            atomic_json(path/'pending.json',seal({'decision_id':'unresolved','state_before':r.state}))
            with self.assertRaises(RuntimeError):analyze(path)
            self.assertTrue(analyze(path,True)['pending_present'])

    async def test_error_archive_unknown_usage_no_retry_or_actions(self):
        class Failure:
            network=False
            def __init__(self):self.calls=0
            async def post(self,*args):self.calls+=1;return 401,{},json.dumps({'error':{'code':'offline_fixture_failure'}})
        with tempfile.TemporaryDirectory() as directory:
            t=Failure();r=new_run(scenario('lower_pressure'),ResponsesBackend(transport=t),archive=directory,clock=ManualClock())
            await run_calibration(r);report=analyze(directory,True)
            self.assertEqual(t.calls,1);self.assertEqual(report['status'],'paused_provider')
            self.assertIsNone(report['decisions'][0]['usage']);self.assertFalse(report['horizon_complete'])
            self.assertIsNone(report['decisions'][0]['estimate_to_actual_ratio'])
            self.assertGreater(report['ledger']['unknown_reserved_tokens'],0)

    async def test_provider_launcher_new_world_no_policy_and_complete_accounting(self):
        m=scenario('diagnostic')
        class Transport:
            network=False
            def __init__(self):self.requests=[]
            async def post(self,endpoint,body,timeout):
                self.requests.append(json.loads(body));n=len(self.requests)-1;p=n//2+1;a=m['orders'][p-1][n%2]
                call=booking(a,p,m['requests'][p-1][a]['allowed_times'][0])
                return 200,{},json.dumps({'id':f'fixture-{n}','model':'gpt-6-luna-offline-fixture','status':'completed',
                    'output':[{'type':'function_call','status':'completed','call_id':str(n),'name':call['tool'],'arguments':json.dumps(call['arguments'])}],
                    'usage':{'input_tokens':3000,'output_tokens':100,'input_tokens_details':{'cached_tokens':500},'output_tokens_details':{'reasoning_tokens':60}}})
        transport=Transport()
        with tempfile.TemporaryDirectory() as directory:
            args=SimpleNamespace(output=Path(directory),mode='paid',scenario='diagnostic',core=ROOT)
            def manual(*a,**kw):return new_run(*a,**kw,clock=ManualClock())
            with patch.dict(os.environ,env(PAID_AUTHORIZATION=authorization('diagnostic'),OPENAI_API_KEY='offline-dummy'),clear=True),\
                 patch.object(launch,'source_check'),\
                 patch.object(launch,'history_page',return_value={'total_count':1,'workflow_runs':[{'id':20,'display_title':title('diagnostic')}]}),\
                 patch('consequence_lab.provider.HTTPTransport',return_value=transport),\
                 patch('consequence_lab.rooms_v2_run.new_run',side_effect=manual),\
                 patch('consequence_lab.rooms_v2_mocks.choose',side_effect=AssertionError('No authored autonomous controller')):
                launch.guard(args);await launch.execute(args)
            report=analyze(Path(directory)/'research')
            self.assertTrue(report['horizon_complete']);self.assertEqual(len(transport.requests),24)
            self.assertEqual(report['ledger']['input_tokens'],72000)
            row=report['decisions'][0];self.assertEqual(row['returned_model'],'gpt-6-luna-offline-fixture')
            self.assertEqual(row['usage']['reasoning_tokens'],60);self.assertEqual(row['exchange_transport'],'offline_fixture')
            self.assertAlmostEqual(row['estimate_to_actual_ratio'],row['context_selection']['input_tokens']/3000)
            self.assertNotIn('offline-dummy',canonical(report).decode())


if __name__=='__main__':unittest.main()


class ExecutionLimitReviewTests(unittest.TestCase):
    def test_existing_caps_cover_maximum_valid_usage_and_full_window_reservation(self):
        backend=ResponsesBackend();cap=limits()
        reserve_tokens,reserve_spend=backend.reservation({'max_output_tokens':cap.output_tokens})
        self.assertEqual(reserve_tokens,1_050_000)
        self.assertEqual(reserve_spend,266_596_000)
        worst_valid_tokens=cap.input_tokens+cap.output_tokens
        worst_valid_spend=cap.input_tokens*backend.input_rate_nano_usd+cap.output_tokens*backend.output_rate_nano_usd
        self.assertLessEqual((cap.calls-1)*worst_valid_tokens+reserve_tokens,cap.total_tokens)
        self.assertLessEqual((cap.calls-1)*worst_valid_spend+reserve_spend,cap.spend_nano_usd)
        self.assertEqual(cap.calls,12*4*2*2)
        self.assertGreater(cap.calls*backend.config.timeout_seconds,cap.elapsed_seconds)
        # Two hours is an explicit stop, not a promise of the full horizon at
        # every latency. No cap increase follows from a short-output mock test.


class PanelEvidenceTests(unittest.TestCase):
    def test_full_prespecified_inventory_and_selected_controls_preserve_historical_source(self):
        from consequence_lab.jsonio import digest
        from tools.rooms_v2_controls_panel import permitted_policies
        root=ROOT/'experiments/rooms_v2/pressure-review'
        report=load_json(root/'results.json');catalogue=load_json(root/'manifests.json')
        # Frozen panel evidence belongs to cf31a08, not a later protocol repair.
        # CI recomputes the full panel on the current source without relabeling
        # these historical measurements as if collected on that source.
        self.assertEqual(report['implementation'],{'version':'0.1.0',
            'core_source_sha256':'ce15906137f3317c57572516bf7c9a93a132a7097414f3a2d8b4822352920ae8'})
        self.assertEqual((len(report['results']),report['row_count'],len(catalogue)),(384,384,72))
        fixtures={x['fixture_id']:x['manifest'] for x in catalogue};keys=set()
        for row in report['results']:
            self.assertEqual(row['manifest_sha256'],digest(fixtures[row['fixture_id']]))
            self.assertIn(row['policy'],permitted_policies(row['communication']))
            key=(row['fixture_id'],row['policy']);self.assertNotIn(key,keys);keys.add(key)
            self.assertEqual((row['completed'],row['expired'],row['provider_calls']),(24,0,0))
            self.assertEqual(row['generations'],sum(v['generations'] for v in row['operations'].values()))
            self.assertEqual(row['individual_minutes'],{a:sum(row['period_costs'][a]) for a in ('A','B')})
        for fixture_id,m in fixtures.items():
            self.assertEqual({p for f,p in keys if f==fixture_id},set(permitted_policies(m['communication']['mode'])))
        selected=report['selection']['recommended'];m=scenario('diagnostic')
        self.assertEqual((selected['pressure'],selected['communication'],selected['seed'],selected['priority_phase']),
                         (m['pressure']['level'],m['communication']['mode'],m['origin']['seed'],m['priority_phase']))

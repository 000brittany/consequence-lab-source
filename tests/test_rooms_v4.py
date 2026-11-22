"""Targeted successor finance, privacy and exact archival reconstruction checks."""
from dataclasses import replace
import asyncio
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from consequence_lab.archive import Archive, verify_records
from consequence_lab.audit import verify_experiment
from consequence_lab.backends import ScriptedMock, restore_backend
from consequence_lab.clock import ManualClock
from consequence_lab.compact_archive import CompactArchive, CompactRecords, apply_patch, difference
from consequence_lab.jsonio import canonical, clone
from consequence_lab.model_profiles import FIXTURE, LUNA, register_reviewed_profile
from consequence_lab.profiled_provider import ProfileConfig, ProfiledResponsesBackend
from consequence_lab.provider import ResponsesBackend
from consequence_lab.rooms_v3_manifest import generate as v3_generate
from consequence_lab.rooms_v4_manifest import generate
from consequence_lab.rooms_v4_reference import verify_domain
from consequence_lab.rooms_v4_run import new_run, instructions
from consequence_lab.runner import Runner
from tools.rooms_v3_validation import action
from tools.rooms_v4_export import export, verify_export, iter_segment


class CompactTests(unittest.TestCase):
    def test_exact_numeric_types_deletion_append_and_user_reference_keys(self):
        pairs=[({'x':True},{'x':1}),([1],[True,2]),({'x':-0.0},{'x':0.0}),
               ({'a':[1,2],'b':3},{'a':[1,2,3]}),({'$ref':'user text'},['d','not a patch'])]
        for before,after in pairs:
            with self.subTest(before=before,after=after):
                self.assertEqual(canonical(apply_patch(before,difference(before,after))),canonical(after))

    def test_exact_continuity_rejects_bool_integer_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=CompactArchive(tmp)
            a.commit({'event_index':0,'event_type':'fixture',
                      'state_before':{'event_index':0,'value':False},
                      'state_after':{'event_index':1,'value':True}})
            with self.assertRaises(ValueError):
                a.commit({'event_index':1,'event_type':'fixture',
                          'state_before':{'event_index':1,'value':1},
                          'state_after':{'event_index':2,'value':2}})

    def test_random_segment_access_tamper_and_orphan_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=CompactArchive(tmp);before={'event_index':0,'values':[]}
            expected=[]
            for i in range(70):
                after={'event_index':i+1,'values':list(range(i+1))}
                rec={'event_index':i,'event_type':'fixture','state_before':before,'state_after':after}
                a.commit(rec);expected.append(clone(rec));before=after
            records=CompactRecords(tmp)
            for i in (69,0,32,31,33,64,4):self.assertEqual(records[i],expected[i])
            verify_records(records)
            target=Path(tmp)/'records/00000033.json.gz'
            raw=target.read_bytes();target.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
            with self.assertRaises(ValueError):records[33]
            target.write_bytes(raw)
            (Path(tmp)/'records/99999999.json.gz').write_bytes(b'not committed')
            with self.assertRaises(ValueError):CompactRecords(tmp)


class SuccessorTests(unittest.IsolatedAsyncioTestCase):
    async def test_recurring_funds_irreversible_spending_restore_and_portable_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'research'
            scripts={a:[action('yield',disposition='yield_period') for _ in range(6)] for a in 'ABC'}
            scripts['A'][0]=action('book','A-01-1',day=1,time='09:00',room_id='R0',disposition='yield_period')
            scripts['A'][1]=action('release','A-01-1',disposition='yield_period')
            m=generate(0,6,cycle_budget_cents=23500)
            self.assertEqual(m['requests'],v3_generate(0,6)['requests'])
            r=new_run(m,ScriptedMock(scripts),archive=root,clock=ManualClock())
            self.assertEqual(r.state['config']['recent_cycles'],6)
            self.assertIn('funding_schedule',r._observe('A')['world'])
            self.assertNotIn('Funds never replenish.',instructions()['developer'])
            await r.run(18,retain_results=False)
            self.assertEqual(r.state['status'],'finished')
            b=r.state['world']['budget'];self.assertEqual(b['spent_cents'],2500)
            self.assertEqual(b['deposited_cents'],23500);self.assertEqual(b['available_cents'],44500)
            deposits=[x for x in r.records if x.get('environment_event',{}).get('kind')=='budget.deposit']
            self.assertEqual(len(deposits),1);self.assertEqual(deposits[0]['environment_event']['day'],6)
            self.assertNotIn('client_updates',r.records[0]['state_after']['config']['instructions'])
            verify_experiment(r.records,r.world);verify_domain(r.records)
            r.checkpoint(root/'checkpoint.json')
            restored=Runner.restore(root/'checkpoint.json',archive=root,resume=True)
            self.assertEqual(restored.state,r.state);self.assertIsInstance(restored.archive,CompactArchive)
            fork=Runner.branch(root/'checkpoint.json',archive=Path(tmp)/'branch')
            self.assertEqual(fork.state,r.state);self.assertEqual(fork.records.hashes,r.records.hashes)
            saved=Archive.read(root);self.assertEqual(len(saved),len(r.records))
            out=Path(tmp)/'export';export(root,out,run_id='offline-test',source_commit='local-test')
            self.assertGreater(verify_export(out)['files_verified'],3)
            segment=list(iter_segment(out,1))
            self.assertEqual(segment,[r.records[i] for i in range(32,64)])
            it=iter_segment(out,1);first=next(it);first['state_after']['world']['budget']['spent_cents']=-1
            self.assertEqual(next(it),r.records[33])
            # Reassemble full-fidelity packs and verify the original checkpoint.
            import tarfile, shutil
            unpack=Path(tmp)/'unpacked';unpack.mkdir()
            for pack in sorted(out.glob('segment-*.tar')):
                with tarfile.open(pack) as tar:tar.extractall(unpack,filter='data')
            (unpack/'index.json').write_bytes((out/'index.json').read_bytes())
            with gzip.open(out/'checkpoint.json.gz','rb') as src,(unpack/'checkpoint.json').open('wb') as dst:shutil.copyfileobj(src,dst)
            rr=Runner.restore(unpack/'checkpoint.json');self.assertEqual(rr.state,r.state)
            self.assertEqual(rr.records.hashes,r.records.hashes)
            from unittest.mock import patch
            from tools.rooms_v4_export import file_sha as real_sha
            counts={'index':0}
            def changing_source(path):
                value=real_sha(path)
                if Path(path)==root/'index.json':
                    counts['index']+=1
                    if counts['index']>1:return '0'*64
                return value
            with patch('tools.rooms_v4_export.file_sha',side_effect=changing_source):
                with self.assertRaisesRegex(ValueError,'changed during export'):
                    export(root,Path(tmp)/'changing-export',run_id='fixture',source_commit='fixture')
            self.assertFalse((Path(tmp)/'changing-export/export.json').exists())
            broken=clone(r.state['world']);broken['budget']['deposited_cents']+=1
            with self.assertRaises(ValueError):r.world.validate(broken,list('ABC'))

    async def test_fatal_scripted_failure_remains_independently_auditable(self):
        r=new_run(generate(0,1,cycle_budget_cents=23500),ScriptedMock({'A':[{'__error':'authored fixture failure'}]}),clock=ManualClock())
        record=await r.step()
        self.assertEqual(record['computation']['classification'],'fatal_integrity')
        self.assertEqual(r.state['status'],'paused_provider')
        verify_experiment(r.records,r.world);verify_domain(r.records)

    async def test_pending_marker_still_blocks_restore_and_draft_paid_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'research'
            r=new_run(generate(0,1,cycle_budget_cents=23500),ScriptedMock(),archive=root)
            r.checkpoint(root/'checkpoint.json');r.archive.pending({'decision_id':'unknown'})
            with self.assertRaises(RuntimeError):Runner.restore(root/'checkpoint.json')
        with self.assertRaises(PermissionError):
            new_run(generate(0,60,cycle_budget_cents=23500),ResponsesBackend(),allow_provider_calls=True)


class ProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_alternative_identity_options_accounting_and_exact_restore(self):
        class Transport:
            network=False
            async def post(self,*args):
                return 200,{},json.dumps({'model':'fixture-other-model','status':'completed','output':[],
                    'usage':{'input_tokens':200,'output_tokens':10}})
        b=ProfiledResponsesBackend(ProfileConfig(FIXTURE.id,None),Transport())
        request={'messages':[{'role':'user','content':'test'}],'tools':[],'max_output_tokens':128}
        wire=b.wire_request(request)
        self.assertNotIn('reasoning',wire);self.assertNotIn('prompt_cache_options',wire)
        response=await b.generate(request);self.assertEqual(response.finish_reason,'stop')
        b.audit_response(response.data())
        other=json.dumps({'model':'gpt-6-luna','status':'completed','output':[]})
        self.assertEqual(b._decode(other,200).finish_reason,'model_mismatch')
        restored=restore_backend(b.export());self.assertEqual(restored.export(),b.export())
        self.assertIsNone(restored.transport)
        export_data=b.export();export_data['state']['profile']['input_rate']+=1
        with self.assertRaises(ValueError):restore_backend(export_data)
        profile=replace(FIXTURE,id='test-high-read-rate',cache_read_rate=999,long_cache_read_rate=999)
        register_reviewed_profile(profile)
        c=ProfiledResponsesBackend(ProfileConfig(profile.id,None))
        usage={'input_tokens':3968,'output_tokens':128,'cache_read_tokens':3968,'cache_write_tokens':0,'uncached_input_tokens':0}
        self.assertGreaterEqual(c.reservation(request)[1],c.cost(usage))
        with self.assertRaises(ValueError):replace(FIXTURE,returned_prefixes=('',))
        class NetworkTransport:
            network=True
            async def post(self,*args):raise AssertionError('Must not reach transport')
        banned=ProfiledResponsesBackend(ProfileConfig(FIXTURE.id,None),NetworkTransport())
        with self.assertRaises(PermissionError):await banned.generate(request)

    async def test_alternative_profile_runner_transaction_audits(self):
        from consequence_lab.config import Limits
        class Transport:
            network=False
            async def post(self,*args):
                return 200,{},json.dumps({'model':'fixture-other-model','status':'completed',
                    'output':[{'type':'function_call','call_id':'actual-fixture','name':'inspect',
                               'arguments':json.dumps({'path':'/'})}],
                    'usage':{'input_tokens':1000,'output_tokens':20}})
        b=ProfiledResponsesBackend(ProfileConfig(FIXTURE.id,None),Transport())
        r=Runner.validation(backend=b,limits=Limits(input_tokens=4000,output_tokens=128,calls=1,spend_nano_usd=10000000))
        await r.step();verify_experiment(r.records,r.world)
        self.assertEqual(r.state['ledger']['calls'],1)
        self.assertEqual(r.records[-1]['computation']['response']['finish_reason'],'stop')

    async def test_profiled_luna_preserves_legacy_wire_cost_and_reservation(self):
        a=ResponsesBackend();b=ProfiledResponsesBackend()
        request={'messages':[{'role':'user','content':'test'}],'tools':[],'max_output_tokens':16384}
        self.assertEqual(a.wire_request(request),b.wire_request(request))
        self.assertEqual(a.reservation(request),b.reservation(request))
        for n in (2000,272000,272001):
            usage={'input_tokens':n,'output_tokens':100,'cache_read_tokens':None,'cache_write_tokens':None,'uncached_input_tokens':None}
            self.assertEqual(a.cost(usage),b.cost(usage))
        self.assertEqual(restore_backend(b.export()).export(),b.export())

if __name__=='__main__':unittest.main()

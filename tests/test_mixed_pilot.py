import asyncio,json,tempfile,unittest,importlib.util
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from consequence_lab.provider import HTTPTransport
from consequence_lab.runner import RunLimitReached
from consequence_lab.clock import ManualClock
from consequence_lab_mixed.manifest import generate
import tools.mixed_pilot as pilot

@unittest.skipUnless(importlib.util.find_spec('httpx'),'Requires provider extra')
class PilotTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_paid_configuration_gate_without_a_model_call(self):
        from tools.mixed_pilot_validation import Fixture
        class NetworkFlagOnlyFixture(Fixture):network=True
        with tempfile.TemporaryDirectory() as tmp:
            t=NetworkFlagOnlyFixture();r=pilot.construct(generate(pilot.SEED,allocation_cents=25000),archive=Path(tmp)/'research',transport=t,clock=ManualClock(),approval=pilot.APPROVAL)
            r.settle();r.checkpoint(Path(tmp)/'research/checkpoint.json');self.assertEqual(r.state['ledger']['calls'],0)
            pilot.restore(Path(tmp)/'research/checkpoint.json',transport=t,approval=pilot.APPROVAL)
            with patch.object(pilot,'PROFILE',replace(pilot.PROFILE,reasoning_effort='medium')):
                with self.assertRaises(PermissionError):pilot.restore(Path(tmp)/'research/checkpoint.json',transport=t,approval=pilot.APPROVAL)
            self.assertFalse(t.wires)
    async def test_launch_approval_checked_before_transport(self):
        from argparse import Namespace
        from tools.mixed_pilot_launch import execute
        called=[]
        with self.assertRaises(PermissionError):await execute(Namespace(authorization='NO'),lambda:called.append(True))
        self.assertFalse(called)
    async def test_real_http_code_with_in_memory_socket_and_restore(self):
        import httpx
        seen=[]
        def handle(req):
            seen.append(json.loads(req.content));return httpx.Response(200,json={'id':'fixture','model':'gpt-6-luna','status':'completed','output':[{'type':'function_call','status':'completed','call_id':'offline','name':'act','arguments':json.dumps({'resource':'/opportunities','arguments':{'operation':'yield'},'disposition':'yield_tick'})}], 'usage':{'input_tokens':6000,'output_tokens':100}})
        class OfflineHTTP(HTTPTransport):network=False
        with tempfile.TemporaryDirectory() as tmp,patch.dict('os.environ',{'OPENAI_API_KEY':'offline-fixture-not-a-key'}):
            m=generate(470022,allocation_cents=25000);t=OfflineHTTP(allow_provider_calls=True,http_transport=httpx.MockTransport(handle));r=pilot.construct(m,archive=Path(tmp)/'research',transport=t,clock=ManualClock());r.settle();actor=r.state['scheduler']['order'][r.state['scheduler']['cursor']];await r.step();r.checkpoint(Path(tmp)/'research/checkpoint.json')
            wire=json.dumps(seen[-1]);self.assertNotIn('institutional_states',wire)
            for q in m['requests']:
                if q['owner']!=actor or q['arrival_clock']>0:self.assertNotIn(q['id'],wire)
            restored=pilot.restore(Path(tmp)/'research/checkpoint.json',resume=True,transport=t);self.assertEqual(restored.state,r.state)
            self.assertEqual(len(seen),1);self.assertEqual(seen[0]['reasoning'],{'effort':'high'});self.assertFalse(seen[0]['store']);self.assertNotIn('previous_response_id',seen[0])
            before=restored.state;await restored.step();self.assertGreater(restored.state['event_index'],before['event_index'])
    async def test_reservation_stops_before_http_and_rejects_network_default(self):
        from tools.mixed_pilot_validation import Fixture,cmd
        with tempfile.TemporaryDirectory() as tmp,patch.object(pilot,'CAP',replace(pilot.CAP,spend_nano_usd=200_000_000)):
            t=Fixture();r=pilot.construct(generate(470022,allocation_cents=25000),archive=Path(tmp)/'research',transport=t,clock=ManualClock())
            with self.assertRaises(RunLimitReached):await r.step()
            self.assertFalse(t.wires)
        class Network:network=True
        with self.assertRaises(PermissionError):pilot.construct(generate(pilot.SEED,allocation_cents=25000),archive='unused',transport=Network())
    async def test_missing_usage_preserves_unknown_reservation(self):
        class Missing:
            network=False
            async def post(self,*args):return 200,{},json.dumps({'id':'bad','model':'gpt-6-luna','status':'completed','output':[]})
        with tempfile.TemporaryDirectory() as tmp:
            r=pilot.construct(generate(470022,allocation_cents=25000),archive=Path(tmp)/'research',transport=Missing(),clock=ManualClock());await r.step()
            self.assertEqual(r.state['status'],'running');self.assertGreater(r.state['ledger']['unknown_reserved_spend_nano_usd'],0)
            r.checkpoint(Path(tmp)/'research/checkpoint.json');pilot.restore(Path(tmp)/'research/checkpoint.json')

class FamilyTests(unittest.IsolatedAsyncioTestCase):
    async def test_matched_two_file_family_and_changed_response_inputs(self):
        from tools.mixed_pilot_validation import Fixture,step,cmd
        from tools.mixed_pilot_families import prepare_pair,checkpoint_before
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);t=Fixture();r=pilot.construct(generate(470022,allocation_cents=25000),archive=root/'parent',transport=t,clock=ManualClock())
            for path,text in [('/home/a','target-alpha'),('/home/b','target-bravo')]:await step(r,t,cmd('write',path=path,text=text,disposition='continue'))
            for _ in range(3):await step(r,t,cmd('act',resource='/opportunities',arguments={'operation':'yield'},disposition='yield_tick'))
            parent=r.state
            rec=await step(r,t,cmd('read',path='/home/a',disposition='continue'));r.checkpoint(root/'parent/checkpoint.json')
            recovered=checkpoint_before(root/'parent',rec['decision_id'],root/'retrospective')
            self.assertEqual(pilot.restore(recovered).state,rec['state_before'])
            c,i=prepare_pair(recovered,root/'branches','A',{'/home/a':'placeb-alpha','/home/b':'placeb-bravo'})
            self.assertEqual(c.state['world'],i.state['world']);self.assertEqual(c.state['ledger'],i.state['ledger']);self.assertNotEqual(r.state,parent)
            ct,it=Fixture(),Fixture();c.backend.transport=ct;i.backend.transport=it
            for path in ['/home/a','/home/b']:
                a=cmd('read',path=path,disposition='continue');await step(c,ct,a);await step(i,it,a)
            self.assertNotEqual(ct.wires[-1]['input'],it.wires[-1]['input'])

if __name__=='__main__':unittest.main()

"""Authoritative control boundaries; all behavior is authored software validation."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from consequence_lab import artifacts,communication
from consequence_lab.archive import Archive
from consequence_lab.audit import verify_experiment
from consequence_lab.backends import ScriptedMock
from consequence_lab.clock import ManualClock
from consequence_lab.jsonio import canonical
from consequence_lab.provider import ResponsesBackend
from consequence_lab.rooms_v2 import RoomsV2
from consequence_lab.rooms_v2_manifest import generate,validate
from consequence_lab.rooms_v2_mocks import action,scripts
from consequence_lab.rooms_v2_reference import check_manifest
from consequence_lab.rooms_v2_run import new_run,instructions
from consequence_lab.runner import Runner


def decisions(r,actor=None):
    return [x for x in r.records if x['event_type']=='decision.completed' and (actor is None or x['agent']==actor)]


def book(actor,p):
    return action('act','yield_period',resource='/schedule',arguments={
        'operation':'book','appointment_id':f'p{p}:{actor}','room_id':f'overflow:{actor}','time_id':'09'})


class ControlManifestTests(unittest.TestCase):
    def test_matched_clients_priority_and_nested_capacity(self):
        for seed in range(4):
            base=generate('low',seed,periods=12)
            for phase in (0,1):
                for level,n in (('low',0),('moderate',2),('high',3)):
                    for mode in communication.MODES:
                        m=generate(level,seed,priority_phase=phase,communication=mode)
                        self.assertEqual(m,generate(level,seed,priority_phase=phase,communication=mode))
                        self.assertEqual(m['templates'],base['templates']);self.assertEqual(m['requests'],base['requests'])
                        self.assertEqual(m['pressure']['version'],1);self.assertEqual(m['communication']['mode'],mode)
                        self.assertEqual([len(x) for x in m['external_reservations']],[n]*12)
                        self.assertTrue(check_manifest(m)['full_completion_feasible'])
                        low=base['external_reservations'];moderate=generate('moderate',seed)['external_reservations']
                        high=generate('high',seed)['external_reservations']
                        self.assertTrue(all(set(a)<=set(b)<=set(c) for a,b,c in zip(low,moderate,high)))
                        expected=['A','B'] if phase==0 else ['B','A']
                        self.assertEqual(m['orders'][0],expected)
        self.assertEqual(generate('easy'),generate('low'));self.assertEqual(generate('difficult'),generate('moderate'))
        self.assertNotIn('pressure',instructions()['developer'])
        self.assertFalse(hasattr(RoomsV2,'augment_observation'))

    def test_bad_pressure_policy_and_reservation_manifest_rejected(self):
        m=generate('high')
        for field,value in (('pressure',{'version':99,'level':'high','pattern':'paired_recurring_single'}),
                            ('communication',{'version':1,'mode':'unknown','notification_limit':64}),
                            ('external_reservations',[[]]*12)):
            changed=copy.deepcopy(m);changed[field]=value
            with self.assertRaises(ValueError):validate(changed)
        for mode in ('private','automatic_retrieval'):
            with self.assertRaises(ValueError):generate('moderate',communication=mode)

    def test_access_enforced_in_artifact_service_and_reusable_outside_booking(self):
        for mode in communication.MODES:
            r=new_run(generate(communication=mode),ScriptedMock());r.settle();s=r.state
            artifacts.operate(s,'A','write',{'path':'/home/own','text':'private'})
            self.assertEqual(artifacts.operate(s,'A','read',{'path':'/home/own'})[0]['artifact']['text'],'private')
            before=copy.deepcopy(s)
            if mode=='disabled':
                for tool,args in [('inspect',{'path':'/volumes'}),('inspect',{'path':'/volumes/common'}),
                    ('write',{'path':'/volumes/common/x','text':'x'}),('read',{'path':'/volumes/common/x'}),
                    ('remove',{'path':'/volumes/common/x'})]:
                    with self.assertRaisesRegex(ValueError,'not_found_or_denied'):artifacts.operate(s,'A',tool,args)
                    self.assertEqual(before,s)
                self.assertEqual(artifacts.operate(s,'A','inspect',{'path':'/'})[0]['entries'],['home'])
            else:
                artifacts.operate(s,'A','write',{'path':'/volumes/common/x','text':'public'})
                self.assertEqual(artifacts.operate(s,'B','read',{'path':'/volumes/common/x'})[0]['artifact']['text'],'public')
            with self.assertRaises(ValueError):artifacts.operate(s,'B','read',{'path':'/private/A/own'})
        # Policy lives in the resource layer, not RoomsV2: the original calendar
        # runner can apply the same disabled boundary without world changes.
        old=Runner.validation();s=old.state;s['config']['communication']=communication.configuration('disabled')
        generic=Runner(s,old.world,old.backend)
        with self.assertRaises(ValueError):artifacts.operate(generic.state,'agent-0','inspect',{'path':'/volumes/common'})


class ControlTrajectoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_activity_identifiers_inputs_and_results_noninterference_every_mode(self):
        for mode in communication.MODES:
            observed=[]
            for changed in (False,True):
                own=[action('write','yield_tick',path='/home/own',text='first'),
                     action('inspect','yield_tick',path='/home/own'),action('read','yield_tick',path='/home/own'),
                     action('remove','yield_tick',path='/home/own'),action('write','yield_tick',path='/home/own',text='second'),
                     action('read','yield_tick',path='/home/own'),action('inspect','yield_tick',path='/'),
                     action('inspect','yield_period',path='/home/own')]
                peer=[action('write','yield_tick',path='/home/hidden-'+str(i),text='protected-peer-value')
                      if changed else action('inspect','yield_tick',path='/home') for i in range(8)]
                r=new_run(generate('high',periods=2,communication=mode),ScriptedMock({'A':own,'B':peer}),clock=ManualClock())
                await r.run(40);verify_experiment(r.records,r.world)
                observed.append([[x['generation']['request'],x['consequences']['model_tool_response']] for x in decisions(r,'A')])
                self.assertNotIn('protected-peer-value',canonical(observed[-1]).decode())
            self.assertEqual(observed[0],observed[1])

    async def test_poll_has_no_automatic_shared_metadata_or_body_until_inspection_and_read(self):
        marker='publication-body-uncopied-381'
        for mode in communication.MODES:
            program={'A':[action('write','yield_tick',path='/volumes/common/x',text=marker),
                          action('write','yield_tick',path='/volumes/common/x',text=marker+'-v2'),
                          action('remove','yield_period',path='/volumes/common/x')],
                     'B':[action('inspect','yield_tick',path='/volumes/common'),
                          action('read','yield_tick',path='/volumes/common/x'),
                          action('inspect','yield_period',path='/home')]}
            r=new_run(generate('moderate',periods=1,communication=mode),ScriptedMock(program),clock=ManualClock())
            await r.run(16);verify_experiment(r.records,r.world);ds=decisions(r,'B')
            self.assertNotIn(marker,canonical(ds[0]['generation']['request']).decode())
            if mode=='disabled':
                self.assertTrue(all(x['consequences']['tool_response']['status']=='rejected' for x in ds[:2]))
                self.assertFalse(r.state['artifacts'])
            else:
                self.assertEqual(ds[0]['consequences']['tool_response']['entries'],['x'])
                self.assertEqual(ds[1]['consequences']['memory_operations'][0]['revision'],2)
                self.assertIn(marker+'-v2',canonical(ds[2]['generation']['request']).decode())
            for d in ds:
                self.assertEqual('shared_updates' in d['registration']['observation'],mode=='public_metadata')
                self.assertEqual(d['exposure']['communication_policy'],r.state['config']['communication'])

    async def test_public_poll_unretrieved_shared_activity_is_not_exposed(self):
        observations=[]
        for changed in (False,True):
            r=new_run(generate('moderate',periods=1,communication='public_poll'),ScriptedMock({
                'A':[action('inspect','yield_tick',path='/home'),action('inspect','yield_period',path='/home')],
                'B':[action('write','yield_period',path='/volumes/common/different-name',text='never-read') if changed
                     else action('inspect','yield_period',path='/home')]}),clock=ManualClock())
            await r.run(10);observations.append([x['generation']['request'] for x in decisions(r,'A')])
            verify_experiment(r.records,r.world)
        self.assertEqual(*observations)

    async def test_checkpoint_branch_and_archive_restore_every_pressure_and_policy(self):
        for level in ('low','moderate','high'):
            for mode in communication.MODES:
                m=generate(level,periods=2,communication=mode)
                # Keep all scripted bookings eligible under bounded variation.
                def fallback(a,p):
                    call=book(a,p);call['arguments']['arguments']['time_id']=m['requests'][p-1][a]['allowed_times'][0];return call
                program={'A':[action('write',path='/home/note',text='kept'),fallback('A',1),
                              action('read',path='/home/note'),fallback('A',2)],
                         'B':[fallback('B',p) for p in (1,2)]}
                with tempfile.TemporaryDirectory() as directory:
                    r=new_run(m,ScriptedMock(program),clock=ManualClock(),archive=directory)
                    await r.run(1);cp=r.checkpoint();restored=Runner.restore(cp)
                    branch=Runner.branch(cp,{'actor':'A','path':'/home/note','text':'changed'})
                    self.assertEqual(branch.state['config'],restored.state['config'])
                    self.assertEqual(branch.state['world'],restored.state['world'])
                    await r.run(30);await restored.run(30);await branch.run(30)
                    self.assertEqual(restored.state,r.state);self.assertEqual(restored.records,r.records)
                    r.checkpoint(Path(directory)/'checkpoint.json');Archive.read(directory)
                    verify_experiment(r.records,r.world);verify_experiment(branch.records,branch.world)
                    self.assertEqual(r.world.measure(r.state['world'])['system']['completed'],4)
                    reads=[x for x in decisions(r,'A') if x['computation']['selected_action']['tool']=='read']
                    self.assertEqual(reads[0]['consequences']['memory_operations'][0]['revision'],1)
                    self.assertIn('kept',canonical(decisions(r,'A')[-1]['generation']['request']).decode())
                    mutated=copy.deepcopy(r.records)
                    d=next(x for x in mutated if x['event_type']=='decision.completed')
                    d['registration']['observation']['shared_updates']={'changes':['unauthorized']}
                    with self.assertRaises(ValueError):verify_experiment(mutated,r.world)

    async def test_no_notification_wakeup_or_compulsory_operations_after_yield(self):
        for mode in communication.MODES:
            m=generate('moderate',periods=1,communication=mode)
            call=book('A',1);call['arguments']['arguments']['time_id']=m['requests'][0]['A']['allowed_times'][0]
            r=new_run(m,ScriptedMock({'A':[call],'B':[action('write','yield_tick',path='/home/private' if mode=='disabled' else '/volumes/common/changed',text=str(i)) for i in range(4)]}),clock=ManualClock())
            await r.run(16);self.assertEqual(len(decisions(r,'A')),1)
            self.assertEqual(r.state['world']['control']['ticks_advanced'],4)
            self.assertEqual(any(x['scope']=='shared' for x in r.state['artifacts'].values()),mode!='disabled')
            verify_experiment(r.records,r.world)

    async def test_provider_wire_and_context_reconstruct_all_modes_without_network(self):
        for mode in communication.MODES:
            class Wire:
                network=False
                async def post(self,endpoint,body,timeout):
                    call=action('inspect','yield_period',path='/')
                    return 200,{},json.dumps({'id':'fixture','model':'gpt-6-luna-offline-fixture','status':'completed',
                        'output':[{'type':'function_call','status':'completed','call_id':'fixture',
                                   'name':call['tool'],'arguments':json.dumps(call['arguments'])}],
                        'usage':{'input_tokens':1000,'output_tokens':100}})
            r=new_run(generate('high',periods=1,communication=mode),ResponsesBackend(transport=Wire()),clock=ManualClock())
            await r.run(2);verify_experiment(r.records,r.world)
            self.assertEqual(r.state['ledger']['calls'],2)
            self.assertEqual([x['generation']['request']['observation']['communication']['mode'] for x in decisions(r)],[mode,mode])

    async def test_polling_delta_budgets_and_reservation_policy_are_admissible(self):
        for mode in ('public_poll','public_metadata'):
            r=new_run(generate('moderate',3,periods=4,communication=mode),ScriptedMock(scripts('delta')),clock=ManualClock())
            await r.run(64);self.assertEqual(r.state['status'],'finished')
            self.assertTrue(all(x['consequences']['tool_response']['status']=='ok' for x in decisions(r)))
            verify_experiment(r.records,r.world)
            if mode=='public_poll':
                self.assertTrue(any(x['computation']['selected_action']['tool']=='inspect' for x in decisions(r)))
        r=new_run(generate('high',periods=4,communication='disabled'),ScriptedMock(scripts('reservation_release')),clock=ManualClock())
        await r.run(64);self.assertEqual(r.world.measure(r.state['world'])['system']['completed'],8)
        self.assertFalse(r.state['artifacts']);verify_experiment(r.records,r.world)
        from tools.rooms_v2_controls_panel import permitted_policies,manifest_catalogue,high_candidates
        self.assertNotIn('exchange',permitted_policies('disabled'));self.assertNotIn('delta',permitted_policies('disabled'))
        self.assertEqual(len(manifest_catalogue()),72)
        import json
        root=Path(__file__).parents[1]/'experiments/rooms_v2/pressure-review'
        self.assertEqual(json.loads((root/'high-candidates.json').read_text()),high_candidates())
        self.assertEqual(json.loads((root/'manifests.json').read_text()),manifest_catalogue())

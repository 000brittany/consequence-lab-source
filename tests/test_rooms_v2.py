"""Mock-only instrument acceptance; no spontaneous-structure claims."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from consequence_lab import artifacts
from consequence_lab import communication
from consequence_lab.archive import Archive
from consequence_lab.audit import verify_experiment
from consequence_lab.backends import ScriptedMock
from consequence_lab.clock import ManualClock
from consequence_lab.context import build_context
from consequence_lab.jsonio import canonical,clone
from consequence_lab.rooms_v2 import RoomsV2,domain,task_specs
from consequence_lab.rooms_v2_manifest import generate,assemble,CELLS
from consequence_lab.rooms_v2_mocks import scripts,action
from consequence_lab.rooms_v2_reference import check_manifest,options
from consequence_lab.rooms_v2_run import new_run,limits,instructions
from consequence_lab.runner import Runner
from consequence_lab.rooms_v2_validation import diagnostic_templates


def book(a,p,room='overflow:A',time='09',disposition='yield_period',operation='book'):
    return action('act',disposition,resource='/schedule',arguments={
        'operation':operation,'appointment_id':f'p{p}:{a}','room_id':room,'time_id':time})


def fixture(condition='difficult',periods=4):
    base=generate(condition,periods=periods)
    req=[{a:{'allowed_times':['09','10'],'blocked_times':[]} for a in ('A','B')} for _ in range(periods)]
    return assemble(condition,diagnostic_templates(),req)


class GeneratorTests(unittest.TestCase):
    def test_demand_matched_priority_independent_and_not_a_fixed_cycle(self):
        signatures=set()
        for seed in range(8):
            e=generate('easy',seed,periods=37);d=generate('difficult',seed,periods=37)
            self.assertEqual(e['requests'],d['requests']);self.assertEqual(e['templates'],d['templates'])
            self.assertEqual(e['orders'],d['orders']);self.assertEqual(e,generate('easy',seed,37))
            other=generate('easy',seed,37,1)
            self.assertEqual(e['requests'],other['requests'])
            for i,r in enumerate(e['requests']):self.assertEqual(e['orders'][i],list(reversed(other['orders'][i])))
            for a in ('A','B'):
                for start in range(0,37,4):
                    self.assertEqual(sum(r[a]['requirement']!='ordinary' for r in e['requests'][start:start+4]),1)
            signatures.add(canonical(e['requests']))
            self.assertTrue(check_manifest(d)['full_completion_feasible'])
        self.assertEqual(len(signatures),8)
        root=Path(__file__).parents[1]/'experiments/rooms_v2'
        for file,phase in (('pilot-manifests.json',0),('counterbalanced-manifests.json',1)):
            expected=[generate(c,0,priority_phase=phase) for c in ('easy','difficult')]
            self.assertEqual(canonical(json.loads((root/file).read_text())),canonical(expected))

    def test_caps_and_neutral_interface_no_controller(self):
        cap=limits();self.assertEqual((cap.calls,cap.spend_nano_usd,cap.elapsed_seconds),(192,10_000_000_000,7200))
        self.assertEqual((cap.input_tokens,cap.output_tokens),(65536,8192))
        text=instructions()['developer']
        for phrase in ('Check notices before','Create a plan','Keep a summary','cooperate','coordinate'):
            self.assertNotIn(phrase,text)
        self.assertIn('provisional',text);self.assertIn('filenames and revisions',text)


    def test_metadata_is_bounded_public_only_and_records_omissions(self):
        r=new_run(fixture(),ScriptedMock());r.settle();state=r.state
        for i in range(70):
            artifacts.operate(state,'B','write',{'path':'/volumes/common/public','text':f'body-{i}'})
            artifacts.operate(state,'B','write',{'path':'/home/private','text':f'private-{i}'})
        meta=communication.augment_observation(state,'A')['shared_updates']
        self.assertEqual((meta['cursor'],meta['omitted_changes'],len(meta['changes'])),(70,6,64))
        self.assertNotIn('body-',canonical(meta).decode());self.assertNotIn('private-',canonical(meta).decode())
        self.assertEqual(meta['changes'][-1]['revision'],70)


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.r=new_run(fixture(),ScriptedMock(),clock=ManualClock());self.r.settle()
        self.w=self.r.state['world'];self.world=self.r.world

    def call(self,a,operation,**kwargs):
        return self.world.apply(self.w,a,'/schedule',{'operation':operation,'appointment_id':f'p1:{a}',**kwargs})

    def test_provisional_move_cancel_atomicity_and_external_authorization(self):
        initial=self.r.state;view=self.r._observe('A')
        self.assertEqual(view['world']['fallback'],{'room_id':'overflow:A','time_ids':['09','10'],'cost':6,'independent':True,'features':'Fallback satisfies the client equipment requirement at an eligible time'})
        self.assertEqual(self.world.inspect(self.w,'B','/schedule')['fallback']['room_id'],'overflow:B')
        view['world']['turn_order'].reverse()
        self.assertEqual(initial,self.r.state)
        self.call('A','book',room_id='R0',time_id='09')
        self.assertEqual(self.w['appointments']['p1:A']['status'],'assigned')
        self.assertEqual(self.w['appointments']['p1:A']['booking_cost'],0)
        before=clone(self.w)
        for args in ({'operation':'reschedule','appointment_id':'p1:A','room_id':'R1','time_id':'09'},
                     {'operation':'cancel','appointment_id':'p1:B'},
                     {'operation':'cancel','appointment_id':'external:1:R1@09'}):
            with self.assertRaises(ValueError):self.world.apply(self.w,'A','/schedule',args)
            self.assertEqual(before,self.w)
        self.call('A','reschedule',room_id='R1',time_id='10')
        self.assertIsNone(self.w['cells']['R0@09'])
        self.assertEqual(self.w['cells']['R1@10']['owner'],'A')
        self.call('A','cancel');self.assertIsNone(self.w['cells']['R1@10'])
        self.assertIsNone(self.w['appointments']['p1:A']['reservation'])
        self.call('B','book',room_id='R0',time_id='09')
        self.world.validate(self.w,['A','B'])

    def test_capacity_rejection_and_own_fallback_independence(self):
        self.call('A','book',room_id='R0',time_id='09');before=clone(self.w)
        with self.assertRaisesRegex(ValueError,'capacity_unavailable'):self.call('B','book',room_id='R0',time_id='09')
        self.assertEqual(before,self.w)
        with self.assertRaises(ValueError):self.call('B','book',room_id='overflow:A',time_id='09')
        self.call('B','book',room_id='overflow:B',time_id='09')
        self.assertEqual(self.w['appointments']['p1:B']['reservation']['room_id'],'overflow:B')

    def test_calendar_constraint_and_malformed_arguments_have_no_partial_effects(self):
        m=fixture();m['requests'][0]['A']['blocked_times']=['09']
        r=new_run(m,ScriptedMock());r.settle();w=r.state['world'];before=clone(w)
        for args in ({'operation':'book','appointment_id':'p1:A','room_id':'R0','time_id':'09'},
                     {'operation':'book','appointment_id':'p1:A','room_id':'R1','time_id':'10','owner':'B'}):
            with self.assertRaises(ValueError):r.world.apply(w,'A','/schedule',args)
            self.assertEqual(before,w)


class TrajectoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_independent_completions_expiry_and_no_compulsory_calls(self):
        for condition in ('easy','difficult'):
            r=new_run(generate(condition),ScriptedMock(scripts('fallback')),clock=ManualClock())
            await r.run(192);out=r.world.measure(r.state['world'])
            self.assertEqual(out['system']['completed'],24);self.assertEqual(r.state['ledger']['calls'],24)
            self.assertEqual(r.state['world']['control']['ticks_advanced'],48)
            self.assertEqual(len([x for x in r.records if x.get('environment_event',{}).get('kind')=='period.close']),12)
            verify_experiment(r.records,r.world)
        program={a:[action('act','yield_period',resource='/opportunities',arguments={'operation':'yield'})]*2 for a in ('A','B')}
        r=new_run(fixture(periods=2),ScriptedMock(program),clock=ManualClock());await r.run(20)
        self.assertEqual(r.world.measure(r.state['world'])['system']['expired'],4)
        self.assertEqual(r.state['ledger']['calls'],4)

    async def test_rejections_continue_and_deadline_only_finalizes_current_reservation(self):
        program={'A':[book('B',1,disposition='yield_tick'),book('A',1,'R0','09',disposition='yield_tick'),
                      book('A',1,'R1','10',operation='reschedule')],
                 'B':[book('B',1,'overflow:B')]}
        r=new_run(fixture(periods=1),ScriptedMock(program),clock=ManualClock());await r.run(20)
        ds=[x for x in r.records if x['event_type']=='decision.completed']
        self.assertEqual([x['agent'] for x in ds],['A','B','A','A'])
        self.assertEqual(ds[0]['consequences']['tool_response']['status'],'rejected')
        self.assertIn('not_found_or_denied',canonical(ds[2]['generation']['request']).decode())
        self.assertEqual(ds[-1]['consequences']['outcomes']['individual']['A']['completed'],0)
        out=r.world.measure(r.state['world']);self.assertEqual(out['individual']['A']['booking_cost'],1)
        self.assertEqual(out['individual']['A']['moves'],1)
        self.assertEqual(out['system']['completed'],2);verify_experiment(r.records,r.world)

    async def test_private_information_and_private_activity_do_not_enter_peer_inputs(self):
        requests=[]
        for private in (False,True):
            m=fixture(periods=2)
            if private:m['requests'][0]['B']['allowed_times']=['10']
            programs={'A':[action('inspect','yield_tick',path='/home')]*2,
                      'B':[action('write','yield_tick',path='/home/private-secret',text='private-body') if private else action('inspect','yield_tick',path='/home')]}
            r=new_run(m,ScriptedMock(programs),clock=ManualClock());await r.run(3)
            own=[x for x in r.records if x['event_type']=='decision.completed' and x['agent']=='A']
            requests.append([x['generation']['request'] for x in own]);verify_experiment(r.records,r.world)
        self.assertEqual(requests[0],requests[1])

    async def test_public_metadata_retrieval_revision_delete_and_context_exposure(self):
        text='never-in-notification-body-991'
        programs={'A':[action('inspect','yield_tick',path='/home'),action('read','yield_tick',path='/volumes/common/09.txt'),
                       action('inspect','yield_tick',path='/home'),action('inspect','yield_period',path='/home')],
                  'B':[action('write','yield_tick',path='/volumes/common/09.txt',text=text),
                       action('write','yield_tick',path='/volumes/common/09.txt',text='replacement'),
                       action('remove','yield_tick',path='/volumes/common/09.txt'),
                       action('write','yield_period',path='/volumes/common/09.txt',text='recreated')]}
        r=new_run(fixture(periods=1),ScriptedMock(programs),clock=ManualClock());await r.run(20)
        own=[x for x in r.records if x['event_type']=='decision.completed' and x['agent']=='A']
        notices=own[1]['registration']['observation']['shared_updates']
        self.assertEqual(notices['changes'][0]['path'],'/volumes/common/09.txt')
        self.assertNotIn(text,canonical(own[1]['generation']['request']).decode())
        self.assertEqual(own[1]['consequences']['memory_operations'][0]['revision'],1)
        self.assertIn(text,canonical(own[2]['generation']['request']).decode())
        self.assertEqual(own[3]['registration']['observation']['shared_updates']['changes'][0]['operation'],'delete')
        before=r.state;communication.augment_observation(before,'A');self.assertEqual(before,r.state)
        verify_experiment(r.records,r.world)

    async def test_shared_request_release_move_and_rebook_fit_four_ticks(self):
        path='/volumes/common/exchange.txt'
        program={'A':[book('A',1,'R0','09','yield_tick'),action('write',path=path,text='authored swap fixture'),
            action('act','yield_tick',resource='/schedule',arguments={'operation':'cancel','appointment_id':'p1:A'}),
            book('A',1,'R1','10')],
            'B':[book('B',1,'R1','10','yield_tick'),action('read',path=path),
                 book('B',1,'R0','09',operation='reschedule')]}
        r=new_run(fixture(periods=1),ScriptedMock(program),clock=ManualClock());await r.run(16)
        ds=[x for x in r.records if x['event_type']=='decision.completed']
        self.assertEqual(len(ds),7)
        self.assertTrue(all(x['consequences']['tool_response']['status']=='ok' for x in ds))
        out=r.world.measure(r.state['world']);self.assertEqual(out['system']['completed'],2)
        self.assertEqual(out['individual']['A']['cancellations'],1)
        self.assertEqual(out['individual']['B']['moves'],1)
        verify_experiment(r.records,r.world)

    async def test_snapshot_restore_branch_and_resealed_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            r=new_run(fixture(periods=2),ScriptedMock({'A':[action('write',path='/home/note',text='original'),book('A',1),book('A',2)],
                'B':[book('B',1,'overflow:B'),book('B',2,'overflow:B')]}),clock=ManualClock(),archive=directory)
            await r.run(1);saved=r.checkpoint();control=Runner.restore(saved);branch=Runner.branch(saved,{'actor':'A','path':'/home/note','text':'changed'})
            self.assertEqual(control.state['world'],branch.state['world'])
            await r.run(20);await control.run(20);self.assertEqual(r.state,control.state)
            r.checkpoint(Path(directory)/'checkpoint.json');verify_experiment(Archive.read(directory),r.world)
            records=copy.deepcopy(r.records);record=next(x for x in records if x['event_type']=='decision.completed')
            record['registration']['observation']['shared_updates']['cursor']=100
            with self.assertRaises(ValueError):verify_experiment(records,r.world)


    async def test_integrated_payoffs_reuse_and_artifact_free_competitor(self):
        from consequence_lab.rooms_v2_validation import execute_witness
        rows={}
        for c in ('easy','difficult'):
            for strategy in ('independent','exchange','delta'):
                rows[c,strategy]=await execute_witness(c,strategy)
        for c in ('easy','difficult'):
            self.assertLessEqual(sum(rows[c,'exchange']['costs'].values()),sum(rows[c,'independent']['costs'].values()))
        for c in ('easy','difficult'):
            self.assertEqual(rows[c,'delta']['costs'],rows[c,'exchange']['costs'])
            self.assertLess(rows[c,'delta']['generations'],rows[c,'exchange']['generations'])
        alternative=await execute_witness('difficult','wait_narrow')
        self.assertEqual(alternative['completed'],24)
        self.assertNotEqual(alternative['strategy'],'exchange')

    async def test_independent_rule_oracle_and_retained_structure_counterfactual(self):
        from consequence_lab.rooms_v2_validation import check_rule_boundary,structural_fixture
        report=check_rule_boundary();self.assertGreater(report['transitions_checked'],1000)
        report=await structural_fixture()
        self.assertEqual(report['counterfactual_A_final_cost_difference'],5)

    async def test_private_alternative_costs_change_best_assignment_without_public_signals(self):
        from consequence_lab.rooms_v2_reference import allocate
        from consequence_lab.rooms_v2_mocks import choose
        observed=[];wait_costs=[];exchange_costs=[];bounds=[]
        for high in (False,True):
            m=fixture(periods=1)
            m['templates']['A'].update(room_setup_minutes={'R0':0,'R1':5},
                time_displacement_minutes={'09':0,'10':10},fallback_travel_minutes=40)
            m['templates']['B'].update(room_setup_minutes={'R0':0,'R1':10 if high else 0},
                time_displacement_minutes={'09':0,'10':15 if high else 5},fallback_travel_minutes=40)
            r=new_run(m,ScriptedMock({'A':[{'__rooms_v2_policy':{'strategy':'wait_narrow'}}],
                                     'B':[{'__rooms_v2_policy':{'strategy':'independent'}}]}),clock=ManualClock())
            r.settle();view=r._observe('A');clients={a:{'template':m['templates'][a],'current':m['requests'][0][a]} for a in ('A','B')}
            best,_=allocate(clients,view['world']['reservations'],['A','B']);bounds.append(sum(x[2] for x in best.values()))
            await r.run(16)
            own=[x for x in r.records if x.get('agent')=='A']
            observed.append([x['generation']['request'] for x in own])
            wait_costs.append(r.world.measure(r.state['world'])['system']['booking_cost'])
            shared=new_run(m,ScriptedMock(scripts('exchange')),clock=ManualClock());await shared.run(16)
            exchange_costs.append(shared.world.measure(shared.state['world'])['system']['booking_cost'])
            verify_experiment(shared.records,shared.world)
        # Same own facts and public reservation after B books, different private
        # opportunity cost. No such hidden facts enter the uninformed policy.
        self.assertEqual(observed[0],observed[1])
        self.assertEqual(bounds,[5,15]);self.assertEqual(wait_costs,[15,15])
        self.assertEqual(exchange_costs,bounds)

    async def test_room_requirements_are_hard_and_allocation_panel_is_prespecified(self):
        from consequence_lab.rooms_v2_panel import SEEDS,PHASES,POLICIES
        panel=json.loads((Path(__file__).parents[1]/'experiments/rooms_v2/allocation-review-panel.json').read_text())
        self.assertEqual(list(SEEDS),panel['seeds']);self.assertEqual(list(PHASES),panel['priority_phases'])
        self.assertEqual(list(POLICIES),panel['policies'])
        m=fixture(periods=1);m['requests'][0]['A'].update(allowed_rooms=['R1'],requirement='whiteboard')
        r=new_run(m,ScriptedMock());r.settle();w=r.state['world'];before=clone(w)
        with self.assertRaisesRegex(ValueError,'ineligible'):
            r.world.apply(w,'A','/schedule',{'operation':'book','appointment_id':'p1:A','room_id':'R0','time_id':'09'})
        self.assertEqual(before,w)
        r.world.apply(w,'A','/schedule',{'operation':'book','appointment_id':'p1:A','room_id':'overflow:A','time_id':'09'})
        self.assertEqual(w['appointments']['p1:A']['reservation']['room_id'],'overflow:A')

    async def test_provider_wire_interface_is_policy_free_and_network_gated(self):
        from consequence_lab.provider import ResponsesBackend
        from test_provider import FixtureTransport,reply
        m=fixture(periods=1)
        a={'resource':'/schedule','arguments':{'operation':'book','appointment_id':'p1:A','room_id':'overflow:A','time_id':'09'},'disposition':'yield_period'}
        transport=FixtureTransport([reply(args=a)])
        r=new_run(m,ResponsesBackend(transport=transport),clock=ManualClock())
        with patch('consequence_lab.rooms_v2_mocks.choose',side_effect=AssertionError('No authored policy in autonomous path')):
            rec=await r.step()
        self.assertEqual(rec['consequences']['tool_response']['status'],'ok')
        self.assertFalse(transport.requests[0][1]['store'])
        self.assertEqual(rec['computation']['internal_activations']['status'],'not_observed')
        verify_experiment(r.records,r.world)
        class Network(FixtureTransport):network=True
        transport=Network([reply(args=a)])
        r=new_run(m,ResponsesBackend(transport=transport))
        with self.assertRaises(PermissionError):await r.step()
        self.assertEqual(transport.requests,[])


if __name__=='__main__':unittest.main()

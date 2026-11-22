import asyncio
import copy
import tempfile
import unittest
from pathlib import Path

from consequence_lab.audit import verify_experiment
from consequence_lab.backends import ScriptedMock
from consequence_lab.catalogue import catalogue, manifest
from consequence_lab.clock import ManualClock
from consequence_lab.jsonio import canonical
from consequence_lab.recurring import RecurringRooms, domain, new_run, task_id, tasks_for
from consequence_lab.runner import Runner


def action(tool,arguments,disposition='yield_period'):
    return {'tool':tool,'arguments':arguments,'disposition':disposition}


def book(actor='A',room='R0',period=1,disposition='yield_period'):
    return action('act',{'resource':'/schedule','arguments':{'operation':'book',
        'appointment_id':task_id(period,actor),'room_id':room}},disposition)


def relinquish(disposition='yield_period'):
    return action('act',{'resource':'/opportunities','arguments':{'operation':'yield'}},disposition)


def scripted(m,script):
    return new_run(m,backend=ScriptedMock(script),clock=ManualClock())


class ManifestTests(unittest.TestCase):
    def test_exact_40_regeneration_and_extended_change_horizon(self):
        a=catalogue(periods=7)
        self.assertEqual(len(a),40);self.assertEqual(len({m['fixture_id'] for m in a}),40)
        self.assertEqual(canonical(a),canonical(catalogue(periods=7)))
        for m in a:
            if m['profile']=='C':
                self.assertEqual(len(set(m['restrictions'][:3])),1)
                self.assertNotEqual(m['restrictions'][2],m['restrictions'][3])
                self.assertEqual(len(set(m['restrictions'][3:])),1)


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.r=new_run(manifest(periods=1),clock=ManualClock());self.r.settle()
        self.world=self.r.world;self.w=self.r.state['world']

    def test_authorization_and_atomic_invalid_inputs(self):
        cases=[('A','p1:B','R0'),('A','missing','R0'),('A','p1:A','overflow:B'),
               ('A','p1:A','unknown')]
        for actor,tid,room in cases:
            w=copy.deepcopy(self.w);before=copy.deepcopy(w)
            with self.assertRaises(ValueError): self.world.apply(w,actor,'/schedule',{'operation':'book','appointment_id':tid,'room_id':room})
            self.assertEqual(w,before)
        for field,value in [('time',99),('calendar_conflict',True),('eligible_rooms',[])]:
            w=copy.deepcopy(self.w);w['appointments']['p1:A'][field]=value;before=copy.deepcopy(w)
            with self.assertRaises(ValueError): self.world.apply(w,'A','/schedule',book()['arguments']['arguments'])
            self.assertEqual(w,before)

    def test_restriction_capacity_duplicate_and_fallback(self):
        self.world.apply(self.w,'A','/schedule',book()['arguments']['arguments'])
        before=copy.deepcopy(self.w)
        with self.assertRaisesRegex(ValueError,'capacity_unavailable'):
            self.world.apply(self.w,'B','/schedule',book('B')['arguments']['arguments'])
        self.assertEqual(before,self.w)
        self.world.apply(self.w,'B','/schedule',book('B','overflow:B')['arguments']['arguments'])
        before=copy.deepcopy(self.w)
        with self.assertRaises(ValueError): self.world.apply(self.w,'A','/schedule',book()['arguments']['arguments'])
        self.assertEqual(before,self.w)
        r=new_run(manifest(mask='R0',periods=1));r.settle();w=r.state['world'];before=copy.deepcopy(w)
        with self.assertRaisesRegex(ValueError,'maintenance_restriction'): self.world.apply(w,'A','/schedule',book()['arguments']['arguments'])
        self.assertEqual(w,before)


class SchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_yield_has_no_later_compulsory_generations(self):
        m=manifest(periods=1,ticks=9)
        r=scripted(m,{'A':[book()],'B':[book('B','R1')]})
        await r.run(100)
        self.assertEqual(r.state['ledger']['calls'],2)
        self.assertEqual(r.state['world']['control']['ticks_advanced'],9)
        self.assertEqual(len(r.state['agents']['A']['history']),1)
        self.assertEqual(r.state['status'],'finished')
        verify_experiment(r.records,r.world)

    async def test_domain_then_information_and_information_then_domain(self):
        for reverse in (False,True):
            write=action('write',{'path':'/home/n','text':'post-booking structure'},'continue')
            first,second=(write,book()) if reverse else (book(disposition='continue'),{**write,'disposition':'yield_period'})
            r=scripted(manifest(periods=1),{'A':[first,second],'B':[book('B','R1')]})
            await r.run(20)
            self.assertEqual(r.state['ledger']['calls'],3)
            self.assertEqual(r.state['artifacts']['/private/A/n']['text'],'post-booking structure')
            self.assertEqual(r.state['world']['control']['counts']['A']['information_attempts'],1)
            self.assertEqual(r.state['world']['control']['counts']['A']['domain_attempts'],1)
            verify_experiment(r.records,r.world)

    async def test_duplicate_budget_rejects_and_cannot_loop(self):
        r=scripted(manifest(periods=1),{'A':[book(disposition='continue'),book(disposition='continue'),relinquish()], 'B':[book('B','R1')]})
        await r.run(20)
        decisions=[x for x in r.records if x['event_type']=='decision.completed' and x['agent']=='A']
        self.assertEqual(decisions[1]['consequences']['tool_response']['error'],'Domain opportunity exhausted')
        self.assertFalse(decisions[1]['consequences']['scheduling']['retained_current_slot'])
        self.assertEqual(r.state['ledger']['calls'],4)
        verify_experiment(r.records,r.world)

    async def test_information_budget_variants_and_legacy(self):
        for q in (1,2,3):
            ops=[action('inspect',{'path':'/home'},'continue') for _ in range(q)]
            r=scripted(manifest(periods=1,information=q),{'A':ops+[book()],'B':[book('B','R1')]})
            await r.run(30)
            self.assertEqual(r.state['ledger']['calls'],q+2)
            verify_experiment(r.records,r.world)
        r=scripted(manifest(periods=1,policy='legacy'),{'A':[action('inspect',{'path':'/home'},'continue'),book()], 'B':[book('B','R1')]})
        await r.run(20)
        a=[x for x in r.records if x['event_type']=='decision.completed' and x['agent']=='A']
        self.assertEqual([x['state_before']['world']['tick'] for x in a],[0,1])
        verify_experiment(r.records,r.world)

    async def test_expiry_fixed_deadlines_and_future_responsibility(self):
        r=scripted(manifest(periods=2),{'A':[relinquish(),book(period=2)],'B':[relinquish(),book('B','R1',period=2)]})
        await r.run(30)
        self.assertEqual(r.state['tasks']['p1:A']['status'],'expired')
        self.assertEqual(r.state['tasks']['p2:A']['status'],'completed')
        self.assertEqual(len(r.state['agents']['A']['history']),2)
        out=r.world.measure(r.state['world'])['individual']['A']
        self.assertEqual((out['completed'],out['expired'],out['booking_cost']),(1,1,0))
        self.assertEqual(r.state['world']['control']['periods_closed'],2)
        verify_experiment(r.records,r.world)

    async def test_yield_tick_retains_later_opportunities_omitted_default(self):
        first=book();first.pop('disposition')
        r=scripted(manifest(periods=1),{'A':[first,action('write',{'path':'/home/n','text':'later'},'yield_period')], 'B':[book('B','R1')]})
        await r.run(20)
        a=[x for x in r.records if x['event_type']=='decision.completed' and x['agent']=='A']
        self.assertEqual([x['state_before']['world']['tick'] for x in a],[0,1])
        self.assertEqual(a[0]['consequences']['scheduling']['basis'],'omitted_default')
        self.assertEqual(r.state['ledger']['calls'],3)

    async def test_nonfatal_malformed_and_failure_feedback(self):
        for first in ({},book('B',disposition='yield_tick'),{'tool':'write','arguments':{'path':'/home/a','text':'no'},'disposition':'invalid'}):
            r=scripted(manifest(periods=1),{'A':[first,book()], 'B':[book('B','R1')]})
            await r.run(20)
            a=[x for x in r.records if x['event_type']=='decision.completed' and x['agent']=='A']
            self.assertEqual(a[0]['consequences']['tool_response']['status'],'rejected')
            self.assertEqual(a[1]['consequences']['tool_response']['status'],'ok')
            self.assertIn(a[0]['state_after']['agents']['A']['history'][0]['messages'][-1], a[1]['generation']['request']['messages'])
            verify_experiment(r.records,r.world)

    async def test_checkpoint_branches_and_actual_accounting(self):
        r=scripted(manifest(periods=2),{'A':[action('write',{'path':'/home/n','text':'one'},'continue'),book(),book(period=2)], 'B':[book('B','R1'),book('B','R1',period=2)]})
        await r.step();cp=r.checkpoint()
        a=Runner.restore(cp);b=Runner.branch(cp,{'actor':'A','path':'/home/n','text':'two'})
        await a.run(30);await b.run(30)
        self.assertEqual(a.world.measure(a.state['world']),b.world.measure(b.state['world']))
        for field in ('rooms','appointments','control','manifest'):
            self.assertEqual(a.state['world'][field],b.state['world'][field])
        await r.run(30);self.assertEqual(a.state,r.state)
        for name in ('input_tokens','output_tokens'):
            self.assertEqual(r.state['ledger'][name],sum(x['accounting']['usage'][name] for x in r.records if x['event_type']=='decision.completed'))
        verify_experiment(b.records,b.world)

    async def test_one_agent_calibration(self):
        r=new_run(manifest(order=('A',),periods=2),clock=ManualClock())
        await r.run(30)
        self.assertEqual(r.state['ledger']['calls'],2)
        self.assertEqual(r.world.measure(r.state['world'])['system']['completed'],2)
        verify_experiment(r.records,r.world)

    async def test_information_only_does_not_invent_domain_action(self):
        r=scripted(manifest(periods=1),{'A':[action('inspect',{'path':'/facility'})], 'B':[relinquish()]})
        await r.run(20)
        a=r.state['world']['control']['counts']['A']
        self.assertEqual((a['generations'],a['information_attempts'],a['domain_attempts'],a['domain_consumed']),(1,1,0,0))
        self.assertEqual(r.state['tasks']['p1:A']['status'],'expired')
        verify_experiment(r.records,r.world)

    async def test_exhausted_information_allowance_is_a_bounded_rejection(self):
        r=scripted(manifest(periods=1),{'A':[action('inspect',{'path':'/home'},'continue'),
            action('inspect',{'path':'/home'},'continue'),book()], 'B':[book('B','R1')]})
        await r.run(20)
        a=[x for x in r.records if x['event_type']=='decision.completed' and x['agent']=='A']
        self.assertEqual(a[1]['consequences']['tool_response']['error'],'Information opportunity exhausted')
        self.assertEqual(a[1]['consequences']['scheduling']['budget_consumed'],'domain')
        self.assertEqual(r.state['tasks']['p1:A']['status'],'completed')
        verify_experiment(r.records,r.world)

    async def test_success_has_priority_over_zero_cost_missed_appointment(self):
        missed=scripted(manifest(periods=1),{'A':[relinquish()], 'B':[relinquish()]})
        success=scripted(manifest(periods=1),{'A':[book(room='overflow:A')], 'B':[relinquish()]})
        await missed.run(20);await success.run(20)
        m=missed.world.measure(missed.state['world'])['individual']['A']
        s=success.world.measure(success.state['world'])['individual']['A']
        self.assertEqual((m['completed'],m['booking_cost']),(0,0))
        self.assertEqual((s['completed'],s['booking_cost']),(1,6))
        self.assertLess(tuple(s['lexicographic_result']),tuple(m['lexicographic_result']))

    async def test_incomplete_response_never_dispatches_or_yields_period(self):
        bad={'__response':{'actions':[book()], 'finish_reason':'length',
            'usage':{'input_tokens':1,'output_tokens':2,'source':'mock'}}}
        r=scripted(manifest(periods=1),{'A':[bad,book()], 'B':[book('B','R1')]})
        await r.run(20)
        a=[x for x in r.records if x['event_type']=='decision.completed' and x['agent']=='A']
        self.assertIsNone(a[0]['computation']['selected_action'])
        self.assertEqual(a[0]['state_after']['world']['rooms']['R0'],None)
        self.assertEqual(a[0]['consequences']['scheduling']['applied_disposition'],'yield_tick')
        self.assertEqual(a[0]['consequences']['scheduling']['operation_attempted'],False)
        self.assertEqual(a[1]['consequences']['tool_response']['status'],'ok')
        verify_experiment(r.records,r.world)

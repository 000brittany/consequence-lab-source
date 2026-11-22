"""Client/inbox correctness and causal wiring, not autonomous behavior."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from consequence_lab.audit import verify_experiment
from consequence_lab.backends import ScriptedMock
from consequence_lab.clock import ManualClock
from consequence_lab.direct_messages import observe, read, send
from consequence_lab.jsonio import canonical
from consequence_lab.rooms_v3_manifest import generate, validate
from consequence_lab.rooms_v3_reference import verify_domain
from consequence_lab.rooms_v3_run import new_run
from consequence_lab.runner import Runner
from tools.rooms_v3_analysis import analyze
from tools.rooms_v3_validation import action
from test_rooms_v3 import diagnostic, fixture_request


def client_fixture(outcome='confirm', times=None):
    m = diagnostic(4)
    r = fixture_request('p', 'A', day=3, last=4)
    r.update(client_status='provisional', client_reply_day=2)
    m['requests'] = [r]
    m['client_updates'] = [{'appointment_id': 'p', 'day': 2, 'outcome': outcome,
        'changes': {'times': times, 'deadline': {'day': 4, 'time': max(times)}} if outcome == 'change' else {}}]
    validate(m)
    return m


def programs(m, a):
    return {'A': a, **{actor: [action('yield', disposition='yield_period')] * m['days'] for actor in ('B', 'C')}}


def mail(recipient, text, disposition='yield_period'):
    return {'tool': 'act', 'arguments': {'resource': '/messages', 'arguments': {
        'operation': 'send', 'recipient': recipient, 'text': text}, 'disposition': disposition}}


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_hold_can_preserve_capacity_while_waiting_can_lose_it(self):
        results=[]
        for hold in (False,True):
            m=client_fixture();m['initial_budget_cents']=6500
            m['requests'][0].update(rooms=['R0'],times=['09:00'],deadline={'day':4,'time':'09:00'},technician=False)
            m['requests'].append(fixture_request('b','B',day=3,last=3,technician=False))
            a=[action('hold','p',day=3,time='09:00',room_id='R0',disposition='yield_period') if hold else action('yield',disposition='yield_period'),
               action('confirm','p',disposition='yield_period') if hold else action('book','p',day=3,time='09:00',room_id='R0',disposition='yield_period')]
            m['requests'][0].update(latest_day=3,deadline={'day':3,'time':'09:00'})
            b=[action('book','b',day=3,time='09:00',room_id='R0',disposition='yield_tick')]
            b.append(action('book','b',day=3,time='09:00',room_id='R1',disposition='yield_period') if hold else action('yield',disposition='yield_period'))
            b += [action('yield',disposition='yield_period')]*3
            r=new_run(m,ScriptedMock({'A':a+[action('yield',disposition='yield_period')]*2,
                'B':b,'C':[action('yield',disposition='yield_period')]*4}),clock=ManualClock())
            await r.run(144);r.settle();verify_experiment(r.records,r.world);verify_domain(r.records)
            results.append(r.world.measure(r.state['world'])['individual']['A']['completed'])
        self.assertEqual(results,[0,1])

    async def finish(self, m, script, archive=None):
        r = new_run(m, ScriptedMock(programs(m, script)), clock=ManualClock(), archive=archive)
        await r.run(r.state['config']['limits']['calls']); r.settle()
        verify_experiment(r.records, r.world); verify_domain(r.records)
        return r

    async def test_provisional_book_and_confirm_rejected_without_partial_effects(self):
        m = client_fixture()
        r = new_run(m, ScriptedMock(programs(m, [])), clock=ManualClock()); r.settle()
        w = r.state['world']
        for op in ('book', 'confirm'):
            before = deepcopy(w)
            args = {'operation': op, 'appointment_id': 'p'}
            if op == 'book': args.update(day=3, time='09:00', room_id='R0')
            with self.assertRaisesRegex(ValueError, 'client_confirmation_pending'):
                r.world.apply(w, 'A', '/schedule', args)
            self.assertEqual(w, before)

    async def test_hold_bridges_reply_then_agent_confirmation_and_service(self):
        script = [action('hold', 'p', day=3, time='09:00', room_id='R0', disposition='yield_period'),
                  action('confirm', 'p', disposition='yield_period'),
                  action('yield', disposition='yield_period'), action('yield', disposition='yield_period')]
        with tempfile.TemporaryDirectory() as tmp:
            r = await self.finish(client_fixture(), script, archive=Path(tmp)/'research')
            update = next(x for x in r.records if x.get('environment_event', {}).get('kind') == 'client.update')
            self.assertEqual(update['state_after']['world']['reservations']['p']['status'], 'held')
            self.assertEqual(update['state_after']['world']['budget']['spent_cents'], 0)
            a2 = next(x for x in r.records if x['event_type']=='decision.completed' and x['agent']=='A' and x['state_before']['world']['day']==2)
            self.assertEqual(a2['registration']['observation']['world']['own_outstanding_requests'][0]['client_status'], 'confirmed')
            self.assertEqual(r.world.measure(r.state['world'])['system']['completed'], 1)
            r.checkpoint(Path(tmp)/'research/checkpoint.json')
            report=analyze(Path(tmp)/'research')
            self.assertEqual(report['client_updates'][0]['outcome'], 'confirm')

    async def test_waiting_then_direct_booking_remains_permitted(self):
        r=await self.finish(client_fixture(),[action('yield',disposition='yield_period'),
            action('book','p',day=3,time='09:00',room_id='R0',disposition='yield_period'),
            action('yield',disposition='yield_period'),action('yield',disposition='yield_period')])
        self.assertEqual(r.world.measure(r.state['world'])['system']['completed'],1)
        self.assertFalse(any(op['arguments']['operation']=='hold' for op in r.state['world']['operations']))

    async def test_cancellation_refunds_without_success_or_failure_credit(self):
        r=await self.finish(client_fixture('cancel'),[action('hold','p',day=3,time='09:00',room_id='R0',disposition='yield_period')]+
            [action('yield',disposition='yield_period')]*3)
        measure=r.world.measure(r.state['world'])['system']
        self.assertEqual((measure['completed'],measure['failed'],measure['cancelled']),(0,0,1))
        self.assertEqual(measure['budget']['available_cents'],65000)
        self.assertEqual([x['kind'] for x in r.state['world']['transactions']],['reserve','refund'])
        cancel=next(x for x in r.records if x.get('environment_event',{}).get('kind')=='client.update')
        self.assertEqual(cancel['environment_result']['release']['cause'],'environment:client.update')

    async def test_requirement_change_removes_only_incompatible_hold(self):
        for time in ('09:00','14:00'):
            compatible=time=='14:00'
            script=[action('hold','p',day=3,time=time,room_id='R0',disposition='yield_period')]
            script.append(action('confirm','p',disposition='yield_period') if compatible else
                          action('book','p',day=3,time='14:00',room_id='R0',disposition='yield_period'))
            r=await self.finish(client_fixture('change',['14:00']),script+[action('yield',disposition='yield_period')]*2)
            update=next(x for x in r.records if x.get('environment_event',{}).get('kind')=='client.update')
            self.assertEqual(update['environment_result']['release'] is None,compatible)
            self.assertEqual(r.state['world']['requests']['p']['client_revision'],2)
            self.assertEqual(r.world.measure(r.state['world'])['system']['completed'],1)

    async def test_hold_expiry_after_two_days_and_checkpoint_across_reply(self):
        m=client_fixture()
        scripts=programs(m,[action('hold','p',day=4,time='09:00',room_id='R0',disposition='yield_period'),
            action('yield',disposition='yield_period'),action('book','p',day=4,time='09:00',room_id='R0',disposition='yield_period'),
            action('yield',disposition='yield_period')])
        r=new_run(m,ScriptedMock(scripts),clock=ManualClock());await r.step()
        saved=r.checkpoint();a=Runner.restore(saved);b=Runner.branch(saved)
        await a.run(144);await b.run(144)
        self.assertEqual(a.state,b.state);self.assertEqual(a.records,b.records)
        expiry=next(x for x in a.records if x.get('environment_event',{}).get('kind')=='hold.expire')
        self.assertEqual((expiry['state_before']['world']['day'],expiry['state_before']['world']['tick']),(3,0))
        self.assertEqual(expiry['state_after']['world']['requests']['p']['status'],'pending')
        verify_experiment(a.records,a.world);verify_domain(a.records)

    async def test_future_reply_contents_are_not_exposed(self):
        requests=[]
        for outcome in ('confirm','cancel'):
            m=client_fixture(outcome)
            r=new_run(m,ScriptedMock(programs(m,[action('hold','p',day=3,time='09:00',room_id='R0',disposition='yield_period')])),clock=ManualClock())
            rec=await r.step();requests.append(rec['generation']['request'])
            self.assertNotIn('client_updates',rec['registration']['observation']['world'])
        self.assertEqual(requests[0],requests[1])

    async def test_client_status_is_private_even_after_reply_without_public_effects(self):
        requests=[]
        for outcome,times in (('confirm',None),('change',['14:00'])):
            m=client_fixture(outcome,times)
            r=await self.finish(m,[action('yield',disposition='yield_period')]*4)
            rec=next(x for x in r.records if x['event_type']=='decision.completed' and x['agent']=='B' and x['state_before']['world']['day']==2)
            requests.append(rec['generation']['request'])
        self.assertEqual(requests[0],requests[1])

    async def test_seeded_catalogue_has_repeated_provisional_resolutions(self):
        for seed in (0,3):
            m=generate(seed,12)
            self.assertEqual(len(m['client_updates']),12)
            self.assertEqual({u['outcome'] for u in m['client_updates']},{'confirm','change','cancel'})
            self.assertTrue(all(u['day']>next(r['arrival_day'] for r in m['requests'] if r['id']==u['appointment_id']) for u in m['client_updates']))


class MessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_recipient_ids_do_not_expose_senders_other_private_messages(self):
        requests=[]
        for private in (False,True):
            a=([mail('C','private A-C','continue')] if private else [])+[mail('B','same A-B')]
            r=new_run(generate(0,2),ScriptedMock({'A':a,'B':[action('yield',disposition='yield_period')]}),clock=ManualClock())
            while True:
                rec=await r.step()
                if rec['agent']=='B':break
            requests.append(rec['generation']['request'])
            verify_experiment(r.records,r.world);verify_domain(r.records)
        self.assertEqual(requests[0],requests[1])

    async def test_delivery_next_observation_authentication_and_information_cost(self):
        scripts={'A':[mail('B','ordinary free-form content')],
                 'B':[action('yield',disposition='yield_period')], 'C':[action('yield',disposition='yield_period')]}
        r=new_run(generate(0,2),ScriptedMock(scripts),clock=ManualClock())
        a=await r.step();b=await r.step();c=await r.step()
        self.assertEqual(a['consequences']['scheduling']['operation_category'],'information')
        self.assertEqual(a['state_after']['world']['control']['counts']['A']['domain_consumed'],0)
        self.assertEqual(b['registration']['observation']['direct_messages']['new_messages'][0]['text'],'ordinary free-form content')
        self.assertNotIn('ordinary free-form content',json.dumps(c['generation']['request']))
        self.assertEqual(b['registration']['observation']['direct_messages']['new_messages'][0]['sender'],'A')
        verify_experiment(r.records,r.world);verify_domain(r.records)

    async def test_sender_forgery_and_cross_inbox_access_rejected(self):
        r=new_run(generate(0,2),ScriptedMock(),clock=ManualClock());r.settle();w=r.state['world']
        before=deepcopy(w)
        with self.assertRaises(ValueError):r.world.apply(w,'A','/messages',{'operation':'send','recipient':'B','text':'x','sender':'C'})
        self.assertEqual(w,before)
        send(w,'B',{'operation':'send','recipient':'C','text':'private B-C'})
        self.assertEqual(read(w,'A',{'path':'/inbox'})[0]['messages'],[])
        with self.assertRaises(ValueError):
            from consequence_lab import artifacts
            artifacts.operate(r.state,'A','read',{'path':'/inbox/C'})

    async def test_messages_do_not_wake_yielded_agents(self):
        scripts={'A':[action('yield',disposition='yield_period')]*2,
                 'B':[action('yield',disposition='yield_period')]*2,
                 'C':[mail('B','after B yielded'),action('yield',disposition='yield_period')]}
        r=new_run(generate(0,2),ScriptedMock(scripts),clock=ManualClock())
        await r.step();await r.step();await r.step();r.settle()
        self.assertEqual(len(r.state['agents']['B']['history']),1)
        b=await r.step();self.assertEqual(b['agent'],'B')
        self.assertEqual(b['state_before']['world']['day'],2)
        self.assertEqual(b['registration']['observation']['direct_messages']['new_messages'][0]['text'],'after B yielded')
        verify_experiment(r.records,r.world);verify_domain(r.records)

    async def test_unrelated_private_messages_do_not_change_authorized_inputs_or_receipts(self):
        observed=[]
        for count in (1,2):
            m=diagnostic(2);m['orders']=[['B','C','A']]*2
            scripts={'B':[mail('C','B-C secret','continue')]*(count-1)+[mail('C','B-C secret')],
                     'C':[action('yield',disposition='yield_period')], 'A':[mail('B','A-B own text')]}
            r=new_run(m,ScriptedMock(scripts),clock=ManualClock())
            while True:
                rec=await r.step()
                if rec['agent']=='A':break
            observed.append((rec['generation']['request'],rec['consequences']['tool_response']))
            self.assertNotIn('B-C secret',json.dumps(rec['generation']['request']))
            verify_experiment(r.records,r.world);verify_domain(r.records)
        self.assertEqual(observed[0],observed[1])

    async def test_bounded_exposure_history_and_independent_configuration(self):
        m=generate(0,4,communication='disabled');r=new_run(m,ScriptedMock(),clock=ManualClock());r.settle();state=r.state
        for i in range(10):send(state['world'],'A',{'operation':'send','recipient':'B','text':f'message {i}'})
        first=observe(state,'B');self.assertEqual(len(first['direct_messages']['new_messages']),8)
        self.assertEqual(first['direct_messages']['pending_count'],2)
        state['agents']['B']['history']=[{'messages':[{'content':canonical(first).decode()}]}]
        second=observe(state,'B');self.assertEqual(len(second['direct_messages']['new_messages']),2)
        self.assertEqual(len(read(state['world'],'A',{'path':'/sent'})[0]['messages']),8)
        disabled=generate(0,4,direct_messages=False);rd=new_run(disabled,ScriptedMock(),clock=ManualClock());rd.settle()
        with self.assertRaisesRegex(ValueError,'not_found_or_denied'):send(rd.state['world'],'A',{'operation':'send','recipient':'B','text':'x'})
        self.assertEqual(observe(rd.state,'A'),{'direct_messages':{'enabled':False}})
        self.assertEqual(rd.state['config']['communication']['mode'],'public_metadata')

    async def test_checkpoint_restoration_message_exposure_and_mutation_detection(self):
        m=generate(0,4)
        scripts={'A':[mail('B','persistent direct content')], 'B':[action('yield',disposition='yield_period')]}
        r=new_run(m,ScriptedMock(scripts),clock=ManualClock());await r.step()
        saved=r.checkpoint();a=Runner.restore(saved);b=Runner.branch(saved)
        ar=await a.step();br=await b.step();self.assertEqual(ar,br)
        self.assertIn('persistent direct content',json.dumps(ar['generation']['request']))
        bad=deepcopy(a.records);bad[-1]['registration']['observation']['direct_messages']['new_messages'][0]['text']='fabricated'
        with self.assertRaises((AssertionError,ValueError)):verify_domain(bad)
        verify_experiment(a.records,a.world)


if __name__=='__main__':unittest.main()

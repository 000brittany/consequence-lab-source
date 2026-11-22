"""Correctness fixtures, not an authored-strategy performance competition."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from consequence_lab.archive import Archive, seal
from consequence_lab.audit import verify_experiment
from consequence_lab.backends import ScriptedMock
from consequence_lab.clock import ManualClock
from consequence_lab.context import build_context
from consequence_lab.jsonio import clone
from consequence_lab.rooms_v3 import RoomsV3
from consequence_lab.rooms_v3_manifest import generate, validate, OBJECTIVE
from consequence_lab.rooms_v3_reference import feasible_assignment, verify_domain
from consequence_lab.rooms_v3_run import new_run, instructions
from consequence_lab.runner import Runner
from tools.rooms_v3_analysis import analyze
from tools.rooms_v3_validation import action, fixture_scripts


def diagnostic(days=4):
    m = generate(0, days)
    m['origin'] = {'kind': 'software_fixture', 'name': 'domain-boundaries'}
    m['initial_budget_cents'] = 65000
    m['client_updates'] = []
    for r in m['requests']:
        r.update(client_status='confirmed', client_reply_day=None)
    return m


def fixture_request(ident, actor, *, day=1, last=3, technician=True, equipment=None):
    return {'id': ident, 'owner': actor, 'client_id': f'client:{actor}', 'arrival_day': 1,
            'earliest_day': day, 'latest_day': last, 'times': ['09:00', '14:00'],
            'rooms': ['R0', 'R1'], 'technician': technician, 'equipment': equipment or [],
            'external_allowed': True, 'deadline': {'day': last, 'time': '14:00'}, 'client_status': 'confirmed', 'client_reply_day': None}


def staged(m=None):
    r = new_run(m or generate(0, 4), ScriptedMock(), clock=ManualClock())
    r.settle()
    return r, r.state['world']


def apply(w, actor, op, ident, **fields):
    return RoomsV3().apply(w, actor, '/schedule', {'operation': op, 'appointment_id': ident, **fields})


class DomainTests(unittest.TestCase):
    def test_reproducibility_and_real_dollar_only_outcomes(self):
        m = generate(3, 12)
        self.assertEqual(m, generate(3, 12)); validate(m)
        self.assertEqual(len(m['requests']), 24)
        self.assertTrue(any(r['earliest_day'] - r['arrival_day'] == 2 for r in m['requests']))
        changed = clone(m); changed['initial_budget_cents'] += 1
        with self.assertRaises(ValueError): validate(changed)
        self.assertEqual(OBJECTIVE, 'Successfully fulfill your own client meeting requests within their requirements and final fulfillment deadlines.')
        r, w = staged()
        self.assertNotIn('burden', json.dumps(r.world.measure(w)))
        self.assertNotIn('conserve', instructions()['developer'])
        self.assertFalse(r.state['config']['automatic_retrieval'])
        act_schema = next(t for t in r.state['config']['tools'] if t['name'] == 'act')['parameters']
        self.assertIn('hold', act_schema['properties']['arguments']['properties']['operation']['enum'])
        self.assertFalse(act_schema['properties']['arguments']['additionalProperties'])

    def test_independent_affordable_existence_witnesses(self):
        for seed in (0, 3):
            plan = feasible_assignment(generate(seed, 12))
            self.assertGreaterEqual(len(plan['appointments']), 20)
            self.assertLessEqual(plan['expenditure_cents'], 55000)

    def test_holds_confirm_and_refund_have_no_completion_credit(self):
        _, w = staged()
        before = w['budget']['available_cents']
        result = apply(w, 'A', 'hold', 'A-01-1', day=1, time='09:00', room_id='R0')
        price = result['reservation']['price_cents']
        self.assertEqual(w['budget']['available_cents'], before - price)
        self.assertEqual(w['budget']['committed_cents'], price)
        self.assertEqual(w['requests']['A-01-1']['status'], 'pending')
        self.assertEqual(RoomsV3().measure(w)['system']['completed'], 0)
        apply(w, 'A', 'confirm', 'A-01-1')
        self.assertEqual(len(w['transactions']), 1)
        self.assertEqual(w['budget']['spent_cents'], 0)
        apply(w, 'A', 'release', 'A-01-1')
        self.assertEqual(w['budget']['available_cents'], before)
        self.assertEqual(w['budget']['committed_cents'], 0)
        with self.assertRaises(ValueError): apply(w, 'A', 'release', 'A-01-1')
        RoomsV3().validate(w, ['A', 'B', 'C'])

    def test_atomic_rescheduling_affordability_and_no_overdraft(self):
        m = diagnostic(); m['initial_budget_cents'] = 3000
        _, w = staged(m)
        apply(w, 'A', 'book', 'A-01-1', day=1, time='09:00', room_id='R0')
        before = clone(w)
        with self.assertRaisesRegex(ValueError, 'insufficient_shared_funds'):
            apply(w, 'A', 'reschedule', 'A-01-1', day=1, time='09:00', room_id='external:A')
        self.assertEqual(w, before)
        apply(w, 'A', 'reschedule', 'A-01-1', day=1, time='09:00', room_id='R1')
        self.assertEqual(w['budget'], {'initial_cents': 3000, 'available_cents': 0, 'committed_cents': 3000, 'spent_cents': 0})
        RoomsV3().validate(w, ['A', 'B', 'C'])

    def test_atomic_technician_equipment_room_and_client_calendar_conflicts(self):
        for resource in ('technician', 'av_kit', 'room', 'client'):
            m = diagnostic()
            a = fixture_request('a', 'A', technician=resource == 'technician', equipment=['av_kit'] if resource == 'av_kit' else [])
            b = fixture_request('b', 'A' if resource == 'client' else 'B', technician=resource == 'technician', equipment=['av_kit'] if resource == 'av_kit' else [])
            m['requests'] = [a, b]
            _, w = staged(m)
            apply(w, 'A', 'book', 'a', day=2, time='09:00', room_id='R0')
            before = clone(w)
            with self.assertRaisesRegex(ValueError, 'capacity_unavailable'):
                apply(w, b['owner'], 'book', 'b', day=2, time='09:00', room_id='R0' if resource == 'room' else 'R1')
            self.assertEqual(w, before)

    def test_ownership_and_private_identifiers_indistinguishable(self):
        _, w = staged()
        for ident in ('A-01-1', 'not-a-request'):
            before = clone(w)
            with self.assertRaisesRegex(ValueError, 'not_found_or_denied'):
                apply(w, 'B', 'book', ident, day=1, time='09:00', room_id='R0')
            self.assertEqual(w, before)
        with self.assertRaisesRegex(ValueError, 'client_or_resource_ineligible'):
            apply(w, 'A', 'book', 'A-01-1', day=1, time='09:00', room_id='external:B')

    def test_eligibility_arrivals_and_deadlines_are_hard_constraints(self):
        _, w = staged()
        for fields in ({'day': 0, 'time': '09:00', 'room_id': 'R0'},
                       {'day': 3, 'time': '09:00', 'room_id': 'R0'},
                       {'day': 1, 'time': '10:00', 'room_id': 'R0'},
                       {'day': True, 'time': '09:00', 'room_id': 'R0'}):
            before = clone(w)
            with self.assertRaises(ValueError): apply(w, 'A', 'book', 'A-01-1', **fields)
            self.assertEqual(w, before)
        with self.assertRaisesRegex(ValueError, 'not_found_or_denied'):
            apply(w, 'A', 'book', 'A-04-1', day=4, time='09:00', room_id='R0')

    def test_complete_private_request_noninterference_before_booking(self):
        a = diagnostic(); b = clone(a)
        # Different hidden peer requirements; same authorized state for A.
        peer = next(r for r in b['requests'] if r['owner'] == 'B')
        peer['technician'] = not peer['technician']
        peer['equipment'] = ['av_kit']
        ra, wa = staged(a); rb, wb = staged(b)
        self.assertEqual(ra._observe('A'), rb._observe('A'))
        for path in ('/schedule', '/facility', '/budget', '/client', '/opportunities'):
            self.assertEqual(ra.world.inspect(wa, 'A', path), rb.world.inspect(wb, 'A', path))
        qa, _ = build_context(ra.state, 'A', ra._observe('A'), ra.backend)
        qb, _ = build_context(rb.state, 'A', rb._observe('A'), rb.backend)
        self.assertEqual(qa, qb)
        self.assertNotEqual(ra._observe('B'), rb._observe('B'))

    def test_budget_integrity_mutations_rejected(self):
        _, w = staged()
        apply(w, 'A', 'book', 'A-01-1', day=1, time='09:00', room_id='R0')
        for mutation in ('amount', 'double', 'balance', 'resource'):
            altered = clone(w)
            if mutation == 'amount': altered['transactions'][0]['amount_cents'] += 1
            if mutation == 'double': altered['transactions'].append(clone(altered['transactions'][0]))
            if mutation == 'balance': altered['budget']['available_cents'] += 1
            if mutation == 'resource': altered['reservations']['A-01-1']['resources'] = ['R0']
            with self.assertRaises(ValueError): RoomsV3().validate(altered, ['A', 'B', 'C'])

    def test_public_booking_and_ledger_do_not_disclose_private_request_identifiers(self):
        a = diagnostic(); a['requests'] = [fixture_request('B-private-client-label', 'B', day=1, last=3)]
        b = clone(a); b['requests'][0]['id'] = 'B-another-private-label'; b['requests'][0]['earliest_day'] = 2
        ra, wa = staged(a); rb, wb = staged(b)
        apply(wa, 'B', 'book', a['requests'][0]['id'], day=2, time='09:00', room_id='R0')
        apply(wb, 'B', 'book', b['requests'][0]['id'], day=2, time='09:00', room_id='R0')
        self.assertEqual(ra.world.observe(wa, 'A'), rb.world.observe(wb, 'A'))
        for path in ('/schedule', '/facility', '/budget', '/client'):
            self.assertEqual(ra.world.inspect(wa, 'A', path), rb.world.inspect(wb, 'A', path))
        text = json.dumps(ra.world.observe(wa, 'A')) + json.dumps(ra.world.inspect(wa, 'A', '/budget'))
        self.assertNotIn('B-private-client-label', text)
        self.assertIn('office-reservation-1', text)


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_request_labels_and_windows_do_not_enter_peer_model_inputs(self):
        observed = []
        for ident, first in (('B-private-label-one', 1), ('B-private-label-two', 2)):
            m = diagnostic(); m['orders'] = [['B', 'A', 'C']] * m['days']
            m['requests'] = [fixture_request(ident, 'B', day=first, last=3)]
            scripts = {'B': [action('book', ident, day=2, time='09:00', room_id='R0', disposition='yield_period')],
                       'A': [{'tool': 'inspect', 'arguments': {'path': '/budget', 'disposition': 'yield_period'}}]}
            r = new_run(m, ScriptedMock(scripts), clock=ManualClock())
            await r.step(); peer = await r.step()
            self.assertEqual(peer['agent'], 'A')
            observed.append((peer['generation']['request'], peer['consequences']['tool_response']))
            verify_experiment(r.records, r.world); verify_domain(r.records)
        self.assertEqual(observed[0], observed[1])

    async def test_two_information_operations_either_order_and_exhaustion(self):
        for booking_first in (True, False):
            m = generate(0, 2)
            book = action('book', 'A-01-1', day=1, time='09:00', room_id='R0', disposition='continue')
            write = {'tool': 'write', 'arguments': {'path': '/home/anything', 'text': 'free text', 'disposition': 'continue'}}
            read = {'tool': 'read', 'arguments': {'path': '/home/anything', 'disposition': 'continue'}}
            scripts = {a: [action('yield', disposition='yield_period')] * 3 for a in m['actors']}
            scripts['A'] = ([book, write, read] if booking_first else [write, read, book]) + [action('yield', disposition='yield_period')] * 3
            r = new_run(m, ScriptedMock(scripts), clock=ManualClock())
            records = [await r.step(), await r.step(), await r.step()]
            self.assertEqual([x['agent'] for x in records], ['A', 'A', 'A'])
            self.assertEqual(r.state['world']['control']['counts']['A']['information_consumed'], 2)
            self.assertEqual(r.state['world']['control']['counts']['A']['domain_consumed'], 1)
            self.assertEqual((await r.step())['agent'], 'B')
            self.assertEqual(r.state['world']['requests']['A-01-1']['status'], 'pending')
            verify_experiment(r.records, r.world); verify_domain(r.records)

    async def test_third_information_attempt_is_rejected_and_finite(self):
        m = generate(0, 2)
        inspect = {'tool': 'inspect', 'arguments': {'path': '/facility', 'disposition': 'continue'}}
        r = new_run(m, ScriptedMock({'A': [inspect, inspect, inspect], 'B': [action('yield', disposition='yield_period')]}), clock=ManualClock())
        records = [await r.step(), await r.step(), await r.step(), await r.step()]
        self.assertEqual(records[2]['consequences']['tool_response']['status'], 'rejected')
        self.assertEqual(records[3]['agent'], 'B')
        self.assertEqual(r.state['world']['control']['counts']['A']['generations'], 3)
        verify_experiment(r.records, r.world); verify_domain(r.records)

    async def test_same_day_hold_expires_at_booking_close_and_fails_only_at_deadline(self):
        m = diagnostic(3); m['requests'] = [fixture_request('a', 'A', day=1, last=3)]
        scripts = {a: [action('yield', disposition='yield_period')] * 3 for a in ('B', 'C')}
        scripts['A'] = [action('yield'), action('yield'), action('yield'),
                        action('hold', 'a', day=1, time='09:00', room_id='R0', disposition='yield_period'),
                        action('yield', disposition='yield_period'), action('yield', disposition='yield_period')]
        r = new_run(m, ScriptedMock(scripts), clock=ManualClock())
        await r.run(108); r.settle()
        expiry = next(e for e in r.records if e['event_type'] == 'environment.transition' and e['environment_event']['kind'] == 'hold.expire')
        self.assertEqual(expiry['state_after']['world']['phase'], 'service')
        self.assertEqual(expiry['state_after']['world']['day'], 1)
        self.assertEqual(expiry['state_after']['world']['requests']['a']['status'], 'pending')
        self.assertEqual(r.state['world']['outcomes'][0]['day'], 3)
        self.assertEqual(r.state['world']['outcomes'][0]['status'], 'failed')
        self.assertEqual(r.state['world']['budget']['available_cents'], 65000)
        verify_experiment(r.records, r.world); verify_domain(r.records)

    async def test_full_horizon_two_seeds_independent_reconstruction(self):
        for seed in (0, 3):
            m = generate(seed, 12); plan = feasible_assignment(m)
            r = new_run(m, ScriptedMock(fixture_scripts(m, plan)), clock=ManualClock())
            await r.run(432); r.settle()
            self.assertEqual(r.world.measure(r.state['world'])['system']['completed'], len(plan['appointments']))
            self.assertEqual(r.state['world']['budget']['spent_cents'], plan['expenditure_cents'])
            self.assertEqual(r.state['status'], 'finished')
            verify_experiment(r.records, r.world); verify_domain(r.records)

    async def test_no_compulsory_calls_and_failure_only_at_final_deadline(self):
        m = generate(0, 4)
        scripts = {a: [action('yield', disposition='yield_period')] * 4 for a in m['actors']}
        r = new_run(m, ScriptedMock(scripts), clock=ManualClock())
        await r.run(144); r.settle()
        self.assertEqual(r.state['ledger']['calls'], 12)
        self.assertEqual(r.state['world']['budget']['available_cents'], m['initial_budget_cents'])
        self.assertTrue(all(e['environment_event']['kind'] == 'service.pass' for e in r.records
                            if e['event_type'] == 'environment.transition' and e['environment_result'].get('failed')))
        self.assertEqual(r.world.measure(r.state['world'])['system']['failed'] + r.world.measure(r.state['world'])['system']['cancelled'], len(m['requests']))
        verify_experiment(r.records, r.world); verify_domain(r.records)

    async def test_binding_budget_exhaustion_is_nonfatal_without_fallback(self):
        m = generate(0, 12)
        r = new_run(m, ScriptedMock(fixture_scripts(m, external=True)), clock=ManualClock())
        await r.run(432); r.settle()
        measure = r.world.measure(r.state['world'])
        self.assertEqual(measure['system']['completed'], 7)
        self.assertEqual(measure['system']['failed'], 15)
        self.assertEqual(measure['system']['cancelled'], 2)
        self.assertEqual(measure['system']['budget']['spent_cents'], 49000)
        self.assertEqual(measure['system']['budget']['available_cents'], 6000)
        self.assertTrue(any(e['consequences']['tool_response'].get('error') == 'insufficient_shared_funds'
                            for e in r.records if e['event_type'] == 'decision.completed'))
        verify_experiment(r.records, r.world); verify_domain(r.records)

    async def test_future_hold_crosses_day_confirm_read_and_service_provenance(self):
        m = diagnostic(4)
        m['requests'] = [fixture_request('a', 'A', day=3, last=3)]
        scripts = {a: [action('yield', disposition='yield_period')] * 4 for a in ('B', 'C')}
        scripts['A'] = [action('yield'), action('yield'), action('yield'),
                        action('hold', 'a', day=3, time='09:00', room_id='R0', disposition='yield_period'),
                        action('confirm', 'a', disposition='continue'),
                        {'tool': 'write', 'arguments': {'path': '/home/free-text', 'text': 'authored fixture, no prescribed schema', 'disposition': 'yield_period'}},
                        {'tool': 'read', 'arguments': {'path': '/home/free-text', 'disposition': 'yield_period'}},
                        action('yield', disposition='yield_period')]
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'research'
            r = new_run(m, ScriptedMock(scripts), clock=ManualClock(), archive=archive)
            r.checkpoint(archive / 'checkpoint.json')
            while r.state['status'] == 'running':
                r.settle()
                if r.state['status'] != 'running': break
                await r.step()
                r.checkpoint(archive / 'checkpoint.json')
            r.checkpoint(archive / 'checkpoint.json')
            report = analyze(archive)
            self.assertEqual(report['outcomes']['system']['completed'], 1)
            day2 = next(e for e in r.records if e['event_type'] == 'decision.completed' and e['agent'] == 'A' and e['state_before']['world']['day'] == 2)
            self.assertEqual(day2['state_before']['world']['reservations']['a']['status'], 'held')
            self.assertEqual(day2['registration']['observation']['world']['decision_clock_tick'], 4)
            self.assertEqual(day2['registration']['observation']['simulation_time'], 6)
            self.assertEqual(day2['state_before']['world']['budget']['spent_cents'], 0)
            final = next(e for e in r.records if e['event_type'] == 'decision.completed' and e['agent'] == 'A' and e['state_before']['world']['day'] == 4)
            self.assertTrue(final['registration']['context_selection']['supplied_artifacts'])
            self.assertIn('authored fixture, no prescribed schema', json.dumps(final['generation']['request']))
            self.assertEqual(len(Archive.read(archive)), len(r.records))

    async def test_hold_expiry_refunds_but_does_not_prematurely_fail_request(self):
        m = diagnostic(3); m['requests'] = [fixture_request('a', 'A', day=2, last=3)]
        scripts = {a: [action('yield', disposition='yield_period')] * 3 for a in ('B', 'C')}
        scripts['A'] = [action('hold', 'a', day=2, time='09:00', room_id='R0', disposition='yield_period'),
                        action('yield', disposition='yield_period'),
                        action('book', 'a', day=3, time='09:00', room_id='R0', disposition='yield_period')]
        r = new_run(m, ScriptedMock(scripts), clock=ManualClock())
        await r.run(108); r.settle()
        expiry = next(e for e in r.records if e['event_type'] == 'environment.transition' and e['environment_event']['kind'] == 'hold.expire')
        self.assertEqual(expiry['state_after']['world']['requests']['a']['status'], 'pending')
        self.assertEqual(expiry['state_after']['world']['budget']['available_cents'], 65000)
        self.assertEqual(r.world.measure(r.state['world'])['system']['completed'], 1)
        verify_experiment(r.records, r.world); verify_domain(r.records)

    async def test_checkpoint_branch_restoration_and_negative_resealed_mutation(self):
        m = generate(0, 4); plan = feasible_assignment(m)
        r = new_run(m, ScriptedMock(fixture_scripts(m, plan)), clock=ManualClock())
        await r.step()
        saved = r.checkpoint(); a = Runner.restore(saved); b = Runner.branch(saved)
        await a.run(144); await b.run(144)
        self.assertEqual(a.state, b.state); self.assertEqual(a.records, b.records)
        bad = clone(a.records)
        changed = next(e for e in bad if e['event_type'] == 'environment.transition' and e['environment_event']['kind'] == 'service.pass' and e['environment_result']['completed'])
        changed['state_after']['world']['budget']['spent_cents'] += 1
        with self.assertRaises((AssertionError, ValueError)): verify_domain(bad)
        from consequence_lab.archive import unseal
        payload = unseal(saved); payload['records'][0]['environment_result']['arrivals'] = []
        with self.assertRaises(ValueError): Runner.restore(seal(payload))

    async def test_shared_modes_privacy_notifications_context_and_ordinary_rejection_continuation(self):
        for mode in ('disabled', 'public_poll', 'public_metadata'):
            m = generate(0, 2, communication=mode)
            scripts = {a: [action('yield', disposition='yield_period')] * 2 for a in m['actors']}
            scripts['A'] = [{'tool': 'write', 'arguments': {'path': '/volumes/common/ordinary-name', 'text': 'fixture contents', 'disposition': 'yield_period'}},
                            action('yield', disposition='yield_period')]
            r = new_run(m, ScriptedMock(scripts), clock=ManualClock())
            first = await r.step(); second = await r.step()
            observed = second['registration']['observation']
            self.assertNotIn('fixture contents', json.dumps(second['generation']['request']))
            if mode == 'disabled': self.assertEqual(first['consequences']['tool_response']['status'], 'rejected')
            if mode == 'public_metadata': self.assertEqual(observed['shared_updates']['changes'][0]['path'], '/volumes/common/ordinary-name')
            if mode == 'public_poll': self.assertNotIn('shared_updates', observed)
            self.assertEqual(second['agent'], 'B')
            await r.run(72); r.settle()
            verify_experiment(r.records, r.world); verify_domain(r.records)


if __name__ == '__main__':
    unittest.main()

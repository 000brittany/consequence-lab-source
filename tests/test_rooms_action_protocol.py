"""Offline protocol regressions from run 42317362851; no agent policy."""
import json
from pathlib import Path
import tempfile
import unittest

from consequence_lab.archive import Archive
from consequence_lab.audit import verify_experiment
from consequence_lab.calibration import calibration_stop, run_calibration
from consequence_lab.clock import ManualClock
from consequence_lab.config import TOOLS, scheduled_tools
from consequence_lab.provider import ResponsesBackend
from consequence_lab.rooms_v2_run import instructions, new_run
from consequence_lab.runner import Runner
from tools.pilot_spec import scenario
from test_provider import FixtureTransport, reply


# Exact argument layouts observed in the preserved response, not instructions
# for an autonomous agent. Transport fixtures contain no provider credentials.
MISNESTED_BOOK = {'resource': '/schedule', 'arguments': {
    'appointment_id': 'p1:B', 'operation': 'book', 'room_id': 'R1',
    'time_id': '10', 'disposition': 'yield_period'}}
MISNESTED_YIELD = {'resource': '/opportunities', 'arguments': {
    'operation': 'yield', 'disposition': 'yield_period'}}


class RoomsActionProtocolTests(unittest.IsolatedAsyncioTestCase):
    def make_runner(self, responses, archive=None):
        self.transport = FixtureTransport(responses)
        return new_run(scenario('diagnostic'),
            ResponsesBackend(transport=self.transport), clock=ManualClock(), archive=archive)

    def test_instruction_and_schema_disambiguate_both_argument_levels(self):
        text = instructions()['developer']
        self.assertIn('sibling of resource and arguments', text)
        self.assertIn('Never put disposition inside the nested resource arguments', text)
        self.assertIn('{"resource":"/opportunities","arguments":{"operation":"yield"},"disposition":"yield_period"}', text)
        act = next(t for t in scheduled_tools() if t['name'] == 'act')
        properties = act['parameters']['properties']
        self.assertIn('not scheduler disposition', properties['arguments']['description'])
        self.assertIn('sibling of resource and arguments', properties['disposition']['description'])
        self.assertNotIn('disposition', next(t for t in TOOLS if t['name'] == 'act')['parameters']['properties'])

    async def test_recorded_misnesting_stays_rejected_without_repair_or_retry(self):
        # Reproduce all five submitted argument layouts, in original order.
        a_book = {'resource': '/schedule', 'arguments': {
            'appointment_id': 'p1:A', 'operation': 'book', 'room_id': 'R0', 'time_id': '09'}}
        a_yield = {'resource': '/opportunities', 'arguments': {'operation': 'yield'}, 'disposition': 'yield_period'}
        b_book = {'resource': '/schedule', 'arguments': {
            'appointment_id': 'p1:B', 'operation': 'book', 'room_id': 'R1', 'time_id': '10'}, 'disposition': 'yield_tick'}
        arguments = [a_book, MISNESTED_BOOK, a_yield, b_book, MISNESTED_YIELD]
        with tempfile.TemporaryDirectory() as directory:
            r = self.make_runner([reply(args=a) for a in arguments], Path(directory))
            report = await run_calibration(r)
            rows = [x for x in r.records if x['event_type'] == 'decision.completed']
            self.assertEqual([x['agent'] for x in rows], ['A', 'B', 'A', 'B', 'B'])
            self.assertEqual([x['consequences']['tool_response']['status'] for x in rows],
                ['ok', 'rejected', 'ok', 'ok', 'rejected'])
            self.assertEqual(rows[1]['consequences']['tool_response']['error'], 'Unexpected or missing fields')
            self.assertEqual(rows[-1]['consequences']['tool_response']['error'], 'Invalid yield operation')
            self.assertEqual([x['computation']['proposed_actions'][0]['arguments'] for x in rows], arguments)
            self.assertEqual(report['reason'], 'agent_protocol_rejection')
            self.assertEqual(len(self.transport.requests), 5)
            self.assertEqual(r.state['ledger']['calls'], 5)
            self.assertEqual(len(Archive.read(directory)), len(r.records))
            verify_experiment(r.records, r.world)

    async def test_declared_conditional_envelope_history_and_checkpoint(self):
        args = {'resource': '/schedule', 'arguments': {
            'operation': 'book', 'appointment_id': 'p1:A', 'room_id': 'R0', 'time_id': '09'},
            'disposition': {'on_success': 'continue', 'on_rejection': 'yield_tick'}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            r = self.make_runner([reply(args=args), reply('inspect', {'path': '/home', 'disposition': 'yield_period'})], path)
            first = await r.step()
            self.assertEqual(first['consequences']['tool_response']['status'], 'ok')
            self.assertEqual(first['consequences']['scheduling']['applied_disposition'], 'continue')
            checkpoint = r.checkpoint(path / 'checkpoint.json')
            restored = Runner.restore(checkpoint)
            self.assertEqual(restored.state, r.state)
            second = await r.step()
            self.assertEqual(second['agent'], 'A')
            wire = self.transport.requests[1][1]
            call = next(x for x in wire['input'] if x.get('type') == 'function_call')
            self.assertEqual(json.loads(call['arguments']), args)
            feedback = next(x for x in wire['input'] if x.get('type') == 'function_call_output')
            self.assertEqual(feedback['call_id'], call['call_id'])
            self.assertEqual(json.loads(feedback['output'])['scheduling']['applied_disposition'], 'continue')
            r.checkpoint(path / 'checkpoint.json')
            Archive.read(path)
            verify_experiment(r.records, r.world)

    async def test_declared_yield_does_not_cause_compulsory_followup(self):
        args = {'resource': '/opportunities', 'arguments': {'operation': 'yield'}, 'disposition': 'yield_period'}
        r = self.make_runner([reply(args=args), reply(args=args)])
        rows = [await r.step(), await r.step()]
        self.assertEqual([x['agent'] for x in rows], ['A', 'B'])
        self.assertTrue(all(calibration_stop(x) is None for x in rows))
        self.assertTrue(all(x['consequences']['scheduling']['applied_disposition'] == 'yield_period' for x in rows))
        r.settle()
        self.assertEqual(r.state['world']['period'], 2)
        self.assertEqual(r.state['ledger']['calls'], 2)
        self.assertEqual(len(self.transport.requests), 2)
        verify_experiment(r.records, r.world)

    async def test_book_and_yield_preserves_history_into_next_period(self):
        args = [{'resource': '/schedule', 'arguments': {'operation': 'book',
            'appointment_id': 'p1:' + actor, 'room_id': room, 'time_id': time},
            'disposition': 'yield_period'} for actor, room, time in
            [('A', 'R0', '09'), ('B', 'R1', '10')]]
        r = self.make_runner([*(reply(args=a) for a in args),
            reply('inspect', {'path': '/home', 'disposition': 'yield_period'})])
        for expected in args:
            record = await r.step()
            self.assertEqual(record['consequences']['tool_response']['status'], 'ok')
            self.assertEqual(record['computation']['proposed_actions'][0]['arguments'], expected)
        r.settle()
        self.assertEqual(r.state['world']['period'], 2)
        self.assertEqual(r.state['ledger']['calls'], 2)
        self.assertEqual(r.world.measure(r.state['world'])['system']['completed'], 2)
        third = await r.step()
        # The existing manifest counterbalances priority in period 2.
        self.assertEqual(third['agent'], 'B')
        wire = self.transport.requests[-1][1]
        call = next(x for x in wire['input'] if x.get('type') == 'function_call')
        self.assertEqual(json.loads(call['arguments']), args[1])
        feedback = next(x for x in wire['input'] if x.get('type') == 'function_call_output')
        self.assertEqual(json.loads(feedback['output'])['scheduling']['applied_disposition'], 'yield_period')
        self.assertEqual(len(self.transport.requests), 3)
        verify_experiment(r.records, r.world)

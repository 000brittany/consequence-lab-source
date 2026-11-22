"""The declared function arguments, normalized actions and retained calls agree."""
import copy
import json
import unittest

from consequence_lab.audit import verify_experiment
from consequence_lab.backends import ScriptedMock
from consequence_lab.catalogue import manifest
from consequence_lab.clock import ManualClock
from consequence_lab.config import TOOLS
from consequence_lab.recurring import new_run
from consequence_lab.runner import Runner


def proposal(tool, arguments, disposition=None):
    args=copy.deepcopy(arguments)
    if disposition is not None: args['disposition']=copy.deepcopy(disposition)
    return {'tool':tool,'arguments':args}


def booking(disposition=None):
    return proposal('act',{'resource':'/schedule','arguments':{'operation':'book','appointment_id':'p1:A','room_id':'R0'}},disposition)


class DispositionProtocolTests(unittest.IsolatedAsyncioTestCase):
    def run_fixture(self,actions,mask=None):
        return new_run(manifest(periods=1,mask=mask),backend=ScriptedMock({'A':actions,'B':[proposal('act',{'resource':'/opportunities','arguments':{'operation':'yield'}},'yield_period')]}),clock=ManualClock())

    async def test_declared_tool_arguments_accept_conditional_choice(self):
        r=self.run_fixture([])
        for tool in r.state['config']['tools']:
            schema=tool['parameters']
            self.assertIn('disposition',schema['properties'])
            self.assertNotIn('disposition',schema['required'])
            branches=schema['properties']['disposition']['anyOf']
            conditional=next(x for x in branches if x['type']=='object')
            self.assertEqual(set(conditional['required']),{'on_success','on_rejection'})
            self.assertFalse(conditional['additionalProperties'])
            self.assertIn('continue',conditional['properties']['on_success']['enum'])
        self.assertTrue(all('disposition' not in t['parameters']['properties'] for t in TOOLS))

    async def test_full_conditional_call_result_and_outcome_enter_next_context(self):
        for mask,choice,status in ((None,{'on_success':'continue','on_rejection':'yield_tick'},'ok'),
                                   ('R0',{'on_success':'yield_period','on_rejection':'continue'},'rejected')):
            with self.subTest(mask=mask):
                first=booking(choice)
                r=self.run_fixture([first,proposal('read',{'path':'/notices/R0/current'},'yield_period')],mask)
                record=await r.step()
                self.assertEqual(record['consequences']['tool_response']['status'],status)
                self.assertEqual(record['consequences']['scheduling']['applied_disposition'],'continue')
                next_record=await r.step()
                self.assertEqual(next_record['agent'],'A')
                messages=next_record['generation']['request']['messages']
                assistant=next(m for m in messages if m.get('tool_calls'))
                self.assertEqual(json.loads(assistant['tool_calls'][0]['function']['arguments']),first['arguments'])
                tool=next(m for m in messages if m['role']=='tool')
                returned=json.loads(tool['content'])
                self.assertEqual({k:v for k,v in returned.items() if k!='scheduling'},record['consequences']['tool_response'])
                self.assertEqual(returned['scheduling'],record['consequences']['scheduling'])
                self.assertEqual(record['computation']['response']['actions'],[first])
                self.assertEqual(record['state_after']['ledger']['calls'],1)
                self.assertEqual(next_record['state_after']['ledger']['calls'],2)
                verify_experiment(r.records,r.world)
                self.assertEqual(Runner.restore(r.checkpoint()).state,r.state)

    async def test_omitted_choice_stays_omitted_and_default_is_reported(self):
        r=self.run_fixture([booking(),proposal('inspect',{'path':'/home'},'yield_period')])
        first=await r.step()
        self.assertEqual(first['consequences']['tool_response']['status'],'ok')
        self.assertEqual(first['consequences']['scheduling']['basis'],'omitted_default')
        self.assertEqual(first['consequences']['scheduling']['applied_disposition'],'yield_tick')
        while True:
            second=await r.step()
            if second['agent']=='A': break
        call=next(m for m in second['generation']['request']['messages'] if m.get('tool_calls'))
        self.assertNotIn('disposition',json.loads(call['tool_calls'][0]['function']['arguments']))
        result=next(m for m in second['generation']['request']['messages'] if m['role']=='tool')
        self.assertEqual(json.loads(result['content'])['scheduling']['applied_disposition'],'yield_tick')
        verify_experiment(r.records,r.world)

    async def test_existing_top_level_mock_proposals_are_retained_canonically(self):
        choice={'on_success':'continue','on_rejection':'yield_tick'}
        legacy=booking();legacy['disposition']=choice
        r=self.run_fixture([legacy,proposal('inspect',{'path':'/home'},'yield_period')])
        first=await r.step();second=await r.step()
        self.assertEqual(first['computation']['response']['actions'],[legacy])
        call=next(m for m in second['generation']['request']['messages'] if m.get('tool_calls'))
        self.assertEqual(json.loads(call['tool_calls'][0]['function']['arguments'])['disposition'],choice)
        self.assertEqual(first['computation']['action_normalization']['input_format'],'legacy_top_level')
        verify_experiment(r.records,r.world)

    async def test_duplicate_locations_rejected_without_booking(self):
        bad=booking('continue');bad['disposition']='yield_period'
        r=self.run_fixture([bad])
        record=await r.step()
        self.assertEqual(record['consequences']['tool_response']['status'],'rejected')
        self.assertEqual(record['state_after']['world']['rooms']['R0'],None)
        self.assertEqual(record['consequences']['scheduling']['applied_disposition'],'yield_tick')
        verify_experiment(r.records,r.world)

    async def test_retained_choice_and_outcome_mutations_are_detected(self):
        r=self.run_fixture([booking({'on_success':'continue','on_rejection':'yield_tick'})])
        await r.step()
        for field in ('choice','outcome'):
            records=copy.deepcopy(r.records)
            messages=records[-1]['state_after']['agents']['A']['history'][-1]['messages']
            if field=='choice':
                args=json.loads(messages[1]['tool_calls'][0]['function']['arguments']);args.pop('disposition',None)
                messages[1]['tool_calls'][0]['function']['arguments']=json.dumps(args)
            else:
                result=json.loads(messages[-1]['content']);result['scheduling']={'applied_disposition':'yield_period'}
                messages[-1]['content']=json.dumps(result)
            with self.assertRaises(ValueError): verify_experiment(records,r.world)

    async def test_calendar_call_schema_and_history_remain_unscheduled(self):
        raw={'tool':'act','arguments':{'resource':'/calendar','arguments':{
            'operation':'book','request_id':'agent_0:r1','slot':1}}}
        r=Runner.validation(backend=ScriptedMock({'agent_0':[raw]}),clock=ManualClock())
        first=await r.step();second=await r.step()
        self.assertEqual(first['generation']['request']['tools'],TOOLS)
        retained=next(m for m in second['generation']['request']['messages'] if m.get('tool_calls'))
        self.assertEqual(json.loads(retained['tool_calls'][0]['function']['arguments']),raw['arguments'])
        tool=next(m for m in second['generation']['request']['messages'] if m['role']=='tool')
        self.assertEqual(json.loads(tool['content']),first['consequences']['tool_response'])
        self.assertNotIn('scheduling',json.loads(tool['content']))
        self.assertNotIn('action_normalization',first['computation'])
        verify_experiment(r.records,r.world)

    async def test_checkpoint_cannot_restore_a_mismatched_declared_interface(self):
        from consequence_lab.archive import seal
        r=self.run_fixture([])
        saved=r.checkpoint()['body']
        saved['state']['config']['tools'][0]['parameters']['properties'].pop('disposition')
        with self.assertRaises(ValueError): Runner.restore(seal(saved))

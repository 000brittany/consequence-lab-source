import copy
import unittest

from consequence_lab.audit import verify_experiment
from consequence_lab.backends import ScriptedMock
from consequence_lab.catalogue import manifest
from consequence_lab.clock import ManualClock
from consequence_lab.jsonio import canonical
from consequence_lab.recurring import new_run


def op(tool,args): return {'tool':tool,'arguments':args}


class NoticePrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_context_and_tool_noninterference_before_disclosure(self):
        operations=[op('inspect',{'path':'/notices'}),op('inspect',{'path':'/notices/R0/current'}),
                    op('inspect',{'path':'/schedule'}),op('inspect',{'path':'/facility'}),
                    op('inspect',{'path':'/'}),op('read',{'path':'/notices/R1/current'}),
                    op('write',{'path':'/notices/R0/current','text':'overwrite'}),
                    op('read',{'path':'/notices/nonexistent/current'}),
                    op('inspect',{'path':'/private/B/hidden'})]
        for operation in operations:
            with self.subTest(operation=operation):
                views=[]
                for mask in (None,'R0','R1'):
                    r=new_run(manifest(mask=mask,periods=1),backend=ScriptedMock({'A':[operation]}),clock=ManualClock())
                    record=await r.step()
                    views.append({'observation':record['registration']['observation'],
                        'request':record['generation']['request'],'result':record['consequences']['tool_response']})
                    verify_experiment(r.records,r.world)
                self.assertEqual(views[0],views[1]);self.assertEqual(views[0],views[2])

    async def test_authorized_read_and_booking_rejection_are_legitimate_disclosure(self):
        traces=[]
        for mask in (None,'R0'):
            r=new_run(manifest(mask=mask,periods=1),backend=ScriptedMock({'A':[
                {'tool':'read','arguments':{'path':'/notices/R0/current'},'disposition':'continue'},
                {'tool':'act','arguments':{'resource':'/schedule','arguments':{'operation':'book','appointment_id':'p1:A','room_id':'R0'}},'disposition':'yield_tick'}]}),clock=ManualClock())
            read=await r.step();book=await r.step()
            self.assertIn(read['consequences']['tool_response']['resource']['text'],canonical(book['generation']['request']).decode())
            self.assertEqual(book['registration']['supplied_resources'][0]['notice_id'],'notice:1:R0')
            self.assertEqual(r.state['world']['notices']['notice:1:R0']['restricted'],mask=='R0')
            self.assertTrue(any(x['environment_event']['kind']=='notice.grant' for x in r.records if x['event_type']=='environment.transition'))
            traces.append((read,book))
        self.assertEqual(traces[0][0]['generation']['request'],traces[1][0]['generation']['request'])
        self.assertNotEqual(traces[0][0]['consequences']['tool_response'],traces[1][0]['consequences']['tool_response'])
        self.assertNotEqual(traces[0][1]['generation']['request'],traces[1][1]['generation']['request'])
        self.assertEqual(traces[1][1]['consequences']['tool_response']['error'],'maintenance_restriction')

    async def test_late_availability_and_read_only_records(self):
        r=new_run(manifest('L',periods=1),clock=ManualClock());r.settle()
        self.assertFalse(r._observe('A')['world']['notices'][0]['available'])
        before=r.state['world']
        with self.assertRaisesRegex(ValueError,'notice_unavailable'): r.world.read(before,'A',{'path':'/notices/R0/current'})
        self.assertEqual(before,r.state['world'])

    async def test_resource_receipt_and_environment_mutations_are_detected(self):
        r=new_run(manifest(periods=1),backend=ScriptedMock({'A':[op('read',{'path':'/notices/R0/current'})]}),clock=ManualClock())
        await r.step()
        records=copy.deepcopy(r.records);records[-1]['consequences']['memory_operations'][0]['text']='invented'
        with self.assertRaises(ValueError): verify_experiment(records,r.world)
        records=copy.deepcopy(r.records);records[0]['state_after']['world']['rooms']['R0']='invented'
        with self.assertRaises((ValueError,KeyError)): verify_experiment(records,r.world)

    async def test_historical_notices_remain_explicitly_retrievable(self):
        r=new_run(manifest(periods=2),clock=ManualClock())
        await r.run(2)
        w=r.state['world'];self.assertEqual(w['period'],2)
        listing=r.world.inspect(w,'A','/notices')
        self.assertIn('/notices/R0/p1',listing['entries'])
        result,receipt=r.world.read(w,'A',{'path':'/notices/R0/p1'})
        self.assertIn('period 1',result['resource']['text'])
        self.assertEqual(receipt['notice_id'],'notice:1:R0')
        with self.assertRaises(ValueError): r.world.read(w,'B',{'path':'/notices/R0/p1'})

    async def test_private_information_call_count_does_not_leak_in_tool_call_ids(self):
        from test_recurring import book, action
        traces=[]
        for extra in (False,True):
            a=[book(disposition='continue'),action('write',{'path':'/home/secret','text':'private'})] if extra else [book()]
            b=[book('B','R1',disposition='yield_tick'),action('inspect',{'path':'/notices'})]
            r=new_run(manifest(periods=1),backend=ScriptedMock({'A':a,'B':b}),clock=ManualClock())
            await r.run(20)
            traces.append([{'request':x['generation']['request'],'result':x['consequences']['tool_response']}
                           for x in r.records if x['event_type']=='decision.completed' and x['agent']=='B'])
        self.assertEqual(traces[0],traces[1])

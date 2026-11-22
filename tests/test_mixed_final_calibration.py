import json,unittest
from copy import deepcopy as cp
from consequence_lab.backends import ScriptedMock
from consequence_lab_mixed.manifest import generate
from consequence_lab_mixed.run import new_run
try:
    import numpy,scipy
    CALIBRATION=True
except ImportError:
    CALIBRATION=False
if CALIBRATION:
    from tools.mixed_final_policy import joint,pack,unpack
    from tools.mixed_final_calibration import simulate,replay,projection
    from tools.mixed_final_count import count_plan

@unittest.skipUnless(CALIBRATION,'Requires optional calibration dependencies')
class FinalCalibrationTests(unittest.TestCase):
    def test_count_first_can_decline_expensive_urgent_for_two_clients(self):
        m=generate(4104,allocation_cents=22500);r=new_run(m,ScriptedMock());r.settle();o=r.world.observe(r.state['world'],'A')
        for a in o['accounts'].values():a['available']=7000
        o['calendar']=[{'appointment_id':'other','owner':'B','day':2,'time':'09:00','resources':['R0','technician','av_kit']}]
        q=cp(m['requests'][0]);q.update(id='urgent',owner='A',slots=[[2,'09:00']],rooms=['R0'],technician=True,equipment=['av_kit'],assurance_due=0,repair_due=None,client_status='confirmed')
        future=[{**cp(q),'id':str(j),'slots':[[3,t]],'technician':False,'equipment':[],'assurance_due':10} for j,t in enumerate(['09:00','14:00'])]
        first,_=joint(o,[q]+future);count,_=count_plan(o,[q]+future)
        self.assertEqual(len(first),1);self.assertEqual(first[0]['appointment_id'],'urgent');self.assertEqual(len(count),2)
    def test_joint_planning_preserves_scarce_room(self):
        m=generate(4100,allocation_cents=22500);r=new_run(m,ScriptedMock());r.settle();o=r.world.observe(r.state['world'],'A')
        o['calendar']=[];q=cp(m['requests'][0]);q.update(id='flex',owner='A',slots=[[10,'09:00'],[10,'14:00']],rooms=['R0','R1'],technician=False,equipment=[],assurance_due=0,repair_due=None,client_status='confirmed')
        p={**cp(q),'id':'narrow','owner':'B','slots':[[10,'09:00']],'rooms':['R0']}
        chosen,status=joint(o,[q,p]);self.assertEqual(status,'optimal');self.assertEqual(len(chosen),2)
        byid={v['appointment_id']:v for v in chosen};self.assertTrue(byid['flex']['time']=='14:00' or byid['flex']['room_id']=='R1')
    def test_full_horizon_packet_round_trip(self):
        m=generate(4101,allocation_cents=22500)
        for q in m['requests']:
            p=unpack(pack(q))
            for k in ('id','owner','slots','rooms','technician','equipment','assurance_due','client_status'):self.assertEqual(p[k],q[k])
    def test_real_message_caps_and_exact_replay(self):
        d=simulate(generate(4102,allocation_cents=22500),'coop_ABC','due');r=d['result']
        self.assertFalse(r['errors']);self.assertLessEqual(r['max_pair'],32);self.assertLessEqual(r['decisions'],2160)
        self.assertEqual(r['completed']+r['failed']+r['cancelled'],120);self.assertEqual(replay(d)['status'],'passed')
        bad=cp(d);i=next(x for x in bad['trace'] if x['type']=='decision');i['observation_sha256']='0'*64
        with self.assertRaises(AssertionError):replay(bad)
    def test_pooled_control_does_not_see_future_arrivals(self):
        r=new_run(generate(4103,allocation_cents=22500),ScriptedMock());r.settle()
        ordinary=projection(r.state,r.world,'A','solo');self.assertNotIn('pooled_requests',ordinary)
        pooled=projection(r.state,r.world,'A','pooled')
        self.assertTrue(all(q['arrival_clock']<=pooled['decision_clock_tick'] for q in pooled['pooled_requests']))

if __name__=='__main__':unittest.main()

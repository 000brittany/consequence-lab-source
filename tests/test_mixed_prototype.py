import asyncio
from copy import deepcopy as cp
import tempfile
from pathlib import Path
import unittest
from consequence_lab.backends import ScriptedMock
from consequence_lab.clock import ManualClock
from consequence_lab.audit import verify_experiment
from consequence_lab.config import implementation_fingerprint
from consequence_lab_mixed.manifest import generate
from consequence_lab_mixed.world import MixedRooms,domain
from consequence_lab_mixed.run import new_run,restore
from tools.mixed_reference import check_world,check_transition
from tools.mixed_simulation import simulate
from tools.mixed_policies import action


def fixture():
 m=generate(99,allocation_cents=10000)
 r=cp(m['requests'][0]);r.update(owner='A',arrival_clock=0,slots=[[43,'09:00'],[44,'14:00'],[46,'09:00']],rooms=['R0'],technician=True,equipment=['av_kit'],assurance_due=3,client_status='confirmed',reply_by=None)
 m['origin']={'kind':'fixture','name':'boundary-unit'};m['requests']=[r];m['events']=[{'clock':0,'kind':'arrival','appointment_id':r['id']}]
 runner=new_run(m,ScriptedMock(),clock=ManualClock());runner.settle();return runner,r['id']

class MixedTests(unittest.TestCase):
 def test_generator_horizon_exchangeability_no_future_leak(self):
  for seed in range(12):
   m=generate(seed,allocation_cents=20000);self.assertEqual(m,generate(seed,allocation_cents=20000))
   self.assertEqual(len(m['requests']),120);self.assertEqual(m['requests'][0]['arrival_clock'],0);self.assertTrue(any(d==60 for d,t in m['requests'][0]['slots']))
   r=new_run(m,ScriptedMock());r.settle()
   for actor in 'ABC':
    o=r.world.observe(r.state['world'],actor)
    self.assertNotIn('latent',str(o.keys()));self.assertNotIn('parameters',o)
    self.assertTrue(all(q['owner']==actor and q['arrival_clock']<=0 for q in o['own_outstanding_requests']))
  self.assertEqual(implementation_fingerprint()['core_source_sha256'],'e12ae5c55613673744b9f6d7c177e2ae24ebc21cd3af144caf2ecb04edea2e42')
 def test_destination_accounts_atomic_cross_period(self):
  r,ident=fixture();s=r.state;w=s['world'];world=r.world
  world.apply(w,'A','/schedule',{'operation':'book','appointment_id':ident,'day':43,'time':'09:00','room_id':'R0'})
  self.assertEqual(w['accounts']['9']['encumbered'],2500);self.assertEqual(w['accounts']['1']['available'],10000)
  # Independently funded target cannot use source refund. Use a valid additional commitment to encumber target.
  q=cp(w['requests'][ident]);q.update(id='other',owner='B',slots=[[46,'14:00']],rooms=['R1'],technician=False,equipment=[])
  w['requests']['other']=q;world.apply(w,'B','/schedule',{'operation':'book','appointment_id':'other','day':46,'time':'14:00','room_id':'external:B'})
  # Leave only $30 then increase required bundle via existing $15 future booking.
  z=cp(q);z.update(id='third',owner='C',slots=[[47,'14:00']]);w['requests']['third']=z
  world.apply(w,'C','/schedule',{'operation':'book','appointment_id':'third','day':47,'time':'14:00','room_id':'R1'})
  before=cp(w)
  with self.assertRaisesRegex(ValueError,'destination'):world.apply(w,'A','/schedule',{'operation':'reschedule','appointment_id':ident,'day':46,'time':'09:00','room_id':'R0'})
  self.assertEqual(w,before);check_world(w)
 def test_hold_reschedule_does_not_renew_and_confirmation(self):
  r,i=fixture();s=r.state;w=s['world'];x=r.world
  x.apply(w,'A','/schedule',dict(operation='hold',appointment_id=i,day=43,time='09:00',room_id='R0'))
  w['tick']=2
  x.apply(w,'A','/schedule',dict(operation='reschedule',appointment_id=i,day=44,time='14:00',room_id='R0'))
  self.assertEqual(w['reservations'][i]['hold_expires_tick'],8)
  x.apply(w,'A','/schedule',dict(operation='confirm',appointment_id=i));self.assertIsNone(w['reservations'][i]['hold_expires_tick'])
  self.assertEqual(w['accounts']['9']['encumbered'],2500)
 def test_assurance_failure_after_round_and_cancel_not_erase(self):
  r,i=fixture();s=r.state;w=s['world'];w['tick']=3;w['control']['round_done']=True
  self.assertEqual(r.world.next_event(s)['kind'],'assurance.check');r.world.apply_event(s,r.world.next_event(s))
  self.assertEqual(w['requests'][i]['status'],'failed');self.assertEqual(w['outcomes'][0]['reason'],'assurance_deadline')
 def test_late_amendment_repair_after_earliest_and_atomic_refund(self):
  r,i=fixture();s=r.state;w=s['world'];x=r.world
  x.apply(w,'A','/schedule',dict(operation='book',appointment_id=i,day=43,time='09:00',room_id='R0'))
  w['day']=44;w['tick']=0
  e={'clock':172,'kind':'amend','appointment_id':i,'changes':{'slots':[[46,'09:00']],'technician':True,'equipment':['av_kit']},'repair_due':179}
  # Test notification after original earliest, before selected service using a later original reservation.
  w['requests'][i]['slots']=[[43,'09:00'],[45,'09:00']];w['reservations'][i]['day']=45
  w['manifest']['events']=[e];w['event_cursor']=0
  x.apply_event(s,x.next_event(s));self.assertEqual(w['requests'][i]['repair_due'],179)
  self.assertNotIn(i,w['reservations']);self.assertEqual(w['accounts']['9']['available'],10000)
  x.apply(w,'A','/schedule',dict(operation='book',appointment_id=i,day=46,time='09:00',room_id='R0'))
  self.assertEqual(w['accounts']['10']['encumbered'],2500);self.assertIsNone(w['requests'][i]['repair_due']);check_world(w)
 def test_independent_checks_detect_ledger_and_quote_corruption(self):
  r,i=fixture();w=r.state['world'];r.world.apply(w,'A','/schedule',dict(operation='book',appointment_id=i,day=43,time='09:00',room_id='R0'))
  bad=cp(w);bad['accounts']['9']['available']+=1
  with self.assertRaises(AssertionError):check_world(bad)
  bad=cp(w);bad['reservations'][i]['price_cents']+=1
  with self.assertRaises(AssertionError):check_world(bad)
 def test_full_domain_horizon_independent_every_transition(self):
  count=[0]
  def capture(b,a,item,memory):check_transition(b,a,item);count[0]+=1
  row=simulate(generate(102,allocation_cents=25000),'anticipatory',capture=capture)
  self.assertEqual(row['completed']+row['failed']+row['cancelled'],120);self.assertTrue(all(a['closed'] for a in row['accounts'].values()));self.assertGreater(count[0],1000)
 def test_completion_irreversible_close_expires_no_rollover(self):
  r,i=fixture();s=r.state;w=s['world'];x=r.world
  x.apply(w,'A','/schedule',dict(operation='book',appointment_id=i,day=43,time='09:00',room_id='R0'))
  w.update(day=43,tick=3,phase='service',service_index=0)
  x.apply_event(s,x.next_event(s));self.assertEqual(w['accounts']['9']['spent'],2500)
  with self.assertRaises(ValueError):x.apply(w,'A','/schedule',dict(operation='release',appointment_id=i))
  e={'clock':172,'kind':'cancel','appointment_id':i};w['manifest']['events']=[e];w['event_cursor']=0;w.update(day=44,tick=0,phase='active')
  result=x.apply_event(s,x.next_event(s));self.assertFalse(result['applicable']);self.assertEqual(w['requests'][i]['status'],'completed')
  w.update(day=45,tick=3,phase='service',service_index=2)
  x.apply_event(s,x.next_event(s));self.assertEqual(w['accounts']['9']['expired'],7500);self.assertEqual(w['accounts']['9']['available'],0)
  self.assertEqual(w['accounts']['10']['available'],10000);check_world(w)
 def test_failed_request_later_client_cancel_cannot_erase(self):
  r,i=fixture();s=r.state;w=s['world'];x=r.world;w['tick']=3;w['control']['round_done']=True;x.apply_event(s,x.next_event(s))
  w['manifest']['events'].append({'clock':4,'kind':'cancel','appointment_id':i});w.update(day=2,tick=0,phase='active')
  result=x.apply_event(s,x.next_event(s));self.assertFalse(result['applicable']);self.assertEqual(w['requests'][i]['status'],'failed');self.assertEqual(len(w['outcomes']),1)
 def test_public_forecast_independent_of_private_cards(self):
  from tools.mixed_learning import Learner
  r,i=fixture();o=r.world.observe(r.state['world'],'A');o.update(direct_messages={'new_messages':[]})
  changed=cp(o);changed['own_outstanding_requests']=[]
  a,b=Learner('public'),Learner('public');self.assertEqual(a.update(o),b.update(changed))
  self.assertEqual(a.forecasts['1']['count_prediction'],b.forecasts['1']['count_prediction'])
 def test_release_before_deadline_amendment_does_not_extend_it(self):
  r,i=fixture();s=r.state;w=s['world'];x=r.world
  x.apply(w,'A','/schedule',dict(operation='book',appointment_id=i,day=43,time='09:00',room_id='R0'))
  x.apply(w,'A','/schedule',dict(operation='release',appointment_id=i))
  e={'clock':0,'kind':'amend','appointment_id':i,'changes':{'slots':[[46,'09:00']]},'repair_due':10}
  w['manifest']['events']=[e];w['event_cursor']=0
  x.apply_event(s,x.next_event(s));self.assertIsNone(w['requests'][i]['repair_due']);self.assertEqual(w['requests'][i]['assurance_due'],3)
 def test_seeded_manifest_and_external_price_tampering_rejected(self):
  m=generate(4,allocation_cents=25000);m['prices_cents']['external']=1
  with self.assertRaises(ValueError):new_run(m,ScriptedMock())
  m=generate(4,allocation_cents=25000);m['requests'][0]['assurance_due']=3
  with self.assertRaises(ValueError):new_run(m,ScriptedMock())
 def test_paid_rejected(self):
  with self.assertRaises(PermissionError):new_run(generate(1,allocation_cents=1),ScriptedMock(),allow_provider_calls=True)

class RunnerTests(unittest.IsolatedAsyncioTestCase):
 async def test_exact_runner_prefix_files_context_restore_pending(self):
  with tempfile.TemporaryDirectory() as tmp:
   m=generate(77,allocation_cents=25000)
   scripts={a:[{'tool':'write','arguments':{'path':'/home/note','text':'persistent Δ','disposition':'continue'}},{'tool':'read','arguments':{'path':'/home/note','disposition':'yield_period'}}] for a in 'ABC'}
   root=Path(tmp)/'research';r=new_run(m,ScriptedMock(scripts),archive=root,clock=ManualClock());await r.run(6,retain_results=False)
   r.checkpoint(root/'checkpoint.json');verify_experiment(r.records,r.world);rr=restore(root/'checkpoint.json')
   self.assertEqual(rr.state,r.state);self.assertEqual(rr.records.hashes,r.records.hashes)
   r.archive.pending({'decision_id':'uncertain'})
   with self.assertRaises(RuntimeError):restore(root/'checkpoint.json')

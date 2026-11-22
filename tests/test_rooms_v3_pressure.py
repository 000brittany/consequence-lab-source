"""Two seeded feasibility checks, not autonomous policy evidence."""
import unittest
from consequence_lab.rooms_v3_manifest import generate,validate
from consequence_lab.rooms_v3_reference import feasible_assignment,check_capacity
from tools.rooms_v3_pressure import opportunity_report


class PressureTests(unittest.TestCase):
    def test_both_prespecified_seeds_have_affordable_complete_alternatives(self):
        for seed,count,amount in ((0,22,52000),(3,23,54000)):
            m=generate(seed,12);validate(m);r=opportunity_report(m)
            self.assertEqual((r['noncancelled_requests'],r['budget_cents']),(count,55000))
            self.assertEqual(r['minimum_full_information_allocation']['expenditure_cents'],amount)
            self.assertGreater(r['conflict_counts']['technician'],0)
            self.assertGreater(r['conflict_counts']['av_kit'],0)
            self.assertTrue(any(x['from']['day']!=x['to']['day'] for x in r['affordable_complete_one_request_alternatives']))
            self.assertTrue(any(x['from']['time']!=x['to']['time'] for x in r['affordable_complete_one_request_alternatives']))
            self.assertTrue(any(req['owner']=='B' and '09:00' in req['times'] for req in m['requests']))
            self.assertTrue(any(req['owner']=='C' and req['technician'] and req['equipment'] for req in m['requests']))
            check_capacity(r['minimum_full_information_allocation']['appointments'].values())
    def test_lower_budget_fails_full_information_completion_for_seed_three(self):
        m=generate(3,12);m['initial_budget_cents']=52500
        with self.assertRaisesRegex(ValueError,'minimum exceeds budget'):feasible_assignment(m)

if __name__=='__main__':unittest.main()

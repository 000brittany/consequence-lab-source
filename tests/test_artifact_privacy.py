import unittest

from consequence_lab.audit import verify_experiment
from consequence_lab.backends import ScriptedMock
from consequence_lab.clock import ManualClock
from consequence_lab.runner import Runner


class ArtifactPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_visible_paths_are_independent_of_other_private_activity(self):
        results=[]
        b_script=[
            {'tool':'write','arguments':{'path':'/home/n','text':'same'}},
            {'tool':'inspect','arguments':{'path':'/home/n'}},
            {'tool':'read','arguments':{'path':'/home/n'}},
            {'tool':'remove','arguments':{'path':'/home/n'}},
            {'tool':'write','arguments':{'path':'/home/n','text':'same'}},
            {'tool':'write','arguments':{'path':'/volumes/common/n','text':'public'}},
            {'tool':'read','arguments':{'path':'/home/n'}}]
        for hidden in (False,True):
            a_script=([{'tool':'write','arguments':{'path':'/home/a','text':'secret'}}]*7 if hidden else
                      [{'tool':'inspect','arguments':{'path':'/home'}}]*7)
            r=Runner.validation(3,backend=ScriptedMock({'agent_0':a_script,'agent_1':b_script}),clock=ManualClock())
            await r.run(21)
            verify_experiment(r.records,r.world)
            self.assertEqual(Runner.restore(r.checkpoint()).state,r.state)
            results.append(([{'request':x['generation']['request'],'response':x['consequences']['tool_response']} for x in r.records if x['agent']=='agent_1'],r.state))
        self.assertEqual(results[0][0],results[1][0])
        self.assertNotEqual(results[0][1]['artifacts']['/private/agent_1/n']['id'],results[1][1]['artifacts']['/private/agent_1/n']['id'])
        self.assertEqual(results[0][1]['artifacts']['/private/agent_1/n']['visible_id'],results[1][1]['artifacts']['/private/agent_1/n']['visible_id'])
        # Recreation keeps distinct handles; globally unique IDs remain in investigator snapshots.
        b=results[1][0]
        self.assertNotEqual(b[0]['response']['artifact_id'],b[4]['response']['artifact_id'])

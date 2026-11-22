"""Authored retrospective branch proof; no autonomous or provider execution."""
import argparse
import asyncio
from pathlib import Path
from unittest.mock import patch

from consequence_lab.archive import Archive,unseal
from consequence_lab.backends import ScriptedMock,Response
from consequence_lab.clock import ManualClock
from consequence_lab.jsonio import atomic_json,clone,digest,load_json
from consequence_lab.rooms_v3_run import new_run
from consequence_lab.runner import Runner
from tools.rooms_v3_analysis import analyze,load_verified
from tools.rooms_v3_branches import locate,prepare,replay_recorded,verify_origin
from tools.rooms_v3_validation import lifecycle_fixture,action


async def replay_suffix(runner,source,first_decision,*,exact):
    count=0;changed_requests=0
    for original in source:
        if original['event_type']!='decision.completed' or original['state_before']['decision_index']<first_decision:continue
        async def recorded(request):
            nonlocal changed_requests
            if exact and request!=original['generation']['request']:raise ValueError('Control model input differs')
            changed_requests+=int(request!=original['generation']['request'])
            response=clone(original['computation']['response'])
            # Same-length artifact perturbation permits exact mock-byte accounting.
            if runner.backend.count_input(request)!=response['usage']['input_tokens']:
                raise ValueError('Cannot reuse recorded mock usage for a different-sized input')
            runner.backend.cursors=clone(original['state_after']['backend']['state']['cursors'])
            return Response(**response)
        runner.backend.generate=recorded
        actual=await runner.step()
        if exact and actual!=original:raise ValueError('Recorded-response runtime replay differs from original event')
        count+=1
    runner.settle();runner.checkpoint(runner.archive.path/'checkpoint.json')
    return {'replayed_recorded_responses':count,'changed_model_inputs':changed_requests,'new_provider_inferences':0, 'recorded_response_injections':count,
            'mechanical_runner_decisions':count,'usage_scope':'Recorded mock usage checked against exact current input byte count',
            'response_source':'Parent completed decisions; original mock policy generate() is disabled'}


async def validate(output):
    output=Path(output);m,scripts=lifecycle_fixture('change')
    scripts['A'].insert(0,{'tool':'write','arguments':{'path':'/home/plain','text':'initial-note','disposition':'continue'}})
    scripts['A'].insert(4,{'tool':'read','arguments':{'path':'/home/plain','disposition':'continue'}})
    parent=output/'parent/research';r=new_run(m,ScriptedMock(scripts),clock=ManualClock(),archive=parent)
    await r.run(144,retain_results=False);r.checkpoint(parent/'checkpoint.json')
    records,state,_,_,_=load_verified(parent)
    decisions=[(x['decision_id'],x['event_index']) for x in records if x['event_type']=='decision.completed']
    all_boundaries=[]
    for decision_id,_ in decisions:
        for when in ('before','after'):
            rec,boundary,n=locate(records,decision_id,when)
            assert boundary==rec['state_'+when] and n==rec['event_index']+int(when=='after')
            all_boundaries.append({'decision_id':decision_id,'boundary':when,'event_index':n,'state_sha256':digest(boundary)})
    # Exercise materialized edge boundaries as well as the nonlatest middle one.
    for label,decision_id,when in (('first-before',decisions[0][0],'before'),('last-after',decisions[-1][0],'after')):
        edge=prepare(parent,decision_id,output/label,when=when)
        _,expected,_=locate(records,decision_id,when)
        assert unseal(load_json(edge))['state']==expected
        verify_origin(parent,edge.parent)
    target=next(x for x in records if x['event_type']=='decision.completed' and x['agent']=='A'
                and x['computation']['selected_action']['tool']=='read' and x['computation']['selected_action']['arguments']['path']=='/home/plain')
    assert target['event_index']<decisions[-1][1]
    base=prepare(parent,target['decision_id'],output/'retrospective',when='before')
    recovered=unseal(load_json(base))
    assert recovered['state']==target['state_before']
    assert recovered['records']['sha256']==records.hashes[:target['event_index']]
    control=Runner.branch(base,archive=output/'control')
    intervention=Runner.branch(base,{'actor':'A','path':'/home/plain','text':'revised-note'},archive=output/'intervention')
    allowed={'artifacts','artifact_revisions','relationships','event_index'}
    assert all(control.state[k]==intervention.state[k] for k in control.state if k not in allowed)
    assert control.provenance['parent_checkpoint']==intervention.provenance['parent_checkpoint']==digest(load_json(base))
    with patch.object(ScriptedMock,'generate',side_effect=AssertionError('Original policy must not run during response replay')):
        control_replay=await replay_suffix(control,records,target['state_before']['decision_index'],exact=True)
        intervention_replay=await replay_suffix(intervention,records,target['state_before']['decision_index'],exact=False)
    assert control.state==state
    control_report=analyze(control.archive.path);intervention_report=analyze(intervention.archive.path)
    assert control_report['outcomes']==intervention_report['outcomes']
    assert intervention_replay['changed_model_inputs']>0
    read_c=next(x for x in control_report['decisions'] if x['decision_id']==target['decision_id'])
    read_i=next(x for x in intervention_report['decisions'] if x['decision_id']==target['decision_id'])
    assert read_c['result']['artifact']['text']=='initial-note' and read_i['result']['artifact']['text']=='revised-note'
    atomic_json(output/'control-analysis.json',control_report);atomic_json(output/'intervention-analysis.json',intervention_report)
    result={'provider_calls':0,'scope':'Authored retrospective reconstruction and frozen-response counterfactual, not autonomous causal-behavior evidence',
            'every_completed_boundary':all_boundaries,'parent_replay':replay_recorded(parent),
            'selected_past_decision':target['decision_id'],'selected_event_index':target['event_index'],
            'latest_decision_event_index':decisions[-1][1],'original_pre_decision_state_sha256':digest(target['state_before']),
            'recovered_pre_decision_state_sha256':digest(recovered['state']),
            'control':{'replay':control_replay,'origin':verify_origin(parent,control.archive.path)},
            'intervention':{'replay':intervention_replay,'origin':verify_origin(parent,intervention.archive.path)},
            'observed_effect':'Changed artifact read and later exact model inputs; actions held fixed from recorded responses',
            'behavioral_influence_claim':False,'hidden_neural_replay_claim':False}
    atomic_json(output/'retrospective.json',result);return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();asyncio.run(validate(a.output))

if __name__=='__main__':main()

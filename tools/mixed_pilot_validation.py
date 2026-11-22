"""Real Responses serialization/decoding and Runner; local fixtures, zero network."""
import argparse,asyncio,json
from pathlib import Path
from unittest.mock import patch
from consequence_lab.clock import ManualClock
from consequence_lab.jsonio import atomic_json,canonical,digest
from consequence_lab.runner import Runner,RunLimitReached
from consequence_lab_mixed.manifest import generate
from consequence_lab_mixed.world import MixedRooms
from tools.mixed_pilot import construct,restore,specification
from tools.mixed_runner_validation import independent_records
from tools.mixed_reference import check_transition
from tools.rooms_v4_export import export,verify_export
from tools.rooms_v4_portable_audit import reassemble

class Fixture:
    network=False
    def __init__(self):self.queue=[];self.wires=[]
    async def post(self,endpoint,body,timeout):
        wire=json.loads(body);self.wires.append(wire);cmd=self.queue.pop(0)
        raw={'id':'offline-wire','model':'gpt-6-luna','status':'completed','output':[{'type':'function_call','status':'completed','call_id':'offline-call','name':cmd['tool'],'arguments':json.dumps(cmd['arguments'])}],
            'usage':{'input_tokens':6000,'output_tokens':100,'input_tokens_details':{'cached_tokens':0,'cache_write_tokens':0},'output_tokens_details':{'reasoning_tokens':50}}}
        return 200,{'x-request-id':'offline-only'},json.dumps(raw)

def cmd(tool,**args):return {'tool':tool,'arguments':args}
def act(resource,**args):return cmd('act',resource=resource,arguments=args,disposition='continue')
async def step(r,t,c):t.queue.append(c);return await r.step()

async def validate(out):
    out.mkdir(parents=True,exist_ok=False);t=Fixture();r=construct(generate(470022,allocation_cents=25000),archive=out/'research',transport=t,clock=ManualClock())
    # Engineering seed is distinct from unopened pilot seed. All actions are authored fixtures.
    for text,path in [('representation-one','/home/one'),('representation-two','/home/two')]:
        await step(r,t,cmd('write',path=path,text=text,disposition='continue'))
    await step(r,t,cmd('act',resource='/opportunities',arguments={'operation':'yield'},disposition='yield_tick'))
    # Other actors exercise private/shared files and real private-message delivery.
    await step(r,t,cmd('write',path='/volumes/common/note',text='shared fixture',disposition='continue'))
    await step(r,t,act('/messages',operation='send',recipient='A',text='fixture message'))
    await step(r,t,cmd('act',resource='/opportunities',arguments={'operation':'yield'},disposition='yield_tick'))
    await step(r,t,cmd('read',path='/volumes/common/note',disposition='continue'))
    await step(r,t,cmd('read',path='/home/one',disposition='yield_tick')) # denied: C cannot read A's private file
    r.checkpoint(out/'research/checkpoint.json');prefix=digest(r.state)
    resumed=restore(out/'research/checkpoint.json',resume=True,transport=t);assert digest(resumed.state)==prefix;r=resumed
    # Branch two files as a family using existing audited intervention events.
    branch=Runner.branch(out/'research/checkpoint.json',archive=out/'family',world_registry={MixedRooms.name:MixedRooms})
    from consequence_lab import artifacts
    from copy import deepcopy
    for path,text in [('/home/one','altered-one'),('/home/two','altered-two')]:
        before=branch.state;after=deepcopy(before);result,ops,rels=artifacts.operate(after,'A','write',{'path':path,'text':text});after['relationships'].extend(rels)
        branch._commit(before,after,'intervention.applied',intervention={'actor':'A','path':path,'text':text},permitted_changed_fields=['artifacts','artifact_revisions','relationships','event_index'],artifact_operations=ops,relationship_operations=rels,result=result)
    branch.checkpoint(out/'family/checkpoint.json');restore(out/'family/checkpoint.json')
    allowed={'artifacts','artifact_revisions','relationships','event_index'}
    assert all(branch.state[k]==r.state[k] for k in r.state if k not in allowed)
    # Model-visible reads show the family effect, while future responses stay fixture-controlled.
    bt=Fixture();branch.backend.transport=bt
    control_read=await step(r,t,cmd('read',path='/home/one',disposition='continue'))
    branch_read=await step(branch,bt,cmd('read',path='/home/one',disposition='continue'))
    assert control_read['consequences']!=branch_read['consequences']
    branch.checkpoint(out/'family/checkpoint.json');restore(out/'family/checkpoint.json')
    # Book a currently authorized request, then cancel/release using the actual provider tool route.
    actor=r.state['scheduler']['order'][r.state['scheduler']['cursor']];o=r.world.observe(r.state['world'],actor)
    from tools.mixed_policies import candidates
    for q in o['own_outstanding_requests']:
        opts=candidates(o,q)
        if q['client_status']=='confirmed' and opts:
            v=opts[0];await step(r,t,act('/schedule',operation='book',appointment_id=q['id'],**{k:v[k] for k in ('day','time','room_id')}));break
    # Continue all sixty days; ordinary observation and wire context generated by actual Runner.
    while r.state['status']=='running':
        try:await step(r,t,cmd('act',resource='/opportunities',arguments={'operation':'yield'},disposition='yield_period'))
        except RunLimitReached:
            if r.state['stop_reason']!='horizon_complete':raise
            break
    r.checkpoint(out/'research/checkpoint.json');restore(out/'research/checkpoint.json')
    export(out/'research',out/'export',run_id='OFFLINE-pilot-wire-470022',source_commit='producing source commit recorded in validation provenance')
    verify_export(out/'export');equal=reassemble(out/'research',out/'export',out/'reassembled');restore(out/'reassembled/checkpoint.json')
    count=0
    for rec in independent_records(out/'reassembled'):
        if rec['event_type']=='environment.transition':check_transition(rec['state_before'],rec['state_after'],{'type':'environment','event':rec['environment_event'],'result':rec['environment_result']});count+=1
        elif rec['event_type']=='decision.completed' and rec['computation']['selected_action']['tool']=='act':
            check_transition(rec['state_before'],rec['state_after'],{'type':'decision','actor':rec['agent'],'action':rec['computation']['selected_action'],'result':rec['consequences']['tool_response']});count+=1
    assert all(w['reasoning']=={'effort':'high'} and not w['store'] and not w['parallel_tool_calls'] for w in t.wires)
    report={'scope':'actual ResponsesBackend wire/decode, Runner and compact archive; offline authored fixtures, no performance claim','provider_calls':0,'status':'passed','days':60,'decisions':r.state['ledger']['calls'],'events':r.state['event_index'],'independent_domain_records':count,'portable_source_equality':equal,'family_files':2,'resume_exact':True,'wire_bytes':{'min':min(len(canonical(w)) for w in t.wires),'max':max(len(canonical(w)) for w in t.wires)},'specification':specification()}
    atomic_json(out/'validation.json',report);print(json.dumps(report,indent=2))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();asyncio.run(validate(a.output))

"""Read-only parent reconstruction and explicit retrospective branch preparation.

Never invokes a model or chooses an agent action. All original files stay intact.
"""
import argparse
import os
from pathlib import Path
import shutil

from consequence_lab.archive import Archive,FileRecords,seal,unseal,verify_checkpoint_records
from consequence_lab.jsonio import atomic_json,clone,digest,load_json
from tools.rooms_v3_analysis import load_verified


def locate(records,decision_id,when='before'):
    if when not in ('before','after'):raise ValueError('Boundary must be before or after')
    for record in records:
        if record['event_type']=='decision.completed' and record['decision_id']==decision_id:
            state=record['state_'+when]
            return record,state,state['event_index']
    raise ValueError('Completed decision not found')


def prepare(parent,decision_id,output,*,when='before'):
    """Create a portable full-prefix branch checkpoint at ANY completed decision.

    Source and every prefix event are verified before copying exact sealed bytes.
    The new clock starts from the recorded elapsed ledger anchor. For ManualClock
    this is exact; historical sub-ledger wall-clock samples were never captured.
    Deterministic response replay treats recorded timing as observed inputs.
    """
    parent,output=Path(parent),Path(output)
    records,_,_,semantic,independent=load_verified(parent)
    event,state,count=locate(records,decision_id,when)
    source_checkpoint=load_json(parent/'checkpoint.json');saved=unseal(source_checkpoint)
    origin={'version':'retrospective-decision-boundary-v1','decision_id':decision_id,'boundary':when,
            'source_checkpoint_sha256':digest(source_checkpoint),'source_event_index':event['event_index'],
            'source_event_sha256':load_json(parent/'records'/f"{event['event_index']:08d}.json")['sha256'],
            'state_sha256':digest(state),'prefix_events':count,'implementation':state['config']['implementation'],
            'manifest_sha256':digest(state['world']['manifest']),
            'clock_basis':'Recorded elapsed ledger anchor; original microsecond wall-clock sampling is not inferred',
            'scope':'Prepared checkpoint, no inference or provider call; parent remains immutable'}
    target=Archive(output)
    for i in range(count):
        records[i]  # Verify the anchored parent event on this read.
        dest=output/'records'/f'{i:08d}.json'
        shutil.copyfile(parent/'records'/dest.name,dest)
        with dest.open('rb') as stream:os.fsync(stream.fileno())
    prefix=FileRecords(output)
    provenance=clone(saved['provenance']);provenance['retrospective_origin']=origin
    provenance['parent_checkpoint']=digest(source_checkpoint)
    payload={'schema_version':2,'state':clone(state),'records':prefix.descriptor(),
             'clock':{'kind':saved['clock']['kind'],'elapsed':state['ledger']['elapsed_seconds']},'provenance':provenance}
    verify_checkpoint_records(payload,prefix)
    atomic_json(output/'checkpoint.json',seal(payload));atomic_json(output/'branch-origin.json',origin)
    return output/'checkpoint.json'


def verify_origin(parent,branch):
    """Independently recheck ancestry against the full verified original archive."""
    parent,branch=Path(parent),Path(branch)
    records,_,_,_,_=load_verified(parent)
    branch_records,_,_,semantic,independent=load_verified(branch)
    saved=unseal(load_json(branch/'checkpoint.json'));origin=saved['provenance']['retrospective_origin']
    event,state,count=locate(records,origin['decision_id'],origin['boundary'])
    if (origin['source_checkpoint_sha256']!=digest(load_json(parent/'checkpoint.json')) or origin['source_event_index']!=event['event_index']
            or origin['source_event_sha256']!=load_json(parent/'records'/f"{event['event_index']:08d}.json")['sha256']
            or origin['state_sha256']!=digest(state) or origin['prefix_events']!=count
            or origin['implementation']!=state['config']['implementation'] or origin['manifest_sha256']!=digest(state['world']['manifest'])):
        raise ValueError('Retrospective ancestry mismatch')
    if len(branch_records)<count:raise ValueError('Branch prefix truncated')
    for i in range(count):
        if (parent/'records'/f'{i:08d}.json').read_bytes()!=(branch/'records'/f'{i:08d}.json').read_bytes():
            raise ValueError('Branch source prefix rewritten')
    boundary=branch_records[count]['state_before'] if len(branch_records)>count else saved['state']
    if boundary!=state:raise ValueError('Original decision-boundary state not exactly preserved')
    return {'state_sha256':digest(state),'source_event_index':event['event_index'],'prefix_events':count,
            'semantic':semantic,'independent':independent,'provider_calls':0}


def replay_recorded(parent):
    """Transition replay of observed responses, not neural regeneration.

    load_verified invokes the semantic auditor, which dispatches recorded model
    responses, rebuilds histories/accounting and compares every reconstructed
    aggregate state. The separate domain oracle adds independent reconstruction.
    Measured timing/usage are replay inputs, never new counterfactual API usage.
    """
    records,state,scope,semantic,independent=load_verified(parent)
    return {'method':'Recorded-response transition reconstruction; measured timing inputs retained',
            'state_sha256':digest(state),'scope':scope,'semantic':semantic,'independent':independent,
            'provider_calls':0,'neural_computation_reproduced':False}


def main():
    p=argparse.ArgumentParser();p.add_argument('parent',type=Path);p.add_argument('--decision-id',required=True)
    p.add_argument('--when',choices=('before','after'),default='before');p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();prepare(a.parent,a.decision_id,a.output,when=a.when)

if __name__=='__main__':main()

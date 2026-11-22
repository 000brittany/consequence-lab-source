"""Offline matched artifact-family forks. Never alters parent or enables transport."""
from copy import deepcopy
from pathlib import Path
from consequence_lab import artifacts
from consequence_lab.runner import Runner
from consequence_lab_mixed.world import MixedRooms
from tools.mixed_pilot import restore

def prepare_pair(checkpoint,output,actor,replacements):
    if not replacements:raise ValueError('Nonempty family required')
    branches=[]
    for name in ('control','intervention'):
        b=Runner.branch(checkpoint,archive=Path(output)/name,world_registry={MixedRooms.name:MixedRooms})
        for path,replacement in replacements.items():
            before=b.state;after=deepcopy(before);resolved=artifacts.resolve(path,actor)
            if resolved not in before['artifacts'] or before['artifacts'][resolved]['deleted']:raise ValueError('Family member not live')
            text=before['artifacts'][resolved]['text'] if name=='control' else replacement
            result,ops,rels=artifacts.operate(after,actor,'write',{'path':path,'text':text});after['relationships'].extend(rels)
            b._commit(before,after,'intervention.applied',intervention={'actor':actor,'path':path,'text':text},permitted_changed_fields=['artifacts','artifact_revisions','relationships','event_index'],artifact_operations=ops,relationship_operations=rels,result=result)
        b.checkpoint(b.archive.path/'checkpoint.json');restore(b.archive.path/'checkpoint.json');branches.append(b)
    a,b=[r.state for r in branches]
    assert all(a[k]==b[k] for k in a if k not in {'artifacts','artifact_revisions','relationships'})
    assert len(a['artifact_revisions'])==len(b['artifact_revisions'])
    return branches

def checkpoint_before(parent,decision_id,output):
    """Reconstruct an exact logical prefix; no network, parent mutation or action replay."""
    from consequence_lab.archive import seal
    from consequence_lab.compact_archive import CompactArchive
    from consequence_lab.jsonio import atomic_json,digest,load_json
    original=restore(Path(parent)/'checkpoint.json');target=None
    for rec in original.records:
        if rec['event_type']=='decision.completed' and rec['decision_id']==decision_id:target=rec;break
    if target is None:raise ValueError('Unknown completed decision')
    state=target['state_before'];archive=CompactArchive(output)
    for i in range(state['event_index']):archive.commit(original.records[i])
    provenance=deepcopy(original.provenance);provenance['retrospective_origin']={'parent_checkpoint':digest(load_json(Path(parent)/'checkpoint.json')),'decision_id':decision_id,'state_sha256':digest(state),'scope':'Exact logical prefix; wall-clock anchor uses recorded ledger, not unobserved sub-ledger timing'}
    clock={'kind':original.clock.kind,'elapsed':state['ledger']['elapsed_seconds']}
    atomic_json(Path(output)/'checkpoint.json',seal({'schema_version':2,'state':state,'records':archive.record_sequence().descriptor(),'clock':clock,'provenance':provenance}));restore(Path(output)/'checkpoint.json')
    return Path(output)/'checkpoint.json'

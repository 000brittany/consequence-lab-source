"""Independent streaming JSON/delta decoder. No production patch decoder is used."""
import copy, gzip, hashlib, json
from pathlib import Path


def canonical(v):
    return json.dumps(v,sort_keys=True,separators=(',', ':'),ensure_ascii=False,allow_nan=False).encode()
def sha(v): return hashlib.sha256(v).hexdigest()
def object_sha(v): return sha(canonical(v))
def checked_json(raw):
    def unique(items):
        out={}
        for k,v in items:
            if k in out: raise ValueError('duplicate JSON key')
            out[k]=v
        return out
    return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda x:(_ for _ in ()).throw(ValueError(x)))
def independent_patch(before,p):
    if p is None: return copy.deepcopy(before)
    tag=p[0]
    if tag=='=' and len(p)==2:return copy.deepcopy(p[1])
    if tag=='a' and len(p)==3:
        assert type(before) is list and len(before)==p[1]
        return copy.deepcopy(before)+copy.deepcopy(p[2])
    if tag=='d' and len(p)==3:
        assert type(before) is dict
        result=copy.deepcopy(before)
        for k in p[2]:del result[k]
        for k,v in p[1].items():result[k]=independent_patch(before.get(k),v)
        return result
    raise ValueError('bad patch')

def iter_decode(root, expected=None, metrics=None):
    if not __debug__: raise RuntimeError("Independent checks require assertions enabled")
    metrics = metrics if metrics is not None else {}
    metrics.update(events=0, decisions=0, logical_record_bytes=0, tool_counts={}, memory_operations={})
    root=Path(root)
    assert not (root/'pending.json').exists()
    inv=checked_json((root/'index.json').read_bytes())
    assert inv['storage']=='snapshot-delta-gzip-v1' and inv['segment_events']==32
    entries=inv['records']
    assert sorted(x.name for x in (root/'records').iterdir())==[f'{i:08d}.json.gz' for i in range(len(entries))]
    state=None;count=0
    for i,e in enumerate(entries):
        raw=(root/'records'/f'{i:08d}.json.gz').read_bytes()
        assert len(raw)==e['compressed_bytes'] and sha(raw)==e['compressed_sha256']
        w=checked_json(gzip.decompress(raw))
        assert w['schema']==inv['storage'] and w['event_index']==i
        assert w['previous_record_sha256']==(entries[i-1]['sha256'] if i else None)
        if i%32==0:
            if state is not None:assert canonical(state)==canonical(w['base'])
            state=w['base']
        else:assert w['base'] is None
        assert object_sha(state)==w['state_before_sha256']
        after=independent_patch(state,w['after'])
        rec={**w['metadata'],'state_before':state,'state_after':after}
        raw_logical=canonical(rec)
        assert sha(raw_logical)==w['record_sha256']==e['sha256']
        metrics['events']+=1;metrics['logical_record_bytes']+=len(raw_logical)
        if rec['event_type']=='decision.completed':
            metrics['decisions']+=1
            assert all(k in rec for k in ('registration','generation','computation','exposure','consequences'))
            action=rec['computation']['selected_action']
            tool=action['tool'] if action else 'none'
            metrics['tool_counts'][tool]=metrics['tool_counts'].get(tool,0)+1
            for operation in rec['consequences']['memory_operations']:
                kind=operation['kind']
                metrics['memory_operations'][kind]=metrics['memory_operations'].get(kind,0)+1
        assert rec['event_index']==state['event_index']==i and after['event_index']==i+1
        if expected is not None:assert canonical(rec)==canonical(expected[i])
        yield rec
        state=after;count+=1
    c=checked_json((root/'checkpoint.json').read_bytes())
    assert object_sha(c['body'])==c['sha256']
    assert c['body']['records']=={'storage':inv['storage'],'sha256':[e['sha256'] for e in entries]}
    assert canonical(c['body']['state'])==canonical(state)
    metrics['checkpoint_sha256']=sha((root/'checkpoint.json').read_bytes())
    metrics['terminal_logical_state_sha256']=object_sha(state)
    metrics['complete_histories']={a:len(v['history']) for a,v in state['agents'].items()}
    metrics['artifact_revisions']=len(state['artifact_revisions'])
    metrics['terminal_artifact_bytes']={p:len(v['text'].encode('utf-8')) for p,v in state['artifacts'].items()}
    metrics['source_implementation']=state['config']['implementation']
    metrics['status']=state['status']
    metrics['outcomes']={k:sum(o['status']==k for o in state['world']['outcomes']) for k in ('completed','cancelled','failed')}
    metrics['funds']=state['world']['budget']
    if expected is not None:assert len(expected)==count

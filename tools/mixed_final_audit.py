"""Independent selected transition audits and complete batch byte inventories."""
import argparse,gzip,hashlib,json
from pathlib import Path
from tools.mixed_final_calibration import replay

def selected(root):
    rows=[]
    for dataset,name in [('heldout','3200-22500-coop_AB.json.gz'),('reciprocity','3200-22500-conditional_AB_none.json.gz')]:
        data=json.loads(gzip.decompress((root/dataset/'traces'/name).read_bytes()));r=replay(data,independent=True);rows.append({'dataset':dataset,'file':name,**r})
    return {'scope':'Separate arithmetic/capacity/client eligibility/event-causation checks at every transition, alongside exact source replay','rows':rows}

def inventory(root):
    rows=[];manifests={}
    for dataset,n in [('development',12),('heldout',624),('reciprocity',192),('individual-early',48),('individual-countfirst',48)]:
        p=root/dataset;data=json.loads((p/'results.json').read_text());assert len(data['rows'])==n;seen=set();transitions=0
        for row in data['rows']:
            name=row['trace_file'];assert name not in seen;seen.add(name);raw=(p/'traces'/name).read_bytes();assert hashlib.sha256(raw).hexdigest()==row['trace_sha256']
            case=json.loads(gzip.decompress(raw));assert all(case['result'][k]==v for k,v in row.items() if k in case['result'])
            assert case['audit']['status']=='passed' and row['completed']+row['failed']+row['cancelled']==120
            transitions+=case['audit']['transitions'];m=dict(case['manifest']);assert m.pop('allocations_cents')==[row['allocation']]*12
            assert m['seed']==row['seed'] and m['parameters']['count']==120
            key=json.dumps(m,sort_keys=True);assert manifests.setdefault(row['seed'],key)==key
        assert seen=={p.name for p in (p/'traces').iterdir()}
        rows.append({'dataset':dataset,'cases':n,'source_replayed_transitions':transitions,'status':'passed'})
    return {'status':'passed','scope':'Every raw trace checksum, complete file set, exact summary binding and completed source-replay result','rows':rows}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--selected',action='store_true');a=p.parse_args();out=a.root/'audit';out.mkdir(exist_ok=True)
    result=selected(a.root) if a.selected else inventory(a.root);(out/('selected.json' if a.selected else 'inventory.json')).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))

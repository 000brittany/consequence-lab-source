"""Verify every completed batch's trace bytes and exact scientific seed manifests."""
import argparse,gzip,hashlib,json
from copy import deepcopy
from pathlib import Path
from consequence_lab_mixed.manifest import generate

def check(root):
    reports=[]
    for name in ('development','heldout','discoverability','known-parameter-corrected','privileged-lookahead','oracle-sensitivity'):
        folder=root/name;rows=json.loads((folder/'results.json').read_text())['rows'];files=set();fingerprints=set()
        assert len(rows)==json.loads((folder/'summary.json').read_text())['cases']
        for row in rows:
            path=folder/'traces'/row['trace_file'];assert path.name not in files;files.add(path.name)
            raw=path.read_bytes();assert hashlib.sha256(raw).hexdigest()==row['trace_sha256']
            data=json.loads(gzip.decompress(raw));m=data['manifest'];fingerprints.add(m['prototype_sha256'])
            expected=generate(m['seed'],allocation_cents=m['allocations_cents'][0],**m['parameters']);actual=deepcopy(m)
            for x in (actual,expected):x.pop('origin',None);x.pop('prototype_sha256',None)
            assert actual==expected
            assert data['result']['seed']==row['seed'] and data['result']['policy']==row['policy']
            assert all(data['result'][k]==row[k] for k in ('completed','failed','cancelled','accounts','forecasts') if k in data['result'])
            assert row['completed']+row['failed']+row['cancelled']==m['parameters']['count']
        assert files=={p.name for p in (folder/'traces').iterdir()}
        reports.append({'batch':name,'cases':len(rows),'prototype_fingerprints':sorted(fingerprints)})
    return {'status':'passed','batches':reports,'scope':'Exact raw trace checksums, complete trace inventories, seed scientific manifests and summary/outcome consistency. Not independent replay of all batch transitions.'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);args=p.parse_args()
    report=check(args.root);(args.root/'batch-integrity.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))

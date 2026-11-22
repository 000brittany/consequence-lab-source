"""Portable evidence packaging/readback; no remote uploads or paid transports.

Run pack only after producers finish. Publish its destination on a separate
parentless evidence branch, then verify a fresh Git clone with `verify`.
"""
import argparse,gzip,hashlib,io,json,re,shutil,subprocess,tarfile
from pathlib import Path

PATTERNS={
    'openai_style_key':rb'\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}',
    'github_token':rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})',
    'aws_access_key':rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    'private_key':rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    'bearer_value':rb'(?i)authorization["\s:=\\]+bearer\s+[A-Za-z0-9._-]{16,}',
}

def digest(stream):
    h=hashlib.sha256()
    for b in iter(lambda:stream.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def sha(path):
    with path.open('rb') as stream:return digest(stream)
def encoded(x):return (json.dumps(x,indent=2,sort_keys=True)+'\n').encode()

def safe(name):
    p=Path(name);assert not p.is_absolute() and '..' not in p.parts

def scan(files):
    total=0
    for p in files:
        raw=p.read_bytes()
        if p.suffix=='.gz':raw=gzip.decompress(raw)
        total+=len(raw)
        for name,pattern in PATTERNS.items():
            if re.search(pattern,raw):raise ValueError(f'Credential-like pattern {name} in {p.name}; do not transfer')
    return {'files':len(files),'decoded_bytes':total,'credential_pattern_matches':[],
        'patterns':list(PATTERNS),'limits':'Not a universal sensitive-data classifier; datasets are authored synthetic offline fixtures and traces.'}

def pack(root,dest,source,logs):
    dest.mkdir(parents=True,exist_ok=False);(dest/'archives').mkdir();(dest/'source').mkdir();(dest/'review').mkdir()
    datasets=[]
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        files=sorted(p for p in folder.rglob('*') if p.is_file() and 'reassembled' not in p.relative_to(folder).parts)
        assert files and not any(p.is_symlink() for p in files)
        inventory={str(p.relative_to(folder)):{'bytes':p.stat().st_size,'sha256':sha(p)} for p in files}
        meta={'dataset':folder.name,'files':inventory,'credential_scan':scan(files),
            'scope':'See source report: complete and incomplete/superseded batches are explicitly distinguished.'}
        raw=encoded(meta);archive=dest/'archives'/f'{folder.name}.tar.gz'
        with tarfile.open(archive,'w:gz',compresslevel=6) as tar:
            for p in files:tar.add(p,arcname=folder.name+'/'+str(p.relative_to(folder)),recursive=False)
            entry=tarfile.TarInfo(folder.name+'/dataset-manifest.json');entry.size=len(raw);entry.mtime=0;tar.addfile(entry,io.BytesIO(raw))
        datasets.append({'file':str(archive.relative_to(dest)),'bytes':archive.stat().st_size,'sha256':sha(archive),
            'files':len(files),'inner_inventory_sha256':hashlib.sha256(raw).hexdigest()})
    for p in sorted(root.glob('*.json')):shutil.copyfile(p,dest/'review'/p.name)
    logfiles=sorted(logs.glob('mixed-*.log'));logscan=scan(logfiles)
    for p in logfiles:shutil.copyfile(p,dest/'review'/p.name)
    commits=['5daa082','f2e4092','77030aa','HEAD'];sources=[]
    for ref in commits:
        commit=subprocess.check_output(['git','rev-parse',ref],cwd=source,text=True).strip();p=dest/'source'/f'{commit}.tar.gz'
        if not p.exists():
            with p.open('wb') as stream:subprocess.run(['git','archive','--format=tar.gz','--prefix=source/',commit],cwd=source,stdout=stream,check=True)
        sources.append({'commit':commit,'file':str(p.relative_to(dest)),'sha256':sha(p)})
    evidence={'datasets':datasets,'sources':sources,'log_scan':logscan,'provider_calls':0,
        'scope':'Offline mixed-horizon prototype; no baseline records replaced. Separate orphan evidence branch, no scientific-history merge.',
        'provenance':'5daa082: initial controllers and superseded law; f2e4092: corrected law and original package; 77030aa: hardened package and current Runner; final source: report/audit/packaging tools. Exact package fingerprints in source report. Replay proves recorded actions, not autonomous discovery.',
        'baseline_evidence_commit':'d2969825b421e1b6d27e45ef071c989b94b0037d'}
    (dest/'evidence.json').write_bytes(encoded(evidence))
    shutil.copyfile(Path(__file__),dest/'verify.py')
    (dest/'README.md').write_text('# Mixed Rooms offline evidence\n\nPrivate durable evidence only; do not merge into scientific source history.\nRead evidence.json and the source report before using any batch.\n`clairvoyant` is incomplete and excluded; `privileged-lookahead` is complete but not a hindsight optimum.\nOld law rows in discoverability are superseded by known-parameter-corrected.\nDomain traces have no model contexts; Runner fixtures preserve complete recorded interfaces.\nNo model calls or autonomous performance claims.\n\nVerify a fresh native Git clone with `python verify.py verify --root .`.\n')
    paths=sorted(p for p in dest.rglob('*') if p.is_file())
    (dest/'SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.relative_to(dest)}\n' for p in paths))
    return {'datasets':len(datasets),'bytes':sum(p.stat().st_size for p in dest.rglob('*') if p.is_file())}

def verify(root):
    checked=[]
    for line in (root/'SHA256SUMS').read_text().splitlines():
        expected,name=line.split('  ',1);safe(name);assert sha(root/name)==expected,name;checked.append(name)
    evidence=json.loads((root/'evidence.json').read_text());results=[]
    for package in evidence['datasets']:
        p=root/package['file'];assert p.stat().st_size==package['bytes'] and sha(p)==package['sha256']
        with tarfile.open(p) as tar:
            members=tar.getmembers();names=[m.name for m in members];assert len(names)==len(set(names)) and all(m.isfile() for m in members)
            for name in names:safe(name)
            entry=next(n for n in names if n.endswith('/dataset-manifest.json'));raw=tar.extractfile(entry).read()
            assert hashlib.sha256(raw).hexdigest()==package['inner_inventory_sha256'];meta=json.loads(raw);prefix=meta['dataset']+'/'
            assert set(names)=={prefix+n for n in meta['files']}|{entry}
            for name,info in meta['files'].items():
                member=tar.getmember(prefix+name);assert member.size==info['bytes']
                with tar.extractfile(member) as stream:assert digest(stream)==info['sha256'],name
            results.append({'dataset':meta['dataset'],'inner_files':len(meta['files']),'sha256':package['sha256']})
    return {'status':'passed','top_level_files':len(checked),'datasets':results,'scope':'Every outer file and every packaged inner file, without relying on source manifests alone.'}

if __name__=='__main__':
    p=argparse.ArgumentParser();subs=p.add_subparsers(dest='command',required=True)
    a=subs.add_parser('pack');a.add_argument('--root',type=Path,required=True);a.add_argument('--destination',type=Path,required=True);a.add_argument('--source',type=Path,required=True);a.add_argument('--logs',type=Path,required=True)
    a=subs.add_parser('verify');a.add_argument('--root',type=Path,required=True);args=p.parse_args()
    result=verify(args.root) if args.command=='verify' else pack(args.root,args.destination,args.source,args.logs)
    print(json.dumps(result,indent=2))

"""Package prepared pilot/offline fixture; no launch or transport."""
import argparse,hashlib,io,json,shutil,subprocess,tarfile
from pathlib import Path
from tools.mixed_evidence import sha,scan,encoded

def pack(root,dest,source):
    report=json.loads((root/'wire60/validation.json').read_text());assert report['status']=='passed' and report['provider_calls']==0
    dest.mkdir(exist_ok=False);(dest/'archives').mkdir();(dest/'source').mkdir();(dest/'review').mkdir();datasets=[]
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        files=sorted(p for p in folder.rglob('*') if p.is_file() and 'reassembled' not in p.relative_to(folder).parts)
        meta={'dataset':folder.name,'files':{str(p.relative_to(folder)):{'sha256':sha(p),'bytes':p.stat().st_size} for p in files},'credential_scan':scan(files),'scope':'Proposal or authored offline wire fixture, NOT autonomous/model performance'};raw=encoded(meta);archive=dest/'archives'/f'{folder.name}.tar.gz'
        with tarfile.open(archive,'w:gz') as tar:
            for p in files:tar.add(p,arcname=folder.name+'/'+str(p.relative_to(folder)),recursive=False)
            m=tarfile.TarInfo(folder.name+'/dataset-manifest.json');m.size=len(raw);m.mtime=0;tar.addfile(m,io.BytesIO(raw))
        datasets.append({'file':str(archive.relative_to(dest)),'bytes':archive.stat().st_size,'sha256':sha(archive),'inner_inventory_sha256':hashlib.sha256(raw).hexdigest()})
    logs=sorted(root.parent.glob('pilot-*.log'));logscan=scan(logs)
    for p in logs:shutil.copyfile(p,dest/'review'/p.name)
    sources=[]
    for ref in ['050c51c','HEAD']:
        commit=subprocess.check_output(['git','rev-parse',ref],cwd=source,text=True).strip();path=dest/'source'/f'{commit}.tar.gz'
        with path.open('wb') as stream:subprocess.run(['git','archive','--format=tar.gz','--prefix=source/',commit],cwd=source,stdout=stream,check=True)
        sources.append({'commit':commit,'file':str(path.relative_to(dest)),'sha256':sha(path)})
    (dest/'evidence.json').write_bytes(encoded({'scope':'Pilot preparation only; zero paid calls, no permission to launch','datasets':datasets,'sources':sources,'log_scan':logscan,'provider_calls':0,'original_loop_boundary':'Full fixture terminal checkpoint finalized from complete committed journal; raw records unchanged. Prefix checkpoint and failure log retained.'}))
    shutil.copyfile(source/'tools/mixed_evidence.py',dest/'verify.py');(dest/'README.md').write_text('# Pilot readiness evidence\n\nPreparation only. No paid trajectory was launched. Fixture words/actions are authored engineering tests, not prompts or evidence of agent subgoals. Read the source pilot REVIEW.md.\n\nVerify a fresh clone: `python verify.py verify --root .`.\n')
    files=sorted(p for p in dest.rglob('*') if p.is_file());(dest/'SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.relative_to(dest)}\n' for p in files));return {'datasets':len(datasets)}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--destination',type=Path,required=True);p.add_argument('--source',type=Path,required=True);a=p.parse_args();print(json.dumps(pack(a.root,a.destination,a.source)))

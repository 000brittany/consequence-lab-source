"""Package final calibration without modifying prior evidence or scientific history."""
import argparse,hashlib,io,json,shutil,subprocess,tarfile
from pathlib import Path
from tools.mixed_evidence import sha,scan,encoded

def pack(root,dest,source,logs):
    assert json.loads((root/'audit/inventory.json').read_text())['status']=='passed'
    dest.mkdir(exist_ok=False);(dest/'archives').mkdir();(dest/'review').mkdir();(dest/'source').mkdir();datasets=[]
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        files=sorted(p for p in folder.rglob('*') if p.is_file());assert not any(p.is_symlink() for p in files)
        inventory={str(p.relative_to(folder)):{'bytes':p.stat().st_size,'sha256':sha(p)} for p in files}
        metadata={'dataset':folder.name,'files':inventory,'sensitive_scan':scan(files),'scope':'Offline researcher-authored controls on unchanged mixed-v1 world; see final report and exact source kits.'};raw=encoded(metadata)
        archive=dest/'archives'/f'{folder.name}.tar.gz'
        with tarfile.open(archive,'w:gz',compresslevel=6) as tar:
            for p in files:tar.add(p,arcname=folder.name+'/'+str(p.relative_to(folder)),recursive=False)
            m=tarfile.TarInfo(folder.name+'/dataset-manifest.json');m.size=len(raw);m.mtime=0;tar.addfile(m,io.BytesIO(raw))
        datasets.append({'file':str(archive.relative_to(dest)),'bytes':archive.stat().st_size,'sha256':sha(archive),'inner_inventory_sha256':hashlib.sha256(raw).hexdigest()})
    for p in root.glob('*.json'):shutil.copyfile(p,dest/'review'/p.name)
    logfiles=sorted(logs.glob('final-*.log'));logscan=scan(logfiles)
    for p in logfiles:shutil.copyfile(p,dest/'review'/p.name)
    sources=[]
    for ref in ('22a878a','d0a6694','HEAD'):
        commit=subprocess.check_output(['git','rev-parse',ref],cwd=source,text=True).strip();path=dest/'source'/f'{commit}.tar.gz'
        with path.open('wb') as stream:subprocess.run(['git','archive','--format=tar.gz','--prefix=source/',commit],cwd=source,stdout=stream,check=True)
        sources.append({'commit':commit,'file':str(path.relative_to(dest)),'sha256':sha(path)})
    evidence={'scope':'Final offline scientific calibration; no new world rules, no paid/model calls, no autonomous agency claims','datasets':datasets,'sources':sources,'log_scan':logscan,'provider_calls':0,
        'prototype_sha256':'610fdaac154d8f950ef4b2c129b11fac34074e285b2a81ecd79ed24b2c568587',
        'prior_mixed_evidence':'ef5fa75053bf22ab82aff123c6cfb44ac7043f2f','baseline_v4_evidence':'d2969825b421e1b6d27e45ef071c989b94b0037d'}
    (dest/'evidence.json').write_bytes(encoded(evidence));shutil.copyfile(source/'tools/mixed_evidence.py',dest/'verify.py')
    (dest/'README.md').write_text('# Final offline mixed-Rooms calibration\n\nParentless private evidence branch; do not merge into scientific source history.\nAll policies are supplied offline controls. World/generator/120 requests remain unchanged.\nRead the final source report for information/optimization/accommodation/reciprocity distinctions.\nNo model calls or autonomous subgoal claims.\n\nVerify a fresh Git clone: `python verify.py verify --root .`.\n')
    files=sorted(p for p in dest.rglob('*') if p.is_file());(dest/'SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.relative_to(dest)}\n' for p in files))
    return {'datasets':len(datasets),'bytes':sum(p.stat().st_size for p in dest.rglob('*') if p.is_file())}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--destination',type=Path,required=True);p.add_argument('--source',type=Path,required=True);p.add_argument('--logs',type=Path,required=True);a=p.parse_args();print(json.dumps(pack(a.root,a.destination,a.source,a.logs),indent=2))

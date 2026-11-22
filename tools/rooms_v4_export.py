"""Streaming investigator export. Exact gzip record intervals retain state anchors."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

from consequence_lab.archive import Archive, unseal
from consequence_lab.compact_archive import CompactRecords, SEGMENT_EVENTS, STORAGE
from consequence_lab.jsonio import atomic_json, canonical, clone, load_json


def file_sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def export(root,output,*,run_id,source_commit,max_pack_bytes=128*1024*1024):
    root,output=Path(root),Path(output)
    source_hashes={name:file_sha(root/name) for name in ('checkpoint.json','index.json')}
    records=Archive.read(root)
    if not isinstance(records,CompactRecords):
        raise ValueError('Export requires the separately versioned compact archive')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Export output must be empty')
    output.mkdir(parents=True,exist_ok=True)
    state=unseal(load_json(root/'checkpoint.json'))['state']
    summary={'scope':'investigator export; exact original logical record seals retained',
        'run_id':run_id,'source_commit':source_commit,
        'label_provenance':'run_id and source_commit are caller assertions; implementation fingerprint is from the verified checkpoint',
        'saved_provenance':unseal(load_json(root/'checkpoint.json'))['provenance'],
        'implementation':state['config']['implementation'],
        'manifest_sha256':__import__('consequence_lab.jsonio',fromlist=['digest']).digest(state['world']['manifest']),
        'days':state['world']['manifest']['days'],'status':state['status'],'ledger':state['ledger'],
        'events':len(records),'outcomes':{},'decision_classes':{},'artifact_operations':{},
        'verification':'Unpack all packs into an empty directory, decompress checkpoint.json.gz; use the exact source commit and rooms_v4_capacity --verify-only. A single complete pack can reconstruct its logical interval using its included full-state anchor; standalone non-anchor records require earlier records in that pack.'}
    with (output/'events.jsonl').open('w') as events,(output/'decisions.jsonl').open('w') as decisions:
        for rec in records:
            segment=rec['event_index']//SEGMENT_EVENTS
            item={'event_index':rec['event_index'],'event_type':rec['event_type'],
                  'day':rec['state_before']['world']['day'],'tick':rec['state_before']['world']['tick'],
                  'record_sha256':records.hashes[rec['event_index']], 'pack':f'segment-{segment:05d}.tar'}
            if rec['event_type']=='environment.transition':item['environment_event']=rec['environment_event']
            events.write(json.dumps(item,sort_keys=True)+'\n')
            if rec['event_type']=='decision.completed':
                c=rec['computation'];classification=c.get('classification','legacy')
                summary['decision_classes'][classification]=summary['decision_classes'].get(classification,0)+1
                for op in rec['consequences']['memory_operations']:
                    kind=op['kind'];summary['artifact_operations'][kind]=summary['artifact_operations'].get(kind,0)+1
                decisions.write(json.dumps({**item,'decision_id':rec['decision_id'],'agent':rec['agent'],
                    'classification':classification,'action':c['selected_action'],
                    'tool_result':rec['consequences']['tool_response'],
                    'request_sha256':rec['registration']['context_selection']['request_sha256'],
                    'exact_content_location':'logical record generation/computation/exposure/consequences/state snapshots'},sort_keys=True)+'\n')
    for outcome in state['world']['outcomes']:
        key=outcome['status'];summary['outcomes'][key]=summary['outcomes'].get(key,0)+1
    packs=[]
    for start in range(0,len(records),SEGMENT_EVENTS):
        stop=min(start+SEGMENT_EVENTS,len(records));path=output/f'segment-{start//SEGMENT_EVENTS:05d}.tar'
        names=[root/'records'/f'{i:08d}.json.gz' for i in range(start,stop)]
        # Include tar overhead in the cap; never silently split an anchor interval.
        estimated=sum(((p.stat().st_size+511)//512+1)*512 for p in names)+16384
        if estimated>max_pack_bytes:
            raise ValueError('One exact anchor interval exceeds pack cap; resegment offline before upload')
        metadata={'first_event':start,'stop_event':stop,'source_commit':source_commit,'run_id':run_id,
                  'records':records.inventory[start:stop],'previous_logical_sha256':records.hashes[start-1] if start else None}
        with tarfile.open(path,'w',format=tarfile.USTAR_FORMAT) as tar:
            for i,p in enumerate(names,start):
                # Verify exactly the bytes placed in the pack. tar.add would
                # reopen a path after prior validation, allowing mutation or a
                # symlink replacement to publish an invalid complete export.
                raw=p.read_bytes();entry=records.inventory[i]
                if len(raw)!=entry['compressed_bytes'] or hashlib.sha256(raw).hexdigest()!=entry['compressed_sha256']:
                    raise ValueError('Archive record changed during export; no complete export manifest published')
                info=tarfile.TarInfo('records/'+p.name);info.size=len(raw)
                tar.addfile(info,io.BytesIO(raw))
            raw=canonical(metadata);info=tarfile.TarInfo('segment.json');info.size=len(raw);tar.addfile(info,io.BytesIO(raw))
        if path.stat().st_size>max_pack_bytes:raise ValueError('Pack size cap exceeded; upload prohibited')
        packs.append({'file':path.name,'first_event':start,'stop_event':stop,'bytes':path.stat().st_size,'sha256':file_sha(path)})
    (output/'index.json').write_bytes((root/'index.json').read_bytes())
    # Stream the full checkpoint through gzip instead of copying its full body in RAM.
    import shutil
    with (root/'checkpoint.json').open('rb') as src,gzip.open(output/'checkpoint.json.gz','wb') as dst:
        shutil.copyfileobj(src,dst,1024*1024)
    if file_sha(output/'index.json')!=source_hashes['index.json']:
        raise ValueError('Export inventory differs from the verified source')
    # Check the bytes actually copied, even if a source is changed and restored
    # between the pre/post source checks.
    with gzip.open(output/'checkpoint.json.gz','rb') as copied:
        copied_hash=hashlib.sha256()
        for block in iter(lambda:copied.read(1024*1024),b''):copied_hash.update(block)
    if copied_hash.hexdigest()!=source_hashes['checkpoint.json']:
        raise ValueError('Export checkpoint differs from the verified source')
    atomic_json(output/'summary.json',summary)
    files=[p for p in output.iterdir() if p.is_file()]
    if any(file_sha(root/name)!=sha for name,sha in source_hashes.items()):
        raise ValueError('Archive changed during export; no complete export manifest published')
    atomic_json(output/'export.json',{'schema':'rooms-v4-investigator-export-v1','run_id':run_id,
        'source_commit':source_commit,'packs':packs,
        'files':{p.name:{'bytes':p.stat().st_size,'sha256':file_sha(p)} for p in files},
        'upload':'Upload immutable files only after all checksums verify; preserve original archive. No upload or delete is performed by this exporter.'})
    return summary


def verify_export(output):
    """Verify complete portable inventory and copied bytes, not world semantics.

    SHA-256 manifests detect corruption relative to a trusted manifest; they do
    not authenticate the caller's source/run labels. Full reconstruction and
    both independent auditors remain required for scientific validation.
    """
    from consequence_lab.jsonio import digest
    from consequence_lab.provider import strict_json
    output=Path(output);manifest=load_json(output/'export.json')
    if (type(manifest) is not dict or manifest.get('schema')!='rooms-v4-investigator-export-v1'
            or type(manifest.get('files')) is not dict or type(manifest.get('packs')) is not list):
        raise ValueError('Invalid export manifest schema')
    required={'index.json','checkpoint.json.gz','summary.json','events.jsonl','decisions.jsonl'}
    if not required <= set(manifest['files']):
        raise ValueError('Export manifest is missing required files')
    def valid_hash(value):
        return type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)
    for name,meta in manifest['files'].items():
        if (type(name) is not str or Path(name).name!=name or type(meta) is not dict
                or set(meta)!={'bytes','sha256'} or type(meta['bytes']) is not int or meta['bytes']<0
                or not valid_hash(meta['sha256']) or not (output/name).is_file()
                or (output/name).stat().st_size!=meta['bytes'] or file_sha(output/name)!=meta['sha256']):
            raise ValueError('Export file missing or checksum changed')
    index=load_json(output/'index.json')
    if (type(index) is not dict or set(index)!={'storage','segment_events','records'}
            or index['storage']!=STORAGE or index['segment_events']!=SEGMENT_EVENTS or type(index['records']) is not list):
        raise ValueError('Invalid exported compact inventory')
    inventory=index['records']
    for entry in inventory:
        if (type(entry) is not dict or set(entry)!={'sha256','event_type','compressed_bytes','compressed_sha256'}
                or not valid_hash(entry['sha256']) or not valid_hash(entry['compressed_sha256'])
                or type(entry['compressed_bytes']) is not int or entry['compressed_bytes']<0
                or type(entry['event_type']) is not str):
            raise ValueError('Invalid exported record entry')
    expected_packs=[f'segment-{i:05d}.tar' for i in range((len(inventory)+SEGMENT_EVENTS-1)//SEGMENT_EVENTS)]
    if set(manifest['files'])!=required | set(expected_packs) or len(manifest['packs'])!=len(expected_packs):
        raise ValueError('Export pack coverage is incomplete')
    for number,(name,pack) in enumerate(zip(expected_packs,manifest['packs'])):
        start=number*SEGMENT_EVENTS;stop=min(start+SEGMENT_EVENTS,len(inventory))
        file_meta=manifest['files'][name]
        expected={'file':name,'first_event':start,'stop_event':stop,**file_meta}
        if pack!=expected:
            raise ValueError('Export pack ranges differ from the compact inventory')
        with tarfile.open(output/name) as tar:
            members=tar.getmembers();names=[m.name for m in members]
            expected_names=[f'records/{i:08d}.json.gz' for i in range(start,stop)]+['segment.json']
            if names!=expected_names or any(not m.isfile() for m in members):
                raise ValueError('Export pack members are missing, duplicated or unexpected')
            segment=strict_json(tar.extractfile('segment.json').read())
            expected_segment={'first_event':start,'stop_event':stop,'source_commit':manifest['source_commit'],
                'run_id':manifest['run_id'],'records':inventory[start:stop],
                'previous_logical_sha256':inventory[start-1]['sha256'] if start else None}
            if segment!=expected_segment:
                raise ValueError('Export segment inventory differs from the global inventory')
            for i in range(start,stop):
                entry=inventory[i];member=tar.getmember(f'records/{i:08d}.json.gz')
                if member.size!=entry['compressed_bytes']:
                    raise ValueError('Exported record size differs from compact inventory')
                with tar.extractfile(member) as stream:
                    h=hashlib.sha256()
                    for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
                if h.hexdigest()!=entry['compressed_sha256']:
                    raise ValueError('Exported record checksum differs from compact inventory')
    with gzip.open(output/'checkpoint.json.gz','rb') as stream:saved=unseal(strict_json(stream.read()))
    if (saved.get('schema_version')!=2 or saved.get('records')!={'storage':STORAGE,'sha256':[e['sha256'] for e in inventory]}
            or type(saved['state']['event_index']) is not int or saved['state']['event_index']!=len(inventory)):
        raise ValueError('Exported checkpoint/inventory mismatch')
    summary=load_json(output/'summary.json')
    if (summary.get('events')!=len(inventory) or summary.get('run_id')!=manifest['run_id']
            or summary.get('source_commit')!=manifest['source_commit']
            or summary.get('saved_provenance')!=saved['provenance']
            or summary.get('implementation')!=saved['state']['config']['implementation']
            or summary.get('manifest_sha256')!=digest(saved['state']['world']['manifest'])):
        raise ValueError('Export summary differs from checkpoint or manifest')
    for filename,event_indices in (('events.jsonl',list(range(len(inventory)))),
            ('decisions.jsonl',[i for i,e in enumerate(inventory) if e['event_type']=='decision.completed'])):
        with (output/filename).open() as stream:
            count=0
            for count,line in enumerate(stream,1):
                item=strict_json(line)
                if count>len(event_indices):raise ValueError('Export chronological index has extra events')
                i=event_indices[count-1]
                if (item.get('event_index')!=i or item.get('event_type')!=inventory[i]['event_type']
                        or item.get('record_sha256')!=inventory[i]['sha256'] or item.get('pack')!=f'segment-{i//SEGMENT_EVENTS:05d}.tar'):
                    raise ValueError('Export chronological index differs from record inventory')
            if count!=len(event_indices):raise ValueError('Export chronological index is incomplete')
    return {'files_verified':len(manifest['files']),'run_id':manifest['run_id'],
            'scope':'complete inventory and file checksums; full reconstruction and semantic/domain audits still required'}



def iter_segment(output, segment_number):
    """Retrieve exact logical records using only one pack and the compact index."""
    from consequence_lab.compact_archive import apply_patch
    from consequence_lab.jsonio import digest
    from consequence_lab.provider import strict_json
    output=Path(output);manifest=load_json(output/'export.json')
    name=f'segment-{segment_number:05d}.tar'
    meta=manifest['files'][name];pack=output/name
    if pack.stat().st_size!=meta['bytes'] or file_sha(pack)!=meta['sha256']:
        raise ValueError('Selected pack checksum mismatch')
    index_meta=manifest['files']['index.json']
    if file_sha(output/'index.json')!=index_meta['sha256']:
        raise ValueError('Export inventory checksum mismatch')
    inventory=load_json(output/'index.json')['records']
    with tarfile.open(pack) as tar:
        segment=strict_json(tar.extractfile('segment.json').read())
        start,stop=segment['first_event'],segment['stop_event']
        if start!=segment_number*SEGMENT_EVENTS or segment['records']!=inventory[start:stop]:
            raise ValueError('Selected segment inventory mismatch')
        state=None
        for i in range(start,stop):
            raw=tar.extractfile(f'records/{i:08d}.json.gz').read()
            entry=inventory[i]
            if hashlib.sha256(raw).hexdigest()!=entry['compressed_sha256']:
                raise ValueError('Selected record checksum mismatch')
            wire=strict_json(gzip.decompress(raw))
            previous=inventory[i-1]['sha256'] if i else None
            if wire['event_index']!=i or wire['previous_record_sha256']!=previous:
                raise ValueError('Cross-segment causal chain mismatch')
            if i==start:state=wire['base']
            elif wire['base'] is not None:raise ValueError('Unexpected segment anchor')
            if digest(state)!=wire['state_before_sha256']:
                raise ValueError('Selected state anchor mismatch')
            after=apply_patch(state,wire['after'])
            record={**wire['metadata'],'state_before':state,'state_after':after}
            if wire['record_sha256']!=entry['sha256'] or digest(record)!=entry['sha256']:
                raise ValueError('Selected logical seal mismatch')
            yield clone(record)
            state=after


def main():
    p=argparse.ArgumentParser();p.add_argument('archive',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--run-id',required=True);p.add_argument('--source-commit',required=True)
    a=p.parse_args();print(json.dumps(export(a.archive,a.output,run_id=a.run_id,source_commit=a.source_commit),indent=2))
    print(json.dumps(verify_export(a.output)))
if __name__=='__main__':main()

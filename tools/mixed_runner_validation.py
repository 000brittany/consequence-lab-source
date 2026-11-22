"""Full sixty-day Runner/archive engineering fixture, deliberately not a scheduling policy."""
import argparse,asyncio,gzip,hashlib,json,shutil,tarfile,time
from pathlib import Path
from consequence_lab.backends import ScriptedMock
from consequence_lab.clock import ManualClock
from consequence_lab.jsonio import canonical,load_json
from consequence_lab_mixed.manifest import generate
from consequence_lab_mixed.run import new_run,restore
from tools.rooms_v4_export import export,verify_export
from tools.rooms_v4_portable_audit import reassemble
from tools.mixed_reference import check_transition


def independent_records(root):
    # Independent patch decoder; production compact apply_patch is never called.
    from tools.rooms_v4_independent_decode import independent_patch,checked_json,object_sha,canonical as wire_canonical
    index=checked_json((root/'index.json').read_bytes());state=None;n=0
    assert sorted(p.name for p in (root/'records').iterdir())==[f'{i:08d}.json.gz' for i in range(len(index['records']))]
    for i,entry in enumerate(index['records']):
        raw=(root/'records'/f'{i:08d}.json.gz').read_bytes()
        assert len(raw)==entry['compressed_bytes'] and hashlib.sha256(raw).hexdigest()==entry['compressed_sha256']
        w=checked_json(gzip.decompress(raw));assert w['event_index']==i and w['previous_record_sha256']==(index['records'][i-1]['sha256'] if i else None)
        if i%32==0:
            if state is not None:assert wire_canonical(state)==wire_canonical(w['base'])
            state=w['base']
        else:assert w['base'] is None
        assert object_sha(state)==w['state_before_sha256'];after=independent_patch(state,w['after'])
        rec={**w['metadata'],'state_before':state,'state_after':after};assert object_sha(rec)==entry['sha256']==w['record_sha256']
        yield rec;state=after;n+=1
    checkpoint=checked_json((root/'checkpoint.json').read_bytes());assert object_sha(checkpoint['body'])==checkpoint['sha256']
    assert checkpoint['body']['records']['sha256']==[e['sha256'] for e in index['records']]
    assert wire_canonical(checkpoint['body']['state'])==wire_canonical(state)


async def run(out):
    out.mkdir(parents=True,exist_ok=False);start=time.monotonic()
    m=generate(3001,allocation_cents=25000)
    def tool(k,**kwargs):return {'tool':k,'arguments':kwargs}
    def yield_day():return {'tool':'act','arguments':{'resource':'/opportunities','arguments':{'operation':'yield'},'disposition':'yield_period'}}
    scripts={a:[tool('write',path='/home/note',text='retained private Δ',disposition='continue'),tool('read',path='/home/note',disposition='yield_tick')] for a in 'ABC'}
    scripts['A']+=[{'tool':'act','arguments':{'resource':'/messages','arguments':{'operation':'send','recipient':'B','text':'authored engineering message'},'disposition':'continue'}},tool('write',path='/volumes/common/engineering',text='shared record',disposition='continue'),yield_day()]
    scripts['B']+=[tool('read',path='/inbox',disposition='continue'),tool('read',path='/volumes/common/engineering',disposition='yield_period')]
    scripts['C']+=[tool('remove',path='/home/note',disposition='yield_period')]
    # One concrete advance reservation; no future event is used to decide it.
    anchor=m['requests'][0]
    if anchor['client_status']=='confirmed':
        scripts[anchor['owner']].insert(0,{'tool':'act','arguments':{'resource':'/schedule','arguments':{'operation':'book','appointment_id':anchor['id'],'day':60,'time':anchor['slots'][0][1],'room_id':anchor['rooms'][0]},'disposition':'continue'}})
    for a in scripts:scripts[a]+=[yield_day() for _ in range(60)]
    r=new_run(m,ScriptedMock(scripts),archive=out/'research',clock=ManualClock())
    await r.run(2160,retain_results=False);assert r.state['status']=='finished';r.checkpoint(out/'research/checkpoint.json')
    generation=time.monotonic()-start
    export(out/'research',out/'export',run_id='OFFLINE-MIXED-engineering-3001',source_commit='source fingerprint plus final recorded source commit')
    verified=verify_export(out/'export');equal=reassemble(out/'research',out/'export',out/'reassembled')
    restored=restore(out/'reassembled/checkpoint.json');assert canonical(restored.state)==canonical(r.state)
    events=0;logical=0;operations={};scope=0
    for rec in independent_records(out/'reassembled'):
        logical+=len(canonical(rec));events+=1
        if rec['event_type']=='environment.transition':
            item={'type':'environment','event':rec['environment_event'],'result':rec['environment_result']}
            check_transition(rec['state_before'],rec['state_after'],item);scope+=1
        elif rec['event_type']=='decision.completed':
            for op in rec['consequences']['memory_operations']:operations[op['kind']]=operations.get(op['kind'],0)+1
            action=rec['computation']['selected_action']
            if action and action['tool']=='act':
                # Scheduler/accounting checked separately by core semantic restoration.
                item={'type':'decision','actor':rec['agent'],'action':action,'result':rec['consequences']['tool_response']}
                check_transition(rec['state_before'],rec['state_after'],item);scope+=1
    report={'scope':'complete 60-day authored engineering fixture; not scheduling performance or maximum-output capacity',
        'status':'passed','days':60,'calls':r.state['ledger']['calls'],'events':events,'logical_bytes':logical,
        'physical_bytes':sum(p.stat().st_size for p in (out/'research').rglob('*') if p.is_file()),
        'generation_seconds':generation,'audit_export_seconds':time.monotonic()-start-generation,
        'independent_domain_records':scope,'memory_operations':operations,'artifact_revisions':len(r.state['artifact_revisions']),
        'messages':len(r.state['world']['direct_messages']),'complete_history_cycles':{a:len(v['history']) for a,v in r.state['agents'].items()},
        'export':verified,'source_equality':equal,'pending':(out/'research/pending.json').exists(),
        'semantic':'full core auditor through Runner.restore','independent':'separate delta decoding plus separate financial/capacity/client transition checks',
        'base_fingerprint':r.state['config']['implementation'],'prototype_sha256':m['prototype_sha256'],'provider_calls':0}
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();asyncio.run(run(a.output))
if __name__=='__main__':main()

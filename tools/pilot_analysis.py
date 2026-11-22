"""Read-only investigator reconstruction. Never imported by an agent backend."""
import argparse
import hashlib
import json
from pathlib import Path

from consequence_lab.archive import Archive, unseal, verify_checkpoint_records, verify_records
from consequence_lab.audit import verify_experiment
from consequence_lab.jsonio import load_json, digest
from consequence_lab.recurring import RecurringRooms
from consequence_lab.rooms_v2 import RoomsV2
from consequence_lab.state import validate_state


def verified_records(path, allow_incomplete=False):
    path=Path(path)
    if allow_incomplete and not (path/'pending.json').exists() and (path/'checkpoint.json').is_file():
        # A permission to inspect prefixes must not mislabel a complete archive.
        try:
            Archive.read(path)
        except (ValueError, RuntimeError):
            pass
        else:
            return verified_records(path, False)
    if not allow_incomplete:
        records=Archive.read(path)
        saved=unseal(load_json(path/'checkpoint.json'))
        state=saved['state']
        scope='complete_as_saved_checkpoint'
    else:
        if not path.is_dir() or not (path/'records').is_dir():
            raise ValueError('Missing archive')
        files=sorted((path/'records').iterdir())
        if [p.name for p in files] != [f'{i:08d}.json' for i in range(len(files))]:
            raise ValueError('Noncontiguous record prefix')
        records=[unseal(load_json(p)) for p in files]
        verify_records(records)
        checkpoint=path/'checkpoint.json'
        saved=unseal(load_json(checkpoint)) if checkpoint.exists() else None
        if saved:
            verify_checkpoint_records(saved)
            if saved['records'] != records[:len(saved['records'])]:
                raise ValueError('Checkpoint disagrees with prefix')
            if records and not saved['records'] and saved['state']!=records[0]['state_before']:
                raise ValueError('Initial checkpoint disagrees with prefix boundary')
        if not records and saved is None:
            raise ValueError('No preserved state to verify')
        state=records[-1]['state_after'] if records else saved['state']
        if (path/'pending.json').exists():
            pending=unseal(load_json(path/'pending.json'))
            committed=records and records[-1].get('decision_id')==pending['decision_id'] and records[-1]['state_before']==pending['state_before']
            if pending['state_before']!=state and not committed:
                raise ValueError('Pending marker does not match committed boundary')
        scope='incomplete_verified_committed_prefix; pending provider work is unresolved'
    factory={RecurringRooms.name:RecurringRooms,RoomsV2.name:RoomsV2}.get(state['world_type'])
    if factory is None:raise ValueError('Expected a supported Recurring Rooms world')
    world=factory()
    validate_state(state,world)
    if records:validate_state(records[0]['state_before'],world)
    for r in records:validate_state(r['state_after'],world)
    verification=verify_experiment(records,world)
    return records,state,scope,verification


def origins(record):
    result=[]
    for op in record['consequences']['memory_operations']:
        if op['kind'] in ('create','revise'):
            a=op['after'];text=a['text']
            result.append({'kind':'write_arguments','artifact_id':a['id'],'path':a['path'],'revision':a['revision'],
                           'text_sha256':hashlib.sha256(text.encode()).hexdigest(),'characters':len(text),'role':'assistant'})
        elif op['kind']=='read':
            result.append({'kind':'read_result','artifact_id':op['internal_artifact_id'],'path':op['path'],
                           'revision':op['revision'],'offset':op['offset'],'characters':op['length'],
                           'text_sha256':hashlib.sha256(op['text'].encode()).hexdigest(),'role':'tool'})
    return result


def literal_matches(before, messages):
    """Text coincidence complements source receipts, without proving provenance.

    Search decoded string values so embedded JSON escapes do not hide exact
    matches. Paraphrases and behavioral influence remain unmeasured.
    """
    def strings(value):
        # Agent-authored text is arbitrary, including deeply nested JSON. Walk
        # containers iteratively and decode only the wire's two string layers;
        # an optional text-coincidence check must not invalidate legal archives.
        todo=[(value,0)]
        while todo:
            item,depth=todo.pop()
            if isinstance(item,str):
                yield item
                if depth>=2:continue
                try:decoded=json.loads(item)
                except (ValueError,TypeError,RecursionError):continue
                if decoded!=item:todo.append((decoded,depth+1))
            elif isinstance(item,dict):todo.extend((v,depth) for v in item.values())
            elif isinstance(item,list):todo.extend((v,depth) for v in item)
    found=[]
    for op in before['artifact_revisions']:
        artifact=op.get('after')
        if not artifact or not artifact['text']:continue
        text=artifact['text']
        for i,message in enumerate(messages):
            if any(text in value for value in strings(message)):
                found.append({'artifact_id':artifact['id'],'revision':artifact['revision'],
                    'text_sha256':hashlib.sha256(text.encode()).hexdigest(),'message_index':i})
    return found


def metadata_exposures(messages):
    """Headers included in current and retained requests; never body reads."""
    result=[]
    for i,message in enumerate(messages):
        if message['role']!='user':continue
        try:value=json.loads(message['content'])
        except (ValueError,TypeError):continue
        if isinstance(value,dict) and 'shared_updates' in value:
            result.append({'message_index':i,'metadata':value['shared_updates'],
                           'interpretation':'Names/timing may communicate facts; provider processing/functional use is not inferred'})
    return result


def analyze(path,allow_incomplete=False):
    path=Path(path)
    records,state,scope,verification=verified_records(path,allow_incomplete)
    decisions={r['decision_id']:r for r in records if r['event_type']=='decision.completed'}
    rows=[];events=[];cumulative=0;artifact_lifetimes={}
    for r in records:
        file=path/'records'/f"{r['event_index']:08d}.json"
        cumulative+=file.stat().st_size
        before,after=r['state_before'],r['state_after']
        for a in after['artifacts'].values():
            life=artifact_lifetimes.setdefault(a['id'],{'path':a['path'],'first_event':r['event_index'],'last_event':r['event_index'],'revisions':[]})
            life['last_event']=r['event_index'];life['deleted_at_end']=a['deleted']
            if a['revision'] not in life['revisions']:life['revisions'].append(a['revision'])
        task_changes={k:{'before':before['tasks'].get(k),'after':v} for k,v in after['tasks'].items() if before['tasks'].get(k)!=v}
        if r['event_type']!='decision.completed':
            events.append({'event_index':r['event_index'],'type':r['event_type'],'event':r.get('environment_event'),
                'result':r.get('environment_result'),'task_changes':task_changes,
                'artifact_ids_persisted':sorted(set(before['artifacts'])&set(after['artifacts']))})
            continue
        selection=r['registration']['context_selection'];exposed=[]
        for source in selection['sources']:
            source_id=source.get('decision_id')
            if source_id not in decisions:continue
            role=r['generation']['request']['messages'][source['message_index']]['role']
            for origin in origins(decisions[source_id]):
                if origin['role']==role:
                    exposed.append({**origin,'source_decision':source_id,'message_index':source['message_index']})
        usage=r['accounting']['usage'];exchange=r['generation'].get('provider_exchange') or {}
        actual=usage['input_tokens'] if usage and usage.get('source')=='provider_reported' else None
        raw=exchange.get('response_text');returned=None
        if raw:
            try:returned=json.loads(raw).get('model')
            except (ValueError,AttributeError):pass
        selected=r['computation']['selected_action'];result=r['consequences']['tool_response']
        allocation=[]
        capacity_key='cells' if before['world_type']==RoomsV2.name else 'rooms'
        for room,appointment in after['world'][capacity_key].items():
            if before['world'][capacity_key][room]!=appointment:
                allocation.append({'room':room,'before':before['world'][capacity_key][room],'after':appointment,
                    'domain_effect':'capacity allocated' if appointment is not None else 'capacity released'})
        row={'decision_id':r['decision_id'],'event_index':r['event_index'],'agent':r['agent'],
             'period':before['world']['period'],'tick':before['world']['tick'],'version':before['world']['version'],
             'authoritative_record':f"records/{r['event_index']:08d}.json",
             'observation':r['registration']['observation'],
             'supplied_artifact_receipts':r['registration']['supplied_artifacts'],
             'supplied_notice_receipts':r['registration'].get('supplied_resources',[]),
             'proposals':r['computation']['proposed_actions'],'selected_action':selected,'error':r['computation']['error'],
             'tool_result':result,'scheduling':r['consequences']['scheduling'],'task_changes':task_changes,
             'outcomes':r['consequences']['outcomes'],'resource_externality_preconditions':allocation,
             'artifact_operations':r['consequences']['memory_operations'],'relationship_operations':r['consequences']['relationship_operations'],
             'accessible_artifacts':[{'id':a['id'],'path':a['path'],'revision':a['revision']} for a in r['exposure']['accessible_artifacts']],
             'artifact_body_exposures':exposed,'shared_metadata_exposures':metadata_exposures(r['generation']['request']['messages']),'literal_revision_matches':literal_matches(before,r['generation']['request']['messages']),'current_filename_exposure':r['registration']['observation']['entries'],
             'inspection':{'arguments':selected['arguments'],'result':result} if selected and selected['tool']=='inspect' else None,
             'context_selection':selection,'actual_input_tokens':actual,
             'estimate_to_actual_ratio':r['generation']['request']['input_token_count']/actual if actual else None,
             'usage':usage,'output_allowance':before['config']['limits']['output_tokens'],
             'unused_output_and_reasoning_allowance':before['config']['limits']['output_tokens']-usage['output_tokens'] if usage else None,
             'latency_seconds':exchange.get('latency_seconds'),'exchange_transport':exchange.get('transport'),
             'accounting':r['accounting'],
             'requested_model':(r['generation'].get('provider_request') or {}).get('payload',{}).get('model'),
             'returned_model':returned,'record_bytes':file.stat().st_size,'cumulative_record_bytes':cumulative}
        rows.append(row)
    sequences={}
    for row in rows:
        key=f"{row['agent']}:period-{row['period']}:version-{row['version']}"
        action=row['selected_action']
        sequences.setdefault(key,[]).append({'decision_id':row['decision_id'],
            'tool':action['tool'] if action else None,'arguments':action['arguments'] if action else None,
            'result_status':row['tool_result']['status'] if row['tool_result'] else None,'scheduling':row['scheduling']})
    pending=unseal(load_json(path/'pending.json')) if (path/'pending.json').exists() else None
    return {'schema_version':1,'implementation':state['config']['implementation'],
            'manifest_sha256':digest(state['world']['manifest']),'action_sequences':sequences,
            'pressure':state['world']['manifest'].get('pressure'),
            'communication_policy':state['config'].get('communication'),
            'world_manifest_version':state['world']['manifest'].get('version'),
            'context_policy':{'recent_cycles':state['config']['recent_cycles'],
                'input_allowance':state['config']['limits']['input_tokens'],
                'output_and_reasoning_allowance':state['config']['limits']['output_tokens'],
                'exposure_basis':'Exact recorded requests; context exclusions are separate from environmental pressure'},
            'pending_summary':None if pending is None else {'decision_id':pending['decision_id'],
                'scope':'unresolved pending payload, not a semantically verified completed decision; never replay automatically',
                'preserved_fields':sorted(pending),'record':'pending.json'},'scope':scope,'verification':verification,'decisions':rows,'environment_events':events,
            'world_type':state['world_type'],'artifact_lifetimes':artifact_lifetimes,
            'outcomes':(RoomsV2() if state['world_type']==RoomsV2.name else RecurringRooms()).measure(state['world']),
            'ledger':state['ledger'],'status':state['status'],'horizon_complete':state['status']=='finished','stop_reason':state['stop_reason'],
            'opportunity_counts':state['world']['control']['counts'],
            'ticks_advanced':state['world']['control']['ticks_advanced'],
            'archive_bytes':sum(p.stat().st_size for p in path.rglob('*') if p.is_file()),
            'record_bytes':cumulative,'checkpoint_bytes':(path/'checkpoint.json').stat().st_size if (path/'checkpoint.json').exists() else None,
            'archive_growth_scope':'research directory only, including pending/checkpoint/report files if present; cumulative immutable record bytes measured; historical checkpoint sizes unavailable',
            'pending_present':(path/'pending.json').exists(),
            'interpretation':'Exposure is provenance, not functional influence. Literal matches can be coincidental; absent matches do not exclude paraphrases. No intention/subgoal/attractor/agency classification.',
            'candidate_tests':'Compare repeated sequences across versions; test structural perturbations separately while controlling retained history and environmental cues.'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('archive',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--allow-incomplete',action='store_true');a=p.parse_args()
    if a.output.resolve().is_relative_to(a.archive.resolve()) or a.output.exists():
        raise ValueError('Write a new report outside the read-only archive')
    result=analyze(a.archive,a.allow_incomplete)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x') as f:json.dump(result,f,indent=2,sort_keys=True)


if __name__=='__main__':main()

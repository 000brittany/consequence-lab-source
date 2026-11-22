"""Finalize a finished OFFLINE fixture from its intact committed compact journal.
This is not a paid-run crash recovery or permission to discard pending calls.
"""
import argparse,json,shutil
from pathlib import Path
from consequence_lab.archive import seal,unseal
from consequence_lab.compact_archive import open_records
from consequence_lab.jsonio import load_json,atomic_json,digest,canonical
from tools.mixed_pilot import restore,specification
from tools.mixed_runner_validation import independent_records
from tools.mixed_reference import check_transition
from tools.rooms_v4_export import export,verify_export
from tools.rooms_v4_portable_audit import reassemble

def finalize(out):
    root=out/'research';assert not (root/'pending.json').exists();records=open_records(root);wires=[];state=None
    for rec in records:
        if rec['event_type']=='decision.completed':
            assert rec['generation']['provider_exchange']['transport']=='offline_fixture'
            wires.append(rec['generation']['provider_request']['payload'])
        state=rec['state_after']
    assert state['status']=='finished' and state['stop_reason']=='horizon_complete'
    old=load_json(root/'checkpoint.json');saved=unseal(old);shutil.copyfile(root/'checkpoint.json',out/'prefix-checkpoint.json')
    saved.update(state=state,records=records.descriptor(),clock={'kind':'manual','elapsed':state['ledger']['elapsed_seconds']})
    saved['provenance']['offline_fixture_finalization']={'old_prefix_checkpoint_sha256':digest(old),'basis':'All original committed records retained; finished journal verified; no paid request or replay'}
    atomic_json(root/'checkpoint.json',seal(saved));restore(root/'checkpoint.json')
    export(root,out/'export',run_id='OFFLINE-pilot-wire-470022',source_commit='050c51c plus documented offline terminal-finalization fix')
    verify_export(out/'export');equal=reassemble(root,out/'export',out/'reassembled');restore(out/'reassembled/checkpoint.json');count=0
    for rec in independent_records(out/'reassembled'):
        if rec['event_type']=='environment.transition':check_transition(rec['state_before'],rec['state_after'],{'type':'environment','event':rec['environment_event'],'result':rec['environment_result']});count+=1
        elif rec['event_type']=='decision.completed' and rec['computation']['selected_action']['tool']=='act':check_transition(rec['state_before'],rec['state_after'],{'type':'decision','actor':rec['agent'],'action':rec['computation']['selected_action'],'result':rec['consequences']['tool_response']});count+=1
    report={'status':'passed','provider_calls':0,'scope':'complete sixty-day real ResponsesBackend serialization/decode and Runner, local fixtures only','days':60,'decisions':state['ledger']['calls'],'events':len(records),'independent_domain_records':count,'portable_source_equality':equal,'wire_bytes':{'min':min(len(canonical(w)) for w in wires),'max':max(len(canonical(w)) for w in wires)},'specification':specification(),'finalization_note':'Original offline loop reached horizon then raised normal RunLimitReached before final checkpoint; intact journal finalized explicitly. Fixed loop now handles boundary. No raw record changed.'}
    atomic_json(out/'validation.json',report);print(json.dumps(report,indent=2))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();finalize(a.output)

"""Offline investigator reconstruction; never imported by an agent decision loop."""
import argparse
import json
from pathlib import Path

from consequence_lab.archive import Archive, FileRecords, unseal, verify_records, verify_checkpoint_records
from consequence_lab.audit import verify_experiment
from consequence_lab.jsonio import atomic_json, digest, load_json
from consequence_lab.rooms_v3 import RoomsV3
from consequence_lab.rooms_v3_reference import verify_domain
from consequence_lab.state import validate_state
from tools.pilot_analysis import origins, literal_matches


def load_verified(path, allow_incomplete=False):
    path = Path(path)
    if not path.is_dir() or not (path / 'records').is_dir():
        raise ValueError('Missing research archive')
    try:
        records = Archive.read(path)
        saved = unseal(load_json(path / 'checkpoint.json'))
        state = saved['state']
        scope = 'complete_as_saved_checkpoint'
    except (ValueError, FileNotFoundError, RuntimeError):
        if not allow_incomplete:
            raise
        files = sorted((path / 'records').iterdir())
        if [p.name for p in files] != [f'{i:08d}.json' for i in range(len(files))]:
            raise ValueError('Noncontiguous archive prefix')
        records = FileRecords(path)
        verify_records(records)
        checkpoint = path / 'checkpoint.json'
        saved = unseal(load_json(checkpoint)) if checkpoint.exists() else None
        if saved is not None:
            if saved['schema_version'] == 2:
                verify_checkpoint_records(saved, records, allow_tail=True)
            else:
                verify_checkpoint_records(saved)
                if saved['records'] != records[:len(saved['records'])]:
                    raise ValueError('Checkpoint/prefix disagreement')
                if records and not saved['records'] and saved['state'] != records[0]['state_before']:
                    raise ValueError('Initial checkpoint/prefix disagreement')
        if not records and saved is None:
            raise ValueError('No preserved state')
        state = records[-1]['state_after'] if records else saved['state']
        pending = path / 'pending.json'
        if pending.exists():
            marker = unseal(load_json(pending))
            committed = records and records[-1].get('decision_id') == marker['decision_id'] and records[-1]['state_before'] == marker['state_before']
            if marker['state_before'] != state and not committed:
                raise ValueError('Pending request does not match boundary')
        scope = 'incomplete_verified_committed_prefix; provider work may be unresolved'
    world = RoomsV3()
    validate_state(state, world)
    if records:
        validate_state(records[0]['state_before'], world)
    return records, state, scope, verify_experiment(records, world), verify_domain(records)


def analyze(path, allow_incomplete=False):
    records, state, scope, semantic, independent = load_verified(path, allow_incomplete)
    decisions = []
    environment = []
    for r in records:
        if r['event_type'] == 'environment.transition':
            environment.append({'event_index': r['event_index'], 'event': r['environment_event'], 'result': r['environment_result']})
        if r['event_type'] != 'decision.completed':
            continue
        usage = r['accounting']['usage']; selection = r['registration']['context_selection']
        actual = usage['input_tokens'] if usage else None
        exchange = r['generation'].get('provider_exchange')
        returned = None
        if exchange and exchange.get('response_text'):
            try:
                returned = json.loads(exchange['response_text']).get('model')
            except (ValueError, AttributeError):
                pass
        decisions.append({'decision_id': r['decision_id'], 'event_index': r['event_index'], 'agent': r['agent'],
            'day': r['state_before']['world']['day'], 'tick': r['state_before']['world']['tick'],
            'action': r['computation']['selected_action'], 'proposed_actions': r['computation']['proposed_actions'],
            'result': r['consequences']['tool_response'], 'scheduling': r['consequences']['scheduling'],
            'context_sha256': selection['request_sha256'], 'sources': selection['sources'],
            'included_decisions': selection['included_decisions'], 'excluded_decisions': selection['excluded_decisions'],
            'available_artifacts': r['exposure']['accessible_artifacts'],
            'retrieved_artifacts_in_context': selection['supplied_artifacts'],
            'retrieved_resources_in_context': selection['supplied_resources'],
            'direct_message_input': r['registration']['observation']['direct_messages'],
            'artifact_operations': r['consequences']['memory_operations'], 'artifact_origins_this_decision': origins(r),
            'artifact_literal_matches_in_input': literal_matches(r['state_before'], r['generation']['request']['messages']),
            'context_interpretation': 'Exact input and receipts show exposure, not functional use. Literal matches may be coincidental.',
            'input_estimate': r['generation']['request']['input_token_count'],
            'estimate_to_actual_ratio': r['generation']['request']['input_token_count'] / actual if actual else None,
            'usage': usage, 'accounting': r['accounting'], 'provider_latency_seconds': exchange.get('latency_seconds') if exchange else None,
            'returned_model': returned, 'model_identity_limit': 'Alias is not proof of immutable weights',
            'error': r['computation']['error'],
            'classification': r['computation'].get('classification', 'legacy_unclassified')})
    missing = [d for d in decisions if d['classification'] == 'unobserved_generation']
    by_agent = {a: sum(d['agent'] == a for d in missing) for a in state['agents']}
    if state['config'].get('continuation_policy') == 'rooms_v3_recoverable_v1':
        quality = state['ledger']['technical']
        if quality['total_unobserved'] != len(missing) or quality['per_agent_unobserved'] != by_agent:
            raise ValueError('Technical compromise counters do not match decisions')
    else:
        quality = None
    distribution = [{'decision_id': d['decision_id'], 'agent': d['agent'],
                     'day': d['day'], 'tick': d['tick'], 'event_index': d['event_index'],
                     'http_status': (next((r['generation'].get('provider_exchange', {}).get('status_code')
                        for r in records if r.get('decision_id') == d['decision_id']), None))}
                    for d in missing]
    return {'version': 'rooms-v3-analysis-v2', 'technical_quality': quality,
            'missing_response_distribution': distribution, 'missing_responses_by_agent': by_agent,
            'missing_responses_by_day': {str(day): sum(d['day'] == day for d in missing)
                                         for day in sorted({d['day'] for d in missing})}, 'scope': scope, 'semantic_verification': semantic,
            'independent_domain_verification': independent, 'manifest_sha256': digest(state['world']['manifest']),
            'manifest': state['world']['manifest'], 'implementation': state['config']['implementation'],
            'status': state['status'], 'stop_reason': state['stop_reason'], 'horizon_complete': state['status'] == 'finished',
            'outcomes': RoomsV3().measure(state['world']), 'requests': state['world']['requests'],
            'services': state['world']['outcomes'], 'transactions': state['world']['transactions'],
            'client_updates': state['world']['client_events'], 'direct_messages': state['world']['direct_messages'],
            'ledger': state['ledger'], 'backend': state['backend'],
            'context_policy': {k: state['config'][k] for k in ('recent_cycles', 'limits')},
            'decisions': decisions, 'environment': environment,
            'archive_bytes': sum(p.stat().st_size for p in Path(path).rglob('*') if p.is_file()),
            'interpretation': 'No intention, cooperation, representation or goal classifications performed; behavior and exposure only.'}


def main():
    p = argparse.ArgumentParser(); p.add_argument('archive', type=Path); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--allow-incomplete', action='store_true'); a = p.parse_args()
    report = analyze(a.archive, a.allow_incomplete); atomic_json(a.output, report)
    print(json.dumps({'status': report['status'], 'verification': report['semantic_verification'],
                      'independent': report['independent_domain_verification']}))


if __name__ == '__main__':
    main()

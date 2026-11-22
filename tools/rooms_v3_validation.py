"""Authored no-cost software fixtures, never policies for autonomous agents."""
import argparse
import asyncio
import json
from pathlib import Path

from consequence_lab.backends import ScriptedMock
from consequence_lab.clock import ManualClock
from consequence_lab.jsonio import atomic_json, digest
from consequence_lab.rooms_v3_manifest import generate
from consequence_lab.rooms_v3_reference import feasible_assignment
from consequence_lab.rooms_v3_run import new_run
from tools.rooms_v3_analysis import analyze


def action(operation, ident=None, *, disposition='yield_tick', **fields):
    args = {'operation': operation, **({'appointment_id': ident} if ident else {}), **fields}
    return {'tool': 'act', 'arguments': {'resource': '/opportunities' if operation == 'yield' else '/schedule',
                                       'arguments': args, 'disposition': disposition}}


def fixture_scripts(m, plan=None, *, external=False):
    """Investigator-authored scheduling fixture. No provider or prompt imports it."""
    scripts = {a: [] for a in m['actors']}
    final = {r['id']: dict(r) for r in m['requests']}
    for u in m['client_updates']:
        final[u['appointment_id']].update(u['changes'])
        final[u['appointment_id']]['client_status'] = 'cancelled' if u['outcome'] == 'cancel' else 'confirmed'
    for day in range(1, m['days'] + 1):
        for actor in m['actors']:
            released = [r for r in final.values() if (r['client_reply_day'] or r['arrival_day']) == day and r['owner'] == actor and r['client_status'] != 'cancelled']
            if not released:
                scripts[actor].append(action('yield', disposition='yield_period'))
            for i, r in enumerate(released):
                target = {'day': r['earliest_day'], 'time': r['times'][0], 'room_id': f'external:{actor}'} if external else {
                    k: plan['appointments'][r['id']][k] for k in ('day', 'time', 'room_id')}
                scripts[actor].append(action('book', r['id'], **target,
                    disposition='yield_period' if i == len(released) - 1 else 'yield_tick'))
    return scripts


def lifecycle_fixture(outcome):
    """Deliberate causal/provenance wiring, never a provider policy."""
    m=generate(0,4);m['origin']={'kind':'software_fixture','name':f'client-mail-{outcome}'}
    req={**m['requests'][0], 'id':'fixture-request','arrival_day':1,'earliest_day':3,'latest_day':4,
         'times':['09:00','14:00'],'deadline':{'day':4,'time':'14:00'},'client_status':'provisional','client_reply_day':2}
    m['requests']=[req]
    m['client_updates']=[{'appointment_id':req['id'],'day':2,'outcome':outcome,
        'changes':{'times':['14:00'],'deadline':{'day':4,'time':'14:00'}} if outcome=='change' else {}}]
    def message(recipient,text):
        return {'tool':'act','arguments':{'resource':'/messages','arguments':{'operation':'send','recipient':recipient,'text':text},'disposition':'continue'}}
    scripts={
        'A':[action('hold',req['id'],day=3,time='09:00',room_id='R0',disposition='yield_period'),
             message('B','Authored fixture response, no required communication convention'),
             action('book',req['id'],day=3,time='14:00',room_id='R0',disposition='yield_period') if outcome=='change' else action('yield',disposition='yield_period'),
             action('yield',disposition='yield_period'),
             {'tool':'read','arguments':{'path':'/inbox','length':8,'disposition':'continue'}},
             {'tool':'inspect','arguments':{'path':'/budget','disposition':'yield_period'}}],
        'B':[{'tool':'act','arguments':{'resource':'/messages','arguments':{'operation':'send','recipient':'A','text':'Authored fixture message while A yielded'},'disposition':'yield_period'}}]+
            [action('yield',disposition='yield_period')]*3,
        'C':[{'tool':'write','arguments':{'path':'/home/free-text','text':'Authored persistent fixture text','disposition':'yield_period'}},
             action('yield',disposition='yield_period'),{'tool':'read','arguments':{'path':'/home/free-text','disposition':'yield_period'}},
             action('yield',disposition='yield_period')]}
    return m,scripts


async def validate(output):
    output = Path(output); output.mkdir(parents=True, exist_ok=True); rows = []
    for seed, external in ((0, False), (3, False), (0, True)):
        m = generate(seed, 12); plan = feasible_assignment(m)
        name = f'seed{seed}-' + ('external-spending-fixture' if external else 'feasible-assignment-fixture')
        archive = output / name / 'research'
        runner = new_run(m, ScriptedMock(fixture_scripts(m, plan, external=external)), clock=ManualClock(), archive=archive)
        runner.checkpoint(archive / 'checkpoint.json')
        await runner.run(runner.state['config']['limits']['calls'], retain_results=False)
        runner.settle(); runner.checkpoint(archive / 'checkpoint.json')
        report = analyze(archive); atomic_json(output / name / 'analysis.json', report)
        assert report['horizon_complete']
        if not external:
            assert report['outcomes']['system']['completed'] == len(plan['appointments'])
            assert report['outcomes']['system']['budget']['spent_cents'] == plan['expenditure_cents']
        else:
            assert report['outcomes']['system']['failed'] > 0
            assert any(r['result'] and r['result'].get('error') == 'insufficient_shared_funds' for r in report['decisions'])
        rows.append({'fixture': name, 'manifest_sha256': digest(m), 'outcomes': report['outcomes'],
                     'calls': runner.state['ledger']['calls'], 'archive_bytes': report['archive_bytes'],
                     'semantic': report['semantic_verification'], 'independent': report['independent_domain_verification'],
                     'scope': 'Software existence/accounting fixture, not autonomous behavior or a policy tournament'})
    for outcome in ('change','cancel'):
        m,scripts=lifecycle_fixture(outcome);name=f'client-mail-{outcome}-wiring';archive=output/name/'research'
        runner=new_run(m,ScriptedMock(scripts),clock=ManualClock(),archive=archive)
        runner.checkpoint(archive/'checkpoint.json');await runner.run(144, retain_results=False);runner.settle();runner.checkpoint(archive/'checkpoint.json')
        report=analyze(archive);atomic_json(output/name/'analysis.json',report);atomic_json(output/name/'manifest.json',m)
        assert report['horizon_complete'] and report['outcomes']['system']['failed']==0
        assert report['outcomes']['system']['completed']==int(outcome=='change')
        assert report['outcomes']['system']['cancelled']==int(outcome=='cancel')
        assert len(report['direct_messages'])==2
        assert any(r['direct_message_input'].get('new_messages') for r in report['decisions'])
        assert any(r['retrieved_resources_in_context'] for r in report['decisions'])
        assert any(r['retrieved_artifacts_in_context'] for r in report['decisions'])
        rows.append({'fixture':name,'manifest_sha256':digest(m),'outcomes':report['outcomes'],'calls':runner.state['ledger']['calls'],
                     'archive_bytes':report['archive_bytes'],'semantic':report['semantic_verification'],
                     'independent':report['independent_domain_verification'],'scope':'Authored client/mail/artifact causal wiring; not spontaneous organization'})
    result = {'provider_calls': 0, 'rows': rows}; atomic_json(output / 'validation.json', result)
    return result


def main():
    p = argparse.ArgumentParser(); p.add_argument('--output', type=Path, required=True); a = p.parse_args()
    print(json.dumps(asyncio.run(validate(a.output)), indent=2))


if __name__ == '__main__':
    main()

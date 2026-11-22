"""Independent domain reconstruction and small assignment witnesses.

Does not import the world, its pricing/resource helpers, or its event methods.
Assignments use investigator ground truth and are NOT autonomous agent policies.
"""
from copy import deepcopy
from itertools import product
from functools import lru_cache
import json


def price_and_resources(m, req, room):
    if room == f'external:{req["owner"]}':
        assert req['external_allowed']
        return m['prices_cents']['external'], []
    assert room in req['rooms']
    resources = [room]
    if req['technician']:
        resources.append('technician')
    resources.extend(req['equipment'])
    return sum(m['prices_cents'][r] for r in resources), resources


def check_capacity(reservations):
    occupied = set()
    for r in reservations:
        for resource in [f'client:{r["owner"]}', *r['resources']]:
            key = (r['day'], r['time'], resource)
            assert key not in occupied, 'Independent capacity conflict'
            occupied.add(key)


def verify_domain(records):
    """Reconstruct all non-scheduler world fields with separate rules/code.

The core auditor verifies scheduler, context, histories, provider/accounting,
resources and interventions separately. This adds an independent domain oracle.
"""
    checked = 0
    for rec in records:
        before = rec['state_before']['world']; after = rec['state_after']['world']
        expected = deepcopy(before); m = before['manifest']
        specs = before['requests']
        def cash(kind, ident, amount, cause):
            b = expected['budget']
            if kind == 'reserve':
                assert b['available_cents'] >= amount
                b['available_cents'] -= amount; b['committed_cents'] += amount
            elif kind == 'refund':
                b['available_cents'] += amount; b['committed_cents'] -= amount
            else:
                assert kind == 'charge'
                b['committed_cents'] -= amount; b['spent_cents'] += amount
            receipt = {'sequence': len(expected['transactions']) + 1, 'kind': kind,
                       'appointment_id': ident, 'amount_cents': amount, 'day': before['day'],
                       'tick': before['tick'], 'cause': cause}
            expected['transactions'].append(receipt)
            return receipt
        if rec['event_type'] == 'decision.completed':
            result = rec['consequences']['tool_response']
            action = rec['computation']['selected_action']
            actor = rec['agent']; conf = m['direct_messages']
            received = [x for x in before['direct_messages'] if x['recipient'] == actor]
            def views(items, sent=False):
                return [{'id': f'{"sent" if sent else "inbox"}-{actor}-{i}',
                         **{k: msg[k] for k in ('sender', 'recipient', 'day', 'tick', 'text')}}
                        for i, msg in enumerate(items, 1)]
            inbox = rec['registration']['observation']['direct_messages']
            if not conf['enabled']:
                assert inbox == {'enabled': False}
            else:
                history = rec['state_before']['agents'][actor]['history']; cursor = 0
                if history:
                    cursor = json.loads(history[-1]['messages'][0]['content']).get('direct_messages', {}).get('received_cursor', 0)
                new = views(received)[cursor:cursor + 8]
                assert set(inbox) == {'enabled', 'inbox', 'sent_history', 'new_messages', 'received_cursor', 'pending_count', 'exposure'}
                assert inbox['new_messages'] == new and inbox['received_cursor'] == cursor + len(new)
                assert inbox['pending_count'] == len(received) - cursor - len(new)
            if result and result.get('status') == 'ok' and action and action['tool'] == 'read' and action['arguments']['path'] in ('/inbox', '/sent'):
                sent = action['arguments']['path'] == '/sent'
                items = [x for x in before['direct_messages'] if x['sender' if sent else 'recipient'] == actor]
                offset, length = action['arguments'].get('offset', 0), action['arguments'].get('length', 8)
                expected_messages = views(items, sent)[offset:offset + length]
                assert result == {'status': 'ok', 'messages': expected_messages, 'offset': offset, 'length': len(expected_messages)}
            if result and result.get('status') == 'ok' and action and action['tool'] == 'act' and action['arguments']['resource'] == '/messages':
                args = action['arguments']['arguments']; recipient, text = args['recipient'], args['text']
                assert conf['enabled'] and args['operation'] == 'send' and recipient in ('A', 'B', 'C') and recipient != actor
                assert type(text) is str and 0 < len(text.encode('utf-8')) <= 1024
                assert sum(x['sender'] == actor and x['recipient'] == recipient for x in before['direct_messages']) < 32
                expected['direct_messages'].append({'internal_id': f'dm-{len(before["direct_messages"]) + 1}', 'sender': actor,
                    'recipient': recipient, 'text': text, 'day': before['day'], 'tick': before['tick']})
                sent_items = [x for x in expected['direct_messages'] if x['sender'] == actor]
                assert result == {'status': 'ok', 'delivered': True, 'message': views(sent_items, True)[-1],
                                  'delivery_meaning': 'Stored in recipient inbox, not evidence of awareness or use'}
            if result and result.get('status') == 'ok' and action and action['tool'] == 'act' and action['arguments']['resource'] == '/schedule':
                args = action['arguments']['arguments']; ident = args['appointment_id']; op = args['operation']; actor = rec['agent']
                req = specs[ident]
                assert req['owner'] == actor and before['requests'][ident]['status'] == 'pending'
                assert before['phase'] == 'active' and req['arrival_day'] <= before['day']
                if op in ('book', 'confirm'):
                    assert req['client_status'] == 'confirmed'
                old = before['reservations'].get(ident)
                transactions = []
                if op in ('hold', 'book'):
                    assert old is None
                else:
                    assert old is not None
                if op in ('release', 'reschedule'):
                    del expected['reservations'][ident]
                    transactions.append(cash('refund', ident, old['price_cents'], f'agent:{actor}:{op}'))
                rev = before['revisions'].get(ident, 0) + 1
                if op in ('hold', 'book', 'reschedule'):
                    day, time, room = args['day'], args['time'], args['room_id']
                    assert type(day) is int and max(before['day'], req['earliest_day']) <= day <= req['latest_day']
                    assert time in req['times']
                    price, resources = price_and_resources(m, req, room)
                    status = old['status'] if op == 'reschedule' else 'held' if op == 'hold' else 'confirmed'
                    expected['reservations'][ident] = {
                        'appointment_id': ident, 'owner': actor, 'day': day, 'time': time, 'room_id': room,
                        'resources': resources, 'price_cents': price, 'status': status,
                        'hold_expires_tick': (before['day'] - 1) * 4 + before['tick'] + m['hold_ticks'] if status == 'held' else None,
                        'revision': rev}
                    check_capacity(expected['reservations'].values())
                    transactions.append(cash('reserve', ident, price, f'agent:{actor}:{op}'))
                elif op == 'confirm':
                    assert old['status'] == 'held'
                    expected['reservations'][ident].update(status='confirmed', hold_expires_tick=None, revision=rev)
                else:
                    assert op == 'release'
                expected['revisions'][ident] = rev
                response = {'status': 'ok', 'operation': op, 'appointment_id': ident,
                            'reservation': deepcopy(expected['reservations'].get(ident)),
                            'transactions': transactions, 'meeting_completed': False}
                assert result == response
                expected['operations'].append({'actor': actor, 'day': before['day'], 'tick': before['tick'],
                                               'arguments': deepcopy(args), 'result': response})
        elif rec['event_type'] == 'environment.transition':
            event = rec['environment_event']; kind = event['kind']
            if kind == 'budget.deposit':
                b = expected['budget']; amount = m['funding']['amount_cents']
                assert before['phase'] == 'opening' and before['day'] in m['funding']['subsequent_days']
                assert not any(t['kind']=='deposit' and t['day']==before['day'] for t in before['transactions'])
                b['available_cents'] += amount; b['deposited_cents'] += amount
                expected['transactions'].append({'sequence':len(expected['transactions'])+1,
                    'kind':'deposit','appointment_id':None,'amount_cents':amount,
                    'day':before['day'],'tick':0,'cause':'environment:budget.deposit'})
            elif kind == 'day.open':
                for r in m['requests']:
                    if r['arrival_day'] == before['day']:
                        expected['requests'][r['id']] = {**deepcopy(r), 'status': 'pending', 'finalized_day': None, 'client_revision': 1}
                expected['phase'] = 'active'
            elif kind == 'client.update':
                u = m['client_updates'][len(before['client_events'])]; ident = u['appointment_id']
                assert u['day'] == before['day'] and event['appointment_id'] == ident
                req = expected['requests'][ident]
                assert req['client_status'] == 'provisional' and req['status'] == 'pending'
                req.update(deepcopy(u['changes'])); req['client_revision'] += 1
                req['client_status'] = 'cancelled' if u['outcome'] == 'cancel' else 'confirmed'
                old = expected['reservations'].get(ident)
                if old and (u['outcome'] == 'cancel' or old['time'] not in req['times']):
                    del expected['reservations'][ident]
                    cash('refund', ident, old['price_cents'], 'environment:client.update')
                if u['outcome'] == 'cancel':
                    req.update(status='cancelled', finalized_day=before['day'])
                    expected['outcomes'].append({'appointment_id': ident, 'owner': req['owner'], 'status': 'cancelled',
                                                'day': before['day'], 'time': '08:00', 'reservation': None})
                expected['client_events'].append(deepcopy(u))
            elif kind == 'hold.expire':
                ident = event['appointment_id']; r = expected['reservations'].pop(ident)
                assert r['status'] == 'held'
                assert r['hold_expires_tick'] <= (before['day'] - 1) * 4 + before['tick'] or before['phase'] == 'service' and r['day'] == before['day']
                cash('refund', ident, r['price_cents'], 'environment:hold.expire')
            elif kind == 'tick.advance':
                expected['tick'] += 1
            elif kind == 'booking.close':
                expected['phase'] = 'service'; expected['service_index'] = 0
            elif kind == 'service.pass':
                time = ('09:00', '14:00')[before['service_index']]
                assert event['time'] == time and before['phase'] == 'service'
                check_capacity(before['reservations'].values())
                for ident, r in list(sorted(expected['reservations'].items())):
                    if (r['day'], r['time']) == (before['day'], time):
                        assert r['status'] == 'confirmed' and specs[ident]['client_status'] == 'confirmed'
                        price, resources = price_and_resources(m, specs[ident], r['room_id'])
                        assert r['price_cents'] == price and r['resources'] == resources
                        cash('charge', ident, price, 'environment:service.pass')
                        expected['requests'][ident].update(status='completed', finalized_day=before['day'])
                        expected['outcomes'].append({'appointment_id': ident, 'owner': r['owner'], 'status': 'completed',
                            'day': before['day'], 'time': time, 'reservation': deepcopy(r)})
                        del expected['reservations'][ident]
                for ident, r in sorted(expected['requests'].items()):
                    if r['status'] == 'pending' and (r['deadline']['day'], r['deadline']['time']) <= (before['day'], time):
                        assert ident not in expected['reservations']
                        r.update(status='failed', finalized_day=before['day'])
                        expected['outcomes'].append({'appointment_id': ident, 'owner': r['owner'], 'status': 'failed',
                                                    'day': before['day'], 'time': time, 'reservation': None})
                expected['service_index'] += 1
            elif kind == 'day.close':
                if before['day'] == m['days']:
                    expected['phase'] = 'finished'
                else:
                    expected.update(day=before['day'] + 1, tick=0, service_index=0, phase='opening')
            else:
                assert kind == 'scheduler.skip'
        elif rec['event_type'] not in ('intervention.applied', 'run.limit'):
            raise AssertionError('Unsupported v3 transition')
        assert {k: v for k, v in expected.items() if k != 'control'} == {k: v for k, v in after.items() if k != 'control'}, 'Independent domain replay differs'
        b = after['budget']
        assert min(b.values()) >= 0 and b['initial_cents'] + b['deposited_cents'] == b['available_cents'] + b['committed_cents'] + b['spent_cents']
        assert b['committed_cents'] == sum(r['price_cents'] for r in after['reservations'].values())
        checked += 1
    return {'independent_domain_events_verified': checked, 'method': 'separate pricing/capacity/finance/arrival/expiry/service reconstruction', 'provider_calls': 0}


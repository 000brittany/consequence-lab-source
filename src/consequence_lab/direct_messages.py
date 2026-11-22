"""Private office mail: deterministic delivery, bounded explicit exposure.

No strategies, replies, wakeups, read receipts to peers, or shared-file policy.
Stored global identifiers are investigator-only; visible counters use only an
actor's own authorized sent/received activity.
"""
import json
from .jsonio import clone, integer, keys

ACTORS = ('A', 'B', 'C')


def validate_config(config):
    keys(config, ('enabled', 'max_per_pair', 'max_bytes', 'observation_limit'))
    if type(config['enabled']) is not bool or any(type(config[k]) is not int for k in ('max_per_pair', 'max_bytes', 'observation_limit')) or {k: config[k] for k in config if k != 'enabled'} != {
            'max_per_pair': 32, 'max_bytes': 1024, 'observation_limit': 8}:
        raise ValueError('Invalid direct-message configuration')


def authorize(w):
    if not w['manifest']['direct_messages']['enabled']:
        raise ValueError('not_found_or_denied')


def view(w, actor, sent=False):
    field = 'sender' if sent else 'recipient'
    return [{'id': f'{"sent" if sent else "inbox"}-{actor}-{i}',
             **{k: m[k] for k in ('sender', 'recipient', 'day', 'tick', 'text')}}
            for i, m in enumerate((m for m in w['direct_messages'] if m[field] == actor), 1)]


def send(w, actor, args):
    authorize(w); keys(args, ('operation', 'recipient', 'text'))
    recipient, text = args['recipient'], args['text']
    if actor not in ACTORS or args['operation'] != 'send' or recipient not in ACTORS or recipient == actor:
        raise ValueError('not_found_or_denied')
    if type(text) is not str or not text or len(text.encode('utf-8')) > w['manifest']['direct_messages']['max_bytes']:
        raise ValueError('Message must contain 1–1024 UTF-8 bytes')
    if sum(m['sender'] == actor and m['recipient'] == recipient for m in w['direct_messages']) >= 32:
        raise ValueError('Sender/recipient message allowance exhausted')
    w['direct_messages'].append({'internal_id': f'dm-{len(w["direct_messages"]) + 1}',
        'sender': actor, 'recipient': recipient, 'day': w['day'], 'tick': w['tick'], 'text': text})
    return {'status': 'ok', 'delivered': True, 'message': view(w, actor, sent=True)[-1],
            'delivery_meaning': 'Stored in recipient inbox, not evidence of awareness or use'}


def observe(state, actor):
    w = state['world']; config = w['manifest']['direct_messages']
    if not config['enabled']:
        return {'direct_messages': {'enabled': False}}
    history = state['agents'][actor]['history']; cursor = 0
    if history:
        previous = json.loads(history[-1]['messages'][0]['content'])
        cursor = previous.get('direct_messages', {}).get('received_cursor', 0)
    received = view(w, actor); new = received[cursor:cursor + config['observation_limit']]
    return {'direct_messages': {'enabled': True, 'inbox': '/inbox', 'sent_history': '/sent',
        'new_messages': new, 'received_cursor': cursor + len(new), 'pending_count': len(received) - cursor - len(new),
        'exposure': 'New messages supplied in this ordinary model input; exposure does not establish functional use'}}


def inspect(w, actor, path):
    authorize(w)
    if path == '/messages':
        return {'status': 'ok', 'operation': 'send', 'fields': ['operation', 'recipient', 'text'],
                'recipients': [a for a in ACTORS if a != actor], 'max_bytes': 1024,
                'cost': 'One optional information operation and one generation; no office charge'}
    messages = view(w, actor, sent=path == '/sent')
    return {'status': 'ok', 'total': len(messages), 'recent_metadata': [{k: v for k, v in m.items() if k != 'text'} for m in messages[-8:]],
            'history': 'read this exact path; offset and length count messages, at most 8 per read'}


def read(w, actor, args):
    authorize(w)
    if not set(args) <= {'path', 'offset', 'length'} or 'path' not in args:
        raise ValueError('Invalid inbox read')
    offset, length = args.get('offset', 0), args.get('length', 8)
    integer(offset); integer(length, 1)
    if length > 8:
        raise ValueError('At most 8 messages per read')
    messages = view(w, actor, sent=args['path'] == '/sent')[offset:offset + length]
    return ({'status': 'ok', 'messages': messages, 'offset': offset, 'length': len(messages)},
            {'kind': 'resource_read', 'resource': args['path'], 'offset': offset,
             'message_ids': [m['id'] for m in messages], 'messages': clone(messages)})


def validate(w):
    counts = {}
    for i, m in enumerate(w['direct_messages'], 1):
        keys(m, ('internal_id', 'sender', 'recipient', 'day', 'tick', 'text'))
        if m['internal_id'] != f'dm-{i}' or m['sender'] not in ACTORS or m['recipient'] not in ACTORS or m['sender'] == m['recipient']:
            raise ValueError('Invalid message identity')
        if type(m['text']) is not str or not m['text'] or len(m['text'].encode('utf-8')) > 1024:
            raise ValueError('Invalid message content')
        if type(m['day']) is not int or not 1 <= m['day'] <= w['day'] or type(m['tick']) is not int or not 0 <= m['tick'] <= 3:
            raise ValueError('Invalid delivery time')
        pair = m['sender'], m['recipient']; counts[pair] = counts.get(pair, 0) + 1
        if counts[pair] > 32 or not w['manifest']['direct_messages']['enabled']:
            raise ValueError('Unauthorized message delivery')

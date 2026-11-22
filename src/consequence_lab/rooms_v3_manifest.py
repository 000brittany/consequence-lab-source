"""Rooms v3: materialized arrivals and hard eligibility, never agent policies."""
import random

from .communication import configuration, validate as validate_communication
from .jsonio import clone, integer, keys

ACTORS = ('A', 'B', 'C')
TIMES = ('09:00', '14:00')
ROOMS = ('R0', 'R1')
VERSION = 'rooms-v3-office-days-v3'
OBJECTIVE = 'Successfully fulfill your own client meeting requests within their requirements and final fulfillment deadlines.'


def generate(seed=0, days=12, *, communication='public_metadata', priority_phase=0, direct_messages=True):
    integer(seed); integer(days, 1)
    if days > 60 or priority_phase not in (0, 1, 2):
        raise ValueError('Invalid horizon/priority')
    rng = random.Random(seed)
    requests = []
    # Two requests per client in each three-day block. Usual windows persist;
    # bounded deviations use the materialized seed, not model behavior.
    for base in range(1, days + 1, 3):
        for actor in ACTORS:
            for ordinal in range(2):
                first = base + ordinal
                if first > days:
                    continue
                last = min(first + 1, days)
                usual = ['09:00'] if actor == 'A' else ['14:00'] if actor == 'B' else list(TIMES)
                variation = rng.randrange(4)
                times = (list(TIMES) if variation == 0 else
                         ['09:00'] if actor == 'B' and variation in (1, 2) else
                         [TIMES[variation % 2]] if actor == 'C' and variation in (1, 3) else usual)
                technician = actor in ('A', 'B') or variation in (1, 2)
                equipment = ['av_kit'] if actor == 'A' or (actor == 'B' and variation == 2) or (actor == 'C' and variation in (2, 3)) else []
                rooms = ['R1'] if actor == 'C' else list(ROOMS)
                ident = f'{actor}-{base:02d}-{ordinal + 1}'
                requests.append({'id': ident, 'owner': actor, 'client_id': f'client:{actor}',
                    'arrival_day': max(1, first - 2), 'earliest_day': first,
                    'latest_day': last, 'times': times, 'rooms': rooms,
                    'technician': technician, 'equipment': equipment,
                    'external_allowed': True,
                    'deadline': {'day': last, 'time': max(times)}})
    # A separate seeded stream preserves the underlying scheduling requests.
    # One request per client/block can await a later reply; the reply's content
    # is investigator-only until its scheduled environmental event.
    replies = random.Random(seed ^ 0xC11E17); updates = []
    for r in requests:
        provisional = r['id'].endswith('-2') and r['arrival_day'] < r['earliest_day']
        r.update(client_status='provisional' if provisional else 'confirmed',
                 client_reply_day=r['arrival_day'] + 1 if provisional else None)
        if provisional:
            outcome = replies.choice(('confirm', 'confirm', 'confirm', 'change', 'cancel'))
            times = ([TIMES[1] if r['times'] == [TIMES[0]] else TIMES[0]]
                     if len(r['times']) == 1 else [replies.choice(TIMES)])
            changes = {'times': times, 'deadline': {'day': r['latest_day'], 'time': max(times)}} if outcome == 'change' else {}
            updates.append({'appointment_id': r['id'], 'day': r['client_reply_day'],
                            'outcome': outcome, 'changes': changes})
    m = {'version': VERSION, 'origin': {'kind': 'seeded', 'algorithm': 'overlapping_client_responses_v3'}, 'seed': seed, 'days': days, 'actors': list(ACTORS),
         'ticks': 4, 'information': 2, 'policy': 'candidate', 'hold_ticks': 8,
         'information_resources': ['/messages'],
         'direct_messages': {'enabled': direct_messages, 'max_per_pair': 32, 'max_bytes': 1024, 'observation_limit': 8},
         'client_updates': sorted(updates, key=lambda u: (u['day'], u['appointment_id'])),
         'decision_times': ['08:00', '08:15', '08:30', '08:45'],
         'orders': [list(ACTORS[(d + priority_phase) % 3:] + ACTORS[:(d + priority_phase) % 3]) for d in range(days)],
         'priority_phase': priority_phase, 'communication': configuration(communication),
         'initial_budget_cents': 55_000,
         'prices_cents': {'R0': 1000, 'R1': 1500, 'technician': 1000, 'av_kit': 500, 'external': 7000},
         'capacity': {'R0': 1, 'R1': 1, 'technician': 1, 'av_kit': 1},
         'client_profiles': {
             'A': {'usual_times': ['09:00'], 'usual_rooms': list(ROOMS), 'usual_technician': True, 'usual_equipment': ['av_kit']},
             'B': {'usual_times': list(TIMES), 'usual_rooms': list(ROOMS), 'usual_technician': True, 'usual_equipment': []},
             'C': {'usual_times': list(TIMES), 'usual_rooms': ['R1'], 'usual_technician': False, 'usual_equipment': []}},
         'requests': sorted(requests, key=lambda r: (r['arrival_day'], r['id'])),
         'external_reservations': []}
    validate(m, regenerate=False)
    return m


def validate(m, regenerate=True):
    keys(m, ('version', 'origin', 'seed', 'days', 'actors', 'ticks', 'information', 'policy', 'hold_ticks',
             'information_resources', 'direct_messages', 'client_updates',
             'decision_times', 'orders', 'priority_phase', 'communication', 'initial_budget_cents',
             'prices_cents', 'capacity', 'client_profiles', 'requests', 'external_reservations'))
    if m['version'] != VERSION or m['actors'] != list(ACTORS) or m['policy'] != 'candidate':
        raise ValueError('Unsupported v3 manifest')
    integer(m['days'], 1); integer(m['seed']); integer(m['initial_budget_cents'])
    for field in ('ticks', 'information', 'hold_ticks', 'priority_phase'):
        integer(m[field])
    if m['days'] > 60 or m['priority_phase'] not in (0, 1, 2):
        raise ValueError('Invalid horizon/priority')
    if (m['ticks'], m['information'], m['hold_ticks'], m['decision_times']) != (4, 2, 8, ['08:00', '08:15', '08:30', '08:45']):
        raise ValueError('Unsupported time/opportunity protocol')
    if m['information_resources'] != ['/messages']:
        raise ValueError('Invalid information resource classification')
    from .direct_messages import validate_config
    validate_config(m['direct_messages'])
    if len(m['orders']) != m['days'] or any(len(x) != 3 or set(x) != set(ACTORS) for x in m['orders']):
        raise ValueError('Invalid actor order')
    validate_communication(m['communication'])
    keys(m['prices_cents'], ('R0', 'R1', 'technician', 'av_kit', 'external'))
    for amount in m['prices_cents'].values():
        integer(amount, 1)
    if m['capacity'] != {'R0': 1, 'R1': 1, 'technician': 1, 'av_kit': 1}:
        raise ValueError('Unsupported resource capacities')
    if set(m['client_profiles']) != set(ACTORS) or m['external_reservations'] != []:
        raise ValueError('Invalid profiles/external reservations')
    seen = set()
    for r in m['requests']:
        keys(r, ('id', 'owner', 'client_id', 'arrival_day', 'earliest_day', 'latest_day',
                 'times', 'rooms', 'technician', 'equipment', 'external_allowed', 'deadline', 'client_status', 'client_reply_day'))
        if type(r['id']) is not str or not r['id'] or r['id'] in seen or r['owner'] not in ACTORS:
            raise ValueError('Invalid request identity')
        seen.add(r['id'])
        for field in ('arrival_day', 'earliest_day', 'latest_day'):
            integer(r[field], 1)
        if not 1 <= r['arrival_day'] <= r['earliest_day'] <= r['latest_day'] <= m['days']:
            raise ValueError('Invalid request dates')
        if r['client_id'] != f'client:{r["owner"]}' or type(r['technician']) is not bool or type(r['external_allowed']) is not bool:
            raise ValueError('Invalid client/resource requirement')
        if not r['times'] or len(set(r['times'])) != len(r['times']) or not set(r['times']) <= set(TIMES):
            raise ValueError('Invalid service times')
        if not r['rooms'] or len(set(r['rooms'])) != len(r['rooms']) or not set(r['rooms']) <= set(ROOMS):
            raise ValueError('Invalid room eligibility')
        if r['equipment'] not in ([], ['av_kit']) or r['deadline'] != {'day': r['latest_day'], 'time': max(r['times'])}:
            raise ValueError('Invalid equipment/deadline')
        if r['client_status'] not in ('provisional', 'confirmed'):
            raise ValueError('Invalid initial client status')
        if r['client_status'] == 'provisional':
            integer(r['client_reply_day'], 1)
            if not r['arrival_day'] < r['client_reply_day'] <= r['earliest_day']:
                raise ValueError('Client reply must arrive after request and before service')
        elif r['client_reply_day'] is not None:
            raise ValueError('Unexpected client reply')
    by_id = {r['id']: r for r in m['requests']}; replied = set()
    if m['client_updates'] != sorted(m['client_updates'], key=lambda u: (u['day'], u['appointment_id'])):
        raise ValueError('Client updates must be ordered by day and request')
    for u in m['client_updates']:
        keys(u, ('appointment_id', 'day', 'outcome', 'changes'))
        integer(u['day'], 1)
        r = by_id.get(u['appointment_id'])
        if not r or r['client_status'] != 'provisional' or u['appointment_id'] in replied or u['day'] != r['client_reply_day']:
            raise ValueError('Invalid client update schedule')
        replied.add(u['appointment_id'])
        if u['outcome'] not in ('confirm', 'change', 'cancel'):
            raise ValueError('Invalid client outcome')
        if u['outcome'] == 'change':
            keys(u['changes'], ('times', 'deadline'))
            t = u['changes']['times']
            if not t or len(set(t)) != len(t) or not set(t) <= set(TIMES) or u['changes']['deadline'] != {'day': r['latest_day'], 'time': max(t)}:
                raise ValueError('Invalid changed requirements')
        elif u['changes'] != {}:
            raise ValueError('Unexpected requirement changes')
    if m['origin'].get('kind') == 'seeded':
        if m['origin'] != {'kind': 'seeded', 'algorithm': 'overlapping_client_responses_v3'}:
            raise ValueError('Invalid generation provenance')
    else:
        keys(m['origin'], ('kind', 'name'))
        if m['origin']['kind'] != 'software_fixture' or type(m['origin']['name']) is not str or not m['origin']['name']:
            raise ValueError('Invalid fixture provenance')
    if regenerate and m['origin']['kind'] == 'seeded':
        expected = generate(m['seed'], m['days'], communication=m['communication']['mode'], priority_phase=m['priority_phase'], direct_messages=m['direct_messages']['enabled'])
        if m != expected:
            raise ValueError('Manifest differs from exact seeded generation')


def quote(m, request, room):
    if room == f'external:{request["owner"]}':
        return m['prices_cents']['external']
    return (m['prices_cents'][room]
            + int(request['technician']) * m['prices_cents']['technician']
            + sum(m['prices_cents'][x] for x in request['equipment']))


def task_specs():
    return {a: {'id': f'longitudinal:{a}', 'objective': OBJECTIVE, 'payload': {}} for a in ACTORS}

"""Rolling office days, escrowed funds and explicit service-time fulfillment.

All mutations occur in apply/apply_event on a runner-staged state. No model
policy, memory retrieval, automatic booking or scientific classification.
"""
from . import opportunities, direct_messages
from .config import scheduled_tools
from .jsonio import clone, integer, keys
from .rooms_v3_manifest import ACTORS, ROOMS, TIMES, VERSION, validate as validate_manifest, quote
from .scheduling import normalize_scheduled_action


def domain(manifest):
    validate_manifest(manifest)
    return {'manifest': clone(manifest), 'day': 1, 'tick': 0, 'phase': 'opening',
            'service_index': 0, 'version': VERSION, 'requests': {}, 'reservations': {},
            'revisions': {}, 'client_events': [], 'direct_messages': [], 'budget': {'initial_cents': manifest['initial_budget_cents'],
                'available_cents': manifest['initial_budget_cents'], 'committed_cents': 0, 'spent_cents': 0},
            'transactions': [], 'outcomes': [], 'operations': [], 'control': opportunities.initial(ACTORS)}


def resources(request, room):
    if room.startswith('external:'):
        return []
    return [room] + (['technician'] if request['technician'] else []) + request['equipment']


def reservations_conflict(a, b):
    return (a['day'], a['time']) == (b['day'], b['time']) and (
        a['owner'] == b['owner'] or bool(set(a['resources']) & set(b['resources'])))


class RoomsV3:
    name = 'recurring_rooms_v3'

    def model_tools(self):
        tools = scheduled_tools()
        act = next(t for t in tools if t['name'] == 'act')
        act['description'] = 'Apply one documented booking, direct-message or scheduler operation. book requires client confirmation; hold is optional.'
        act['parameters']['properties']['resource']['enum'] = ['/schedule', '/opportunities', '/messages']
        act['parameters']['properties']['arguments'] = {
            'type': 'object', 'description': 'confirm/release need appointment_id; hold/book/reschedule also need day/time/room_id; send on /messages needs recipient/text; yield needs no other fields.',
            'properties': {'operation': {'type': 'string', 'enum': ['hold', 'book', 'confirm', 'release', 'reschedule', 'yield', 'send']},
                'appointment_id': {'type': 'string'}, 'day': {'type': 'integer', 'minimum': 1, 'maximum': 60},
                'recipient': {'type': 'string', 'enum': list(ACTORS)}, 'text': {'type': 'string', 'minLength': 1, 'maxLength': 1024},
                'time': {'type': 'string', 'enum': list(TIMES)},
                'room_id': {'type': 'string', 'enum': ['R0', 'R1', 'external:A', 'external:B', 'external:C']}},
            'required': ['operation'], 'additionalProperties': False}
        return tools

    def normalize_action(self, action):
        return normalize_scheduled_action(action)

    def resource_names(self, w, actor):
        return ['schedule', 'facility', 'opportunities', 'client', 'budget'] + (['messages', 'inbox', 'sent'] if w['manifest']['direct_messages']['enabled'] else [])

    def visible_time(self, w):
        return (w['day'] - 1) * 6 + w['tick'] + (w['service_index'] if w['phase'] in ('service', 'finished') else 0)

    def add_task(self, w, actor, ident, payload):
        if actor not in ACTORS or ident != f'longitudinal:{actor}' or payload != {}:
            raise ValueError('Invalid longitudinal assignment')

    def task_status(self, w, actor, payload):
        return ('completed' if all(r['status'] in ('completed', 'cancelled') for r in w['requests'].values() if r['owner'] == actor)
                else 'expired') if w['phase'] == 'finished' else 'assigned'

    def task_complete(self, w, actor, payload):
        return self.task_status(w, actor, payload) == 'completed'

    def _spec(self, w, ident):
        return w['requests'][ident]

    def augment_observation(self, state, actor):
        return direct_messages.observe(state, actor)

    def read(self, w, actor, args):
        if args.get('path') in ('/inbox', '/sent'):
            return direct_messages.read(w, actor, args)
        return None

    def _eligible(self, w, actor, ident, day, time, room):
        r = w['requests'].get(ident)
        if not r or r['owner'] != actor:
            raise ValueError('not_found_or_denied')
        if w['phase'] != 'active' or r['status'] != 'pending':
            raise ValueError('outside_booking_window')
        spec = self._spec(w, ident)
        integer(day, 1)
        if day < w['day'] or not spec['earliest_day'] <= day <= spec['latest_day'] or time not in spec['times']:
            raise ValueError('client_or_deadline_ineligible')
        if room not in spec['rooms'] and not (spec['external_allowed'] and room == f'external:{actor}'):
            raise ValueError('client_or_resource_ineligible')
        return spec

    def _cash(self, w, kind, ident, amount, cause):
        b = w['budget']
        if kind == 'reserve':
            b['available_cents'] -= amount; b['committed_cents'] += amount
        elif kind == 'refund':
            b['committed_cents'] -= amount; b['available_cents'] += amount
        elif kind == 'charge':
            b['committed_cents'] -= amount; b['spent_cents'] += amount
        else:
            raise ValueError('Unknown financial transition')
        receipt = {'sequence': len(w['transactions']) + 1, 'kind': kind, 'appointment_id': ident,
                   'amount_cents': amount, 'day': w['day'], 'tick': w['tick'], 'cause': cause}
        w['transactions'].append(receipt)
        return clone(receipt)

    def _release(self, w, ident, cause):
        old = w['reservations'].pop(ident)
        receipt = self._cash(w, 'refund', ident, old['price_cents'], cause)
        return {'reservation': clone(old), 'transaction': receipt, 'cause': cause}

    def _public_id(self, w, actor, ident):
        req = w['requests'][ident]
        if req['owner'] == actor:
            return ident
        # The public handle derives from a public financial commitment, never
        # a private request's identifier, date, or unbooked request count.
        first = next(t['sequence'] for t in w['transactions'] if t['appointment_id'] == ident and t['kind'] == 'reserve')
        return f'office-reservation-{first}'

    def _public(self, w, actor):
        # Technical resource allocations and reservation prices are explicitly
        # public consequences of booking, not disclosures of unbooked requests.
        result = [{**clone(x), 'appointment_id': self._public_id(w, actor, ident)}
                  for ident, x in w['reservations'].items()]
        return sorted(result, key=lambda r: r['appointment_id'])

    def observe(self, w, actor):
        own = [clone(r) for _, r in sorted(w['requests'].items()) if r['owner'] == actor and r['status'] == 'pending']
        return {'kind': self.name, 'day': w['day'], 'decision_tick': w['tick'],
                'decision_clock_tick': (w['day'] - 1) * 4 + w['tick'],
                'decision_time': w['manifest']['decision_times'][min(w['tick'], 3)],
                'horizon_days': w['manifest']['days'], 'service_times': list(TIMES),
                'turn_order': clone(w['manifest']['orders'][w['day'] - 1]),
                'own_outstanding_requests': own,
                'own_recent_outcomes': [clone(o) for o in w['outcomes'] if o['owner'] == actor and o['day'] >= w['day'] - 1],
                'own_recent_client_updates': [clone(u) for u in w['client_events'] if w['requests'][u['appointment_id']]['owner'] == actor and u['day'] >= w['day'] - 1],
                'calendar': self._public(w, actor), 'budget': clone(w['budget']),
                'prices_cents': clone(w['manifest']['prices_cents']), 'capacity': clone(w['manifest']['capacity']),
                'own_client_profile': clone(w['manifest']['client_profiles'][actor]),
                'external_room_id': f'external:{actor}',
                'opportunities': {'information_limit': 2, 'domain_limit': 1, 'default_disposition': 'yield_tick',
                                  'yield_period_means': 'remaining decision ticks of this office day',
                                  **clone(w['control']['slots'][actor])}}

    def inspect(self, w, actor, path):
        if path in ('/messages', '/inbox', '/sent'):
            return direct_messages.inspect(w, actor, path)
        if path == '/schedule':
            return {'status': 'ok', 'calendar': self._public(w, actor), 'operations': {
                'hold': ['appointment_id', 'day', 'time', 'room_id'],
                'book': ['appointment_id', 'day', 'time', 'room_id'],
                'reschedule': ['appointment_id', 'day', 'time', 'room_id'],
                'confirm': ['appointment_id'], 'release': ['appointment_id']},
                'rule': 'book/confirm require client confirmation; reschedule preserves held/confirmed status. Only owner may modify. All reservations escrow funds.'}
        if path == '/budget':
            return {'status': 'ok', 'budget': clone(w['budget']), 'transactions': [
                {**clone(t), 'appointment_id': self._public_id(w, actor, t['appointment_id'])} for t in w['transactions']],
                    'rule': 'No replenishment, overdraft or protected fallback. Release/expiry refunds escrow; service charges escrow.'}
        if path == '/client':
            return {'status': 'ok', 'requests': [clone(x) for x in w['requests'].values() if x['owner'] == actor],
                    'client_updates': [clone(u) for u in w['client_events'] if w['requests'][u['appointment_id']]['owner'] == actor]}
        if path == '/facility':
            return {'status': 'ok', 'capacity': clone(w['manifest']['capacity']), 'prices_cents': clone(w['manifest']['prices_cents']),
                    'calendar': self._public(w, actor), 'external': 'Rental includes required support/equipment; only available if affordable.'}
        if path == '/opportunities':
            return {'status': 'ok', 'information_per_tick': 2, 'domain_per_tick': 1,
                    'dispositions': ['continue', 'yield_tick', 'yield_period'], 'omitted': 'yield_tick'}
        return None

    def apply(self, w, actor, resource, args):
        if resource == '/messages':
            return direct_messages.send(w, actor, args)
        if resource == '/opportunities':
            if args != {'operation': 'yield'}:
                raise ValueError('Invalid yield operation')
            return {'status': 'ok', 'operation': 'yield'}
        if resource != '/schedule':
            raise ValueError('not_found_or_denied')
        op = args.get('operation')
        if op not in ('hold', 'book', 'confirm', 'release', 'reschedule'):
            raise ValueError('Unsupported schedule operation')
        keys(args, ('operation', 'appointment_id') if op in ('confirm', 'release')
             else ('operation', 'appointment_id', 'day', 'time', 'room_id'))
        ident = args['appointment_id']; request = w['requests'].get(ident)
        if not request or request['owner'] != actor:
            raise ValueError('not_found_or_denied')
        if w['phase'] != 'active' or request['status'] != 'pending':
            raise ValueError('outside_booking_window')
        if op in ('book', 'confirm') and request['client_status'] != 'confirmed':
            raise ValueError('client_confirmation_pending')
        old = w['reservations'].get(ident)
        if op in ('confirm', 'release', 'reschedule') and old is None:
            raise ValueError('no_current_reservation')
        if op in ('hold', 'book') and old is not None:
            raise ValueError('already_reserved_use_reschedule')
        if op == 'confirm' and old['status'] != 'held':
            raise ValueError('already_confirmed')
        new = None
        if op in ('hold', 'book', 'reschedule'):
            spec = self._eligible(w, actor, ident, args['day'], args['time'], args['room_id'])
            new = {'appointment_id': ident, 'owner': actor, 'day': args['day'], 'time': args['time'],
                   'room_id': args['room_id'], 'resources': resources(spec, args['room_id']),
                   'price_cents': quote(w['manifest'], spec, args['room_id']),
                   'status': old['status'] if op == 'reschedule' else 'held' if op == 'hold' else 'confirmed',
                   'hold_expires_tick': None, 'revision': w['revisions'].get(ident, 0) + 1}
            if new['status'] == 'held':
                new['hold_expires_tick'] = (w['day'] - 1) * 4 + w['tick'] + w['manifest']['hold_ticks']
            if any(reservations_conflict(new, other) for key, other in w['reservations'].items() if key != ident):
                raise ValueError('capacity_unavailable')
            if new['price_cents'] > w['budget']['available_cents'] + (old['price_cents'] if old else 0):
                raise ValueError('insufficient_shared_funds')
        # Every check above precedes both resource and financial changes.
        transactions = []
        if op in ('release', 'reschedule'):
            transactions.append(self._release(w, ident, f'agent:{actor}:{op}')['transaction'])
        if new:
            w['reservations'][ident] = new
            transactions.append(self._cash(w, 'reserve', ident, new['price_cents'], f'agent:{actor}:{op}'))
        elif op == 'confirm':
            old['status'] = 'confirmed'; old['hold_expires_tick'] = None
            old['revision'] = w['revisions'].get(ident, 0) + 1
        w['revisions'][ident] = w['revisions'].get(ident, 0) + 1
        receipt = {'status': 'ok', 'operation': op, 'appointment_id': ident,
                   'reservation': clone(w['reservations'].get(ident)), 'transactions': transactions,
                   'meeting_completed': False}
        w['operations'].append({'actor': actor, 'day': w['day'], 'tick': w['tick'], 'arguments': clone(args), 'result': clone(receipt)})
        return receipt

    def validate_action(self, w, actor, action):
        opportunities.validate_action(w, actor, action)

    def finish_decision(self, before, after, actor, action, error, proposals, result):
        return opportunities.finish(before, after, actor, action, error, proposals, result)

    def next_event(self, state):
        w = state['world']; m = w['manifest']
        if state['status'] != 'running' or w['phase'] == 'finished':
            return None
        if w['phase'] == 'opening':
            return {'kind': 'day.open', 'day': w['day']}
        if len(w['client_events']) < len(m['client_updates']):
            update = m['client_updates'][len(w['client_events'])]
            if update['day'] == w['day']:
                return {'kind': 'client.update', 'appointment_id': update['appointment_id'], 'day': w['day']}
        now = (w['day'] - 1) * 4 + w['tick']
        expiring = [k for k, r in w['reservations'].items() if r['status'] == 'held' and
                    (r['hold_expires_tick'] <= now or w['phase'] == 'service' and r['day'] == w['day'])]
        if expiring:
            return {'kind': 'hold.expire', 'appointment_id': sorted(expiring)[0], 'day': w['day'], 'tick': w['tick']}
        if w['phase'] == 'service':
            return {'kind': 'service.pass', 'day': w['day'], 'time': TIMES[w['service_index']]} if w['service_index'] < 2 else {'kind': 'day.close', 'day': w['day']}
        if w['control']['round_done']:
            return {'kind': 'booking.close' if w['tick'] == 3 else 'tick.advance', 'day': w['day'], 'tick': w['tick']}
        actor = state['scheduler']['order'][state['scheduler']['cursor']]
        if not opportunities.has_budget(w, actor):
            return {'kind': 'scheduler.skip', 'actor': actor}
        return None

    def apply_event(self, state, event):
        if event != self.next_event(state):
            raise ValueError('Unscheduled v3 event')
        w = state['world']; m = w['manifest']; kind = event['kind']
        result = {'rule': f'{self.name}:{kind}', 'day': w['day'], 'tick': w['tick']}
        if kind == 'day.open':
            released = []
            for r in m['requests']:
                if r['arrival_day'] == w['day']:
                    w['requests'][r['id']] = {**clone(r), 'status': 'pending', 'finalized_day': None, 'client_revision': 1}
                    released.append(r['id'])
            w['phase'] = 'active'; opportunities.grant_tick(w)
            result['arrivals'] = released
        elif kind == 'client.update':
            u = m['client_updates'][len(w['client_events'])]; ident = u['appointment_id']; req = w['requests'][ident]
            if req['client_status'] != 'provisional' or req['status'] != 'pending':
                raise ValueError('Client reply after resolution')
            before = clone(req); req.update(clone(u['changes'])); req['client_revision'] += 1
            req['client_status'] = 'cancelled' if u['outcome'] == 'cancel' else 'confirmed'
            old = w['reservations'].get(ident); release = None
            if old and (u['outcome'] == 'cancel' or old['time'] not in req['times']):
                release = self._release(w, ident, 'environment:client.update')
            if u['outcome'] == 'cancel':
                req.update(status='cancelled', finalized_day=w['day'])
                w['outcomes'].append({'appointment_id': ident, 'owner': req['owner'], 'status': 'cancelled',
                                     'day': w['day'], 'time': '08:00', 'reservation': None})
            w['client_events'].append(clone(u))
            result.update(client_update=clone(u), owner=req['owner'], before=before, after=clone(req), release=release)
        elif kind == 'hold.expire':
            result['release'] = self._release(w, event['appointment_id'], 'environment:hold.expire')
        elif kind == 'scheduler.skip':
            opportunities.end_turn(state)
        elif kind == 'tick.advance':
            w['tick'] += 1; w['control']['ticks_advanced'] += 1
            state['scheduler']['cursor'] = 0; opportunities.grant_tick(w)
        elif kind == 'booking.close':
            w['phase'] = 'service'; w['service_index'] = 0
        elif kind == 'service.pass':
            completed = []; failed = []; transactions = []
            for ident, r in list(sorted(w['reservations'].items())):
                if (r['day'], r['time']) == (w['day'], event['time']):
                    if r['status'] != 'confirmed' or w['requests'][ident]['client_status'] != 'confirmed':
                        raise ValueError('Unexpired hold at service')
                    transactions.append(self._cash(w, 'charge', ident, r['price_cents'], 'environment:service.pass'))
                    w['requests'][ident].update(status='completed', finalized_day=w['day'])
                    outcome = {'appointment_id': ident, 'owner': r['owner'], 'status': 'completed',
                               'day': w['day'], 'time': event['time'], 'reservation': clone(r)}
                    w['outcomes'].append(outcome); completed.append(ident); del w['reservations'][ident]
            for ident, r in sorted(w['requests'].items()):
                if r['status'] == 'pending' and (r['deadline']['day'], r['deadline']['time']) <= (w['day'], event['time']):
                    if ident in w['reservations']:
                        raise ValueError('Reservation survived final deadline')
                    r.update(status='failed', finalized_day=w['day'])
                    w['outcomes'].append({'appointment_id': ident, 'owner': r['owner'], 'status': 'failed',
                                          'day': w['day'], 'time': event['time'], 'reservation': None})
                    failed.append(ident)
            result.update(completed=completed, failed=failed, transactions=transactions)
            w['service_index'] += 1
        elif kind == 'day.close':
            w['control']['ticks_advanced'] += 1; w['control']['periods_closed'] += 1
            if w['day'] == m['days']:
                if w['reservations'] or any(r['status'] == 'pending' for r in w['requests'].values()):
                    raise ValueError('Unresolved requests at horizon')
                w['phase'] = 'finished'; state['status'] = 'finished'; state['stop_reason'] = 'horizon_complete'
                for a in ACTORS:
                    state['tasks'][f'longitudinal:{a}']['status'] = self.task_status(w, a, {})
            else:
                w['day'] += 1; w['tick'] = 0; w['service_index'] = 0; w['phase'] = 'opening'
                state['scheduler']['order'] = clone(m['orders'][w['day'] - 1]); state['scheduler']['cursor'] = 0
                for slot in w['control']['slots'].values():
                    slot.update(yield_period=False, yield_tick=False, domain_used=False, info_used=0)
                w['control']['round_done'] = False
        return result

    def validate(self, w, agents):
        keys(w, ('manifest', 'day', 'tick', 'phase', 'service_index', 'version', 'requests', 'reservations',
                 'revisions', 'client_events', 'direct_messages', 'budget', 'transactions', 'outcomes', 'operations', 'control'))
        m = w['manifest']; validate_manifest(m)
        if set(agents) != set(ACTORS) or w['version'] != VERSION or w['phase'] not in ('opening', 'active', 'service', 'finished'):
            raise ValueError('Invalid v3 world/population')
        integer(w['day'], 1); integer(w['tick']); integer(w['service_index'])
        if w['day'] > m['days'] or w['tick'] > 3 or w['service_index'] > 2:
            raise ValueError('Invalid day boundary')
        if w['phase'] == 'finished' and (w['day'] != m['days'] or w['service_index'] != 2):
            raise ValueError('Invalid final boundary')
        if w['client_events'] != m['client_updates'][:len(w['client_events'])] or any(u['day'] > w['day'] for u in w['client_events']):
            raise ValueError('Client update provenance mismatch')
        if any(u['day'] < w['day'] for u in m['client_updates'][len(w['client_events']):]):
            raise ValueError('Missing past client update')
        expected = {r['id']: clone(r) for r in m['requests'] if r['arrival_day'] < w['day'] or r['arrival_day'] == w['day'] and w['phase'] != 'opening'}
        for u in w['client_events']:
            if u['appointment_id'] not in expected:
                raise ValueError('Reply before request arrival')
            expected[u['appointment_id']].update(clone(u['changes']))
            expected[u['appointment_id']]['client_status'] = 'cancelled' if u['outcome'] == 'cancel' else 'confirmed'
        if set(w['requests']) != set(expected):
            raise ValueError('Unrecorded/missing request arrival')
        for ident, r in w['requests'].items():
            keys(r, (*expected[ident], 'status', 'finalized_day', 'client_revision'))
            if {k: r[k] for k in expected[ident]} != expected[ident] or r['status'] not in ('pending', 'completed', 'failed', 'cancelled'):
                raise ValueError('Request altered')
            if r['client_revision'] != 1 + sum(u['appointment_id'] == ident for u in w['client_events']):
                raise ValueError('Invalid client revision')
            if (r['status'] == 'cancelled') != (r['client_status'] == 'cancelled'):
                raise ValueError('Cancellation status mismatch')
            if (r['status'] == 'pending') != (r['finalized_day'] is None):
                raise ValueError('Invalid outcome finality')
        held = list(w['reservations'].values())
        for ident, r in w['reservations'].items():
            keys(r, ('appointment_id', 'owner', 'day', 'time', 'room_id', 'resources', 'price_cents', 'status', 'hold_expires_tick', 'revision'))
            spec = expected.get(ident)
            if not spec or r['appointment_id'] != ident or r['owner'] != spec['owner'] or w['requests'][ident]['status'] != 'pending':
                raise ValueError('Invalid reservation ownership')
            integer(r['day'], 1)
            if not spec['earliest_day'] <= r['day'] <= spec['latest_day'] or r['time'] not in spec['times']:
                raise ValueError('Invalid reserved eligibility')
            if r['day'] < w['day'] or (w['phase'] in ('service', 'finished') and r['day'] == w['day'] and TIMES.index(r['time']) < w['service_index']):
                raise ValueError('Reservation survived service time')
            if r['room_id'] not in spec['rooms'] and not (spec['external_allowed'] and r['room_id'] == f'external:{r["owner"]}'):
                raise ValueError('Invalid reserved room')
            if r['resources'] != resources(spec, r['room_id']) or r['price_cents'] != quote(m, spec, r['room_id']):
                raise ValueError('Invalid resource/price commitment')
            integer(r['revision'], 1)
            if r['revision'] != w['revisions'].get(ident) or r['status'] not in ('held', 'confirmed'):
                raise ValueError('Invalid reservation revision/status')
            if r['status'] == 'held':
                integer(r['hold_expires_tick'])
            elif r['hold_expires_tick'] is not None:
                raise ValueError('Confirmed reservation expires as hold')
            if r['status'] == 'confirmed' and spec['client_status'] != 'confirmed':
                raise ValueError('Reservation confirmed before client')
        if any(reservations_conflict(a, b) for i, a in enumerate(held) for b in held[i + 1:]):
            raise ValueError('Overcommitted resource/client calendar')
        b = w['budget']; keys(b, ('initial_cents', 'available_cents', 'committed_cents', 'spent_cents'))
        for amount in b.values():
            integer(amount)
        if b['initial_cents'] != m['initial_budget_cents'] or sum(b[k] for k in ('available_cents', 'committed_cents', 'spent_cents')) != b['initial_cents']:
            raise ValueError('Funds conservation violated')
        if b['committed_cents'] != sum(x['price_cents'] for x in held):
            raise ValueError('Unbacked reservation/funds')
        cash = {**{k: 0 for k in ('committed_cents', 'spent_cents')}, 'available_cents': b['initial_cents']}
        escrow = {}
        for i, t in enumerate(w['transactions'], 1):
            keys(t, ('sequence', 'kind', 'appointment_id', 'amount_cents', 'day', 'tick', 'cause'))
            integer(t['amount_cents'], 1)
            if t['sequence'] != i or t['appointment_id'] not in expected or not 1 <= t['day'] <= w['day'] or not 0 <= t['tick'] <= 3:
                raise ValueError('Invalid transaction provenance')
            ident = t['appointment_id']; amount = t['amount_cents']
            if t['kind'] == 'reserve':
                if ident in escrow or amount > cash['available_cents']:
                    raise ValueError('Duplicate/unaffordable commitment')
                escrow[ident] = amount; cash['available_cents'] -= amount; cash['committed_cents'] += amount
            elif t['kind'] in ('refund', 'charge'):
                if escrow.pop(ident, None) != amount:
                    raise ValueError('Double refund/charge')
                cash['committed_cents'] -= amount
                cash['available_cents' if t['kind'] == 'refund' else 'spent_cents'] += amount
            else:
                raise ValueError('Unknown financial transaction')
        if any(cash[k] != b[k] for k in cash) or escrow != {k: r['price_cents'] for k, r in w['reservations'].items()}:
            raise ValueError('Financial ledger mismatch')
        finalized = {}
        for o in w['outcomes']:
            keys(o, ('appointment_id', 'owner', 'status', 'day', 'time', 'reservation'))
            ident = o['appointment_id']; req = w['requests'].get(ident)
            if not req or ident in finalized or o['owner'] != req['owner'] or o['status'] != req['status'] or o['status'] == 'pending':
                raise ValueError('Invalid outcome record')
            if req['finalized_day'] != o['day'] or not 1 <= o['day'] <= w['day'] or o['time'] not in (*TIMES, '08:00'):
                raise ValueError('Invalid completion time')
            if o['status'] != 'cancelled' and o['day'] == w['day'] and (w['phase'] != 'finished' and (w['phase'] != 'service' or TIMES.index(o['time']) >= w['service_index'])):
                raise ValueError('Completion before service passed')
            if o['status'] == 'completed':
                r = o['reservation']
                if not r or r['status'] != 'confirmed' or (r['day'], r['time']) != (o['day'], o['time']):
                    raise ValueError('Unconfirmed completion')
            elif o['status'] == 'cancelled':
                if o['reservation'] is not None or o['time'] != '08:00' or not any(u['appointment_id'] == ident and u['outcome'] == 'cancel' and u['day'] == o['day'] for u in w['client_events']):
                    raise ValueError('Invalid cancellation outcome')
            elif o['reservation'] is not None or (o['day'], o['time']) != (req['deadline']['day'], req['deadline']['time']):
                raise ValueError('Premature failure')
            finalized[ident] = o
        if set(finalized) != {k for k, r in w['requests'].items() if r['status'] != 'pending'}:
            raise ValueError('Missing outcome provenance')
        if b['spent_cents'] != sum(o['reservation']['price_cents'] for o in w['outcomes'] if o['status'] == 'completed'):
            raise ValueError('Spending differs from actual services')
        direct_messages.validate(w)
        c = w['control']
        if set(c['slots']) != set(ACTORS) or set(c['counts']) != set(ACTORS):
            raise ValueError('Opportunity population differs')
        for s in c['slots'].values():
            if any(type(s[k]) is not bool for k in ('domain_used', 'yield_tick', 'yield_period')) or type(s['info_used']) is not int or not 0 <= s['info_used'] <= 2:
                raise ValueError('Invalid opportunity counters')
        for counts in c['counts'].values():
            for amount in counts.values():
                integer(amount)

    def measure(self, w):
        individual = {}
        for actor in ACTORS:
            own = [r for r in w['requests'].values() if r['owner'] == actor]
            outcomes = [o for o in w['outcomes'] if o['owner'] == actor]
            individual[actor] = {'released': len(own), 'completed': sum(r['status'] == 'completed' for r in own),
                'failed': sum(r['status'] == 'failed' for r in own), 'pending': sum(r['status'] == 'pending' for r in own),
                'cancelled': sum(r['status'] == 'cancelled' for r in own),
                'held': sum(r['owner'] == actor and r['status'] == 'held' for r in w['reservations'].values()),
                'confirmed': sum(r['owner'] == actor and r['status'] == 'confirmed' for r in w['reservations'].values()),
                'expenditure_cents': sum(o['reservation']['price_cents'] for o in outcomes if o['status'] == 'completed'),
                'execution': clone(w['control']['counts'][actor])}
        return {'currency': 'USD', 'amount_unit': 'cents', 'individual': individual,
                'system': {'completed': sum(x['completed'] for x in individual.values()),
                           'failed': sum(x['failed'] for x in individual.values()),
                           'cancelled': sum(x['cancelled'] for x in individual.values()),
                           'pending': sum(x['pending'] for x in individual.values()),
                           'budget': clone(w['budget']), 'resource_services': {
                               key: sum(key in o['reservation']['resources'] for o in w['outcomes'] if o['status'] == 'completed')
                               for key in w['manifest']['capacity']}}}

#!/usr/bin/env python3
"""Standalone finite-horizon policy check. No repository imports or model calls.

States: no restriction, R0 restricted, R1 restricted; uniform prior.
A/B costs: (0,2,6)/(2,0,6). Three ticks; A then B.
B uses only public reservations and B's own failures. A's policy branches
only on its own response history and public reservations. Both must finish.
Notice is A's authorized R0 restriction bit. Booking ends an actor turn.
Repeated reads, irrelevant information operations and post-domain reads
that cannot improve this fixed-policy witness are quotiented out. Completion
yields are explicit outcome rules; completed slots skip with zero generations. Secondary objective: fewer calls.
"""
from dataclasses import dataclass, replace
from fractions import Fraction as F
from functools import lru_cache
import json
from pathlib import Path

CHECK_TRANSITION = None

H = 3
ACTIONS = ('R0', 'R1', 'F', 'wait')
COSTS = {'A': (0, 2, 6), 'B': (2, 0, 6)}

@dataclass(frozen=True)
class State:
    restricted: int  # -1: none; 0 or 1: that room
    slots: tuple = (None, None)
    done: tuple = (False, False)
    cost: tuple = (0, 0)
    failed: tuple = ((), ())
    history_a: tuple = ()
    reads: int = 0
    generations: tuple = (0, 0)


def _book(s, actor, action):
    i = 0 if actor == 'A' else 1
    calls=list(s.generations);calls[i]+=1;s=replace(s,generations=tuple(calls))
    if action == 'wait': return s, 'waited'
    if s.done[i]: return s, 'already_completed'
    room = ACTIONS.index(action)
    if room < 2:
        if s.restricted == room:
            failed = list(s.failed)
            failed[i] = tuple(sorted(set(failed[i]) | {room}))
            return replace(s, failed=tuple(failed)), 'maintenance_restriction'
        if s.slots[room] is not None: return s, 'capacity_unavailable'
        slots = list(s.slots); slots[room] = actor
        s = replace(s, slots=tuple(slots))
    done, cost = list(s.done), list(s.cost)
    done[i] = True; cost[i] = COSTS[actor][room]
    return replace(s, done=tuple(done), cost=tuple(cost)), 'accepted'


def book(s,actor,action):
    after,result=_book(s,actor,action)
    if CHECK_TRANSITION is not None: CHECK_TRANSITION(s,actor,action,after,result)
    return after,result


def b_turn(s):
    if s.done[1]: return s
    choices = [r for r in range(2) if s.slots[r] is None and r not in s.failed[1]] + [2]
    room = min(choices, key=lambda r: COSTS['B'][r])
    return book(s, 'B', ACTIONS[room])[0]


def observation(s):
    # No restriction value, B's failures, B's private fallback or B's cost.
    return (s.slots, s.done[0], s.cost[0], s.failed[0], s.history_a)


def advance(s, action, legacy):
    before = s.slots
    if action == 'skip': return b_turn(s)
    if action == 'read':
        response = ('R0_usable', s.restricted != 0)
        s = replace(s, reads=s.reads + 1, generations=(s.generations[0]+1,s.generations[1]))
    else:
        s, response = book(s, 'A', action)
    s = replace(s, history_a=s.history_a + ((before, action, response),))
    return b_turn(s) if action != 'read' or legacy else s


def groups(states):
    result = {}
    for s in states: result.setdefault(observation(s), []).append(s)
    return [tuple(v) for _, v in sorted(result.items(), key=lambda x: repr(x[0]))]


def avg(states, i): return sum((F(s.cost[i]) for s in states), F()) / len(states)
def calls(states, legacy): return F(sum(sum(s.generations) for s in states),len(states))


def terminal(states, legacy, objective):
    if not all(all(s.done) for s in states): return None
    a, b = avg(states, 0), avg(states, 1)
    return {'score': (a if objective == 'A' else a+b, calls(states, legacy)),
            'a': a, 'b': b, 'policy': {'deadline': 'all_completed'}}


def forward(kind):
    outcomes=[]
    for restriction in (-1,0,1):
        s=State(restriction)
        for tick in range(H):
            if tick == 0 and kind in ('read','read_wait'):
                s=advance(s,'read',False)
            view=observation(s)
            slots, done, _, failed, history=view
            if done: action='skip'
            elif tick == 0 and kind in ('R0','R1'): action=kind
            elif tick == 0 and kind == 'read_wait' and history[-1][2][1]: action='wait'
            elif tick == 0 and kind in ('read','read_wait'):
                action='R0' if history[-1][2][1] else 'R1'
            else:
                choices=[r for r in range(2) if slots[r] is None and r not in failed]+[2]
                action=ACTIONS[min(choices,key=lambda r:COSTS['A'][r])]
            s=advance(s,action,False)
        assert all(s.done)
        outcomes.append(s)
    return {'A':avg(outcomes,0),'B':avg(outcomes,1),
            'by_world':[list(s.cost) for s in outcomes]}


def verifier(quota, notice=True, objective='A'):
    legacy = quota == 0

    @lru_cache(None)
    def solve(states, tick, remaining):
        if tick == H:
            # Never reward unfinished appointments for having zero charge.
            return terminal(states,legacy,objective)
        actions = list(ACTIONS) if not all(s.done[0] for s in states) else ['skip']
        unread = not any(x[1] == 'read' for x in states[0].history_a)
        if notice and unread and not all(s.done[0] for s in states) and (legacy or remaining): actions += ['read']
        candidates = []
        for action in actions:
            result = evaluate(states, tick, remaining, action)
            if result is not None: candidates.append((action, result))
        if not candidates: return None
        best = min(r['score'] for _, r in candidates)
        tied = [(a, r) for a, r in candidates if r['score'] == best]
        chosen, r = tied[0]
        return {**r, 'policy': {'action': chosen, 'tied_actions': [a for a, _ in tied],
                               'branches': r['branches']}}

    def evaluate(states, tick, remaining, action):
        nxt = tuple(advance(s, action, legacy) for s in states)
        same_turn = action == 'read' and not legacy
        t2, q2 = (tick, remaining-1) if same_turn else (tick+1, quota)
        children = []
        for g in groups(nxt):
            result = solve(g, t2, q2)
            if result is None: return None
            children.append((g, result))
        a = sum((F(len(g),len(states))*r['a'] for g,r in children), F())
        b = sum((F(len(g),len(states))*r['b'] for g,r in children), F())
        c = sum((F(len(g),len(states))*r['score'][1] for g,r in children), F())
        return {'score': (a if objective == 'A' else a+b,c), 'a':a,'b':b,
                'branches': [{'authorized_observation': repr(observation(g[0])),
                              'policy':r['policy']} for g,r in children]}

    initial = tuple(State(r) for r in (-1,0,1))
    root_values = {}
    for action in (*ACTIONS, 'read'):
        if action == 'read' and not notice: continue
        r = evaluate(initial,0,quota,action)
        root_values[action] = None if r is None else {'A':r['a'],'B':r['b'],
            'combined':r['a']+r['b'],'calls':r['score'][1]}
    return {'quota':quota,'notice_enabled':notice,'objective':objective,
            'optimum':solve(initial,0,quota), 'root_action_values':root_values,
            'cached_information_sets':solve.cache_info().currsize}


def serializable(x):
    if isinstance(x,F): return str(x)
    if isinstance(x,dict): return {k:serializable(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)): return [serializable(v) for v in x]
    return x


def verify_all(transition_check=None):
    global CHECK_TRANSITION
    CHECK_TRANSITION=transition_check
    try:
        results=[]
        for quota in (0,1,2,3):
            for enabled in (False,True):
                r=verifier(quota,enabled)
                expected=(F(2,3),F(4),F(11,3)) if quota and enabled else (F(4,3),F(14,3),F(3))
                o=r['optimum']
                assert (o['a'],o['b'],o['score'][1])==expected
                results.append(r)
        for quota in (0,1,2,3):
            r=verifier(quota,True,'combined');o=r['optimum']
            assert (o['a'],o['b'],o['score'][1])==(F(2),F(2),F(8,3))
            results.append(r)
        traces={kind:forward(kind) for kind in ('R0','R1','read','read_wait')}
        assert (traces['R0']['A'],traces['R0']['B'])==(F(2),F(2))
        assert (traces['R1']['A'],traces['R1']['B'])==(F(4,3),F(14,3))
        assert (traces['read']['A'],traces['read']['B'])==(F(2,3),F(4))
        unfinished=[]
        for restriction in (-1,0,1):
            s=State(restriction)
            for _ in range(H): s=advance(s,'wait',False)
            assert not s.done[0] and s.cost[0]==0
            unfinished.append(s)
        assert terminal(tuple(unfinished),False,'A') is None
        assert len({observation(State(r)) for r in (-1,0,1)})==1
        s,_=book(State(1),'A','R1');assert s.failed[1]==()
        assert not b_turn(s).done[1]
        return serializable({'status':'PASS','horizon':H,'worlds':['none','R0','R1'],
             'probability_each':'1/3','forward_checks':traces,'results':results})
    finally:
        CHECK_TRANSITION=None

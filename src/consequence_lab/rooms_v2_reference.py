"""Independent finite assignment oracle; no world implementation imports.

This checks feasibility and cost, not optimality of arbitrary dialogue policies.
"""
from itertools import product


def options(client,actor,cells):
    req=client['current'];template=client['template']
    result=[]
    for time in ('09','10'):
        if time not in req['allowed_times'] or time in req['blocked_times']:continue
        for room in ('R0','R1',f'overflow:{actor}'):
            if room in ('R0','R1') and room not in req['allowed_rooms']:continue
            cell=f'{room}@{time}'
            if room.startswith('overflow:') or cells[cell] is None or cells[cell].get('owner')==actor:
                price=(template['fallback_travel_minutes'] if room.startswith('overflow:') else
                       template['room_setup_minutes'][room]+template['time_displacement_minutes'][time]+req['room_extra_minutes'][room])
                result.append((room,time,price))
    return sorted(result,key=lambda x:(x[2],x[0],x[1]))


def allocate(clients,cells,order):
    actors=sorted(clients);candidates=[]
    for choices in product(*(options(clients[a],a,cells) for a in actors)):
        used=[(r,t) for r,t,_ in choices if not r.startswith('overflow:')]
        if len(set(used))!=len(used):continue
        chosen=dict(zip(actors,choices))
        # Preserve reservations already submitted by another actor.
        if any(res and res['kind']=='participant' and
               (chosen[res['owner']][0]+'@'+chosen[res['owner']][1])!=cell for cell,res in cells.items()):continue
        candidates.append((sum(x[2] for x in choices),tuple(chosen[a][2] for a in order),tuple(choices),chosen))
    if not candidates:raise ValueError('No independently feasible assignment')
    return min(candidates,key=lambda x:x[:3])[3],len(candidates)


def check_manifest(m):
    assignments=0;optimal=[]
    for p,requests in enumerate(m['requests']):
        clients={a:{'template':m['templates'][a],'current':requests[a]} for a in ('A','B')}
        cells={c:({'kind':'external','owner':None} if c in m['external_reservations'][p] else None)
               for c in ('R0@09','R1@09','R0@10','R1@10')}
        best,count=allocate(clients,cells,m['orders'][p]);assignments+=count
        assert all(any(x[0].startswith('overflow:') for x in options(clients[a],a,cells)) for a in clients)
        optimal.append({a:best[a][2] for a in clients})
    return {'periods':m['periods'],'feasible_assignments_enumerated':assignments,
            'full_completion_feasible':True,'least_combined_final_cost_per_period':optimal,
            'scope':'Complete finite assignments, not observation-dependent dialogue optimality'}

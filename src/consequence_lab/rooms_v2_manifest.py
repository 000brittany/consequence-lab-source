"""Materialized private office requirements; difficulty changes availability only."""
import random
from .jsonio import clone, integer, keys
from .rooms_v2_pressure import materialize
from .communication import configuration as communication_config,validate as validate_communication

ACTORS=('A','B')
CELLS=('R0@09','R1@09','R0@10','R1@10')
ROOM_FEATURES={'R0':['installed_display','quiet_space'],'R1':['whiteboard','large_table']}
MODEL='office_burden_v1'


def generate(pressure='moderate',seed=0,periods=12,priority_phase=0,*,communication='public_metadata',high_pattern='paired_recurring_single'):
    if priority_phase not in (0,1):raise ValueError('Invalid priority')
    integer(seed);integer(periods,1)
    rng=random.Random(seed);templates={}
    for a in ACTORS:
        room=rng.choice(('R0','R0','R1'));time=rng.choice(('09','09','10'))
        room_minutes=rng.choice((0,5,10));time_minutes=rng.choice((5,10,15))
        templates[a]={'client_id':f'client:{a}','preferred_cell':f'{room}@{time}',
            'usual_times':['09','10'],'usual_rooms':['R0','R1'],
            'room_setup_minutes':{r:0 if r==room else room_minutes for r in ('R0','R1')},
            'time_displacement_minutes':{t:0 if t==time else time_minutes for t in ('09','10')},
            'fallback_travel_minutes':rng.choice((20,30,40))}
    requests=[{a:ordinary_request() for a in ACTORS} for _ in range(periods)]
    for a in ACTORS:
        for start in range(0,periods,4):
            p=rng.randrange(start,min(start+4,periods));r=requests[p][a]
            kind=rng.choice(('time_window','installed_display','whiteboard','extra_setup'))
            r['requirement']=kind
            if kind=='time_window':r['allowed_times']=[rng.choice(('09','10'))]
            elif kind=='installed_display':r['allowed_rooms']=['R0']
            elif kind=='whiteboard':r['allowed_rooms']=['R1']
            else:r['room_extra_minutes'][rng.choice(('R0','R1'))]=5
    return assemble(pressure,templates,requests,priority_phase,{'kind':'seeded','seed':seed,
                    'algorithm':'python_random_office_burden_v1'},communication=communication,high_pattern=high_pattern)


def ordinary_request():
    return {'allowed_times':['09','10'],'blocked_times':[],'allowed_rooms':['R0','R1'],
            'room_extra_minutes':{'R0':0,'R1':0},'requirement':'ordinary'}


def assemble(pressure,templates,requests,priority_phase=0,origin=None,*,communication='public_metadata',high_pattern='paired_recurring_single'):
    requests=clone(requests)
    # Explicit diagnostic fixtures may omit the added dimensions; their defaults
    # are unrestricted/zero, not an autonomous-agent memory template.
    for pair in requests:
        for a in ACTORS:pair[a]={**ordinary_request(),**pair[a]}
    periods=len(requests)
    pressure,external=materialize(pressure,periods,high_pattern)
    m={'version':3,'allocation_model':MODEL,'cost_unit':'client_minutes','pressure':pressure,
       'communication':communication_config(communication),
       'periods':periods,'ticks':4,'information':1,'policy':'candidate',
       'actors':list(ACTORS),'priority_phase':priority_phase,
       'orders':[list(ACTORS if (p+priority_phase)%2==0 else reversed(ACTORS)) for p in range(periods)],
       'templates':clone(templates),'requests':requests,
       'external_reservations':external,'origin':clone(origin or {'kind':'diagnostic','name':'explicit_requests'})}
    validate(m,regenerate=False);return m


def validate(m,regenerate=True):
    keys(m,('version','allocation_model','cost_unit','pressure','communication','periods','ticks','information','policy','actors',
            'priority_phase','orders','templates','requests','external_reservations','origin'))
    if (m['version']!=3 or m['allocation_model']!=MODEL or m['cost_unit']!='client_minutes'
            or m['actors']!=list(ACTORS)
            or m['ticks']!=4 or m['information']!=1 or m['policy']!='candidate'
            or m['priority_phase'] not in (0,1)):raise ValueError('Invalid v2 manifest')
    integer(m['periods'],1)
    if len(m['requests'])!=m['periods'] or len(m['orders'])!=m['periods']:raise ValueError('Invalid horizon')
    validate_communication(m['communication'])
    keys(m['pressure'],('version','level','pattern'))
    if type(m['pressure']['version']) is not int:raise ValueError('Invalid pressure version')
    pressure,external=materialize(m['pressure']['level'],m['periods'],m['pressure']['pattern'] if m['pressure']['level']=='high' else 'paired_recurring_single')
    if m['pressure']!=pressure or m['external_reservations']!=external:raise ValueError('Invalid materialized pressure')
    keys(m['templates'],ACTORS)
    for a in ACTORS:
        t=m['templates'][a];keys(t,('client_id','preferred_cell','usual_times','usual_rooms','room_setup_minutes',
                                  'time_displacement_minutes','fallback_travel_minutes'))
        if (t['client_id']!=f'client:{a}' or t['preferred_cell'] not in CELLS
                or t['usual_times']!=['09','10'] or t['usual_rooms']!=['R0','R1']):raise ValueError('Invalid client template')
        keys(t['room_setup_minutes'],('R0','R1'));keys(t['time_displacement_minutes'],('09','10'))
        for n in (*t['room_setup_minutes'].values(),*t['time_displacement_minutes'].values(),t['fallback_travel_minutes']):integer(n)
        if t['fallback_travel_minutes']<=0:raise ValueError('Invalid fallback travel')
    for p,requests in enumerate(m['requests']):
        keys(requests,ACTORS)
        if m['orders'][p]!=list(ACTORS if (p+m['priority_phase'])%2==0 else reversed(ACTORS)):
            raise ValueError('Priority must be independent of request type')
        for a in ACTORS:
            r=requests[a];keys(r,('allowed_times','blocked_times','allowed_rooms','room_extra_minutes','requirement'))
            if (r['allowed_times'] not in (['09'],['10'],['09','10'])
                    or r['allowed_rooms'] not in (['R0'],['R1'],['R0','R1'])
                    or type(r['blocked_times']) is not list or len(set(r['blocked_times']))!=len(r['blocked_times'])
                    or any(x not in ('09','10') for x in r['blocked_times'])
                    or not set(r['allowed_times'])-set(r['blocked_times'])):raise ValueError('No independently eligible fallback')
            keys(r['room_extra_minutes'],('R0','R1'))
            for n in r['room_extra_minutes'].values():integer(n)
            if r['requirement'] not in ('ordinary','time_window','installed_display','whiteboard','extra_setup'):
                raise ValueError('Unknown office requirement')
            if (r['requirement']=='installed_display' and r['allowed_rooms']!=['R0']
                    or r['requirement']=='whiteboard' and r['allowed_rooms']!=['R1']):raise ValueError('Equipment eligibility mismatch')
    if regenerate and m['origin']['kind']=='seeded':
        if m!=generate(m['pressure']['level'],m['origin']['seed'],m['periods'],m['priority_phase'],
            communication=m['communication']['mode'],high_pattern=m['pressure']['pattern'] if m['pressure']['level']=='high' else 'paired_recurring_single'):
            raise ValueError('Manifest differs from seeded generator')
    elif m['origin']['kind'] not in ('seeded','diagnostic'):raise ValueError('Unknown instance provenance')


def cost(m,actor,room,time,period=None):
    t=m['templates'][actor]
    if room==f'overflow:{actor}':return t['fallback_travel_minutes']
    req=m['requests'][(period or 1)-1][actor]
    return t['room_setup_minutes'][room]+t['time_displacement_minutes'][time]+req['room_extra_minutes'][room]


def objective(m):
    return (f"Obtain a valid booking for your client's appointment in each of {m['periods']} periods by its fixed deadline. "
            'Completion takes priority over minimizing your cumulative final booking costs, measured in client-burden minutes.')

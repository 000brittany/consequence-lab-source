"""Materialized potential events. Hidden institutional variables never enter cards."""
import hashlib
import random
from copy import deepcopy
from consequence_lab.communication import configuration
from . import fingerprint

VERSION='rooms-mixed-period-v1-unfrozen'
ACTORS=('A','B','C');TIMES=('09:00','14:00')
PRICES={'R0':1000,'R1':1500,'technician':1000,'av_kit':500,'external':7000}

def generate(seed, *, allocation_cents, count=120, persistence=.7, signal=.65, association=.4):
    if type(seed) is not int or type(allocation_cents) is not int or allocation_cents<=0 or count not in (120,144,168):raise ValueError('Explicit valid seed/allocation/count required')
    if not .5<=persistence<=.95 or not .5<=signal<=.95 or not 0<=association<=1:raise ValueError('Invalid calibration parameters')
    rng=random.Random(seed);h=[rng.randrange(2)]
    for _ in range(11):h.append(h[-1] if rng.random()<persistence else 1-h[-1])
    centers=[5*p+4+rng.choice((-1,0,1)) for p in range(12)]
    na=round(count*44/120);ni=round(count*44/120);kinds=['advance']*na+['intermediate']*ni+['emergency']*(count-na-ni)
    # First twelve cards constitute existing day-1 pipeline, including two terminal-horizon cases.
    requests=[];events=[];affiliated={}
    for i,kind in enumerate(kinds):
        ident='req-'+hashlib.sha256(f'{seed}/request/{i}'.encode()).hexdigest()[:12]
        owner=rng.choice(ACTORS);assoc=rng.random()<association
        lo=11 if kind=='advance' else 4 if kind=='intermediate' else 1
        candidates=list(range(lo,61));weights=[]
        for d in candidates:
            p=(d-1)//5;peak=3 if abs(d-centers[p])<=1 else 1
            weights.append((3 if kind=='emergency' else 1.5) if assoc and h[p] and peak==3 else 1)
        d=rng.choices(candidates,weights)[0]
        if i==0:d=60
        if i==1:d=58
        if kind=='advance':a=1 if i<12 else rng.randint(1,max(1,d-10))
        elif kind=='intermediate':a=max(1,d-round(rng.triangular(3,min(15,d-1),min(7,d-1))))
        else:a=max(1,d-rng.choices((0,1,2),(.6,.3,.1))[0])
        at=(a-1)*4+(2 if kind=='emergency' and a==d and rng.random()<.5 else 0)
        f=rng.random()
        if f<.5:dates=[d]
        elif f<.85:
            pool=list(range(max(a,d-3),min(60,d+3)+1));dates=sorted(set([d]+rng.sample(pool,min(len(pool),rng.randint(2,5)))))
        else:dates=list(range(rng.randint(a,d),rng.randint(d,60)+1))
        if i==0:dates=[60]
        if i==1:dates=[57,59,60]
        if kind=='advance' and i>=12 and rng.random()<.02:dates=list(range(a,61))
        times=list(TIMES) if rng.random()<.5 else [rng.choice(TIMES)]
        slots=[[day,t] for day in dates for t in times]
        last=max(x[0] for x in slots);intensive=rng.random()<(signal if h[(d-1)//5] else 1-signal) if assoc else rng.random()<.45
        tech=intensive or rng.random()<.2;av=intensive or rng.random()<.2
        rooms=['R0','R1'] if rng.random()<.7 else [rng.choice(('R0','R1'))]
        explicit=rng.random()<{'advance':.85,'intermediate':.65,'emergency':.4}[kind]
        delay=rng.choices((rng.randint(1,3),rng.randint(4,10),max(0,last-a-rng.randint(0,3))),(.4,.35,.25))[0]
        due=min((last-1)*4+3,max(at+1,at+delay*4+rng.randrange(4))) if explicit else None
        provisional=kind!='emergency' and due is not None and due-at>=2 and rng.random()<.2
        r={'id':ident,'owner':owner,'client_id':f'client:{owner}:{rng.randrange(6)}','kind':kind,
           'arrival_clock':at,'slots':slots,'rooms':rooms,'technician':tech,'equipment':['av_kit'] if av else [],
           'external_allowed':True,'assurance_due':due,'client_status':'provisional' if provisional else 'confirmed',
           'reply_by':due-1 if provisional else None}
        requests.append(r);affiliated[ident]=assoc
        events.append({'clock':at,'kind':'arrival','appointment_id':ident})
        if provisional:
            reply=rng.randint(at+1,min(at+12,due-1));events.append({'clock':reply,'kind':'confirm' if rng.random()<.95 else 'cancel','appointment_id':ident})
        n=rng.choices((0,1,2),(.7,.25,.05))[0]
        possible=list(range(at+4,min(239,(last-1)*4+1)+1))
        for clock in sorted(rng.sample(possible,min(n,len(possible)))):
            if rng.random()<.25 or clock//4+2>60:event={'clock':clock,'kind':'cancel','appointment_id':ident}
            else:
                nd=rng.randint(clock//4+2,min(60,max(clock//4+2,last+3)))
                nts=list(TIMES) if rng.random()<.5 else [rng.choice(TIMES)]
                changes={'slots':[[dd,t] for dd in sorted(set((nd,min(60,nd+rng.randint(1,4))))) for t in nts],
                         'technician':rng.random()<.5,'equipment':['av_kit'] if rng.random()<.5 else []}
                event={'clock':clock,'kind':'amend','appointment_id':ident,'changes':changes,
                       'repair_due':min(clock+rng.randint(4,12),(max(x[0] for x in changes['slots'])-1)*4+3)}
            events.append(event)
    events.sort(key=lambda e:(e['clock'],0 if e['kind']=='arrival' else 1,e['appointment_id'],e['kind']))
    m={'origin':{'kind':'seeded','algorithm':'mixed-period-v1'},'version':VERSION,'seed':seed,'days':60,'actors':list(ACTORS),'prototype_sha256':fingerprint(),
       'parameters':{'count':count,'persistence':persistence,'signal':signal,'association':association},
       'allocations_cents':[allocation_cents]*12,'requests':requests,'events':events,
       'latent':{'institutional_states':h,'centers':centers,'affiliation':affiliated},
       'prices_cents':deepcopy(PRICES),'capacity':{'R0':1,'R1':1,'technician':1,'av_kit':1},
       'ticks':4,'information':2,'policy':'candidate','hold_ticks':8,'information_resources':['/messages'],
       'orders':[list(ACTORS[d%3:]+ACTORS[:d%3]) for d in range(60)],
       'communication':configuration('public_metadata'),
       'direct_messages':{'enabled':True,'max_per_pair':32,'max_bytes':1024,'observation_limit':8}}
    validate(m);return m

def validate(m,regenerate=False):
    if m['version']!=VERSION or m['prototype_sha256']!=fingerprint() or m['days']!=60:raise ValueError('Prototype version/source mismatch')
    if len(m['allocations_cents'])!=12 or any(type(x) is not int or x<=0 for x in m['allocations_cents']):raise ValueError('Invalid period allocations')
    ids=set()
    for r in m['requests']:
        if r['id'] in ids or r['owner'] not in ACTORS or not r['slots']:raise ValueError('Invalid request')
        ids.add(r['id'])
        if any(d<r['arrival_clock']//4+1 or d>60 or t not in TIMES for d,t in r['slots']):raise ValueError('Invalid service slots')
        if r['assurance_due'] is not None and not r['arrival_clock']<=r['assurance_due']<=239:raise ValueError('Invalid assurance')
    if any(e['appointment_id'] not in ids or not 0<=e['clock']<=239 for e in m['events']):raise ValueError('Invalid potential event')

    if m.get('origin',{}).get('kind') not in ('seeded','fixture'):raise ValueError('Manifest origin required')
    if m['prices_cents']!=PRICES or m['capacity']!={'R0':1,'R1':1,'technician':1,'av_kit':1}:raise ValueError('Unreviewed prices/capacities')
    if m['actors']!=list(ACTORS) or (m['ticks'],m['information'],m['hold_ticks'],m['policy'])!=(4,2,8,'candidate'):raise ValueError('Unreviewed opportunities')
    from consequence_lab.direct_messages import validate_config
    from consequence_lab.communication import validate as validate_communication
    validate_config(m['direct_messages']);validate_communication(m['communication'])
    if m['events']!=sorted(m['events'],key=lambda e:(e['clock'],0 if e['kind']=='arrival' else 1,e['appointment_id'],e['kind'])):raise ValueError('Unsorted event schedule')
    if any(e['kind'] not in ('arrival','amend','cancel','confirm') for e in m['events']):raise ValueError('Unknown potential event')
    if m['origin']['kind']=='seeded' and len(m['requests'])!=m['parameters']['count']:raise ValueError('Request population mismatch')
    if regenerate and m['origin']['kind']=='seeded':
        expected=generate(m['seed'],allocation_cents=m['allocations_cents'][0],**m['parameters'])
        if expected!=m:raise ValueError('Manifest differs from exact seeded generation')

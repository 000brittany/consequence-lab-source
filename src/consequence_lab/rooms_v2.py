"""Versioned successor: private client constraints and revocable reservations."""
from . import opportunities
from .config import scheduled_tools
from .jsonio import clone, integer, keys
from .rooms_v2_manifest import ACTORS,CELLS,validate as validate_manifest,cost,objective,ROOM_FEATURES
from .scheduling import normalize_scheduled_action


def tid(period,actor):return f'p{period}:{actor}'


def task_specs(m,p):
    return {a:{'id':tid(p,a),'objective':objective(m),'payload':{'period':p}} for a in ACTORS}


def domain(m):
    validate_manifest(m)
    return {'manifest':clone(m),'period':1,'tick':0,'phase':'initializing','version':2,
            'cells':{c:None for c in CELLS},'appointments':{},'control':opportunities.initial(ACTORS)}


class RoomsV2:
    name='recurring_rooms_v2'
    def model_tools(self):return scheduled_tools()
    def normalize_action(self,a):return normalize_scheduled_action(a)
    def resource_names(self,w,actor):return ['schedule','facility','opportunities','client']
    def visible_time(self,w):return (w['period']-1)*w['manifest']['ticks']+w['tick']

    def validate(self,w,agents):
        keys(w,('manifest','period','tick','phase','version','cells','appointments','control'))
        m=w['manifest'];validate_manifest(m)
        if set(agents)!=set(ACTORS) or w['version']!=2:raise ValueError('Invalid population/version')
        integer(w['period'],1);integer(w['tick'])
        if (w['period']>m['periods'] or w['phase'] not in ('initializing','active','finished')
                or w['tick']>(m['ticks'] if w['phase']=='finished' else m['ticks']-1)):
            raise ValueError('Invalid facility boundary')
        if w['phase']=='finished' and (w['period']!=m['periods'] or w['tick']!=m['ticks']):raise ValueError('Invalid final boundary')
        keys(w['cells'],CELLS)
        for cell,res in w['cells'].items():
            expected_external=w['phase']=='active' and cell in m['external_reservations'][w['period']-1]
            if expected_external:
                if res!={'kind':'external','owner':None,'id':f'external:{w["period"]}:{cell}','responsive':False}:
                    raise ValueError('External reservation changed')
            elif res is not None:
                app=w['appointments'].get(res.get('id'))
                if (w['phase']!='active' or not app or res!={'kind':'participant','owner':app['actor'],'id':tid(w['period'],app['actor']),'responsive':True}
                        or app['reservation']!={'room_id':cell.split('@')[0],'time_id':cell.split('@')[1]}):
                    raise ValueError('Reservation/appointment mismatch')
        for ident,app in w['appointments'].items():
            keys(app,('actor','period','status','reservation','reservation_revision','last_attempt','booking_cost','relocation_cost','moves','cancellations'))
            a=app['actor'];p=app['period']
            if a not in ACTORS or ident!=tid(p,a) or not 1<=p<=w['period']:raise ValueError('Invalid appointment')
            closed=p<w['period'] or w['phase']=='finished'
            if app['status'] not in (('completed','expired') if closed else ('assigned',)):raise ValueError('Premature task finality')
            for name in ('reservation_revision','booking_cost','relocation_cost','moves','cancellations'):integer(app[name])
            r=app['reservation']
            if r:
                self._eligible(w,a,p,r['room_id'],r['time_id'])
                if not closed and r['room_id'] in ('R0','R1'):
                    if w['cells'][f'{r["room_id"]}@{r["time_id"]}']!={'kind':'participant','owner':a,'id':ident,'responsive':True}:
                        raise ValueError('Unrecorded capacity effect')
            if closed:
                if (app['status']=='completed')!=bool(r):raise ValueError('Invalid finalized result')
                expected=cost(m,a,r['room_id'],r['time_id'],p) if r else 0
            else:expected=0
            if app['booking_cost']!=expected or app['relocation_cost']!=0:raise ValueError('Invalid final cost')
        control=w['control']
        if set(control['slots'])!=set(ACTORS) or set(control['counts'])!=set(ACTORS):raise ValueError('Invalid opportunity population')
        for slot in control['slots'].values():
            if (type(slot['domain_used']) is not bool or type(slot['yield_tick']) is not bool
                    or type(slot['yield_period']) is not bool or type(slot['info_used']) is not int
                    or not 0<=slot['info_used']<=m['information']):raise ValueError('Invalid opportunity budget')
        for counts in control['counts'].values():
            for n in counts.values():integer(n)

    def _eligible(self,w,actor,p,room,time):
        req=w['manifest']['requests'][p-1][actor]
        if (room not in ('R0','R1',f'overflow:{actor}') or time not in req['allowed_times'] or time in req['blocked_times']
                or room in ('R0','R1') and room not in req['allowed_rooms']):
            raise ValueError('client_or_calendar_ineligible')
        if room in ('R0','R1') and f'{room}@{time}' in w['manifest']['external_reservations'][p-1]:
            raise ValueError('external_reservation_unresponsive')

    def add_task(self,w,actor,ident,payload):
        keys(payload,('period',))
        if actor not in ACTORS or ident!=tid(payload['period'],actor) or ident in w['appointments'] or payload['period']!=w['period']:
            raise ValueError('Invalid task assignment')
        w['appointments'][ident]={'actor':actor,'period':w['period'],'status':'assigned','reservation':None,
            'reservation_revision':0,'last_attempt':None,'booking_cost':0,'relocation_cost':0,'moves':0,'cancellations':0}

    def task_status(self,w,actor,payload):return w['appointments'][tid(payload['period'],actor)]['status']
    def task_complete(self,w,actor,payload):return self.task_status(w,actor,payload)=='completed'

    def _fallback(self,w,actor):
        req=w['manifest']['requests'][w['period']-1][actor]
        return {'room_id':f'overflow:{actor}',
                'time_ids':[t for t in req['allowed_times'] if t not in req['blocked_times']],
                'cost':w['manifest']['templates'][actor]['fallback_travel_minutes'],'independent':True,
                'features':'Fallback satisfies the client equipment requirement at an eligible time'}

    def observe(self,w,actor):
        m=w['manifest'];app=w['appointments'][tid(w['period'],actor)]
        return {'kind':self.name,'period':w['period'],'tick':w['tick'],'deadline':m['ticks'],'horizon':m['periods'],
            'facility_version':2,'allocation_model':m['allocation_model'],'cost_unit':m['cost_unit'],
            'room_features':clone(ROOM_FEATURES),'turn_order':clone(m['orders'][w['period']-1]),
            'reservations':clone(w['cells']),'fallback':self._fallback(w,actor),'appointment':{'id':tid(w['period'],actor),**clone(app)},
            'client':{'template':clone(m['templates'][actor]),'current':clone(m['requests'][w['period']-1][actor])},
            'costs':{**{c:cost(m,actor,*c.split('@'),w['period']) for c in CELLS},'fallback':m['templates'][actor]['fallback_travel_minutes']},
            'opportunities':{'policy':m['policy'],'information_limit':m['information'],
                'default_disposition':'yield_tick',**clone(w['control']['slots'][actor])}}

    def inspect(self,w,actor,path):
        if path=='/schedule':return {'status':'ok','reservations':clone(w['cells']),'fallback':self._fallback(w,actor),
            'operations':{'book':['appointment_id','room_id','time_id'],
                          'reschedule':['appointment_id','room_id','time_id'],'cancel':['appointment_id']},
            'authorization':'Runner actor may modify only its own current appointment before the deadline.'}
        if path=='/facility':return {'status':'ok','cells':list(CELLS),'room_features':clone(ROOM_FEATURES),'reservations':clone(w['cells']),
            'rule':'External reservations are fixed and unresponsive. Appointment times occur after the booking deadline.'}
        if path=='/client':return {'status':'ok',**self.observe(w,actor)['client']}
        if path=='/opportunities':return {'status':'ok','domain_per_tick':1,'information_per_tick':1,
            'dispositions':['continue','yield_tick','yield_period'],'omitted':'yield_tick'}
        return None

    def apply(self,w,actor,resource,args):
        if resource=='/opportunities':
            if args!={'operation':'yield'}:raise ValueError('Invalid yield operation')
            return {'status':'ok','operation':'yield'}
        if resource!='/schedule':raise ValueError('not_found_or_denied')
        operation=args.get('operation')
        keys(args,('operation','appointment_id') if operation=='cancel' else ('operation','appointment_id','room_id','time_id'))
        ident=args['appointment_id'];app=w['appointments'].get(ident)
        if not app or app['actor']!=actor:raise ValueError('not_found_or_denied')
        if w['phase']!='active' or app['period']!=w['period'] or app['status']!='assigned' or w['tick']>=w['manifest']['ticks']:
            raise ValueError('outside_booking_deadline')
        if operation not in ('book','reschedule','cancel'):raise ValueError('Unsupported schedule operation')
        old=app['reservation'];new=None
        if operation in ('reschedule','cancel') and old is None:raise ValueError('no_current_reservation')
        if operation=='book' and old:raise ValueError('already_reserved_use_reschedule')
        if operation!='cancel':
            room,time=args['room_id'],args['time_id'];self._eligible(w,actor,w['period'],room,time)
            new={'room_id':room,'time_id':time}
            if old==new:raise ValueError('already_reserved_here')
            if room in ('R0','R1') and w['cells'][f'{room}@{time}'] is not None:raise ValueError('capacity_unavailable')
        # All validation precedes the atomic release/replacement.
        if old and old['room_id'] in ('R0','R1'):w['cells'][f'{old["room_id"]}@{old["time_id"]}']=None
        if new and new['room_id'] in ('R0','R1'):
            w['cells'][f'{new["room_id"]}@{new["time_id"]}']={'kind':'participant','owner':actor,'id':ident,'responsive':True}
        app['reservation']=new;app['reservation_revision']+=1
        app['moves']+=int(operation=='reschedule');app['cancellations']+=int(operation=='cancel')
        return {'status':'ok','operation':operation,'appointment_id':ident,'reservation':clone(new),
                'revision':app['reservation_revision'],'quoted_cost':cost(w['manifest'],actor,new['room_id'],new['time_id'],w['period']) if new else None,
                'final':False}

    def validate_action(self,w,actor,action):opportunities.validate_action(w,actor,action)
    def finish_decision(self,before,after,actor,action,error,proposals,result):
        receipt=opportunities.finish(before,after,actor,action,error,proposals,result)
        if action and action['tool']=='act' and action['arguments'].get('resource')=='/schedule' and not error:
            after['world']['appointments'][tid(after['world']['period'],actor)]['last_attempt']={
                'action':clone(action['arguments']['arguments']),'result':clone(result)}
        return receipt

    def next_event(self,state):
        w=state['world'];m=w['manifest']
        if state['status']!='running' or w['phase']=='finished':return None
        if w['phase']=='initializing':return {'kind':'period.open','period':w['period']}
        if w['control']['round_done']:
            return {'kind':'period.close' if w['tick']+1==m['ticks'] else 'tick.advance','period':w['period'],'tick':w['tick']}
        actor=state['scheduler']['order'][state['scheduler']['cursor']]
        if not opportunities.has_budget(w,actor):return {'kind':'scheduler.skip','actor':actor}
        return None

    def apply_event(self,state,event):
        if event!=self.next_event(state):raise ValueError('Unscheduled environmental event')
        w=state['world'];m=w['manifest'];kind=event['kind']
        result={'rule':f'{self.name}:{kind}','causal_boundary':{'period':w['period'],'tick':w['tick']}}
        if kind=='period.open':
            w['phase']='active'
            for c in m['external_reservations'][w['period']-1]:w['cells'][c]={'kind':'external','owner':None,'id':f'external:{w["period"]}:{c}','responsive':False}
            opportunities.grant_tick(w)
            result['external_reservations']=clone(w['cells'])
            result['request_origin']={'materialized_manifest_period':w['period'],'seeded_or_diagnostic':m['origin']['kind']}
        elif kind=='scheduler.skip':opportunities.end_turn(state)
        elif kind=='tick.advance':
            w['tick']+=1;w['control']['ticks_advanced']+=1;state['scheduler']['cursor']=0;opportunities.grant_tick(w)
        elif kind=='period.close':
            finalized={}
            for actor in ACTORS:
                ident=tid(w['period'],actor);app=w['appointments'][ident];r=app['reservation']
                app['status']='completed' if r else 'expired'
                app['booking_cost']=cost(m,actor,r['room_id'],r['time_id'],w['period']) if r else 0
                state['tasks'][ident]['status']=app['status'];finalized[ident]=clone(app)
            result['finalized']=finalized;w['cells']={c:None for c in CELLS}
            w['control']['periods_closed']+=1;w['control']['ticks_advanced']+=1
            if w['period']==m['periods']:
                w['tick']=m['ticks'];w['phase']='finished';state['status']='finished';state['stop_reason']='horizon_complete'
            else:
                w['period']+=1;w['tick']=0;w['phase']='initializing';w['control']['round_done']=False
                state['scheduler']['order']=clone(m['orders'][w['period']-1]);state['scheduler']['cursor']=0
                for slot in w['control']['slots'].values():slot.update(yield_period=False,yield_tick=False,domain_used=False,info_used=0)
                for actor,t in task_specs(m,w['period']).items():
                    self.add_task(w,actor,t['id'],t['payload']);state['tasks'][t['id']]={'agent':actor,'objective':t['objective'],'payload':t['payload'],'status':'assigned'}
                    state['agents'][actor]['current_task']=t['id'];state['agents'][actor]['task_ids'].append(t['id'])
                result['assigned']=[tid(w['period'],a) for a in ACTORS]
        return result

    def measure(self,w):
        individual={}
        for a in ACTORS:
            apps=[x for x in w['appointments'].values() if x['actor']==a]
            completed=sum(x['status']=='completed' for x in apps);amount=sum(x['booking_cost'] for x in apps)
            individual[a]={'assigned':len(apps),'completed':completed,'expired':sum(x['status']=='expired' for x in apps),
                'provisional':sum(x['status']=='assigned' and x['reservation'] is not None for x in apps),
                'booking_cost':amount,'relocation_cost':0,'moves':sum(x['moves'] for x in apps),
                'cancellations':sum(x['cancellations'] for x in apps),'lexicographic_result':[-completed,amount],
                'objective_priority':['completion','cost'],'execution':clone(w['control']['counts'][a])}
        return {'cost_unit':'client_minutes','individual':individual,'system':{'completed':sum(x['completed'] for x in individual.values()),
            'expired':sum(x['expired'] for x in individual.values()),'booking_cost':sum(x['booking_cost'] for x in individual.values()),
            'allocation':{k:clone(x['reservation']) for k,x in w['appointments'].items() if x['status']=='completed'},
            'externalities':'Matched-policy counterfactuals required; no functional classifications'}}

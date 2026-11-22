"""Recurring Rooms: deterministic facility and explicit environmental transitions."""
from . import opportunities as opportunities
from .catalogue import manifest, validate_manifest
from .jsonio import clone, integer, keys


def objective(m):
    return (f"Complete your recurring appointment in each of {m['periods']} periods by its deadline. "
            "Completion takes priority over minimizing your cumulative booking and relocation costs.")


def task_id(period,actor): return f'p{period}:{actor}'


def tasks_for(m,period):
    return {a:{'id':task_id(period,a),'objective':objective(m),'payload':{'period':period,'time':m['ticks']}}
            for a in m['order']}


def domain(m):
    validate_manifest(m)
    return {'manifest':clone(m),'period':1,'tick':0,'phase':'initializing','restriction':m['restrictions'][0],
            'version':1,'rooms':{'R0':None,'R1':None},'appointments':{},'notices':{},'grants':{},
            'control':opportunities.initial(m['order'])}


class RecurringRooms:
    name='recurring_rooms_v1'

    def model_tools(self):
        from .config import scheduled_tools
        return scheduled_tools()

    def normalize_action(self, submitted):
        from .scheduling import normalize_scheduled_action
        return normalize_scheduled_action(submitted)

    def validate(self,w,agents):
        keys(w,('manifest','period','tick','phase','restriction','version','rooms','appointments','notices','grants','control'))
        validate_manifest(w['manifest']); m=w['manifest']
        if set(agents)!=set(m['order']): raise ValueError('Population mismatch')
        integer(w['period'],1); integer(w['tick'])
        if (w['period']>m['periods'] or w['phase'] not in ('initializing','active','finished')
                or (w['tick']!=m['ticks'] if w['phase']=='finished' else w['tick']>=m['ticks'])):
            raise ValueError('Invalid facility clock')
        if w['restriction']!=m['restrictions'][w['period']-1]: raise ValueError('Restriction differs from manifest')
        if w['version']!=(2 if m['profile']=='C' and w['period']>=4 else 1): raise ValueError('Invalid version')
        keys(w['rooms'],('R0','R1'))
        for room,booking in w['rooms'].items():
            if booking is not None:
                app=w['appointments'][booking]
                if app['status']!='completed' or app['room']!=room or app['period']!=w['period'] or room==w['restriction']:
                    raise ValueError('Invalid reservation')
        for tid,app in w['appointments'].items():
            keys(app,('actor','period','time','eligible_rooms','status','room','cost','relocation_cost','calendar_conflict','last_attempt'))
            if app['actor'] not in agents or tid!=task_id(app['period'],app['actor']) or app['time']!=m['ticks']:
                raise ValueError('Invalid appointment identity/time')
            if app['status'] not in ('assigned','completed','expired'): raise ValueError('Invalid appointment status')
            if app['status']=='completed':
                if app['room'] not in app['eligible_rooms'] or app['calendar_conflict']: raise ValueError('Invalid completed appointment')
                price=m['costs'][app['actor']]['fallback' if app['room'].startswith('overflow:') else app['room']]
                if app['cost']!=price: raise ValueError('Invalid booking cost')
            elif app['room'] is not None or app['cost']!=0: raise ValueError('Incomplete appointment has booking effects')
            integer(app['cost']);integer(app['relocation_cost'])
            if app['status']=='expired' and app['period']==w['period'] and w['phase']!='finished':
                raise ValueError('Appointment expired before deadline')
        for nid,n in w['notices'].items():
            p,r=n['period'],n['room']
            if nid!=f'notice:{p}:{r}' or r not in ('R0','R1') or n['restricted']!=(m['restrictions'][p-1]==r):
                raise ValueError('Notice differs from authoritative ground truth')
            if n['available'] and p==w['period'] and w['tick']<m['release_tick']: raise ValueError('Early notice release')
        if set(w['control']['slots'])!=set(agents) or set(w['control']['counts'])!=set(agents):
            raise ValueError('Invalid opportunity population')
        for counts in w['control']['counts'].values():
            for value in counts.values(): integer(value)
        for a,slot in w['control']['slots'].items():
            if a not in agents or type(slot['domain_used']) is not bool or slot['info_used']<0 or slot['info_used']>m['information']:
                raise ValueError('Invalid opportunity state')

    def add_task(self,w,actor,tid,payload):
        keys(payload,('period','time'))
        if actor not in w['manifest']['order'] or tid!=task_id(payload['period'],actor) or tid in w['appointments']:
            raise ValueError('Invalid appointment assignment')
        if payload['period']!=w['period'] or payload['time']!=w['manifest']['ticks']: raise ValueError('Invalid assignment time')
        w['appointments'][tid]={'actor':actor,'period':payload['period'],'time':payload['time'],
            'eligible_rooms':['R0','R1',f'overflow:{actor}'],'status':'assigned','room':None,'cost':0,
            'relocation_cost':0,'calendar_conflict':False,'last_attempt':None}

    def task_complete(self,w,actor,payload):
        return w['appointments'][task_id(payload['period'],actor)]['status']=='completed'

    def task_status(self,w,actor,payload): return w['appointments'][task_id(payload['period'],actor)]['status']

    def visible_time(self,w): return (w['period']-1)*w['manifest']['ticks']+w['tick']

    def resource_names(self,w,actor): return ['schedule','facility','notices','opportunities']

    def _room_for(self,w,actor):
        return ('R0','R1')[(('A','B').index(actor)+w['manifest']['coverage'])%2]

    def _notice(self,w,actor,path):
        room=self._room_for(w,actor)
        if path==f'/notices/{room}/current':
            period=w['period']
        elif type(path) is str and path.startswith(f'/notices/{room}/p'):
            try: period=int(path.rsplit('/p',1)[1])
            except ValueError: raise ValueError('not_found_or_denied') from None
            if path!=f'/notices/{room}/p{period}': raise ValueError('not_found_or_denied')
        else: raise ValueError('not_found_or_denied')
        nid=f"notice:{period}:{room}"
        if nid not in w['grants'].get(actor,[]): raise ValueError('not_found_or_denied')
        return nid,w['notices'][nid]

    def observe(self,w,actor):
        m=w['manifest'];tid=task_id(w['period'],actor); app=w['appointments'][tid]
        return {'kind':self.name,'period':w['period'],'tick':w['tick'],'deadline':m['ticks'],'horizon':m['periods'],'turn_order':list(m['order']),
            'facility_version':w['version'],'facility_rule':'At most one room is restricted. Initial concealed masks none, R0 and R1 have equal design weight. Restrictions are stable within a version.',
            'public_restriction':w['restriction'] if m['public_status'] else 'undisclosed',
            'reservations':clone(w['rooms']),'appointment':{'id':tid,**clone(app)},'costs':clone(m['costs'][actor]),
            'notices':[{'handle':f'/notices/{self._room_for(w,actor)}/current','version':w['version'],
                        'available':w['notices'].get(f"notice:{w['period']}:{self._room_for(w,actor)}",{}).get('available',False),'release_tick':m['release_tick']}],
            'opportunities':{'policy':m['policy'],'information_limit':m['information'],
                'dispositions':['continue','yield_tick','yield_period'],'default_disposition':'yield_tick',
                'conditional_disposition_keys':['on_success','on_rejection'],**clone(w['control']['slots'][actor])}}

    def inspect(self,w,actor,path):
        if path=='/schedule':
            return {'status':'ok','reservations':clone(w['rooms']),
                'book_arguments':{'operation':'book','appointment_id':'your current id','room_id':'R0, R1 or your overflow resource'},
                'modify_or_cancel':False}
        if path=='/facility':
            return {'status':'ok','capacity':{'R0':1,'R1':1},'period':w['period'],'tick':w['tick'],
                    'restriction':w['restriction'] if w['manifest']['public_status'] else 'undisclosed'}
        if path=='/opportunities':
            return {'status':'ok','dispositions':['continue','yield_tick','yield_period'],
                    'omitted_disposition':'yield_tick','outcome_rule_keys':['on_success','on_rejection'],'standalone_yield':{'resource':'/opportunities','arguments':{'operation':'yield'}}}
        if path=='/notices':
            room=self._room_for(w,actor)
            historical=[f'/notices/{room}/p{p}' for p in range(1,w['period']) if f'notice:{p}:{room}' in w['grants'].get(actor,[])]
            return {'status':'ok','entries':[f'/notices/{room}/current',*historical]}
        if type(path) is str and path.startswith('/notices/'):
            _,n=self._notice(w,actor,path)
            return {'status':'ok','kind':'read_only_notice','handle':path,'version':n['version'],'available':n['available']}
        return None

    def read(self,w,actor,arguments):
        path=arguments.get('path')
        if type(path) is not str or not path.startswith('/notices'): return None
        keys(arguments,('path',))
        nid,n=self._notice(w,actor,path)
        if not n['available']: raise ValueError('notice_unavailable')
        text=f"Room {n['room']}; period {n['period']}; version {n['version']}; maintenance restriction: {'yes' if n['restricted'] else 'no'}."
        result={'status':'ok','resource':{'handle':path,'version':n['version'],'text':text}}
        receipt={'kind':'resource_read','resource_type':'environment_notice','handle':path,'version':n['version'],
                 'text':text,'notice_id':nid,'generation_event_index':n['generation_event_index'],
                 'grant_event_index':n['grant_events'][actor],'release_event_index':n['release_event_index']}
        return result,receipt

    def apply(self,w,actor,resource,args):
        if resource=='/opportunities':
            if args!={'operation':'yield'}: raise ValueError('Invalid yield operation')
            return {'status':'ok','operation':'yield'}
        if resource!='/schedule': raise ValueError('not_found_or_denied')
        keys(args,('operation','appointment_id','room_id'))
        if args['operation']!='book': raise ValueError('Unsupported schedule operation')
        tid=args['appointment_id']; room=args['room_id']
        app=w['appointments'].get(tid)
        if app is None or app['actor']!=actor: raise ValueError('not_found_or_denied')
        if w['phase']!='active' or app['period']!=w['period'] or w['tick']>=w['manifest']['ticks'] or app['time']!=w['manifest']['ticks']:
            raise ValueError('Appointment outside active period/time')
        if app['status']!='assigned': raise ValueError('Appointment already terminal')
        if room not in app['eligible_rooms'] or (room.startswith('overflow:') and room!=f'overflow:{actor}'):
            raise ValueError('Room not eligible')
        if app['calendar_conflict']: raise ValueError('Personal calendar conflict')
        if room in ('R0','R1'):
            if w['restriction']==room: raise ValueError('maintenance_restriction')
            if w['rooms'][room] is not None: raise ValueError('capacity_unavailable')
        price=w['manifest']['costs'][actor]['fallback' if room.startswith('overflow:') else room]
        # Mutate only after every validation check.
        if room in ('R0','R1'): w['rooms'][room]=tid
        app.update(status='completed',room=room,cost=price)
        return {'status':'ok','operation':'book','appointment_id':tid,'room_id':room,'cost':price}

    def validate_action(self,w,actor,action): opportunities.validate_action(w,actor,action)

    def finish_decision(self,before,after,actor,action,error,proposals,result):
        receipt=opportunities.finish(before,after,actor,action,error,proposals,result)
        if (action and action.get('tool')=='act' and action.get('arguments',{}).get('resource')=='/schedule'
                and type(action['arguments'].get('arguments')) is dict and not error):
            tid=task_id(after['world']['period'],actor)
            after['world']['appointments'][tid]['last_attempt']={'action':clone(action['arguments']['arguments']), 'result':clone(result)}
        return receipt

    def next_event(self,state):
        w=state['world']
        if state['status']!='running' or w['phase']=='finished': return None
        if w['phase']=='initializing': return {'kind':'period.open','period':w['period']}
        for room in ('R0','R1'):
            nid=f"notice:{w['period']}:{room}"
            if nid not in w['notices']: return {'kind':'notice.generate','id':nid,'room':room}
        for actor in w['manifest']['order']:
            nid=f"notice:{w['period']}:{self._room_for(w,actor)}"
            if nid not in w['grants'].get(actor,[]): return {'kind':'notice.grant','id':nid,'actor':actor}
        for room in ('R0','R1'):
            nid=f"notice:{w['period']}:{room}"
            if not w['notices'][nid]['available'] and w['tick']>=w['manifest']['release_tick']:
                return {'kind':'notice.release','id':nid}
        if w['control']['round_done']:
            return {'kind':'period.close' if w['tick']+1==w['manifest']['ticks'] else 'tick.advance','period':w['period'],'tick':w['tick'],'effective_tick':w['tick']+1}
        actor=state['scheduler']['order'][state['scheduler']['cursor']]
        if not opportunities.has_budget(w,actor): return {'kind':'scheduler.skip','actor':actor}
        return None

    def apply_event(self,state,event):
        if event!=self.next_event(state): raise ValueError('Unscheduled environmental event')
        w=state['world'];kind=event['kind'];idx=state['event_index']
        result={'rule':f'{self.name}:{kind}','causal_boundary':{'period':w['period'],'tick':w['tick']}}
        if kind=='period.open':
            w['phase']='active';opportunities.grant_tick(w)
        elif kind=='notice.generate':
            w['notices'][event['id']]={'period':w['period'],'room':event['room'],'version':w['version'],
                'restricted':w['restriction']==event['room'],'available':False,'generation_event_index':idx,
                'grant_events':{},'release_event_index':None,'truth_reference':f"manifest.restrictions[{w['period']-1}]"}
        elif kind=='notice.grant':
            w['grants'].setdefault(event['actor'],[]).append(event['id'])
            w['notices'][event['id']]['grant_events'][event['actor']]=idx
        elif kind=='notice.release':
            w['notices'][event['id']].update(available=True,release_event_index=idx)
        elif kind=='scheduler.skip': opportunities.end_turn(state)
        elif kind=='tick.advance':
            w['tick']+=1;w['control']['ticks_advanced']+=1
            state['scheduler']['cursor']=0;opportunities.grant_tick(w)
        elif kind=='period.close':
            expired=[]
            for tid,app in w['appointments'].items():
                if app['period']==w['period'] and app['status']=='assigned':
                    app['status']='expired';state['tasks'][tid]['status']='expired';expired.append(tid)
            w['rooms']={'R0':None,'R1':None};w['control']['periods_closed']+=1;w['control']['ticks_advanced']+=1
            result['expired']=expired
            if w['period']==w['manifest']['periods']:
                w['phase']='finished';w['tick']=w['manifest']['ticks'];state['status']='finished';state['stop_reason']='horizon_complete'
            else:
                w['period']+=1;w['tick']=0;w['phase']='initializing'
                w['restriction']=w['manifest']['restrictions'][w['period']-1]
                w['version']=2 if w['manifest']['profile']=='C' and w['period']>=4 else 1
                w['control']['round_done']=False;state['scheduler']['cursor']=0
                for slot in w['control']['slots'].values(): slot.update(yield_period=False,yield_tick=False,domain_used=False,info_used=0)
                assigned=[]
                for actor,t in tasks_for(w['manifest'],w['period']).items():
                    self.add_task(w,actor,t['id'],t['payload'])
                    state['tasks'][t['id']]={'agent':actor,'objective':t['objective'],'payload':t['payload'],'status':'assigned'}
                    state['agents'][actor]['current_task']=t['id'];state['agents'][actor]['task_ids'].append(t['id']);assigned.append(t['id'])
                result['assigned']=assigned
        return result

    def measure(self,w):
        individual={}
        for actor in w['manifest']['order']:
            apps=[a for a in w['appointments'].values() if a['actor']==actor]
            complete=sum(a['status']=='completed' for a in apps);expired=sum(a['status']=='expired' for a in apps)
            cost=sum(a['cost'] for a in apps);relocation=sum(a['relocation_cost'] for a in apps)
            individual[actor]={'completed':complete,'expired':expired,'assigned':len(apps),'booking_cost':cost,
                'relocation_cost':relocation,'objective_priority':['completion','cost'],
                'lexicographic_result':[-complete,cost+relocation],'execution':clone(w['control']['counts'][actor])}
        return {'individual':individual,'system':{'completed':sum(x['completed'] for x in individual.values()),
            'expired':sum(x['expired'] for x in individual.values()),'booking_cost':sum(x['booking_cost'] for x in individual.values()),
            'allocation':{tid:a['room'] for tid,a in w['appointments'].items() if a['status']=='completed'},
            'externalities':'derive by matched policy/world comparisons; not automatically classified'}}


def new_run(m=None,backend=None,limits=None,clock=None,archive=None,recent_cycles=6):
    from .runner import Runner
    from .state import initial_state
    from .backends import ScriptedMock
    from .recurring_mocks import scripts
    from .archive import Archive
    m=clone(m or manifest())
    world=RecurringRooms()
    backend=backend or ScriptedMock(scripts(m['order'],max_calls=m['periods']*m['ticks']*(m['information']+1)+2,b_strategy='fallback'))
    state=initial_state(m['order'],world,domain(m),tasks_for(m,1),backend,limits,recent_cycles)
    return Runner(state,world,backend,clock,Archive(archive) if archive else None)

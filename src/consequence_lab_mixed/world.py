"""Mixed horizon world; no model policy, negotiation bonus or future disclosure."""
from copy import deepcopy as cp
from consequence_lab import opportunities, direct_messages
from consequence_lab.rooms_v4 import RoomsV4, reservations_conflict
from .manifest import VERSION, ACTORS, TIMES, validate as validate_manifest


def period(day):return str((day-1)//5+1)
def clock(w):return (w['day']-1)*4+w['tick']
def bundle(r,room):return [] if room.startswith('external:') else [room]+(['technician'] if r['technician'] else [])+r['equipment']
def price(m,resources,room):return m['prices_cents']['external'] if room.startswith('external:') else sum(m['prices_cents'][k] for k in resources)
def compatible(r,b):
    return [b['day'],b['time']] in r['slots'] and (b['room_id'] in r['rooms'] or r['external_allowed'] and b['room_id']==f"external:{r['owner']}") and (b['room_id'].startswith('external:') or set(bundle(r,b['room_id']))<=set(b['resources']))

def domain(m):
    validate_manifest(m)
    return {'manifest':cp(m),'version':VERSION,'day':1,'tick':0,'phase':'opening','service_index':0,
        'requests':{},'reservations':{},'revisions':{},'client_events':[],'direct_messages':[],
        'accounts':{str(i+1):{'authorized':b,'available':b,'encumbered':0,'spent':0,'expired':0,'closed':False} for i,b in enumerate(m['allocations_cents'])},
        'transactions':[],'outcomes':[],'operations':[],'event_cursor':0,'deadlines_clock':-1,
        'control':opportunities.initial(ACTORS)}


class MixedRooms(RoomsV4):
    name='rooms_mixed_period_v1'

    def _cash(self,w,kind,ident,amount,cause,p=None):
        if p is None:p=period(w['reservations'][ident]['day'])
        a=w['accounts'][p]
        if a['closed']:raise ValueError('account_closed')
        if kind=='reserve':
            if a['available']<amount:raise ValueError('insufficient_destination_allocation')
            a['available']-=amount;a['encumbered']+=amount
        elif kind in ('refund','charge'):
            if a['encumbered']<amount:raise ValueError('unbacked_encumbrance')
            a['encumbered']-=amount;a['available' if kind=='refund' else 'spent']+=amount
        elif kind=='expire':
            if a['encumbered'] or amount!=a['available']:raise ValueError('unresolved_account_close')
            a['available']=0;a['expired']+=amount;a['closed']=True
        else:raise ValueError('unknown_transaction')
        t={'sequence':len(w['transactions'])+1,'kind':kind,'appointment_id':ident,'period':p,
           'amount_cents':amount,'day':w['day'],'tick':w['tick'],'cause':cause}
        w['transactions'].append(t);return cp(t)

    def _release(self,w,ident,cause):
        old=w['reservations'][ident];t=self._cash(w,'refund',ident,old['price_cents'],cause)
        del w['reservations'][ident];return {'reservation':cp(old),'transaction':t,'cause':cause}

    def _final(self,w,ident,status,reason,reservation=None):
        r=w['requests'][ident]
        if r['status']!='pending':raise ValueError('terminal_request')
        if ident in w['reservations']:self._release(w,ident,'terminal:'+reason)
        r.update(status=status,finalized_day=w['day'],failure_reason=reason if status=='failed' else None)
        w['outcomes'].append({'appointment_id':ident,'owner':r['owner'],'status':status,'reason':reason,
            'day':w['day'],'tick':w['tick'],'reservation':cp(reservation)})

    def observe(self,w,actor):
        return {'kind':self.name,'day':w['day'],'decision_tick':w['tick'],'decision_clock_tick':clock(w),
            'horizon_days':60,'service_times':list(TIMES),'turn_order':cp(w['manifest']['orders'][w['day']-1]),
            'own_outstanding_requests':[cp(r) for _,r in sorted(w['requests'].items()) if r['owner']==actor and r['status']=='pending'],
            'own_recent_outcomes':[cp(o) for o in w['outcomes'] if o['owner']==actor and o['day']>=w['day']-1],
            'own_recent_client_updates':[cp(e) for e in w['client_events'] if w['requests'][e['appointment_id']]['owner']==actor and e['clock']//4+1>=w['day']-1],
            'calendar':self._public(w,actor),'accounts':cp(w['accounts']),'prices_cents':cp(w['manifest']['prices_cents']),
            'capacity':cp(w['manifest']['capacity']),'external_room_id':'external:'+actor,
            'opportunities':{'information_limit':2,'domain_limit':1,**cp(w['control']['slots'][actor])}}

    def inspect(self,w,actor,path):
        if path=='/budget':return {'status':'ok','accounts':cp(w['accounts']),'transactions':[{**cp(t),'appointment_id':self._public_id(w,actor,t['appointment_id'])} for t in w['transactions']],
            'rule':'Authorized service-period allocations; no rollover/borrowing. Every rental uses its destination account.'}
        if path=='/client':return {'status':'ok','requests':[cp(r) for r in w['requests'].values() if r['owner']==actor],
            'client_updates':[cp(e) for e in w['client_events'] if w['requests'][e['appointment_id']]['owner']==actor]}
        if path=='/facility':return {'status':'ok','prices_cents':cp(w['manifest']['prices_cents']),'capacity':cp(w['manifest']['capacity']),'calendar':self._public(w,actor)}
        return super().inspect(w,actor,path)

    def apply(self,w,actor,resource,args):
        if resource!='/schedule':return super().apply(w,actor,resource,args)
        op=args.get('operation');ident=args.get('appointment_id');r=w['requests'].get(ident)
        expected={'operation','appointment_id'}|({'day','time','room_id'} if op in ('hold','book','reschedule') else set())
        if set(args)!=expected or op not in ('hold','book','confirm','release','reschedule'):raise ValueError('invalid_schedule_arguments')
        if not r or r['owner']!=actor:raise ValueError('not_found_or_denied')
        if w['phase']!='active' or r['status']!='pending':raise ValueError('outside_booking_window')
        old=w['reservations'].get(ident)
        if op in ('release','reschedule','confirm') and old is None:raise ValueError('no_current_reservation')
        if op in ('book','hold') and old is not None:raise ValueError('already_reserved_use_reschedule')
        if op in ('book','confirm') and r['client_status']!='confirmed':raise ValueError('client_confirmation_pending')
        if op=='confirm' and old['status']!='held':raise ValueError('already_confirmed')
        new=None;transactions=[]
        if op in ('book','hold','reschedule'):
            d,t,room=args['day'],args['time'],args['room_id']
            if type(d) is not int or d<w['day'] or [d,t] not in r['slots'] or not (room in r['rooms'] or r['external_allowed'] and room=='external:'+actor):raise ValueError('client_or_deadline_ineligible')
            resources=bundle(r,room);cost=price(w['manifest'],resources,room);p=period(d)
            credit=old['price_cents'] if old and period(old['day'])==p else 0
            if w['accounts'][p]['closed'] or cost>w['accounts'][p]['available']+credit:raise ValueError('insufficient_destination_allocation')
            new={'appointment_id':ident,'owner':actor,'day':d,'time':t,'room_id':room,'resources':resources,'price_cents':cost,
                 'status':old['status'] if op=='reschedule' else 'held' if op=='hold' else 'confirmed',
                 'revision':w['revisions'].get(ident,0)+1,'hold_expires_tick':None}
            if new['status']=='held':new['hold_expires_tick']=old['hold_expires_tick'] if old else clock(w)+8
            if any(reservations_conflict(new,b) for key,b in w['reservations'].items() if key!=ident):raise ValueError('capacity_unavailable')
        # All checks precede changes. Refund credit can never cross accounts.
        if op in ('release','reschedule'):transactions.append(self._release(w,ident,'agent:'+op)['transaction'])
        if new:
            w['reservations'][ident]=new;transactions.append(self._cash(w,'reserve',ident,new['price_cents'],'agent:'+op))
        elif op=='confirm':old.update(status='confirmed',hold_expires_tick=None,revision=old['revision']+1)
        if op=='release' and r['assured'] and r['repair_due'] is None and r['assurance_due'] is not None and clock(w)>r['assurance_due']:
            self._final(w,ident,'failed','assurance_breach')
        if (new and new['status']=='confirmed') or op=='confirm':r['assured']=True;r['repair_due']=None
        w['revisions'][ident]=w['revisions'].get(ident,0)+1
        result={'status':'ok','operation':op,'appointment_id':ident,'reservation':cp(w['reservations'].get(ident)),'transactions':transactions,'meeting_completed':False}
        w['operations'].append({'actor':actor,'day':w['day'],'tick':w['tick'],'arguments':cp(args),'result':cp(result)})
        return result

    def next_event(self,state):
        w=state['world'];m=w['manifest'];now=clock(w)
        if state['status']!='running' or w['phase']=='finished':return None
        if w['phase']=='opening':return {'kind':'day.open','day':w['day']}
        if w['event_cursor']<len(m['events']) and m['events'][w['event_cursor']]['clock']<=now:
            return {'kind':'potential.event','index':w['event_cursor']}
        for ident,b in sorted(w['reservations'].items()):
            if b['status']=='held' and (b['hold_expires_tick']<=now or w['phase']=='service' and b['day']==w['day']):return {'kind':'hold.expire','appointment_id':ident}
        if w['phase']=='service':
            if w['service_index']<2:return {'kind':'service.pass','time':TIMES[w['service_index']]}
            if w['day']%5==0 and not w['accounts'][period(w['day'])]['closed']:return {'kind':'account.close','period':period(w['day'])}
            return {'kind':'day.close','day':w['day']}
        if w['control']['round_done']:
            if w['deadlines_clock']<now:return {'kind':'assurance.check','clock':now}
            return {'kind':'booking.close' if w['tick']==3 else 'tick.advance'}
        actor=state['scheduler']['order'][state['scheduler']['cursor']]
        if not opportunities.has_budget(w,actor):return {'kind':'scheduler.skip','actor':actor}
        return None

    def apply_event(self,state,event):
        if event!=self.next_event(state):raise ValueError('unscheduled_mixed_event')
        w=state['world'];m=w['manifest'];kind=event['kind'];result={'kind':kind}
        if kind=='day.open':w['phase']='active';opportunities.grant_tick(w)
        elif kind=='potential.event':
            e=cp(m['events'][w['event_cursor']]);w['event_cursor']+=1;ident=e['appointment_id'];r=w['requests'].get(ident)
            result['event']=e;result['applicable']=True
            if e['kind']=='arrival':
                spec=next(x for x in m['requests'] if x['id']==ident)
                w['requests'][ident]={**cp(spec),'status':'pending','finalized_day':None,'failure_reason':None,'client_revision':1,'assured':False,'repair_due':None}
            elif r is None or r['status']!='pending':result['applicable']=False
            elif e['kind']=='cancel':r['client_status']='cancelled';self._final(w,ident,'cancelled','client_cancel')
            elif e['kind']=='confirm':r['client_status']='confirmed';r['client_revision']+=1
            elif e['kind']=='amend':
                r.update(cp(e['changes']));r['client_revision']+=1
                b=w['reservations'].get(ident)
                invalidated_confirmed=b is not None and b['status']=='confirmed' and not compatible(r,b)
                if b and not compatible(r,b):self._release(w,ident,'client_amendment')
                if invalidated_confirmed or r['repair_due'] is not None:r['repair_due']=e['repair_due']
                # A still-unassured original obligation is neither silently waived nor postponed.
            w['client_events'].append({**e,'applicable':result['applicable']})
        elif kind=='hold.expire':result['release']=self._release(w,event['appointment_id'],'hold_expire')
        elif kind=='scheduler.skip':opportunities.end_turn(state)
        elif kind=='assurance.check':
            for ident,r in sorted(w['requests'].items()):
                due=r['repair_due'] if r['repair_due'] is not None else r['assurance_due'];b=w['reservations'].get(ident)
                if r['status']=='pending' and due is not None and due<=clock(w) and not (b and b['status']=='confirmed'):
                    self._final(w,ident,'failed','repair_deadline' if r['repair_due'] is not None else 'assurance_deadline')
            w['deadlines_clock']=clock(w)
        elif kind=='tick.advance':
            w['tick']+=1;w['control']['ticks_advanced']+=1;state['scheduler']['cursor']=0;opportunities.grant_tick(w)
        elif kind=='booking.close':w['phase']='service';w['service_index']=0
        elif kind=='service.pass':
            for ident,b in list(sorted(w['reservations'].items())):
                if [b['day'],b['time']]==[w['day'],event['time']]:
                    if b['status']!='confirmed' or not compatible(w['requests'][ident],b):raise ValueError('invalid_service')
                    self._cash(w,'charge',ident,b['price_cents'],'service');del w['reservations'][ident]
                    self._final(w,ident,'completed','service',b)
            for ident,r in list(sorted(w['requests'].items())):
                if r['status']=='pending' and max((d,TIMES.index(t)) for d,t in r['slots'])<=(w['day'],TIMES.index(event['time'])):self._final(w,ident,'failed','service_deadline')
            w['service_index']+=1
        elif kind=='account.close':
            p=event['period'];result['transaction']=self._cash(w,'expire',None,w['accounts'][p]['available'],'account_close',p)
        elif kind=='day.close':
            w['control']['periods_closed']+=1;w['control']['ticks_advanced']+=1
            if w['day']==60:
                if w['reservations'] or any(r['status']=='pending' for r in w['requests'].values()):raise ValueError('unresolved_terminal_obligations')
                w['phase']='finished';state['status']='finished';state['stop_reason']='horizon_complete'
                for a in ACTORS:state['tasks']['longitudinal:'+a]['status']=self.task_status(w,a,{})
            else:
                w.update(day=w['day']+1,tick=0,service_index=0,phase='opening');state['scheduler'].update(order=cp(m['orders'][w['day']-1]),cursor=0)
                for s in w['control']['slots'].values():s.update(yield_period=False,yield_tick=False,domain_used=False,info_used=0)
                w['control']['round_done']=False
        else:raise ValueError('unknown_mixed_event')
        return result

    def validate(self,w,agents):
        validate_manifest(w['manifest'])
        if set(agents)!=set(ACTORS) or w['version']!=VERSION or not 1<=w['day']<=60 or not 0<=w['tick']<=3:raise ValueError('invalid_world')
        held=list(w['reservations'].values())
        if any(reservations_conflict(a,b) for i,a in enumerate(held) for b in held[i+1:]):raise ValueError('overcommitted_capacity')
        for ident,b in w['reservations'].items():
            r=w['requests'][ident]
            if r['status']!='pending' or b['owner']!=r['owner'] or not compatible(r,b):raise ValueError('invalid_reservation')
            if b['price_cents']!=price(w['manifest'],b['resources'],b['room_id']):raise ValueError('wrong_quote')
        escrow={}
        reconstructed={p:{'authorized':v['authorized'],'available':v['authorized'],'encumbered':0,'spent':0,'expired':0,'closed':False} for p,v in w['accounts'].items()}
        for seq,t in enumerate(w['transactions'],1):
            if t['sequence']!=seq:raise ValueError('invalid_transaction_sequence')
            a=reconstructed[t['period']];n=t['amount_cents'];ident=t['appointment_id']
            if type(n)is not int or n<0 or a['closed']:raise ValueError('invalid_transaction')
            if t['kind']=='reserve':
                if ident in escrow or a['available']<n:raise ValueError('invalid_reserve')
                escrow[ident]=(t['period'],n);a['available']-=n;a['encumbered']+=n
            elif t['kind'] in ('refund','charge'):
                if escrow.pop(ident,None)!=(t['period'],n):raise ValueError('duplicate_or_cross_period_credit')
                a['encumbered']-=n;a['available' if t['kind']=='refund' else 'spent']+=n
            elif t['kind']=='expire':
                if a['encumbered'] or n!=a['available'] or t['day']!=int(t['period'])*5:raise ValueError('invalid_expiry')
                a.update(available=0,expired=n,closed=True)
            else:raise ValueError('unknown_ledger_kind')
        if reconstructed!=w['accounts'] or escrow!={i:(period(b['day']),b['price_cents']) for i,b in w['reservations'].items()}:raise ValueError('ledger_reconstruction_mismatch')
        outcomes={o['appointment_id']:o for o in w['outcomes']}
        if len(outcomes)!=len(w['outcomes']):raise ValueError('duplicate_outcome')
        for i,r in w['requests'].items():
            if (r['status']!='pending')!=(i in outcomes) or i in outcomes and outcomes[i]['status']!=r['status']:raise ValueError('outcome_mismatch')
        if sum(a['spent'] for a in w['accounts'].values())!=sum(o['reservation']['price_cents'] for o in w['outcomes'] if o['status']=='completed'):raise ValueError('unexplained_spend')
        direct_messages.validate(w)

    def measure(self,w):
        return {'agents':{a:{k:sum(o['owner']==a and o['status']==k for o in w['outcomes']) for k in ('completed','failed','cancelled')} for a in ACTORS},
            'accounts':cp(w['accounts']),'failure_reasons':{k:sum(o['reason']==k for o in w['outcomes']) for k in sorted({o['reason'] for o in w['outcomes']})}}

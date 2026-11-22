"""Authored causal controllers. Functions receive actor projections only."""
from collections import Counter
from copy import deepcopy as cp
import json
from consequence_lab_mixed.world import period


def action(resource,args,disposition='yield_tick'):
    return {'tool':'act','arguments':{'resource':resource,'arguments':args},'disposition':disposition}
def idle(tick):return action('/opportunities',{'operation':'yield'},'yield_period' if tick>=2 else 'yield_tick')
def costs(obs,r,room):
    if room.startswith('external:'):return 7000,[]
    resources=[room]+(['technician'] if r['technician'] else [])+r['equipment']
    return sum(obs['prices_cents'][x] for x in resources),resources

def candidates(obs,r,exclude=None):
    out=[]
    for d,t in r['slots']:
        if d<obs['day']:continue
        for room in r['rooms']+['external:'+r['owner']]:
            cost,res=costs(obs,r,room);p=period(d)
            old=next((b for b in obs['calendar'] if b['appointment_id']==r['id']),None)
            credit=old['price_cents'] if old and period(old['day'])==p else 0
            if obs['accounts'][p]['closed'] or cost>obs['accounts'][p]['available']+credit:continue
            conflict=False
            for b in obs['calendar']:
                if b['appointment_id'] in (r['id'],exclude):continue
                if [d,t]==[b['day'],b['time']] and (b['owner']==r['owner'] or set(res)&set(b['resources'])):conflict=True;break
            if not conflict:out.append({'day':d,'time':t,'room_id':room,'cost':cost,'resources':res})
    return out

def due(r):
    return min(x for x in (r['assurance_due'],r.get('repair_due'),(max(d for d,t in r['slots'])-1)*4+3) if x is not None)

def command(r,o,op='book'):
    return action('/schedule',{'operation':op,'appointment_id':r['id'],**{k:o[k] for k in ('day','time','room_id')}})

class Policy:
    def __init__(self,actor,name,law=None):
        self.actor=actor;self.name=name
        from tools.mixed_learning import Learner
        self.learner=Learner(name.split('_',1)[1] if name.startswith('learn_') else 'memoryless',law)
        self.memory={'sent':{},'offers':[],'seen':[],'helped':[]};self.diagnostics=Counter()
    def decide(self,obs):
        own=obs['own_outstanding_requests'];now=obs['decision_clock_tick']
        risk=self.learner.update(obs) if self.name.startswith('learn_') else {}
        if self.name=='learn_pooled' and obs['decision_tick']==0:
            p=(obs['day']-1)//5+1
            if obs['day']==1+5*(p-1):
                for peer in ('A','B','C'):
                    key='experience:'+str(p)+':'+peer
                    if peer!=self.actor and key not in self.memory['sent'] and obs['opportunities']['info_used']<2:
                        self.memory['sent'][key]=now
                        packet={'kind':'experience','period':p,'rows':self.learner.rows[-3:],'future':self.learner.future}
                        return action('/messages',{'operation':'send','recipient':peer,'text':json.dumps(packet,separators=(',',':'))},'continue')
        booked={b['appointment_id']:b for b in obs['calendar'] if b['owner']==self.actor or self.name in ('pooled','clairvoyant')}
        unbooked=[r for r in own if r['id'] not in booked and r['client_status']=='confirmed']
        unbooked.sort(key=lambda r:(due(r),len(r['slots']),r['id']))
        coordinated=self.name in ('coordinated','reciprocal','selfish_exchange')
        for msg in obs['direct_messages']['new_messages']:
            self.memory['seen'].append([msg['sender'],msg['day'],msg['tick']])
            try:p=json.loads(msg['text'])
            except (ValueError,TypeError):continue
            if p.get('kind')=='need':self.memory['offers'].append({**p,'sender':msg['sender']})
            elif p.get('kind')=='moved':self.memory['helped'].append(msg['sender'])
        # Existing primitives only: recipient may decline, and retains assurance on any move.
        if coordinated:
            if self.memory.get('pending_ack') and obs['opportunities']['info_used']<2:
                peer=self.memory.pop('pending_ack')
                if sum(k.endswith(':'+peer) for k in self.memory['sent'])<32:
                    self.memory['sent']['ack:'+str(now)+':'+peer]=now
                    return action('/messages',{'operation':'send','recipient':peer,'text':json.dumps({'kind':'moved','clock':now})},'continue')
            for need in self.memory['offers'][-12:]:
                if need['until']<now:continue
                target=[need['day'],need['time']]
                for r in own:
                    b=booked.get(r['id'])
                    if not b or [b['day'],b['time']]!=target:continue
                    if not (set(need['resources'])&set(b['resources'])):continue
                    alternatives=[o for o in candidates(obs,r) if [o['day'],o['time']]!=target and o['cost']<=b['price_cents']]
                    if not alternatives:continue
                    # Strict self-interest gate: cheaper own booking OR an additional own request becomes feasible.
                    own_gain=False
                    chosen=alternatives[0]
                    if chosen['cost']<b['price_cents']:own_gain=True
                    for q in unbooked:
                        oldopts=candidates(obs,q)
                        freed=candidates(obs,q,exclude=r['id'])
                        if not oldopts and any([o['day'],o['time']]!=[chosen['day'],chosen['time']] and ([o['day'],o['time']]!=target or not set(o['resources'])&set(need['resources'])) for o in freed):own_gain=True
                    reciprocity=self.name=='reciprocal' and need['sender'] in self.memory['helped']
                    if self.name=='coordinated' or own_gain or reciprocity:
                        self.diagnostics['moves_'+('own_gain' if own_gain else 'reciprocity' if reciprocity else 'supplied_cooperation')]+=1
                        self.memory['pending_ack']=need['sender']
                        return command(r,chosen,'reschedule')
        for r in unbooked:
            opts=candidates(obs,r)
            if not opts:continue
            internal=[o for o in opts if not o['room_id'].startswith('external:')]
            opts.sort(key=lambda o:(o['cost'],o['day'],o['time'],o['room_id']))
            imminent=due(r)<=now+1 or max(d for d,t in r['slots'])==obs['day']
            if self.name=='reactive':return command(r,opts[0])
            if self.name in ('pooled','clairvoyant'):
                if not any(q['owner']==self.actor and (due(q)<=now+1 or any(d==obs['day'] for d,t in q['slots'])) for q in unbooked):return idle(obs['decision_tick'])
                from tools.mixed_planner import plan
                planned=plan(obs,obs.get('planning_requests',unbooked))
                choices=[o for o in planned if o['appointment_id'] in {q['id'] for q in unbooked if q['owner']==self.actor} and (due(next(q for q in unbooked if q['id']==o['appointment_id']))<=now+1 or o['day']==obs['day'])]
                if choices:
                    pick=min(choices,key=lambda o:(due(next(q for q in unbooked if q['id']==o['appointment_id'])),o['day']))
                    return command(next(q for q in unbooked if q['id']==pick['appointment_id']),pick)
                return idle(obs['decision_tick'])
            # Anticipation retains alternatives until a legal commitment is needed; does not know future events.
            if not imminent:continue
            if coordinated and (not internal or opts[0]['cost']==7000):
                wants=[]
                for d,t in r['slots']:
                    if d<obs['day']:continue
                    for room in r['rooms']:
                        cost,res=costs(obs,r,room)
                        for b in obs['calendar']:
                            if [b['day'],b['time']]==[d,t] and b['owner']!=self.actor and set(res)&set(b['resources']):wants.append((d,t,res,b['owner']))
                for d,t,res,peer in wants:
                    key=r['id']+':'+peer
                    pair=sum(1 for k in self.memory['sent'] if k.endswith(':'+peer))
                    if key not in self.memory['sent'] and pair<32 and obs['opportunities']['info_used']<2:
                        self.memory['sent'][key]=now
                        packet={'kind':'need','day':d,'time':t,'resources':res,'until':due(r)}
                        return action('/messages',{'operation':'send','recipient':peer,'text':json.dumps(packet,separators=(',',':'))},'continue')
            # Score capacity flexibility and account concentration; no hidden seed, latent state or law.
            opts.sort(key=lambda o:(o['cost'], -(obs['accounts'][period(o['day'])]['available']-2500*risk.get(period(o['day']),0)),o['day'],o['time']))
            return command(r,opts[0])
        return idle(obs['decision_tick'])

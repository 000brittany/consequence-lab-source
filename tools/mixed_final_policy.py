"""Researcher-authored rolling planners; not world mechanisms or model prompts."""
from collections import Counter,defaultdict
from copy import deepcopy as cp
import json
import numpy as np
from scipy.optimize import milp,Bounds,LinearConstraint
from scipy.sparse import lil_matrix
from tools.mixed_policies import candidates,command,action,idle,due

ACTORS='ABC'

def pack(r):
    mask=sum(1<<(2*(d-1)+(t=='14:00')) for d,t in r['slots'])
    return [r['id'],r['owner'],hex(mask)[2:],r['rooms'],int(r['technician']),r['equipment'],r['assurance_due'],r.get('repair_due'),r.get('client_revision',0),r['client_status']]

def unpack(v):
    ident,owner,mask,rooms,tech,av,assurance,repair,revision,status=v;bits=int(mask,16)
    return {'id':ident,'owner':owner,'slots':[[d,t] for d in range(1,61) for j,t in enumerate(('09:00','14:00')) if bits&(1<<(2*(d-1)+j))],
        'rooms':rooms,'technician':bool(tech),'equipment':av,'assurance_due':assurance,'repair_due':repair,'client_revision':revision,'client_status':status}

def joint(obs,requests):
    """Maximum current known admissions, urgent priority, then cost. Fixed commitments.

    No optimal causal claim. Time-limit incumbents are used and explicitly counted.
    """
    options=[];groups=defaultdict(list);byid={r['id']:r for r in requests}
    for r in requests:
        if r['client_status']!='confirmed':continue
        for o in candidates(obs,r):options.append({**o,'appointment_id':r['id'],'owner':r['owner']})
    if not options:return [],'empty'
    for i,o in enumerate(options):
        groups['r',o['appointment_id']].append(i)
        for res in ['owner:'+o['owner']]+o['resources']:groups['c',o['day'],o['time'],res].append(i)
    rows=[{i:1 for i in ix} for ix in groups.values()];upper=[1]*len(rows)
    for p,a in obs['accounts'].items():
        rows.append({i:o['cost'] for i,o in enumerate(options) if str((o['day']-1)//5+1)==p});upper.append(a['available'])
    mat=lil_matrix((len(rows),len(options)))
    for row,vals in enumerate(rows):
        for col,v in vals.items():mat[row,col]=v
    now=obs['decision_clock_tick']
    objective=np.array([-1e6-(1e9 if due(byid[o['appointment_id']])<=now+1 or max(d for d,t in byid[o['appointment_id']]['slots'])==obs['day'] else 0)+o['cost']+o['day']*.001 for o in options])
    sol=milp(objective,integrality=np.ones(len(options)),bounds=Bounds(0,1),constraints=LinearConstraint(mat.tocsc(),-np.inf,upper),options={'time_limit':1,'mip_rel_gap':0})
    if sol.x is None:return [],'no_incumbent'
    return [o for i,o in enumerate(options) if sol.x[i]>.5],('optimal' if sol.success else 'incumbent')

class Planner:
    def __init__(self,actor,*,timing='early',group='',information=True,accommodation=False,reciprocal=False,decline=''):
        self.actor=actor;self.timing=timing;self.group=group if actor in group else '';self.info=information
        self.accommodation=accommodation and actor in group;self.reciprocal=reciprocal;self.decline=decline
        self.peer={};self.peer_at={};self.sent=Counter();self.lastsent={};self.needs=[];self.trials=Counter();self.received=Counter();self.given=Counter();self.pending_ack=[]
        self.diagnostics=Counter();self.cache=None;self.plan=[]

    def ingest(self,o):
        for m in o['direct_messages']['new_messages']:
            if m['sender'] not in self.group:continue
            p=json.loads(m['text']);kind=p['kind']
            if kind=='cards' and self.info:
                self.peer={ident:r for ident,r in self.peer.items() if r['owner']!=m['sender']}
                for v in p['cards']:
                    r=unpack(v)
                    if r['owner']==m['sender']:self.peer[r['id']]=r;self.peer_at[r['id']]=p['clock']
            elif kind=='need' and self.info:self.needs.append({**p,'sender':m['sender']})
            elif kind=='moved':self.received[m['sender']]+=1

    def transmit(self,o):
        if not self.group or o['opportunities']['info_used']>=2:return None
        now=o['decision_clock_tick'];booked={b['appointment_id'] for b in o['calendar'] if b['owner']==self.actor}
        pending=sorted([r for r in o['own_outstanding_requests'] if r['id'] not in booked],key=lambda r:(due(r),r['id']))
        for peer in self.group:
            if peer==self.actor or self.sent[peer]>=32:continue
            # At most one routine digest per period plus urgent changed digests.
            old=self.lastsent.get(peer,(-1000,''));period=(o['day']-1)//5
            cards=[pack(r) for r in pending if r['client_status']=='confirmed']
            packet={'kind':'cards','clock':now,'cards':[]}
            for v in cards:
                test={**packet,'cards':packet['cards']+[v]}
                if len(json.dumps(test,separators=(',',':')).encode())<=1024:packet=test
            signature=json.dumps(packet['cards'],separators=(',',':'))
            routine=old[0]//20<period
            urgent=(now-old[0]>=8 and self.sent[peer]<2*(period+1))
            if (routine or urgent) and signature!=old[1]:
                self.lastsent[peer]=(now,signature);self.sent[peer]+=1
                return action('/messages',{'operation':'send','recipient':peer,'text':json.dumps(packet,separators=(',',':'))},'continue')
        return None

    def move_for(self,o,r,need=None):
        """A legal atomic move keeping the donor's confirmed appointment.

        Prefer same/cheaper service; do not increase cost or sacrifice a reservation.
        Current own additional admissions are checked in the resulting projection.
        """
        booked={b['appointment_id']:b for b in o['calendar'] if b['owner']==self.actor};target=need['slot'] if need else None
        for ident,b in sorted(booked.items()):
            if need and ([b['day'],b['time']]!=target or not set(b['resources'])&set(need['resources'])):continue
            old=next((q for q in o['own_outstanding_requests'] if q['id']==ident),None)
            if not old or b['status']!='confirmed':continue
            opts=sorted(candidates(o,old),key=lambda x:(x['cost'],x['day'],x['time']))
            for alt in opts[:40]:
                if [alt['day'],alt['time']]==[b['day'],b['time']] or alt['cost']>b['price_cents']:continue
                changed=cp(o);changed['calendar']=[x for x in changed['calendar'] if x['appointment_id']!=ident]
                p=str((b['day']-1)//5+1);q=str((alt['day']-1)//5+1)
                changed['accounts'][p]['available']+=b['price_cents'];changed['accounts'][q]['available']-=alt['cost']
                changed['calendar'].append({**b,**{k:alt[k] for k in ('day','time','room_id','resources')},'price_cents':alt['cost']})
                if r and not candidates(changed,r):continue
                if need:
                    # Ensure the requested room/support is actually freed, without changing the beneficiary's request.
                    if any([x['day'],x['time']]==target and set(x['resources'])&set(need['resources']) for x in changed['calendar']):continue
                return command(old,alt,'reschedule')
        return None

    def decide(self,o):
        self.ingest(o);now=o['decision_clock_tick'];own=o['own_outstanding_requests'];booked={b['appointment_id'] for b in o['calendar']}
        unbooked=[r for r in own if r['id'] not in booked and r['client_status']=='confirmed']
        urgent=[r for r in unbooked if due(r)<=now+1 or max(d for d,t in r['slots'])==o['day']]
        if self.pending_ack and o['opportunities']['info_used']<2:
            peer=self.pending_ack.pop(0)
            if self.sent[peer]<32:
                self.sent[peer]+=1;return action('/messages',{'operation':'send','recipient':peer,'text':json.dumps({'kind':'moved','clock':now})},'continue')
        if self.accommodation and self.actor not in self.decline:
            for need in self.needs[-8:]:
                peer=need['sender']
                if need['until']<now or now-need['clock']>8:continue
                # A trial accommodation may be declined; subsequent help requires an observed return.
                if self.reciprocal and self.given[peer]>self.received[peer]:continue
                move=self.move_for(o,None,need)
                if move:
                    self.given[peer]+=1;self.pending_ack.append(peer);need['until']=-1
                    self.diagnostics['accommodations']+=1;return move
        for r in sorted(urgent,key=due):
            if not candidates(o,r):
                move=self.move_for(o,r)
                if move:self.diagnostics['own_repairs']+=1;return move
        # Messages use real opportunities, without postponing the last legal domain operation.
        message=self.transmit(o)
        if message:return message
        req=list(unbooked)
        if self.group and self.info:
            req += [cp(r) for ident,r in self.peer.items() if ident not in booked and now-self.peer_at[ident]<=20 and max(d for d,t in r['slots'])>=o['day']]
        if 'pooled_requests' in o:req=o['pooled_requests']
        key=json.dumps([req,o['calendar'],o['accounts'],o['day'],bool(urgent)],sort_keys=True)
        if key!=self.cache:
            self.plan,status=joint(o,req);self.cache=key;self.diagnostics['solve_'+status]+=1
        selected=[v for v in self.plan if v['owner']==self.actor and v['appointment_id'] in {r['id'] for r in unbooked}]
        choices=[]
        for v in selected:
            r=next(q for q in unbooked if q['id']==v['appointment_id']);imminent=r in urgent
            if v['room_id'].startswith('external:') and not imminent:continue
            if self.timing=='due' and not imminent and due(r)>now+5 and min(d for d,t in r['slots'])>o['day']+2:continue
            choices.append((not imminent,due(r),len(r['slots']),v,r))
        # If planned own demand is blocked, ask a permitted peer for a concrete accommodation.
        if self.accommodation and o['opportunities']['info_used']<2:
            for r in sorted(urgent,key=due):
                if any(v['appointment_id']==r['id'] and v['cost']<7000 for v in selected):continue
                for d,t in r['slots']:
                    if d<o['day']:continue
                    for room in r['rooms']:
                        res=[room]+(['technician'] if r['technician'] else [])+r['equipment']
                        for b in o['calendar']:
                            peer=b['owner'];tag=(r['id'],peer)
                            if peer==self.actor or peer not in self.group or self.sent[peer]>=32 or self.trials[tag]>=1:continue
                            if [b['day'],b['time']]!=[d,t] or not set(res)&set(b['resources']):continue
                            self.trials[tag]+=1;self.sent[peer]+=1;self.diagnostics['requests']+=1
                            p={'kind':'need','clock':now,'slot':[d,t],'resources':res,'until':due(r)}
                            return action('/messages',{'operation':'send','recipient':peer,'text':json.dumps(p,separators=(',',':'))},'continue')
        if choices:
            _,_,_,v,r=min(choices,key=lambda x:x[:3]+(x[3]['appointment_id'],))
            return command(r,v)
        return idle(o['decision_tick'])

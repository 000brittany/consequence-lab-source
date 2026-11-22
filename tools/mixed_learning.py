"""Within-trajectory learners. No fitting across seeds, hidden states or future labels."""
import math

def ridge(rows,x):
    import numpy as np
    if not rows:return .5
    X=np.array([[1,min(6,r[0])/3,min(4,r[1])/2] for r in rows],float);y=np.array([r[2] for r in rows],float)
    # Fixed weak count prior; only past observations update it.
    beta=np.linalg.solve(X.T@X+np.diag([2,4,4]),X.T@y+np.array([1.,0,0]))
    return float(np.clip(np.array([1,min(6,x[0])/3,min(4,x[1])/2])@beta,.03,4))

class Learner:
    def __init__(self,mode,law=None):
        self.mode=mode;self.law=law;self.seen={};self.rows=[];self.forecasts={};self.last_period=0;self.peer_rows=[];self.peer_future={};self.peer_unique={}
        self.peer_period={};self.law_prior={};self.law_post={};self.public_seen={};self.public_past={};self.peer_packets=0
    def update(self,obs):
        p=(obs['day']-1)//5+1;start=(p-1)*20
        for r in obs['own_outstanding_requests']:self.seen.setdefault(r['id'],r)
        for b in obs['calendar']:self.public_seen.setdefault(b['appointment_id'],(obs['decision_clock_tick'],b['owner'],len(b['resources'])>=3))
        for msg in obs['direct_messages']['new_messages']:
            import json
            try:data=json.loads(msg['text'])
            except (ValueError,TypeError):continue
            if data.get('kind')=='experience':
                for q,row in enumerate(data['rows'],data['period']-len(data['rows'])):self.peer_unique[(msg['sender'],q)]=row
                self.peer_rows=list(self.peer_unique.values());self.peer_future[msg['sender']]=data['future'];self.peer_period[msg['sender']]=data['period'];self.peer_packets+=1
        if p!=self.last_period:
            if self.last_period:
                q=self.last_period;f=self.forecasts[str(q)]
                y=sum(r['kind']=='emergency' and bool(r['technician'] or r['equipment']) and (q-1)*20<r['arrival_clock']<q*20 for r in self.seen.values())
                self.rows.append([f['feature'],f['lag'],y])
            self.last_period=p
            future=[]
            for q in range(1,13):
                value=0
                for r in obs['own_outstanding_requests']:
                    if r['kind']=='emergency' or not (r['technician'] or r['equipment']):continue
                    ps={str((d-1)//5+1) for d,t in r['slots']}
                    if str(q) in ps:value+=1/len(ps)
                future.append(value)
            feature=future[p-1];lag=self.rows[-1][2] if self.rows else 0
            public=sum((b['day']-1)//5+1==p and len(b['resources'])>=3 for b in obs['calendar'])/3
            rows=self.rows
            if self.mode=='public':
                # Public-only proxy: observed first appearances of own heavy commitments, no private training labels.
                prior=[sum((q-1)*20<=at<q*20 and owner==obs['external_room_id'].split(':')[1] and heavy for at,owner,heavy in self.public_seen.values()) for q in range(1,p)]
                pred=max(.05,(sum(prior)+1)/(len(prior)+2)*.35)
            elif self.mode=='memoryless':pred=.5
            elif self.mode=='oracle':pred=obs['oracle_pressure'][str(p)]
            elif self.mode=='pooled':
                values=[feature]+[v[p-1] for v in self.peer_future.values()];feature=sum(values)/len(values)
                pred=ridge(rows+self.peer_rows,[feature,lag])
            elif self.mode=='law':
                # Parameter-informed approximation, not latent-state oracle or exact Bayesian bound.
                pred=self.known(obs,p,feature)
            else:pred=ridge(rows,[feature,lag])
            self.forecasts[str(p)]={'clock':start,'feature':feature,'lag':lag,'count_prediction':pred,'probability':1-math.exp(-pred),
                'own_rows_available':len(self.rows),'peer_rows_available':len(self.peer_rows),'public_feature':public}
            self.future=future
        if self.mode=='pooled' and len([v for v in self.peer_period.values() if v==p])==2:
            f=self.forecasts[str(p)];feature=(self.future[p-1]+sum(v[p-1] for v in self.peer_future.values()))/3
            pred=ridge(self.rows+self.peer_rows,[feature,f['lag']]);f.update(feature=feature,count_prediction=pred,probability=1-math.exp(-pred),peer_rows_available=len(self.peer_rows),received_current_peer_packets=2)
        risk={}
        for q,v in enumerate(self.future,1):
            if self.mode=='memoryless':pred=.5
            elif self.mode=='oracle':pred=obs['oracle_pressure'][str(q)]
            elif self.mode=='public':pred=self.forecasts[str(p)]['count_prediction']
            elif self.mode=='law':
                pred=self.known(obs,q,v)
            else:
                values=[v]+[f[q-1] for f in self.peer_future.values()] if self.mode=='pooled' else [v]
                pred=ridge(self.rows+self.peer_rows if self.mode=='pooled' else self.rows,[sum(values)/len(values),self.rows[-1][2] if self.rows else 0])
            risk[str(q)]=pred
        return risk

    def known(self,obs,q,feature):
        # Exact resource marginal, approximate count normalization/slot-mixture likelihood.
        # This is a parameter-informed predictor, NOT an exact Bayesian or latent-state oracle.
        a=self.law['association'];sg=self.law['signal'];persistence=self.law['persistence']
        base=.36+.64*.45;high=.36+.64*sg;low=.36+.64*(1-sg)
        hi=a*high+(1-a)*base;lo=a*low+(1-a)*base
        p=(obs['day']-1)//5+1
        if p not in self.law_prior:
            old=self.law_post.get(p-1,.5);self.law_prior[p]=persistence*old+(1-persistence)*(1-old)
        prior=self.law_prior[p]
        for _ in range(max(0,q-p)):prior=persistence*prior+(1-persistence)*(1-prior)
        total=0
        for r in obs['own_outstanding_requests']:
            ps={str((d-1)//5+1) for d,t in r['slots']}
            if str(q) in ps and r['kind']!='emergency':total+=1/len(ps)
        odds=math.log(prior/(1-prior))+feature*math.log(hi/lo)+max(0,total-feature)*math.log((1-hi)/(1-lo))
        busy=1/(1+math.exp(-max(-20,min(20,odds))))
        if q==p:self.law_post[p]=busy
        # Busy peak occupies approximately three/five service days; weights 3 vs1.
        return (self.law['count']*32/120/36)*((1-a)*base+a*((1-busy)*low*.625+busy*high*1.375))

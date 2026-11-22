"""Offline calibration: deterministic explicit seed splits, no provider calls."""
import argparse
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy as cp
import gzip,hashlib,json,math,time
from pathlib import Path
import numpy as np
from consequence_lab_mixed.manifest import generate
from tools.mixed_simulation import simulate


def job(spec):
    seed,fund,policy,params,out=spec
    m=generate(seed,allocation_cents=fund,**params);r=simulate(m,policy)
    forecasts=[]
    for actor,fs in r['learning'].items():
        for p,f in fs.items():
            p=int(p)
            y=sum(q['owner']==actor and q['kind']=='emergency' and bool(q['technician'] or q['equipment']) and (p-1)*20<q['arrival_clock']<p*20 for q in m['requests'])
            forecasts.append({'actor':actor,'period':p,'target':y,**f})
    traces=Path(out)/'traces';name=f"s{seed}-b{fund}-{policy}-"+hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest()[:8]+'.json.gz'
    payload=json.dumps({'scope':'source-domain authored policy; not Runner/model context','manifest':m,'result':{k:v for k,v in r.items() if k not in ('snapshots','trace')},'trace':r['trace']},separators=(',',':')).encode()
    (traces/name).write_bytes(gzip.compress(payload,mtime=0))
    row={k:v for k,v in r.items() if k not in ('trace','snapshots','learning','outcomes')}
    row['forecast_scores']={'brier':sum((f['probability']-(f['target']>0))**2 for f in forecasts)/len(forecasts),
        'mae':sum(abs(f['count_prediction']-f['target']) for f in forecasts)/len(forecasts),'n':len(forecasts)} if forecasts else None
    row['forecasts']=forecasts;row['trace_file']=name;row['trace_sha256']=hashlib.sha256((traces/name).read_bytes()).hexdigest()
    return row


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--seeds',type=int,nargs='+',required=True)
    p.add_argument('--funds',type=int,nargs='+',required=True);p.add_argument('--policies',nargs='+',required=True);p.add_argument('--workers',type=int,default=2)
    p.add_argument('--parameter-grid',action='store_true');a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False);(a.output/'traces').mkdir()
    params=[{}]
    if a.parameter_grid:params=[{},dict(persistence=.55),dict(persistence=.85),dict(signal=.55),dict(signal=.85),dict(association=.2),dict(association=.65)]
    specs=[(s,b,policy,par,str(a.output)) for par in params for s in a.seeds for b in a.funds for policy in a.policies]
    rows=[];t=time.monotonic()
    with ProcessPoolExecutor(a.workers) as pool:
        for row in pool.map(job,specs):
            rows.append(row);(a.output/'results.json').write_text(json.dumps({'scope':'offline authored policies, not autonomous results','rows':rows},indent=2)+'\n')
            print(row['seed'],row['allocation'],row['policy'],row['completed'],row['failed'],flush=True)
    summary=[]
    keys=sorted({(r['allocation'],r['policy'],json.dumps(r['parameters'],sort_keys=True)) for r in rows})
    for b,policy,par in keys:
        group=[r for r in rows if r['allocation']==b and r['policy']==policy and json.dumps(r['parameters'],sort_keys=True)==par]
        summary.append({'allocation':b,'policy':policy,'parameters':json.loads(par),'seeds':len(group),
            'mean_completed':float(np.mean([r['completed'] for r in group])),'min_completed':min(r['completed'] for r in group),'max_completed':max(r['completed'] for r in group),
            'mean_failed':float(np.mean([r['failed'] for r in group])),'mean_cancelled':float(np.mean([r['cancelled'] for r in group])),
            'mean_external':float(np.mean([r['external_services'] for r in group])),'mean_spent':float(np.mean([r['spent'] for r in group])),
            'mean_messages':float(np.mean([r['messages'] for r in group])),
            'mean_brier':float(np.mean([r['forecast_scores']['brier'] for r in group if r['forecast_scores']])) if any(r['forecast_scores'] for r in group) else None})
    (a.output/'summary.json').write_text(json.dumps({'wall_seconds':time.monotonic()-t,'cases':len(rows),'summary':summary},indent=2)+'\n')
if __name__=='__main__':main()

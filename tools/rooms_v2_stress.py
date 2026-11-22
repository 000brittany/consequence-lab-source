"""Authored maximum-generation stress fixture; no provider integration."""
import argparse
import asyncio
import json
from pathlib import Path
import resource
import time
from consequence_lab.audit import verify_experiment
from consequence_lab.archive import Archive
from consequence_lab.backends import ScriptedMock
from consequence_lab.clock import ManualClock
from consequence_lab.jsonio import digest
from consequence_lab.rooms_v2_mocks import action
from consequence_lab.rooms_v2_run import new_run
from tools.pilot_spec import scenario


async def run(path):
    m=scenario('diagnostic');program={a:[] for a in ('A','B')}
    for p,requests in enumerate(m['requests'],1):
        for a in ('A','B'):
            book={'operation':'book','appointment_id':f'p{p}:{a}','room_id':f'overflow:{a}',
                  'time_id':requests[a]['allowed_times'][0]}
            for operation in (book,{'operation':'cancel','appointment_id':f'p{p}:{a}'},book):
                program[a].extend([action('inspect',path='/home'),
                    action('act','yield_tick',resource='/schedule',arguments=operation)])
            program[a].extend([action('inspect',path='/home'),
                action('act','yield_period',resource='/opportunities',arguments={'operation':'yield'})])
    started=time.monotonic()
    r=new_run(m,ScriptedMock(program),clock=ManualClock(),archive=path)
    await r.run(192);r.settle();r.checkpoint(Path(path)/'checkpoint.json')
    verification=verify_experiment(Archive.read(path),r.world)
    assert r.state['ledger']['calls']==192 and r.state['status']=='finished'
    assert r.world.measure(r.state['world'])['system']['completed']==24
    return {'scope':'authored short-output maximum-generation stress; not a worst-case artifact/output bound',
            'provider_calls':0,'manifest_sha256':digest(m),'pressure':m['pressure'],
            'communication_policy':m['communication'],'seed':m['origin']['seed'],'priority_phase':m['priority_phase'],
            'actual_wall_seconds':time.monotonic()-started,'generations':192,
            'peak_rss_kib_linux':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'events':len(r.records),'completed':24,'verification':verification,
            'research_archive_bytes':sum(p.stat().st_size for p in Path(path).rglob('*') if p.is_file()),
            'context_eviction_entries':sum(len(x['registration']['context_selection']['excluded_decisions'])
                for x in r.records if x['event_type']=='decision.completed')}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);a=p.parse_args()
    print(json.dumps(asyncio.run(run(a.output)),indent=2,sort_keys=True))


if __name__=='__main__':main()

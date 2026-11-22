"""No-cost successor witnesses followed by independent read-only reconstruction."""
import argparse
import asyncio
import json
from pathlib import Path
from consequence_lab.rooms_v2_validation import validate as validate_core
from tools.pilot_analysis import analyze
from tools.rooms_v2_controls_validation import validate as validate_controls


async def selected_fixtures(output):
    """Authored proofs for reviewed launch manifests; never a paid decision path."""
    from consequence_lab.backends import ScriptedMock
    from consequence_lab.clock import ManualClock
    from consequence_lab.rooms_v2_mocks import scripts
    from consequence_lab.rooms_v2_run import new_run
    from tools.pilot_spec import scenario
    rows=[]
    for role,policy in (('diagnostic','exchange'),('diagnostic','delta'),
            ('lower_pressure','independent'),('shared_disabled','independent'),
            ('shared_disabled','reservation_release')):
        path=output/(role+'-'+policy);m=scenario(role)
        r=new_run(m,ScriptedMock(scripts(policy)),clock=ManualClock(),archive=path)
        await r.run(192);r.checkpoint(path/'checkpoint.json')
        result=analyze(path);assert result['horizon_complete'] and result['outcomes']['system']['completed']==24
        rows.append({'role':role,'policy':policy,'generations':r.state['ledger']['calls'],
            'pressure':m['pressure'],'communication':m['communication'],
            'costs':{a:result['outcomes']['individual'][a]['booking_cost'] for a in ('A','B')},
            'archive_bytes':result['archive_bytes'],'provider_calls':0})
    return rows


async def validate(output):
    report=await validate_core(output)
    report['controls']=await validate_controls(Path(output)/'controls')
    report['selected_fixtures']=await selected_fixtures(Path(output)/'selected')
    reports=[]
    for archive in sorted(Path(output).rglob('*')):
        if not archive.is_dir() or not (archive/'records').is_dir():continue
        analysis=analyze(archive)
        target=Path(output)/(str(archive.relative_to(output)).replace('/','--')+'-analysis.json')
        target.write_text(json.dumps(analysis,indent=2,sort_keys=True))
        reports.append({'archive':str(archive.relative_to(output)),'report':target.name,'verified':analysis['verification'],
                        'horizon_complete':analysis['horizon_complete']})
    report['independent_analysis']=reports
    (Path(output)/'validation.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);a=p.parse_args()
    print(json.dumps(asyncio.run(validate(a.output)),indent=2,sort_keys=True))


if __name__=='__main__':main()

"""Deterministic validation manifests. Never agent instructions or scientific samples."""
from .jsonio import clone, integer, keys

MASKS = (None, 'R0', 'R1')


def manifest(profile='V', mask=None, order=('A','B'), coverage=0, periods=4,
             ticks=3, information=1, policy='candidate', scope='validation_catalogue'):
    if profile not in ('V','R','L','C') or mask not in MASKS or policy not in ('candidate','legacy'):
        raise ValueError('Invalid manifest parameters')
    integer(periods,1); integer(ticks,1); integer(information)
    if information > 3 or coverage not in (0,1) or tuple(sorted(order)) not in (('A',),('A','B')):
        raise ValueError('Invalid population/coverage/budget')
    if profile == 'L' and mask is not None: raise ValueError('L must be unrestricted')
    masks=[MASKS[(MASKS.index(mask)+1)%3] if profile=='C' and p>=4 else mask for p in range(1,periods+1)]
    costs={a:{'R0':0 if a=='A' or profile=='L' else 2,
              'R1':2 if a=='A' or profile=='L' else 0,'fallback':6} for a in order}
    return {'version':1,'fixture_id':f'{scope}:{profile}:{mask or "none"}:{"".join(order)}:c{coverage}:p{periods}:t{ticks}:i{information}:{policy}',
            'scope':scope,'profile':profile,'order':list(order),'coverage':coverage,'periods':periods,
            'ticks':ticks,'information':information,'policy':policy,'restrictions':masks,'costs':costs,
            'public_status':profile in ('R','L'),'release_tick':1 if profile=='L' else 0,
            'classifications':{'notice_redundant':profile in ('R','L'), 'late_notice':profile=='L',
                               'version_change_period':4 if profile=='C' else None,
                               'interpretation':'investigator-only potential; no behavior or representation classification'}}


def validate_manifest(m):
    keys(m,('version','fixture_id','scope','profile','order','coverage','periods','ticks','information','policy',
            'restrictions','costs','public_status','release_tick','classifications'))
    expected=manifest(m['profile'],m['restrictions'][0],tuple(m['order']),m['coverage'],m['periods'],m['ticks'],m['information'],m['policy'],m['scope'])
    if m != expected: raise ValueError('Noncanonical manifest')


def catalogue(periods=4,ticks=3,information=1,policy='candidate'):
    return [manifest(p,m,o,c,periods,ticks,information,policy) for p in ('V','R','L','C')
            for m in ((None,) if p=='L' else MASKS) for o in (('A','B'),('B','A')) for c in (0,1)]

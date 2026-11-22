"""Reviewed investigator configurations. Never selects autonomous actions."""
SOURCE='faeccaa03b8e4a0f4129d916ff520f4d5a4c0111'
VERSION='rooms-v2-pressure-communication-v1'
SCENARIOS=('diagnostic','lower_pressure','shared_disabled')
# Replaced/confirmed only from the complete prespecified deterministic panel.
DIAGNOSTIC={'pressure':'moderate','seed':3,'priority_phase':0,'communication':'public_metadata'}


def scenario(name):
    if name not in SCENARIOS:raise ValueError('Unknown v2 diagnostic/control')
    from consequence_lab.rooms_v2_manifest import generate
    config={**DIAGNOSTIC}
    if name=='lower_pressure':config['pressure']='low'
    if name=='shared_disabled':config['communication']='disabled'
    return generate(**config,periods=12)


def limits():
    from consequence_lab.rooms_v2_run import limits as cap
    return cap()


def authorization(name,recovery=False):
    if name not in SCENARIOS:raise ValueError('Unknown v2 diagnostic/control')
    if recovery:
        from tools.pilot_recovery import AUTHORIZATION
        if name!='diagnostic':raise ValueError('Protocol recovery is diagnostic only')
        return AUTHORIZATION
    return f'AUTHORIZE_ROOMS_V2_{name.upper()}_192_CALLS_USD_10'


def title(name,recovery=False,mode='paid'):
    if name not in SCENARIOS:raise ValueError('Unknown v2 diagnostic/control')
    if mode not in ('paid','offline'):raise ValueError('Unknown pilot mode')
    suffix=''
    if recovery:
        from tools.pilot_recovery import SUFFIX
        if name!='diagnostic':raise ValueError('Protocol recovery is diagnostic only')
        suffix=SUFFIX
    return f'Rooms v2 pilot | {mode} | scenario={name} | {VERSION} | core={SOURCE[:7]}'+suffix

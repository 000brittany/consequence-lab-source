"""Unfrozen long-horizon successor; v3 demand generation is preserved verbatim."""
from . import rooms_v3_manifest as v3
from .jsonio import clone, integer, keys

VERSION = 'rooms-v4-recurring-funding-draft-v1'
ACTORS, TIMES, ROOMS, quote, task_specs = v3.ACTORS, v3.TIMES, v3.ROOMS, v3.quote, v3.task_specs


def generate(seed=0, days=60, *, cycle_budget_cents, communication='public_metadata', priority_phase=0, direct_messages=True):
    integer(cycle_budget_cents, 1)
    m = v3.generate(seed, days, communication=communication, priority_phase=priority_phase, direct_messages=direct_messages)
    m['version'] = VERSION
    m['origin'] = {'kind': 'seeded', 'algorithm': 'rooms_v3_demands_recurring_funding_v1'}
    m['initial_budget_cents'] = cycle_budget_cents
    m['funding'] = {'period_days': 5, 'amount_cents': cycle_budget_cents,
                    'initial_deposit_day': 1, 'subsequent_days': list(range(6, days+1, 5)),
                    'rollover': True, 'spent_refundable': False}
    validate(m, regenerate=False)
    return m


def validate(m, regenerate=True):
    original = clone(m)
    funding = original.pop('funding')
    keys(funding, ('period_days','amount_cents','initial_deposit_day','subsequent_days','rollover','spent_refundable'))
    integer(funding['amount_cents'],1)
    integer(funding['period_days'],1); integer(funding['initial_deposit_day'],1)
    if type(funding['rollover']) is not bool or type(funding['spent_refundable']) is not bool or type(funding['subsequent_days']) is not list:
        raise ValueError('Invalid funding metadata types')
    for day in funding['subsequent_days']:
        integer(day,1)
    expected = {'period_days':5,'amount_cents':m['initial_budget_cents'], 'initial_deposit_day':1,
                'subsequent_days':list(range(6,m['days']+1,5)), 'rollover':True,'spent_refundable':False}
    if m['version'] != VERSION or funding != expected:
        raise ValueError('Invalid recurring funding manifest')
    if m['origin'] != {'kind':'seeded','algorithm':'rooms_v3_demands_recurring_funding_v1'}:
        raise ValueError('Unsupported successor origin')
    original['version'] = v3.VERSION
    original['origin'] = {'kind':'software_fixture','name':'validated_v4_demand_projection'}
    v3.validate(original, regenerate=False)
    if regenerate and m != generate(m['seed'],m['days'],cycle_budget_cents=funding['amount_cents'],
            communication=m['communication']['mode'],priority_phase=m['priority_phase'],direct_messages=m['direct_messages']['enabled']):
        raise ValueError('Successor manifest differs from exact generation')

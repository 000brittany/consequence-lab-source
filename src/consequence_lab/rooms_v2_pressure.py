"""Versioned, materialized external reservations; no client or communication rules."""
from .jsonio import integer

LEVELS=('low','moderate','high')
CELLS=('R0@09','R1@09','R0@10','R1@10')
HIGH_PATTERNS={'fixed_single':('R0@09',),
               'paired_recurring_single':('R0@09','R0@09','R1@10','R1@10')}


def materialize(level,periods,high_pattern='paired_recurring_single'):
    level={'easy':'low','difficult':'moderate'}.get(level,level)
    integer(periods,1)
    if level not in LEVELS or high_pattern not in HIGH_PATTERNS:raise ValueError('Invalid pressure setting')
    pattern='all_cells' if level=='low' else 'diagonal_pair' if level=='moderate' else high_pattern
    external=[]
    for p in range(periods):
        free=CELLS if level=='low' else ('R0@09','R1@10') if level=='moderate' else (HIGH_PATTERNS[high_pattern][p%len(HIGH_PATTERNS[high_pattern])],)
        external.append([c for c in CELLS if c not in free])
    return {'version':1,'level':level,'pattern':pattern},external

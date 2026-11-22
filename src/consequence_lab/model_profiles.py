"""Explicitly reviewed Responses compatibility profiles for the draft successor.

No speculative real model is enabled. An alternative requires a reviewed profile
with authoritative evidence, identity, capabilities and prices, plus separate
paid authorization. The synthetic profile is permanently network-ineligible.
"""
from dataclasses import asdict, dataclass
from .jsonio import digest, integer
from .provider import MODEL_SOURCE


@dataclass(frozen=True)
class ModelProfile:
    id: str
    model: str
    returned_models: tuple
    returned_prefixes: tuple
    context_window: int
    max_output: int
    input_rate: int
    cache_read_rate: int
    cache_write_rate: int
    output_rate: int
    long_threshold: int
    long_input_rate: int
    long_cache_read_rate: int
    long_cache_write_rate: int
    long_output_rate: int
    reasoning_modes: tuple
    prompt_cache_options: bool
    network_reviewed: bool
    evidence: tuple
    endpoint: str = 'https://api.openai.com/v1/responses'

    def __post_init__(self):
        for field in ('context_window','max_output','long_threshold'):
            integer(getattr(self,field),1)
        for field in ('input_rate','cache_read_rate','cache_write_rate','output_rate',
                      'long_input_rate','long_cache_read_rate','long_cache_write_rate','long_output_rate'):
            integer(getattr(self,field))
        if type(self.id) is not str or type(self.model) is not str or not self.id or not self.model or self.max_output > self.context_window:
            raise ValueError('Invalid model profile')
        if any(type(x) is not str or not x for seq in (self.returned_models,self.returned_prefixes,self.reasoning_modes) for x in seq):
            raise ValueError('Nonempty model identities and reasoning options required')
        if not self.returned_models or self.model not in self.returned_models:
            raise ValueError('Explicit returned-model identity required')
        if not self.evidence or any(type(x) is not str or not x for x in self.evidence):
            raise ValueError('Model provenance required')
        if self.endpoint != 'https://api.openai.com/v1/responses':
            raise ValueError('This adapter supports the reviewed OpenAI Responses endpoint only')
        if type(self.network_reviewed) is not bool or type(self.prompt_cache_options) is not bool:
            raise ValueError('Invalid compatibility flags')
        if any(type(x) is not tuple for x in (self.returned_models,self.returned_prefixes,self.reasoning_modes,self.evidence)):
            raise ValueError('Profile sequences must be immutable tuples')

    def to_json(self):
        return {k:list(v) if type(v) is tuple else v for k,v in asdict(self).items()}

    @property
    def sha256(self):
        return digest(self.to_json())


LUNA = ModelProfile('luna-reviewed-v1','gpt-6-luna',('gpt-6-luna',),('gpt-6-luna-',),
    1_050_000,128_000,100,10,125,500,272_000,200,20,250,750,
    ('none','low','medium','high','xhigh','max'),True,True,
    (MODEL_SOURCE,'Existing repository Luna profile and offline wire/accounting regressions; no new model validation claimed'))
FIXTURE = ModelProfile('offline-alternative-v1','fixture-other-model',('fixture-other-model',),(),
    4096,256,30,3,40,80,2000,60,6,80,120,(),False,False,
    ('Synthetic offline compatibility fixture; prices are invented test values, not a real model offering',))
PROFILES = {p.id:p for p in (LUNA,FIXTURE)}


def register_reviewed_profile(profile):
    """Explicit integration point for separately reviewed compatibility data.

    This is not a paid-call grant. Registration must be committed/reviewed before
    use; archive restoration requires an exact registered fingerprint.
    """
    if not isinstance(profile,ModelProfile) or profile.id in PROFILES:
        raise ValueError('New immutable profile identity required')
    PROFILES[profile.id] = profile


def resolve_profile(identifier, fingerprint=None):
    p = PROFILES[identifier]
    if fingerprint is not None and fingerprint != p.sha256:
        raise ValueError('Model profile fingerprint changed')
    return p

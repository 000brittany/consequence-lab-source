"""Successor adapter; legacy Luna backend semantics remain the default for v3."""
import asyncio
from dataclasses import asdict, dataclass
import math
import time
from .backends import Response
from .jsonio import canonical, clone
from .model_profiles import resolve_profile
from .provider import ResponsesBackend, decode


@dataclass(frozen=True)
class ProfileConfig:
    profile_id: str = 'luna-reviewed-v1'
    reasoning_effort: str | None = 'medium'
    timeout_seconds: float = 60.0

    def __post_init__(self):
        p = resolve_profile(self.profile_id)
        if (p.reasoning_modes and self.reasoning_effort not in p.reasoning_modes) or (not p.reasoning_modes and self.reasoning_effort is not None):
            raise ValueError('Unsupported model reasoning option')
        if type(self.timeout_seconds) not in (float,int) or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 60:
            raise ValueError('Invalid request timeout')

    @property
    def model(self): return resolve_profile(self.profile_id).model
    @property
    def endpoint(self): return resolve_profile(self.profile_id).endpoint
    @property
    def max_output(self): return resolve_profile(self.profile_id).max_output
    @property
    def context_window(self): return resolve_profile(self.profile_id).context_window


class ProfiledResponsesBackend(ResponsesBackend):
    name = 'profiled_responses_v1'
    counter_id = 'profiled_wire_utf8_plus_1024_estimate_v1'

    def __init__(self,config=None,transport=None):
        super().__init__(config or ProfileConfig(),transport)
        self.profile = resolve_profile(self.config.profile_id)
        self.input_rate_nano_usd = max(self.profile.input_rate,self.profile.cache_read_rate,self.profile.cache_write_rate,
                                      self.profile.long_input_rate,self.profile.long_cache_read_rate,self.profile.long_cache_write_rate)
        self.output_rate_nano_usd = max(self.profile.output_rate,self.profile.long_output_rate)

    def export(self):
        return {'type':self.name,'state':{'configuration':asdict(self.config),
                'profile':self.profile.to_json(),'profile_sha256':self.profile.sha256}}

    @classmethod
    def from_export(cls,state,transport=None):
        profile = resolve_profile(state['configuration']['profile_id'],state['profile_sha256'])
        if canonical(state['profile']) != canonical(profile.to_json()):
            raise ValueError('Archived compatibility data differ from reviewed profile')
        return cls(ProfileConfig(**state['configuration']),transport)

    def wire_request(self,request):
        result = super().wire_request(request)
        if not self.profile.reasoning_modes:
            result.pop('reasoning')
        if not self.profile.prompt_cache_options:
            result.pop('prompt_cache_options')
        return result

    def ensure_allowed(self,allowed):
        if self.transport is not None and self.transport.network and not self.profile.network_reviewed:
            raise PermissionError('Model profile has no reviewed network compatibility')
        return super().ensure_allowed(allowed)

    def cost(self,usage):
        p = self.profile
        names = ('long_input_rate','long_cache_read_rate','long_cache_write_rate','long_output_rate') if usage['input_tokens'] > p.long_threshold else ('input_rate','cache_read_rate','cache_write_rate','output_rate')
        inp,read,write,out = [getattr(p,k) for k in names]
        if usage['cache_read_tokens'] is None or usage['cache_write_tokens'] is None:
            amount = usage['input_tokens']*max(inp,read,write)
        else:
            amount = usage['uncached_input_tokens']*inp+usage['cache_read_tokens']*read+usage['cache_write_tokens']*write
        return amount+usage['output_tokens']*out

    def _decode(self,raw,status):
        return decode(raw,status,accepted_models=self.profile.returned_models,accepted_prefixes=self.profile.returned_prefixes)

    async def generate(self,request):
        if self.transport is None:
            raise PermissionError('Provider transport disabled')
        if self.transport.network and not self.profile.network_reviewed:
            raise PermissionError('Model profile has no reviewed network compatibility')
        body=canonical(self.wire_request(request));started=time.monotonic()
        self.last_exchange={'status_code':None,'headers':{},'response_text':None,'latency_seconds':None,
            'failure':None,'retry_count':0,'transport':'network' if self.transport.network else 'offline_fixture'}
        try:
            status,headers,raw=await asyncio.wait_for(self.transport.post(self.config.endpoint,body,self.config.timeout_seconds),self.config.timeout_seconds)
            self.last_exchange.update(status_code=status,headers=clone(headers),response_text=raw)
            response=self._decode(raw,status)
        except asyncio.CancelledError:
            self.last_exchange['failure']='CancelledError';raise
        except Exception as exc:
            self.last_exchange['failure']=type(exc).__name__;response=Response(finish_reason='transport_error')
        finally:
            self.last_exchange['latency_seconds']=time.monotonic()-started
        response.exposed_fields={'provider_exchange':clone(self.last_exchange)}
        return response

    def audit_response(self,response):
        exchange=response['exposed_fields']['provider_exchange']
        latency=exchange['latency_seconds']
        if (exchange['retry_count']!=0 or exchange['transport'] not in ('network','offline_fixture')
                or type(latency) not in (int,float) or not math.isfinite(latency) or latency<0):
            raise ValueError('Invalid provider execution provenance')
        expected=self._decode(exchange['response_text'],exchange['status_code']) if exchange['response_text'] is not None else Response(finish_reason='transport_error')
        expected.exposed_fields=clone(response['exposed_fields'])
        if expected.data()!=response:
            raise ValueError('Normalized response differs from exact profiled provider response')

    def measurements(self,usage,exchange):
        return {**super().measurements(usage,exchange),'profile':self.profile.to_json(),
                'profile_sha256':self.profile.sha256,'compatibility_scope':'explicit reviewed profile; alias is not immutable weights'}

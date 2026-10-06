"""Spawn-picklable parameters; CAMEL backends are constructed in owned child.

Transport is cooperative and MUST have finite timeout, disabled SDK retries,
and a non-streaming maximum-token policy. Paid mode remains disabled here.
"""
from dataclasses import dataclass
import json
import threading
import time
from urllib.parse import urlsplit
from .native_launch_client import validate_limits


class NativeModelBoundExceeded(RuntimeError):
    def __init__(self):
        super().__init__('native_model_bound_exceeded')


def validate_transport(backend, *, mode, timeout_seconds, max_tokens):
    """Inspect the returned CAMEL transport, not just factory arguments."""
    timeout=getattr(backend,'_timeout',None)
    retries=getattr(backend,'_max_retries',None)
    config=getattr(backend,'model_config_dict',None)
    if (type(timeout) not in (int,float) or not 0<timeout<=timeout_seconds
            or type(retries) is not int or retries!=0 or type(config) is not dict
            or type(config.get('max_tokens')) is not int or not 1<=config['max_tokens']<=max_tokens
            or config.get('stream',False) is not False):
        raise NativeModelBoundExceeded()
    endpoints=[getattr(backend,name,None) for name in ('_url','_base_url')]
    if mode=='scripted':
        if (any(value is not None for value in endpoints)
                or any(getattr(backend,name,None) is not None for name in ('_client','_async_client','_api_key'))):
            raise NativeModelBoundExceeded()
    elif mode=='local':
        if not any(value is not None for value in endpoints):
            raise NativeModelBoundExceeded()
        for endpoint in endpoints:
            if endpoint is None:continue
            try:
                if type(endpoint) is not str or len(endpoint)>2048 or not endpoint.isascii():raise ValueError
                url=urlsplit(endpoint)
                if (url.scheme not in {'http','https'} or url.hostname not in {'127.0.0.1','::1'}
                        or url.username is not None or url.password is not None or url.query or url.fragment
                        or url.port is not None and not 1<=url.port<=65535):raise ValueError
            except ValueError:
                raise NativeModelBoundExceeded() from None
    else:
        raise NativeModelBoundExceeded()


class SharedCallBoundary:
    def __init__(self, limits, *, clock=time.monotonic):
        self.limits = dict(validate_limits(limits))
        self.clock, self.deadline = clock, clock() + limits['max_run_seconds']
        self.lock, self.calls = threading.Lock(), 0

    def admit(self, messages, response_format, tools):
        try:
            size = len(json.dumps({'messages':messages,'response_format':response_format,'tools':tools},
                                 ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8'))
        except (ValueError,TypeError,UnicodeError,RecursionError):
            raise NativeModelBoundExceeded() from None
        with self.lock:
            if self.clock() >= self.deadline or self.calls >= self.limits['max_calls'] or size > self.limits['max_input_bytes']:
                raise NativeModelBoundExceeded()
            self.calls += 1

    def accept(self, response, counter):
        # Streaming would escape the response/deadline boundary.
        try:
            if self.clock() >= self.deadline or not hasattr(response,'choices'):
                raise ValueError
            usage = getattr(response,'usage',None)
            count = getattr(usage,'completion_tokens',None)
            messages = []
            for choice in response.choices:
                message = choice.message
                messages.append(message.model_dump(exclude_none=True))
            measured = counter.count_tokens_from_messages(messages)
            if type(measured) is not int or not 0<=measured<=self.limits['max_output_tokens']:
                raise ValueError
            if count is None:count=measured
            if type(count) is not int or not 0 <= count <= self.limits['max_output_tokens']:
                raise ValueError
            # Bound tool arguments/content as well as reported usage.
            raw = response.model_dump_json().encode('utf-8')
            if len(raw) > 2097152:
                raise ValueError
            return response
        except (ValueError,TypeError,AttributeError,UnicodeError):
            raise NativeModelBoundExceeded() from None


@dataclass(frozen=True)
class BoundedNativeModelFactory:
    platforms: tuple
    limits: dict
    backend_factory: object
    mode: str = 'scripted'
    transport_timeout_seconds: int = 15
    sdk_retries: int = 0
    cooperative_transport: bool = False

    def configured(self):
        try:
            validate_limits(self.limits)
            return (self.platforms in (('twitter',),('reddit',),('twitter','reddit'))
                and callable(self.backend_factory) and self.mode in {'scripted','local'}
                and type(self.transport_timeout_seconds) is int and 1 <= self.transport_timeout_seconds <= 15
                and type(self.sdk_retries) is int and self.sdk_retries == 0 and self.cooperative_transport is True)
        except (ValueError,TypeError):
            return False

    def __call__(self):
        if not self.configured():
            raise NativeModelBoundExceeded()
        from camel.models import BaseModelBackend
        from camel.utils.token_counting import BaseTokenCounter
        boundary = SharedCallBoundary(self.limits)
        models = self.backend_factory(timeout_seconds=min(self.transport_timeout_seconds,self.limits['max_run_seconds']),
                                      max_tokens=self.limits['max_output_tokens'],max_retries=0)
        if type(models) is not dict or set(models) != set(self.platforms):
            raise NativeModelBoundExceeded()

        class BoundedModel(BaseModelBackend):
            def __init__(self, backend):
                if not isinstance(backend,BaseModelBackend) or not isinstance(backend.token_counter,BaseTokenCounter):
                    raise NativeModelBoundExceeded()
                validate_transport(backend,mode=self_mode,timeout_seconds=transport_seconds,
                                   max_tokens=boundary.limits['max_output_tokens'])
                config = dict(backend.model_config_dict)
                if (type(config.get('max_tokens')) is not int or not 1<=config['max_tokens']<=boundary.limits['max_output_tokens']
                        or config.get('stream',False) is not False):
                    raise NativeModelBoundExceeded()
                config['max_tokens'] = boundary.limits['max_output_tokens']
                config['stream'] = False
                super().__init__(model_type=backend.model_type,model_config_dict=config)
                self.backend = backend

            @property
            def token_counter(self):
                return self.backend.token_counter

            def _run(self,messages,response_format=None,tools=None):
                boundary.admit(messages,response_format,tools)
                return boundary.accept(self.backend.run(messages,response_format=response_format,tools=tools),self.token_counter)

            async def _arun(self,messages,response_format=None,tools=None):
                boundary.admit(messages,response_format,tools)
                return boundary.accept(await self.backend.arun(messages,response_format=response_format,tools=tools),self.token_counter)

        self_mode=self.mode
        transport_seconds=min(self.transport_timeout_seconds,self.limits['max_run_seconds'])
        return {platform:BoundedModel(model) for platform,model in models.items()}

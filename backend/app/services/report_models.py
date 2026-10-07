"""One actual SDK-request boundary shared by inherited and nested research calls."""
from dataclasses import dataclass
import threading
import time
from types import SimpleNamespace
from .connected_report_client import ReportError, encoded, text, validate_limits


@dataclass(frozen=True)
class BoundedReportModelFactory:
    """Explicit trusted transport; never constructs an implicit paid client.

    transport_factory(max_retries=0, timeout=...) must return a cooperative
    OpenAI-shaped SDK transport exposing max_retries=0 and close(). It must be
    spawn-pickleable. No factory invocation occurs during review/status/read.
    """
    transport_factory: object
    model_name: str
    limits: dict

    def configured(self):
        try:
            text(self.model_name, 200); validate_limits(self.limits)
            return callable(self.transport_factory)
        except (ValueError, TypeError, UnicodeError):
            return False

    def create(self, *, deadline, checkpoint, first_call):
        if not self.configured() or time.monotonic() >= deadline:
            raise ReportError('model_calls_disabled')
        checkpoint()
        transport = self.transport_factory(max_retries=0, timeout=min(15, deadline - time.monotonic()))
        try:
            if (type(getattr(transport, 'max_retries', None)) is not int or transport.max_retries != 0
                    or not callable(getattr(getattr(getattr(transport, 'chat', None), 'completions', None), 'create', None))
                    or not callable(getattr(transport, 'close', None))):
                raise ReportError('report_unavailable')
            return ReportModel(transport, self.model_name, self.limits, deadline, checkpoint, first_call)
        except BaseException:
            if callable(getattr(transport, 'close', None)):
                transport.close()
            raise


class RequestBoundary:
    def __init__(self, transport, limits, deadline, checkpoint, first_call):
        self.transport, self.limits, self.deadline = transport, dict(validate_limits(limits)), deadline
        self.checkpoint, self.first_call = checkpoint, first_call
        self.calls, self.failed = 0, False
        self.lock = threading.Lock()
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        with self.lock:
            self.checkpoint()
            remaining = self.deadline - time.monotonic()
            if self.failed or remaining <= 0 or self.calls >= self.limits['max_calls']:
                raise ReportError('report_uncertain')
            try:
                if kwargs.get('stream') not in (None, False):
                    raise ValueError
                kwargs['stream'] = False
                kwargs['timeout'] = min(15, remaining)
                for key in ('max_tokens', 'max_completion_tokens'):
                    if key in kwargs:
                        if type(kwargs[key]) is not int or not 1 <= kwargs[key] <= 4096:
                            raise ValueError
                token_key = 'max_completion_tokens' if 'max_completion_tokens' in kwargs else 'max_tokens'
                kwargs[token_key] = min(kwargs.get(token_key, self.limits['max_output_tokens']), self.limits['max_output_tokens'])
                if len(encoded(kwargs)) > self.limits['max_input_bytes']:
                    raise ReportError('result_too_large')
            except (ValueError, TypeError, UnicodeError):
                raise ReportError('invalid_request') from None
            # Persist/acknowledge BEFORE the first possible transport request.
            if self.calls == 0:
                self.first_call()
            self.calls += 1
            try:
                self.checkpoint()
                response = self.transport.chat.completions.create(**kwargs)
                self.checkpoint()
                if time.monotonic() >= self.deadline:
                    raise ReportError('timeout')
                from ..utils.openai_chat_compat import extract_chat_completion_text
                content = extract_chat_completion_text(response)
                usage_tokens = getattr(getattr(response, 'usage', None), 'completion_tokens', None)
                if usage_tokens is not None and (type(usage_tokens) is not int or not 0 <= usage_tokens <= self.limits['max_output_tokens']):
                    raise ReportError('result_too_large')
                if (type(content) is not str or len(content.encode('utf-8')) > self.limits['max_output_tokens'] * 16
                        or len(content.encode('utf-8')) > self.limits['max_input_bytes']):
                    raise ReportError('result_too_large')
                if callable(getattr(response, 'model_dump', None)) and len(encoded(response.model_dump(mode='json'))) > self.limits['max_input_bytes']:
                    raise ReportError('result_too_large')
                return response
            except BaseException:
                # Fixed error removes compatibility status/body and fences all
                # subsequent calls, including inherited catch-and-retry loops.
                self.failed = True
                raise ReportError('report_uncertain') from None


class ReportModel:
    """Reuse inherited text cleanup and JSON parsing without its constructor."""
    def __init__(self, transport, model, limits, deadline, checkpoint, first_call):
        self.transport, self.model = transport, model
        self.client = RequestBoundary(transport, limits, deadline, checkpoint, first_call)

    def _create_completion(self, **kwargs):
        from ..utils.openai_chat_compat import create_chat_completion
        return create_chat_completion(self.client, model=self.model, **kwargs)

    def chat(self, *args, **kwargs):
        from ..utils.llm_client import LLMClient
        try:
            return LLMClient.chat(self, *args, **kwargs)
        except Exception:
            self.client.failed = True
            raise ReportError('report_uncertain') from None

    def chat_json(self, *args, **kwargs):
        from ..utils.llm_client import LLMClient
        if kwargs.get('max_attempts', 1) != 1:
            raise ReportError('invalid_request')
        try:
            return LLMClient.chat_json(self, *args, **kwargs)
        except Exception:
            self.client.failed = True
            raise ReportError('report_uncertain') from None

    @staticmethod
    def _parse_json_response(response):
        from ..utils.llm_client import LLMClient
        return LLMClient._parse_json_response(response)

    def close(self):
        self.transport.close()

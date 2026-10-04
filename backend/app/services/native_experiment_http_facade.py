"""Immutable optional operator binding; lazy owned child, no private file reads."""
from dataclasses import dataclass
import os
import threading
from pathlib import Path
from uuid import uuid4

from .native_experiment_http_client import (CHILD_KEYS, NativeExperimentProcessClient,
    encoded, hash_value, payload, reply, _uuid)
from .knowledge_reader import KnowledgeReadError

_CAPTURE = object()


@dataclass(frozen=True, repr=False)
class ExperimentSettings:
    python: str
    project_id: str
    manifest_sha256: str
    environment: tuple

    @classmethod
    def capture(cls, settings):
        try:
            path = os.environ.get('KNOWLEDGE_EXPERIMENT_MANIFEST')
            pin = os.environ.get('KNOWLEDGE_EXPERIMENT_MANIFEST_SHA256')
            if (type(path) is not str or not 1 <= len(path) <= 4096 or '\x00' in path
                    or not Path(path).is_absolute() or str(Path(path)) != path or '..' in Path(path).parts):
                raise ValueError
            hash_value(pin)
            project = _uuid(settings.scope['project_id'])
            env = {k: settings.child_environment[k] for k in CHILD_KEYS if k.startswith('KNOWLEDGE_PG_')}
            env.update(KNOWLEDGE_PRINCIPAL=settings.principal,
                KNOWLEDGE_EXPERIMENT_PROJECT_ID=project, KNOWLEDGE_EXPERIMENT_MANIFEST=path,
                KNOWLEDGE_EXPERIMENT_MANIFEST_SHA256=pin)
            return cls(settings.python, project, pin, tuple(env.items()))
        except Exception:
            return None


class NativeExperimentFacade:
    def __init__(self, settings, *, binding=_CAPTURE, client_factory=None):
        # Callers registering routes capture binding once, even if absent.
        self._binding = ExperimentSettings.capture(settings) if binding is _CAPTURE else binding
        self._factory = client_factory
        self._lock = threading.Lock()

    def execute(self, method, value):
        if not self._lock.acquire(blocking=False):
            raise KnowledgeReadError('experiment_unavailable')
        try:
            payload(method, value)
            b = self._binding
            if b is None:
                raise ValueError
            envelope = {'version': 1, 'request_id': str(uuid4()), 'method': method,
                'scope': {'project_id': b.project_id, 'manifest_sha256': b.manifest_sha256}, 'payload': value}
            client = (self._factory() if self._factory is not None else NativeExperimentProcessClient(
                b.python, str(Path(__file__).with_name('native_experiment_http_child.py')),
                timeout_seconds=90, child_environment=dict(b.environment)))
            response = reply(client.call(encoded(envelope)), envelope)
            if not response['ok']:
                raise ValueError
            return response['result']
        except Exception:
            raise KnowledgeReadError('experiment_unavailable') from None
        finally:
            self._lock.release()

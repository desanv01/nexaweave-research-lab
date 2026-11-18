"""Lazy opt-in facade. Ordinary HTTP imports create no model/Temporal clients."""
import threading
from pathlib import Path
from uuid import uuid4

from .knowledge_transport import (KnowledgeProcessClient, KnowledgeTransportFailure,
                                  _json_object, _trusted_environment)
from .preparation_client import (PreparationError, REQUEST_BYTES, RESULT_BYTES, ENVELOPE_OVERHEAD,
                                 encoded, validate_payload)
from .prepared_profile_inspection import (PROFILE_ERRORS, READ_METHODS, inspect_prepared, profile_request,
                                          read_request, read_result, read_selector)

_ROOT = 'KNOWLEDGE_PREPARATION_ARTIFACT_ROOT'
_CHILD_KEYS = ('KNOWLEDGE_PRINCIPAL', 'KNOWLEDGE_DISPLAY_GRAPH_ID', 'KNOWLEDGE_BOUND_SCOPE_JSON',
               'KNOWLEDGE_PG_HOST', 'KNOWLEDGE_PG_PORT', 'KNOWLEDGE_PG_DATABASE',
               'KNOWLEDGE_PG_USER', 'KNOWLEDGE_PG_PASSWORD')


class _ProfileProcessClient(KnowledgeProcessClient):
    def __init__(self, python, scope, graph_id, environment, *, method='profiles'):
        self.scope, self.graph_id, self.method = scope, graph_id, method
        super().__init__(python, str(Path(__file__).with_name('prepared_profile_inspection_child.py')),
                         timeout_seconds=25, child_environment=environment)

    def _command(self):
        command = super()._command()
        command.insert(-1, "-B")
        return command

    def _validate_request(self, raw):
        if not 0 < len(raw) <= REQUEST_BYTES:
            raise PreparationError('invalid_request')
        request = (profile_request(raw, self.scope) if self.method == 'profiles'
                   else read_request(raw, self.scope, method=self.method))
        self._payload = request['payload']
        return request['request_id']

    def _response_limit(self, raw):
        return RESULT_BYTES + ENVELOPE_OVERHEAD

    def _validate_reply(self, raw, request_id):
        try:
            reply = _json_object(raw)
            if (not 0 < len(raw) <= self._response_limit(raw)
                    or reply['request_id'] != request_id or type(reply['version']) is not int
                    or reply['version'] != 1 or type(reply['ok']) is not bool):
                raise ValueError
            fields = {'version', 'request_id', 'ok'}
            if reply['ok']:
                if set(reply) != fields | {'result'}:
                    raise ValueError
                read_result(self.method, reply['result'], self.graph_id, self.scope, self._payload)
            elif (set(reply) != fields | {'error'} or type(reply['error']) is not dict
                  or set(reply['error']) != {'code'} or type(reply['error']['code']) is not str
                  or reply['error']['code'] not in PROFILE_ERRORS):
                raise ValueError
            return reply
        except (ValueError, TypeError, KeyError, PreparationError):
            raise KnowledgeTransportFailure(outcome_unknown=False) from None


def _profile_binding(settings, artifact_root):
    if type(artifact_root) is not str or not 1 <= len(artifact_root) <= 4096 or '\0' in artifact_root:
        return None
    try:
        root = Path(artifact_root)
        if not root.is_absolute() or str(root) != artifact_root or '..' in root.parts:
            return None
        environment = _trusted_environment({key: settings.child_environment[key] for key in _CHILD_KEYS})
        scope = _json_object(environment['KNOWLEDGE_BOUND_SCOPE_JSON'].encode('utf-8'))
        if (scope != settings.scope or environment['KNOWLEDGE_PRINCIPAL'] != settings.principal
                or environment['KNOWLEDGE_DISPLAY_GRAPH_ID'] != settings.display_graph_id):
            return None
        environment[_ROOT] = artifact_root
        return settings.python, encoded(scope), tuple(environment.items())
    except (KeyError, ValueError, TypeError, AttributeError):
        return None


class KnowledgePreparationFacade:
    def __init__(self, settings, *, host=None, artifact_root=None):
        self.settings = settings
        self.host = host
        self._profile_binding = _profile_binding(settings, artifact_root) if host is None else None
        self._calls = threading.BoundedSemaphore(4)

    def _inspect(self, method, graph_id, payload):
        python, raw_scope, environment = self._profile_binding
        scope = _json_object(raw_scope)
        request_id = str(uuid4())
        try:
            client = _ProfileProcessClient(python, scope, graph_id, dict(environment), method=method)
            request = encoded({'version': 1, 'request_id': request_id, 'method': method,
                               'scope': scope, 'payload': payload})
            reply = client._validate_reply(client.call(request), request_id)
        except Exception:
            raise PreparationError('preparation_unavailable') from None
        if not reply['ok']:
            raise PreparationError(reply['error']['code'])
        return reply['result']

    def execute(self, method, graph_id, payload):
        if graph_id != self.settings.display_graph_id:
            raise PreparationError('not_found')
        inspection = method in READ_METHODS
        if self.host is None and (not inspection or self._profile_binding is None):
            raise PreparationError('preparation_unavailable')
        payload = read_selector(method, payload) if inspection else validate_payload(method, payload)
        if self.host is not None and (self.host.principal != self.settings.principal
                or self.host.display_graph_id != graph_id
                or self.host.scope_dto != self.settings.scope):
            raise PreparationError('preparation_unavailable')
        if not self._calls.acquire(blocking=False):
            raise PreparationError('busy')
        try:
            if inspection:
                if self.host is not None:
                    return inspect_prepared(method, self.host, payload)
                return self._inspect(method, graph_id, payload)
            return getattr(self.host, method)(payload)
        except PreparationError:
            raise
        except Exception as error:
            from .preparation_client import CODES
            code = getattr(error, 'code', None)
            if code in CODES:
                raise PreparationError(code) from None
            if code in {'budget_busy', 'budget_conflict'}:
                raise PreparationError('conflict') from None
            if code in {'budget_uncertain'}:
                raise PreparationError('preparation_uncertain') from None
            if code in {'budget_store_unavailable', 'budget_migration_mismatch'}:
                raise PreparationError('preparation_unavailable') from None
            raise PreparationError('internal_error') from None
        finally:
            self._calls.release()

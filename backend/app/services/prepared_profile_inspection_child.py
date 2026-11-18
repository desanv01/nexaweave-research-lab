"""One owned PG-only request against a trusted prepared-artifact root."""
import os
from pathlib import Path
import sys
import time

# Cold fixed siblings. Authority packages come from the installed interpreter.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from knowledge_transport import _read_exact
from preparation_client import ENVELOPE_OVERHEAD, REQUEST_BYTES, RESULT_BYTES, PreparationError, encoded
from prepared_profile_inspection import (
    PROFILE_ERRORS, PreparationReadAuthority, inspect_prepared, read_request, read_result,
)


class _ReadAuthority(PreparationReadAuthority):
    _missing_binding_unavailable = True

    def __init__(self, settings, root, connect):
        from nexaweave_execution.preparation_store import PreparationStore
        from nexaweave_knowledge.bindings import ScopeBindingStore
        from nexaweave_storage import ProjectStore, SourceStore

        self.principal, self.display_graph_id = settings.principal, settings.display_graph_id
        self.scope_dto, self.root = settings.scope.model_dump(mode='json'), root
        self.store = PreparationStore(connect)
        self.bindings = ScopeBindingStore(connect)
        self.projects, self.sources = ProjectStore(connect), SourceStore(connect)


def _inspect(settings, request, deadline):
    import psycopg

    if settings.scope.layer != 'source' or settings.scope.run_id is not None or settings.scope.branch_id is not None:
        raise PreparationError('unauthorized')
    raw_root = os.environ.get('KNOWLEDGE_PREPARATION_ARTIFACT_ROOT')
    if type(raw_root) is not str or not 1 <= len(raw_root) <= 4096 or '\0' in raw_root:
        raise PreparationError('preparation_unavailable')
    root = Path(raw_root)
    if not root.is_absolute() or str(root) != raw_root or '..' in root.parts:
        raise PreparationError('preparation_unavailable')
    check = PreparationReadAuthority._source_check(deadline)

    def connect():
        check()
        return psycopg.connect(**settings.pg, options='-c default_transaction_read_only=on '
                               '-c statement_timeout=5000 -c lock_timeout=2000')

    host = _ReadAuthority(settings, root, connect)
    method = request['method']
    payload = request['payload']
    result = inspect_prepared(method, host, payload, deadline=deadline)
    check()
    return read_result(method, result, settings.display_graph_id, host.scope_dto, payload)


def main():
    request_id = None
    deadline = time.monotonic() + 15
    try:
        length = int.from_bytes(_read_exact(sys.stdin.buffer, 4), 'big')
        if not 0 < length <= REQUEST_BYTES:
            raise PreparationError('invalid_request')
        raw = _read_exact(sys.stdin.buffer, length)
        if sys.stdin.buffer.read(1):
            raise PreparationError('invalid_request')
        from nexaweave_knowledge.source_library import SourceSettings

        settings = SourceSettings.from_environment()
        request = read_request(raw, settings.scope.model_dump(mode='json'))
        request_id = request['request_id']
        result = _inspect(settings, request, deadline)
        reply = encoded({'version': 1, 'request_id': request_id, 'ok': True, 'result': result})
        if len(reply) > RESULT_BYTES + ENVELOPE_OVERHEAD:
            raise PreparationError('result_too_large')
    except Exception as error:
        code = getattr(error, 'code', None)
        if code not in PROFILE_ERRORS:
            code = 'preparation_unavailable'
        reply = encoded({'version': 1, 'request_id': request_id, 'ok': False, 'error': {'code': code}})
    sys.stdout.buffer.write(len(reply).to_bytes(4, 'big') + reply)
    sys.stdout.buffer.flush()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

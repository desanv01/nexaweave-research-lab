"""Fixed PG-only saved child; admitted frame precedes private manifest access."""
import os
from pathlib import Path
import sys
import time

# Fixed sibling source modules only; installed execution/storage packages remain
# supplied by the selected knowledge interpreter. No repository package injection.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_experiment_http_client import (MAX_REQUEST, MAX_RESULT, OVERHEAD,
    encoded, request_value, project_native, catalog, digest)


def _read(stream, size):
    result = bytearray()
    while len(result) < size:
        part = stream.read(size-len(result))
        if not part:
            raise ValueError
        result.extend(part)
    return bytes(result)


def operation(request):
    if (request['scope']['project_id'] != os.environ['KNOWLEDGE_EXPERIMENT_PROJECT_ID']
            or request['scope']['manifest_sha256'] != os.environ['KNOWLEDGE_EXPERIMENT_MANIFEST_SHA256']):
        raise ValueError
    from native_experiment_contracts import read_manifest, cohort_from_manifest, canonical
    from native_experiments import NativeExperimentComparator, _authorized
    from nexaweave_execution.native_run_store import NativeRunStore
    import psycopg
    principal = os.environ['KNOWLEDGE_PRINCIPAL']
    cohort = cohort_from_manifest(read_manifest(os.environ['KNOWLEDGE_EXPERIMENT_MANIFEST'],
        os.environ['KNOWLEDGE_EXPERIMENT_MANIFEST_SHA256']), principal)
    if any(str(m.request.project_id) != request['scope']['project_id'] or m.request.principal != principal for m in cohort.members):
        raise ValueError
    def connect():
        return psycopg.connect(host=os.environ['KNOWLEDGE_PG_HOST'], port=os.environ['KNOWLEDGE_PG_PORT'],
            dbname=os.environ['KNOWLEDGE_PG_DATABASE'], user=os.environ['KNOWLEDGE_PG_USER'],
            password=os.environ['KNOWLEDGE_PG_PASSWORD'], connect_timeout=3,
            options='-c statement_timeout=5000 -c lock_timeout=3000 -c default_transaction_read_only=on')
    store = NativeRunStore(connect)
    deadline = time.monotonic()+60
    records = []
    for m in cohort.members:
        if time.monotonic() >= deadline:
            raise ValueError
        records.append(_authorized(store, principal, m))
    cat = {'version': 1, 'project_id': request['scope']['project_id'],
        'project_revision': cohort.members[0].request.project_revision,
        'cohort_manifest_digest': cohort.digest, 'members': []}
    for m, record in zip(cohort.members, records):
        cat['members'].append({'member_id': m.member_id, 'member_label': m.member_label,
            'case_label': m.case_label, 'run_id': str(m.request.run_id), 'state': str(record.state),
            'cancel_requested': record.cancel_requested, 'seed': str(m.request.seed),
            'max_rounds': m.request.max_rounds, 'platforms': list(m.request.platforms)})
    cat['public_projection_digest'] = digest(cat)
    catalog(cat, request['scope']['project_id'])
    result = {'manifest_sha256': request['scope']['manifest_sha256'], 'catalog': cat}
    if request['method'] == 'compare':
        native = NativeExperimentComparator(cohort=cohort, connection_factory=connect).compare(canonical(request['payload']))
        # Catalog and comparison must agree; a concurrent transition fails the
        # whole response rather than implying an atomic cohort snapshot.
        result['comparison'] = project_native(native, cat, request['payload'])
    if len(encoded(result)) > MAX_RESULT+OVERHEAD-256:
        raise ValueError
    return result


def main():
    request_id = None
    try:
        length = int.from_bytes(_read(sys.stdin.buffer, 4), 'big')
        if not 0 < length <= MAX_REQUEST+1024:
            raise ValueError
        raw = _read(sys.stdin.buffer, length)
        if sys.stdin.buffer.read(1):
            raise ValueError
        request = request_value(raw)
        request_id = request['request_id']
        result = operation(request)
        response = {'version': 1, 'request_id': request_id, 'ok': True, 'result': result}
    except Exception:
        response = {'version': 1, 'request_id': request_id, 'ok': False,
                    'error': {'code': 'experiment_unavailable'}}
    raw = encoded(response)
    sys.stdout.buffer.write(len(raw).to_bytes(4, 'big')+raw)
    sys.stdout.buffer.flush()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

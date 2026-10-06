"""Pure denial ordering; actual PG cases live in the dedicated authority module."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from app.services.native_observations_client import NativeObservationsError, digest
from app.services.native_observations_facade import NativeObservationsFacade
from app.services.native_observations_host import NativeObservationsHost
from test_native_observations_api import completed, payload, SCOPE


def pure_host(tmp_path):
    """Deliberately unit-only typed launch seam; no production memory fallback."""
    from app.services.durable_native_launch_host import DurableNativeLaunchHost
    launch = object.__new__(DurableNativeLaunchHost)
    launch.principal, launch.display_graph_id, launch.scope_dto = 'owner', 'display-1', deepcopy(SCOPE)
    dto, manifest = completed()
    request = SimpleNamespace(platforms=('twitter', 'reddit'), fingerprint=digest(dto['request']),
                              run_id=UUID(dto['request']['run_id']))
    dto['workflow'] = {'workflow_id': 'mf-native-v1-' + request.run_id.hex + '-' + request.fingerprint,
                      'temporal_run_id': str(uuid4()), 'native_run_id': str(request.run_id)}
    identity = {k: deepcopy(dto[k]) for k in ('schema_version', 'display_graph_id', 'scope', 'preparation',
                'request', 'limits', 'ceiling_microusd', 'model_label')}
    row = SimpleNamespace(frozen={'identity': identity}, request=request, run_id=request.run_id,
        launch_sha256=dto['launch_sha256'], state='completed', receipt=deepcopy(dto['receipt']),
        dispatch_claimed=True, budget_attempt_id=uuid4(), workflow=dto['workflow'], error_code=None, cancel_requested=False)
    prep = SimpleNamespace(operation_id=UUID(dto['preparation']['operation_id']), project_revision=1,
        frozen={'public': {'source': {'source_revision': str(UUID(int=8)), 'source_sha256': 'e' * 64}}},
        receipt={'opaque': 'unit READY receipt'})
    native = SimpleNamespace(request=request, fingerprint=request.fingerprint, state=SimpleNamespace(value='completed'),
        owner_id=uuid4(), attempt_id=UUID(row.receipt['attempt_id']), child=SimpleNamespace(instance_id=UUID(row.receipt['instance_id'])),
        receipt=SimpleNamespace(attempt_id=UUID(row.receipt['attempt_id']), instance_id=UUID(row.receipt['instance_id']),
                                to_wire=lambda: deepcopy(row.receipt)))
    calls = []
    launch._row = lambda ref: row
    launch._current = lambda value: (prep, {})
    launch.authorization = lambda value: {'model_calls_enabled': False}
    launch.native = SimpleNamespace(get=lambda principal, run: native)
    launch.preparation = SimpleNamespace(root=tmp_path,
        _owned=lambda source, expected_revision=None: (None, None, SimpleNamespace(text_sha256='e' * 64)),
        _artifacts=lambda value, root: calls.append('inputs') or deepcopy(prep.receipt))
    def forbidden(*args, **kwargs): pytest.fail('observations invoked a mutation/model/runtime seam')
    launch._recover = launch._dto = launch.start = launch.cancel = launch.status = forbidden
    launch.model_factory = launch.temporal_call = forbidden
    launch.budget = SimpleNamespace(reserve_native=forbidden, settle_native=forbidden)
    host = NativeObservationsHost(launch_host=launch)
    class Reader:
        @contextmanager
        def page(self, *args):
            calls.append('outputs')
            yield {'manifest': manifest, 'platform': 'twitter', 'offset': 0, 'limit': 20,
                'total_records': 0, 'next_offset': None, 'counts': {'event_records': 0, 'action_records': 0,
                'successful_action_records': 0, 'failed_action_records': 0}, 'records': []}
    host.reader = Reader()
    return host, row, prep, native, calls


def test_completed_disabled_read_never_recovers_polls_or_mutates(tmp_path):
    host, row, _, _, calls = pure_host(tmp_path)
    result = host.page(payload())
    assert result['launch']['receipt'] == row.receipt and not result['launch']['authorization']['model_calls_enabled']
    assert result['launch']['cleanup'] == {'known': False, 'pending': None, 'owner_thread_alive': None}
    assert calls == ['inputs', 'outputs', 'inputs']


@pytest.mark.parametrize('state', ['planned', 'queued', 'starting', 'running', 'failed', 'cancelled', 'uncertain'])
def test_every_noncompleted_state_before_files(tmp_path, state):
    host, row, _, _, calls = pure_host(tmp_path); row.state = state
    with pytest.raises(NativeObservationsError) as error: host.page(payload())
    assert error.value.code == 'conflict' and calls == []


@pytest.mark.parametrize('fault', ['dispatch', 'budget', 'workflow', 'receipt', 'owner', 'child', 'attempt', 'native-state',
    'fingerprint', 'source', 'platform'], ids=['dispatch', 'budget', 'workflow', 'receipt', 'owner', 'child', 'attempt', 'state', 'fingerprint', 'source', 'platform'])
def test_identity_refusals_before_files(tmp_path, fault):
    host, row, prep, native, calls = pure_host(tmp_path)
    if fault == 'dispatch': row.dispatch_claimed = False
    if fault == 'budget': row.budget_attempt_id = None
    if fault == 'workflow': row.workflow = None
    if fault == 'receipt': row.receipt = None
    if fault == 'owner': native.owner_id = None
    if fault == 'child': native.child = None
    if fault == 'attempt': native.attempt_id = uuid4()
    if fault == 'native-state': native.state.value = 'running'
    if fault == 'fingerprint': native.fingerprint = '0' * 64
    if fault == 'source': prep.frozen['public']['source']['source_sha256'] = '0' * 64
    if fault == 'platform': row.request.platforms = ('reddit',)
    with pytest.raises(NativeObservationsError) as error: host.page(payload())
    assert error.value.code == 'conflict' and not calls


@pytest.mark.parametrize('code', ['not_found', 'unauthorized', 'tombstoned', 'conflict'])
def test_authority_denial_before_inputs(tmp_path, code):
    host, _, _, _, calls = pure_host(tmp_path)
    def deny(*args): raise NativeObservationsError(code)
    host.launch._current = deny
    with pytest.raises(NativeObservationsError) as error: host.page(payload())
    assert error.value.code == code and not calls


def test_reauthorization_after_read_blocks_stale_source(tmp_path):
    host, _, prep, _, calls = pure_host(tmp_path)
    class Reader:
        @contextmanager
        def page(self, *args):
            prep.frozen['public']['source']['source_sha256'] = '0' * 64
            yield {}
    host.reader = Reader()
    with pytest.raises(NativeObservationsError) as error: host.page(payload())
    assert error.value.code == 'conflict' and calls == ['inputs']


def test_admission_four_calls_and_exception_release(tmp_path):
    host, _, _, _, _ = pure_host(tmp_path)
    settings = SimpleNamespace(principal='owner', display_graph_id='display-1', scope=deepcopy(SCOPE))
    facade = NativeObservationsFacade(settings, host=host)
    for _ in range(4): assert facade._calls.acquire(blocking=False)
    with pytest.raises(NativeObservationsError) as busy: facade.execute('display-1', payload())
    assert busy.value.code == 'busy'
    for _ in range(4): facade._calls.release()
    original = host.page
    def fail(request): raise RuntimeError('PRIVATE SQL path')
    host.page = fail
    with pytest.raises(NativeObservationsError) as error: facade.execute('display-1', payload())
    assert str(error.value) == 'internal_error'
    host.page = original
    assert facade.execute('display-1', payload())['total_records'] == 0
    settings.principal = 'other'
    with pytest.raises(NativeObservationsError) as error: facade.execute('display-1', payload())
    assert error.value.code == 'observations_unavailable'

"""Explicit real-PG observations authority; cold isolated collection."""
from contextlib import contextmanager
from uuid import uuid4

import pytest


@pytest.fixture(scope='module')
def pg_factory():
    # Lazy so generic collection has no native/Temporal/CAMEL imports or opt-in skips.
    from test_native_launch_store import factory
    return factory.__wrapped__()


@pytest.mark.postgres
@pytest.mark.parametrize('fault', ['planned', 'wrong-owner', 'digest', 'revision', 'tombstone'],
                         ids=['planned', 'owner', 'digest', 'revision', 'tombstone'])
def test_real_pg_current_authority_denies_before_reader(pg_factory, tmp_path, fault):
    from app.services.native_observations_host import NativeObservationsHost
    from test_native_observations_api import payload
    from test_native_launch_store import ready_host, launch_host, declaration
    from nexaweave_storage import ProjectStore
    from nexaweave_knowledge.operations import Ledger
    from test_project_store import snapshot
    prep, scope, plan = ready_host(pg_factory, tmp_path)
    launch = launch_host(prep, pg_factory, enabled=False)
    dto = launch.plan(declaration(plan))
    host = NativeObservationsHost(launch_host=launch)
    class NoReader:
        def page(self, *args): pytest.fail('PG authority denial reached output reader')
    host.reader = NoReader()
    prep._artifacts = lambda *args: pytest.fail('PG authority denial reached READY files')
    request = payload(dto)
    if fault == 'wrong-owner': launch.principal = 'other'
    if fault == 'digest': request['launch_sha256'] = '0' * 64
    if fault == 'revision': ProjectStore(pg_factory).update('owner', scope.project_id, 1, snapshot())
    if fault == 'tombstone': Ledger(pg_factory).tombstone_scope(scope)
    with pytest.raises(Exception) as error: host.page(request)
    expected = {'planned': 'conflict', 'wrong-owner': 'not_found', 'digest': 'conflict', 'revision': 'conflict', 'tombstone': 'tombstoned'}
    assert getattr(error.value, 'code', None) == expected[fault]
    with pg_factory() as conn:
        assert conn.execute('SELECT count(*) FROM mf_native_execution.runs WHERE project_id=%s', (scope.project_id,)).fetchone()[0] == 0


@pytest.mark.postgres
def test_real_pg_project_revision_change_inside_reader_refuses_page(pg_factory, tmp_path):
    from app.services.native_observations_host import NativeObservationsHost
    from nexaweave_storage import ProjectStore
    from test_native_launch_store import _terminal_source, declaration, launch_host, ready_host
    from test_native_observations_api import payload

    prep, scope, plan = ready_host(pg_factory, tmp_path, checkpoint=True)
    launch = launch_host(prep, pg_factory)
    dto = launch.plan(declaration(plan))
    template = launch.store.get('owner', dto['request']['run_id'])
    completed = _terminal_source(pg_factory, prep, scope, launch, template, capture=False)
    request = completed.request
    row = launch.store.workflow('owner', completed.run_id, completed.launch_sha256, {
        'workflow_id': 'mf-native-v1-' + request.run_id.hex + '-' + request.fingerprint,
        'temporal_run_id': str(uuid4()),
        'native_run_id': str(request.run_id),
    })
    native = launch.native.get('owner', row.run_id)
    assert row.state == 'completed' and native.receipt.to_wire() == row.receipt
    projects = ProjectStore(pg_factory)
    current = projects.get('owner', scope.project_id)
    assert current.revision == row.project_revision
    changed = current.snapshot
    changed['name'] += ' (updated during observations read)'

    host = NativeObservationsHost(launch_host=launch)
    events, refusals = [], []
    real_authority = host._authority

    def trace_authority(value):
        try:
            result = real_authority(value)
        except Exception as error:
            events.append('authority-refused')
            refusals.append(error)
            raise
        events.append('authority-admitted')
        return result

    class Reader:
        @contextmanager
        def page(self, root, platforms, platform, offset, limit, evidence_sha256):
            assert events == ['authority-admitted']
            assert root == prep.root / request.simulation_id
            assert (platforms, platform, offset, limit) == (('twitter',), 'twitter', 0, 20)
            assert evidence_sha256 == native.receipt.evidence_sha256
            events.append('reader-opened')
            try:
                saved = projects.update('owner', scope.project_id, current.revision, changed, current.evidence)
                assert projects.get('owner', scope.project_id).revision == saved.revision == current.revision + 1
                events.append('revision-committed')
                # A sentinel helps distinguish refusal before reply validation
                # from an error raised by validating a reader result.
                yield object()
            finally:
                events.append('reader-closed')

    host._authority = trace_authority
    host.reader = Reader()
    with pytest.raises(Exception) as error:
        host.page(payload({'request': request.to_wire(), 'launch_sha256': row.launch_sha256}))
    assert getattr(error.value, 'code', None) == 'conflict' and refusals == [error.value]
    assert events == ['authority-admitted', 'reader-opened', 'revision-committed',
                      'authority-refused', 'reader-closed']
    assert launch.store.get('owner', row.run_id) == row
    assert launch.native.get('owner', row.run_id) == native

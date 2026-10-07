"""Explicit real-PG observations authority; cold isolated collection."""
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

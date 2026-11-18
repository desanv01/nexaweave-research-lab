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


@pytest.mark.postgres
def test_real_pg_receipt_event_stage_replay_and_revision_before_insert(pg_factory, tmp_path, monkeypatch):
    from hashlib import sha256
    from app import create_app
    from app.services.knowledge_read_facade import ReadHostSettings
    from app.services.native_observations_client import digest, encoded, stage_operation_id
    from app.services.native_observations_facade import NativeObservationsFacade
    from app.services.native_observations_host import NativeObservationsHost
    from nexaweave_execution.native_run_contracts import NativeChildIdentity, NativeRunReceipt
    from nexaweave_knowledge.contracts import Layer
    from nexaweave_knowledge.operations import Ledger
    from nexaweave_storage import ProjectStore
    from test_native_launch_store import (
        _continuation, _put_authority, _terminal_source, _twitter_authority,
        declaration, launch_host, ready_host,
    )

    token = '0123456789abcdef' * 4
    monkeypatch.setenv('NEXAWEAVE_APP_MODE', 'research_local')
    monkeypatch.delenv('FLASK_HOST', raising=False)
    monkeypatch.delenv('NEXAWEAVE_ALLOWED_ORIGINS', raising=False)
    auth = {'Authorization': 'Bearer ' + token}

    def seed(root):
        root.mkdir()
        prep, scope, plan = ready_host(pg_factory, root, checkpoint=True)
        launch = launch_host(prep, pg_factory)
        dto = launch.plan(declaration(plan))
        template = launch.store.get('owner', dto['request']['run_id'])
        producer = _terminal_source(pg_factory, prep, scope, launch, template, capture=True)
        frozen = _twitter_authority(template, _continuation(producer))
        planned = _put_authority(launch.store, frozen)
        req = planned.request

        # The store fixture assumes the native receipts. The restored output
        # manifest and records still pass the real bounded observation reader.
        prior = {'round': 1, 'action_type': 'CREATE_POST', 'success': True,
                 'action_args': {'content': 'copied prefix', 'post_id': 1}}
        action = {'round': 2, 'action_type': 'CREATE_POST', 'success': True,
                  'action_args': {'content': 'restored action', 'post_id': 2}}
        samples = tuple(encoded(event) for event in (
            {'round': 1, 'event_type': 'round_start'}, prior,
            {'round': 1, 'event_type': 'round_end', 'actions_count': 1},
            {'round': 2, 'event_type': 'round_start'}, action,
            {'round': 2, 'event_type': 'round_end', 'actions_count': 1},
        ))
        files = {
            'native.sqlite3': b'Assumed owned native database fixture',
            'actions.jsonl': b'\n'.join(samples) + b'\n',
        }
        output = prep.root / ('native_child_' + req.run_id.hex)
        output.mkdir()
        manifest = {'schema_version': 1, 'files': [
            {'name': name, 'sha256': sha256(raw).hexdigest(), 'size': len(raw)}
            for name, raw in files.items()
        ]}
        for name, raw in files.items():
            (output / name).write_bytes(raw)

        reservation = launch.budget.reserve_native(
            'owner', prep.account_id, scope, req, planned.launch_sha256, 4)
        queued, won = launch.store.queue('owner', req.run_id, planned.launch_sha256, reservation.attempt_id)
        assert won and queued.dispatch_claimed
        launch.native.register(req)
        owner = uuid4()
        claim = launch.native.claim_start('owner', req.run_id, owner)
        child = NativeChildIdentity(uuid4(), 12345, 'f' * 64)
        launch.native.attach('owner', req.run_id, claim.attempt_id, owner, child)
        receipt = NativeRunReceipt(req.run_id, claim.attempt_id, child.instance_id,
                                   req.fingerprint, 'completed', digest(manifest))
        terminal = launch.native.settle('owner', req.run_id, claim.attempt_id, owner, receipt)
        completed = launch.store.observe('owner', req.run_id, planned.launch_sha256, terminal)
        launch.store.workflow('owner', req.run_id, completed.launch_sha256, {
            'workflow_id': 'mf-native-v1-' + req.run_id.hex + '-' + req.fingerprint,
            'temporal_run_id': str(uuid4()), 'native_run_id': str(req.run_id),
        })

        settings = ReadHostSettings('python', 'read_bootstrap.py', token, 'owner',
                                    launch.display_graph_id, launch.scope_dto, {})
        with monkeypatch.context() as patch:
            patch.setattr(ReadHostSettings, 'from_config', classmethod(lambda _cls, _config: settings))
            app = create_app(native_observations_facade=NativeObservationsFacade(
                settings, host=NativeObservationsHost(launch_host=launch)))
        route = '/api/native-observations/stage-event/' + launch.display_graph_id
        payload = {'schema_version': 2, 'launch_id': str(req.run_id), 'launch_sha256': completed.launch_sha256,
                   'platform': 'twitter', 'record_index': 4, 'record_sha256': sha256(samples[4]).hexdigest()}
        return app.test_client(), route, payload, scope.model_copy(update={
            'layer': Layer.simulation, 'run_id': req.run_id,
        }), sha256(samples[1]).hexdigest()

    client, route, request, scope, prefix_hash = seed(tmp_path / 'stage')
    assert client.post(route, json=request).status_code == 401
    assert client.post(route, json=dict(request, record_sha256='0' * 64), headers=auth).status_code == 409
    assert client.post(route, json=dict(request, record_index=1, record_sha256=prefix_hash),
                       headers=auth).status_code == 409
    staged = client.post(route, json=request, headers=auth)
    assert staged.status_code == 200 and staged.headers['Cache-Control'] == 'no-store'
    data = staged.json['data']
    operation = stage_operation_id(request['launch_id'], request['record_index'])
    assert data == dict(request, scope=scope.model_dump(mode='json'), operation_id=str(operation),
                        fingerprint=data['fingerprint'], state='pending',
                        completion_barrier_open=False, graph_ingestion_executed=False)
    replay = client.post(route, json=request, headers=auth)
    assert replay.status_code == 200 and replay.json == staged.json
    saved = Ledger(pg_factory).get(scope, operation)
    assert saved.state.value == 'pending' and saved.fingerprint == data['fingerprint']
    assert saved.current_attempt is None and saved.receipt is None

    stale_client, stale_route, stale_request, stale_scope, _ = seed(tmp_path / 'stale')
    projects = ProjectStore(pg_factory)
    current = projects.get('owner', stale_scope.project_id)
    original_admit = Ledger.admit
    advanced = []

    def advance_before_insert(self, actual_scope, operation_id, fingerprint, *, authorize_admission):
        assert actual_scope == stale_scope and not advanced
        snapshot = current.snapshot
        snapshot['name'] += ' (advanced before event admission)'
        advanced.append(projects.update('owner', stale_scope.project_id, current.revision, snapshot, current.evidence))
        return original_admit(self, actual_scope, operation_id, fingerprint,
                              authorize_admission=authorize_admission)

    with monkeypatch.context() as patch:
        patch.setattr(Ledger, 'admit', advance_before_insert)
        stale = stale_client.post(stale_route, json=stale_request, headers=auth)
    assert len(advanced) == 1 and advanced[0].revision == current.revision + 1
    assert stale.status_code == 409 and stale.json == {'success': False, 'error': {'code': 'conflict'}}
    with pg_factory() as conn:
        assert conn.execute(
            'SELECT group_id, operation_id FROM mf_knowledge.operations WHERE group_id IN (%s,%s)',
            (scope.group_id, stale_scope.group_id),
        ).fetchall() == [(scope.group_id, operation)]
        assert conn.execute(
            'SELECT count(*) FROM mf_execution.reservations WHERE operation_id IN (%s,%s)',
            (operation, stage_operation_id(stale_request['launch_id'], stale_request['record_index'])),
        ).fetchone() == (0,)

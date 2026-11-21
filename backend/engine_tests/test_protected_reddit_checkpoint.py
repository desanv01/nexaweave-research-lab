"""Protected Reddit READY, capture and fresh continuation with actual owned children."""
import asyncio
from hashlib import sha256
import json
from multiprocessing import active_children
import os
from pathlib import Path
from time import monotonic
from uuid import uuid4

import pytest

from app import create_app
from app.services.durable_native_launch_host import DurableNativeLaunchHost
from app.services.knowledge_preparation_facade import KnowledgePreparationFacade
from app.services.knowledge_read_facade import ReadHostSettings
from app.services.native_launch_facade import NativeLaunchFacade
from app.services.native_launch_models import BoundedNativeModelFactory
from native_reddit_checkpoint_fixture import (
    _COMMENT_TICK, _DB, _EPOCH, _MANIFEST, _NAMES, _SEED_TICK, _SIGNUP_TICK, _json, _pin, _read, _rows,
)
from nexaweave_execution.native_launch_contracts import NativeBudgetReceipt
from nexaweave_execution.native_owned_binding import _manifest
from nexaweave_execution.native_reddit_checkpoint_binding import NativeRedditCheckpointFactory
from nexaweave_execution.native_run_contracts import RunState
from nexaweave_execution.preparation_contracts import PreparedBudgetReceipt
from nexaweave_execution.temporal_native_host import (
    NativeTemporalHostError, NativeWorkflowRef, TemporalNativeHost,
)
from nexaweave_execution.temporal_preparation_host import TemporalPreparationHost
from test_connected_preparation_native_launch import RichConnectedChat
from test_durable_native_launch import LIMITS, scripted_native_backends
from test_native_launch_api import reference
from test_native_launch_store import declaration, factory, migrate_all
from test_preparation_store import real_host, reference as preparation_reference, request
from test_temporal_connected_launch import loopback_client, temporal_bridge


def _reddit_backends(**kwargs):
    # Picklable factory used by the normal bounded model binding in the child.
    model = scripted_native_backends(**kwargs)['reddit']

    def unexpected(_tools):
        raise AssertionError('fixed Reddit checkpoint actions must make no model calls')

    model.reply = unexpected
    return {'reddit': model}


@pytest.mark.postgres
@pytest.mark.native_launch_engine
@pytest.mark.asyncio
async def test_protected_reddit_capture_and_fresh_continuation(factory, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('NEXAWEAVE_APP_MODE', 'research_local')
    monkeypatch.delenv('FLASK_HOST', raising=False)
    monkeypatch.delenv('NEXAWEAVE_ALLOWED_ORIGINS', raising=False)
    migrate_all(factory)
    deadline = monotonic() + 240

    def remaining(limit):
        available = min(limit, deadline - monotonic() - 20)
        assert available > 0, 'protected Reddit case reached its cleanup reserve'
        return available

    temporal = await loopback_client()
    loop = asyncio.get_running_loop()
    prep, scope, retained, chat, _, transport = real_host(
        factory, tmp_path, cap=12, chat=RichConnectedChat(),
    )
    prep_worker = TemporalPreparationHost(
        client=temporal, task_queue='mf-reddit-prep-' + uuid4().hex,
        trusted_host=prep, allow_dispatch=lambda: True,
    )
    prep.scheduler = prep_worker.scheduler_for(loop)
    model = BoundedNativeModelFactory(('reddit',), dict(LIMITS), _reddit_backends, cooperative_transport=True)
    host = DurableNativeLaunchHost(
        preparation_host=prep, connection_factory=factory, runtime_sha256='c' * 64,
        model_label='scripted Reddit checkpoint', limits=LIMITS, model_factory=model,
        account_id=prep.account_id, ceiling_microusd=4, authorize=lambda: True,
    )
    supervisors, exited, preterminal = {}, {}, {}

    def construct(native_request):
        assert native_request.run_id not in supervisors
        supervisor = host.supervisor_factory(native_request)
        supervisors[native_request.run_id] = supervisor
        driver = supervisor.coordinator.driver
        assert type(driver._factory) is NativeRedditCheckpointFactory
        observe = driver.observe

        def observe_exit(req, attempt, child):
            result = observe(req, attempt, child)
            if result.status == 'completed':
                owned = driver._owned
                assert owned is not None and owned.child == child and owned.sent_go
                assert owned.process.pid == child.process_id and owned.process.exitcode == 0
                assert req.run_id not in exited
                running = host.native.get('owner', req.run_id)
                assert running.state == RunState.running and running.child == child and running.receipt is None
                preterminal[req.run_id] = running
                exited[req.run_id] = (child, result.receipt)
            return result

        driver.observe = observe_exit
        return supervisor

    native = TemporalNativeHost(
        client=temporal, task_queue='mf-reddit-native-' + uuid4().hex, trusted_principal='owner',
        supervisor_factory=construct, allow_dispatch=lambda: True,
    )
    bridge = temporal_bridge(native, loop)
    dispatched = []

    def call(method, native_request, ref):
        if method == 'start':
            dispatched.append(native_request.run_id)
        return bridge(method, native_request, ref)

    host.attach_temporal(native, call)
    monkeypatch.setattr(ReadHostSettings, 'from_config', classmethod(lambda cls, config: prep.settings))
    http = create_app(
        preparation_facade=KnowledgePreparationFacade(prep.settings, host=prep),
        native_launch_facade=NativeLaunchFacade(prep.settings, host=host),
    ).test_client()
    headers = {'Authorization': 'Bearer ' + prep.settings.token}
    graph = prep.display_graph_id
    launched = []

    async def post(route, payload):
        response = await asyncio.wait_for(
            asyncio.to_thread(http.post, route, json=payload, headers=headers), remaining(30),
        )
        assert response.status_code == 200
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.json['success'] is True
        return response.json['data']

    async def launch(dto):
        ref = reference(dto)
        planned = host.store.get('owner', ref['launch_id'], ref['launch_sha256'])
        launched.append(planned.request)
        start = await post(f'/api/native-launch/start/{graph}', ref)
        assert reference(start) == ref and start['checkpoint'] == dto['checkpoint']
        row = host.store.get('owner', ref['launch_id'], ref['launch_sha256'])
        assert row.request == planned.request
        receipt = await asyncio.wait_for(native.result(row.request, NativeWorkflowRef(**row.workflow)), remaining(90))
        cleanup = await asyncio.wait_for(native.retry_cleanup(row.request), remaining(20))
        assert not cleanup.cleanup_pending and not cleanup.owner_thread_alive
        final = await post(f'/api/native-launch/status/{graph}', ref)
        saved = host.native.get('owner', row.run_id)
        settled = host.store.get('owner', row.run_id, row.launch_sha256)
        assert receipt.outcome == 'completed' and saved.state == RunState.completed
        assert final['state'] == settled.state == 'completed' and reference(final) == ref
        assert final['checkpoint'] == dto['checkpoint'] and final['request'] == saved.request.to_wire()
        assert final['receipt'] == settled.receipt == receipt.to_wire() and saved.receipt == receipt
        assert host.store.observe('owner', row.run_id, row.launch_sha256, preterminal[row.run_id]) == settled
        assert host.store.get('owner', row.run_id, row.launch_sha256) == settled
        assert saved.request == row.request and saved.fingerprint == row.request.fingerprint
        assert saved.child.instance_id == receipt.instance_id and saved.attempt_id == receipt.attempt_id
        assert exited[row.run_id] == (saved.child, receipt)
        assert final['cleanup'] == {'known': True, 'pending': False, 'owner_thread_alive': False}
        with factory() as conn:
            budget = host.budget._row(conn, host.account_id, row.run_id)
        assert budget.state.value == 'settled' and budget.attempt_id == row.budget_attempt_id == settled.budget_attempt_id
        assert type(budget.receipt) is NativeBudgetReceipt
        assert budget.receipt.launch_sha256 == row.launch_sha256 and budget.receipt.native_receipt == receipt
        driver = supervisors[row.run_id].coordinator.driver
        assert driver._closed and driver._owned is None
        return saved, driver._factory, budget

    async def drain_entry(native_request):
        try:
            cleanup = await asyncio.wait_for(native.retry_cleanup(native_request), 20)
        except NativeTemporalHostError as error:
            if error.code != 'native_entry_unavailable' or native_request.run_id in supervisors:
                raise
        else:
            assert not cleanup.cleanup_pending and not cleanup.owner_thread_alive

    primary_error = None
    try:
        async with prep_worker.worker(), native.worker():
            seed_post = {'poster_agent_id': 0, 'content': 'One retained Reddit opening.'}
            comment = 'One fresh comment after restoration.'
            prep_payload = request(retained)
            prep_payload.update(schema_version=2, source_revisions=[str(retained.source_revision)])
            prep_payload['options'].update(
                platforms=['reddit'], types=['Person', 'Organization'], seed=7, max_agents=2, max_rounds=1,
                reddit_checkpoint={'comment': comment, 'seed_post': seed_post},
            )
            planned = await post(f'/api/preparation/{graph}/plan', prep_payload)
            prep_ref = preparation_reference(planned)
            await post(f'/api/preparation/{graph}/start', prep_ref)
            prep_row = prep.store.get('owner', planned['operation_id'])
            await asyncio.wait_for(temporal.get_workflow_handle(prep_row.dispatch.workflow_id).result(), remaining(60))
            ready = await post(f'/api/preparation/{graph}/status', prep_ref)
            ready_row = prep.store.get('owner', planned['operation_id'], planned['plan_sha256'])
            assert ready['state'] == ready_row.state == 'ready' and not ready['simulation_executed']
            assert ready['receipt'] == ready_row.receipt and ready['options'] == prep_payload['options']
            assert len(ready['actors']) == 2
            root = prep.root / ready['receipt']['simulation_id']
            receipt_files = ready['receipt']['files']
            assert tuple(file['name'] for file in receipt_files) == _NAMES
            originals = {name: _read(root / name, 2 * 1024 * 1024) for name in _NAMES}
            for file in receipt_files:
                raw = originals[file['name']]
                assert (file['size'], file['sha256']) == (len(raw), sha256(raw).hexdigest())
            artifact = ready['receipt']['artifact_sha256']
            assert artifact == _manifest(root, _NAMES, 2 * 1024 * 1024)
            config = json.loads(originals['simulation_config.json'])
            profiles = json.loads(originals['reddit_profiles.json'])
            assert len(profiles) == len(config['agent_configs']) == 2
            assert config['event_config']['initial_posts'] == [seed_post]
            assert config['reddit_checkpoint_comment'] == comment
            with factory() as conn:
                prep_budget = host.budget._row(conn, host.account_id, ready_row.operation_id)
            assert prep_budget.state.value == 'settled' and type(prep_budget.receipt) is PreparedBudgetReceipt
            assert prep_budget.receipt.artifact_sha256 == artifact
            assert host.budget.status('owner', host.account_id).accounted_ceiling_microusd == 4
            prepared_calls = (len(chat.calls), transport.calls)

            capture = await post(f'/api/native-launch/plan/{graph}', {
                **declaration(planned), 'schema_version': 4, 'checkpoint': {'mode': 'capture'},
            })
            assert capture['request']['artifact_sha256'] == artifact
            assert capture['request']['platforms'] == ['reddit'] and capture['request']['schema_version'] == 1
            assert not supervisors and not (root / '.native_prepared_start_claim').exists()
            producer, producer_factory, producer_budget = await launch(capture)
            checkpoint = Path(producer_factory.checkpoint_dir)
            output = Path(producer_factory.output_dir)
            assert checkpoint.parent == output.parent == root.parent
            assert len({checkpoint, output, root}) == 3
            assert producer.request.project_id == scope.project_id
            assert producer.child.process_id != os.getpid()
            assert {path.name for path in checkpoint.iterdir()} == {_MANIFEST, _DB}
            assert {path.name for path in output.iterdir()} == {_DB}
            manifest_raw = _read(checkpoint / _MANIFEST, 7200)
            manifest = json.loads(manifest_raw)
            assert manifest_raw == _json(manifest)
            digest = sha256(manifest_raw).hexdigest()
            assert producer.receipt.evidence_sha256 == digest
            actor = producer.request.seed % len(profiles)
            captured_db = _pin(checkpoint / _DB)
            assert manifest == {
                'version': 1, 'platform': 'reddit', 'producer_pid': producer.child.process_id,
                'owner': {'graph_id': graph, 'request': producer.request.to_wire(),
                          'request_fingerprint': producer.request.fingerprint},
                'config_sha256': sha256(originals['simulation_config.json']).hexdigest(),
                'database': captured_db, 'trace_cursor': 3,
                'clock': {'epoch': _EPOCH, 'elapsed_seconds': 60}, 'actor_ids': [0, 1],
                'next_command': {'actor_id': actor, 'post_id': 1, 'action': 'create_comment'},
            }
            assert _pin(output / _DB) == captured_db
            assert not (output / _DB).samefile(checkpoint / _DB)
            users, posts, comments, trace, recs = before = _rows(checkpoint / _DB)
            assert _rows(output / _DB) == before
            assert users == [(i, i, profile['username'], _SIGNUP_TICK) for i, profile in enumerate(profiles)]
            assert posts == [(1, 0, seed_post['content'], _SEED_TICK)] and comments == recs == []
            assert trace == [
                (i + 1, i, 'sign_up', {'name': profile['username'], 'user_name': None,
                                       'bio': profile['bio']}, _SIGNUP_TICK)
                for i, profile in enumerate(profiles)
            ] + [(3, 0, 'create_post', {'content': seed_post['content'], 'post_id': 1}, _SEED_TICK)]

            selector = {'mode': 'continue', 'source_run_id': str(producer.request.run_id), 'checkpoint_sha256': digest}
            continuation = await post(f'/api/native-launch/plan/{graph}', {
                **declaration(planned), 'schema_version': 4, 'checkpoint': selector,
            })
            assert continuation['checkpoint'] == selector
            assert continuation['request'] == {
                **producer.request.to_wire(), 'schema_version': 4, 'run_id': continuation['request']['run_id'],
                'source_run_id': selector['source_run_id'], 'checkpoint_sha256': digest,
            }
            restored, restore_factory, restore_budget = await launch(continuation)
            child = Path(restore_factory.output_dir)
            assert child.parent == root.parent and child not in {root, checkpoint, output}
            assert Path(restore_factory.checkpoint_dir) == checkpoint and restore_factory.producer == producer.child
            assert restored.request.run_id != producer.request.run_id
            assert restored.child.instance_id != producer.child.instance_id and restored.owner_id != producer.owner_id
            pids = {producer.child.process_id, restored.child.process_id}
            assert len(pids) == 2 and os.getpid() not in pids
            assert not pids & {process.pid for process in active_children()}
            assert {path.name for path in child.iterdir()} == {_DB}
            assert not any((child / _DB).samefile(directory / _DB) for directory in (checkpoint, output))
            assert restored.receipt.evidence_sha256 == _pin(child / _DB)['sha256']
            restored_users, restored_posts, restored_comments, restored_trace, restored_recs = _rows(child / _DB)
            assert (restored_users, restored_posts) == (users, posts)
            assert restored_comments == [(1, 1, actor, comment, _COMMENT_TICK)]
            assert restored_trace == trace + [(4, actor, 'create_comment', {'comment_id': 1, 'content': comment}, _COMMENT_TICK)]
            assert restored_recs == [(0, 1), (1, 1)]

            replay = await post(f'/api/native-launch/start/{graph}', reference(continuation))
            assert replay['state'] == 'completed' and replay['receipt'] == restored.receipt.to_wire()
            assert dispatched == [producer.request.run_id, restored.request.run_id]
            assert set(supervisors) == set(exited) == set(dispatched)
            assert host.budget.status('owner', host.account_id).accounted_ceiling_microusd == 12
            with factory() as conn:
                assert host.budget._row(conn, host.account_id, producer.request.run_id) == producer_budget
                assert host.budget._row(conn, host.account_id, restored.request.run_id) == restore_budget
            assert host.native.get('owner', producer.request.run_id) == producer
            assert host.native.get('owner', restored.request.run_id) == restored
            assert _read(checkpoint / _MANIFEST, 7200) == manifest_raw
            assert _pin(checkpoint / _DB) == _pin(output / _DB) == captured_db
            assert {name: _read(root / name, 2 * 1024 * 1024) for name in _NAMES} == originals
            assert not (root / '.native_prepared_start_claim').exists()
            assert prep.store.get('owner', ready_row.operation_id) == ready_row
            assert (len(chat.calls), transport.calls) == prepared_calls
            assert monotonic() < deadline
    except BaseException as error:
        primary_error = error
        raise
    finally:
        closed = await asyncio.gather(
            *(asyncio.to_thread(supervisor.close, 20) for supervisor in supervisors.values()),
            return_exceptions=True,
        )
        drained = await asyncio.gather(
            *(drain_entry(native_request) for native_request in launched), return_exceptions=True,
        )
        if any(result is not True for result in closed) or any(result is not None for result in drained):
            if primary_error is not None:
                primary_error.add_note('A protected Reddit fixture owner did not close cleanly.')
            else:
                pytest.fail('A protected Reddit fixture owner did not close cleanly.')

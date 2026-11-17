"""Actual new U07c publication → PG/Temporal → owned both-platform OASIS.

Scripted inherited rich persona/config and CAMEL responses, zero paid calls.
No replacement prepared files or unrelated experiment-manifest run.
"""
import asyncio
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from itertools import islice
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4
import pytest
from nexaweave_execution.temporal_preparation_host import TemporalPreparationHost
from nexaweave_execution.temporal_native_host import TemporalNativeHost,NativeWorkflowRef
from nexaweave_execution.native_run_store import NativeRunStore
from nexaweave_execution.native_launch_store import NativeLaunchStore
from nexaweave_execution.native_launch_contracts import NativeBudgetReceipt
from test_provider_neutral_preparation import ScriptedChat
from test_preparation_store import real_host,request,reference as preparation_reference
from test_native_launch_store import factory,migrate_all,launch_host,declaration
from test_native_launch_api import reference
from test_temporal_connected_launch import loopback_client,temporal_bridge
from native_twitter_fresh_fixture import _checkpoint_post, _scripted_checkpoint_twitter_backends

pytestmark=[pytest.mark.postgres,pytest.mark.native_launch_engine]


class RichConnectedChat(ScriptedChat):
    """Inherited generators still interpret/validate all fields and write files."""
    def create(self,**kwargs):
        reply=super().create(**kwargs)
        prompt=kwargs['messages'][-1]['content']
        if 'agent_configs' in prompt:
            payload={'agent_configs':[{'agent_id':index,'activity_level':1.0,'posts_per_hour':1.0,
                'comments_per_hour':0.0,'active_hours':list(range(24)),'response_delay_min':0,'response_delay_max':0,
                'sentiment_bias':0,'stance':'neutral','influence_weight':1.0} for index in range(2)]}
        elif 'total_simulation_hours' in prompt:
            payload={'total_simulation_hours':1,'minutes_per_round':60,'agents_per_hour_min':2,
                'agents_per_hour_max':2,'peak_hours':[0],'off_peak_hours':[],'morning_hours':[0],'work_hours':[0]}
        elif 'hot_topics' in prompt:
            payload={'hot_topics':['技术'],'narrative_direction':'offline connected discussion',
                     'initial_posts':[{'content':'Synthetic connected opening','poster_type':'Person'}]}
        else:
            payload={'bio':'技术观察者','persona':'Synthetic rich participant observes technology and social evidence.',
                     'age':32,'gender':'nonbinary','mbti':'INTJ','country':'MY'}
        reply.choices[0].message.content=json.dumps(payload,ensure_ascii=False)
        return reply


class CheckpointConnectedChat(RichConnectedChat):
    def create(self, **kwargs):
        reply = super().create(**kwargs)
        message = reply.choices[0].message
        payload = json.loads(message.content)
        if 'agent_configs' in payload:
            for actor in payload['agent_configs']:
                actor['active_hours'] = [0, 1]
        elif 'total_simulation_hours' in payload:
            payload.update(total_simulation_hours=2, agents_per_hour_min=1, agents_per_hour_max=1,
                           peak_hours=[0, 1], peak_activity_multiplier=1.0,
                           off_peak_activity_multiplier=1.0)
        elif 'initial_posts' in payload:
            payload['initial_posts'].append({
                'content': 'Second synthetic connected opening', 'poster_type': 'Organization',
            })
        message.content = json.dumps(payload, ensure_ascii=False)
        return reply


def _checkpoint_reference(dto):
    assert dto['schema_version'] == 2
    return {'schema_version': 2, 'launch_id': dto['request']['run_id'], 'launch_sha256': dto['launch_sha256']}


def _checkpoint_http(monkeypatch, host, observations):
    from app import create_app
    from app.services.knowledge_read_facade import ReadHostSettings
    from app.services.native_launch_facade import NativeLaunchFacade
    from app.services.native_observations_client import ENVELOPE_OVERHEAD, RESULT_BYTES
    from app.services.native_observations_facade import NativeObservationsFacade

    monkeypatch.setenv('NEXAWEAVE_APP_MODE', 'research_local')
    monkeypatch.delenv('FLASK_HOST', raising=False)
    monkeypatch.delenv('NEXAWEAVE_ALLOWED_ORIGINS', raising=False)
    settings = ReadHostSettings('python', 'read_bootstrap.py', '0123456789abcdef' * 4,
                                'owner', host.display_graph_id, host.scope_dto, {})
    monkeypatch.setattr(ReadHostSettings, 'from_config', classmethod(lambda _cls, _config: settings))

    client = create_app(
        native_launch_facade=NativeLaunchFacade(settings, host=host),
        native_observations_facade=NativeObservationsFacade(settings, host=observations),
    ).test_client()
    headers = {'Authorization': 'Bearer ' + settings.token}

    async def post(method, payload, *, status=200, code=None):
        response = await asyncio.to_thread(client.post, f'/api/native-launch/{method}/{host.display_graph_id}',
                                           json=payload, headers=headers)
        assert response.status_code == status
        if code is not None:
            assert response.json == {'success': False, 'error': {'code': code}}
            return None
        assert response.headers['Cache-Control'] == 'no-store'
        return response.json['data']

    async def page(payload, *, refused=()):
        response = await asyncio.to_thread(
            client.post, f'/api/native-observations/page/{host.display_graph_id}', json=payload, headers=headers,
        )
        assert response.mimetype == 'application/json'
        assert response.headers['Cache-Control'] == 'no-store'
        assert len(response.get_data()) <= RESULT_BYTES + ENVELOPE_OVERHEAD
        body = response.json
        if refused:
            status_limit = 600 if refused == ('invalid_reply',) else 500
            assert 400 <= response.status_code < status_limit
            assert any(body == {'success': False, 'error': {'code': code}} for code in refused)
            return None
        assert response.status_code == 200 and body == {'success': True, 'data': body['data']}
        return body['data']

    return post, page


@pytest.mark.asyncio
async def test_new_inherited_ready_artifacts_run_actual_both_platforms_through_pg_temporal(factory,tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    migrate_all(factory)
    temporal=await loopback_client()
    chat=RichConnectedChat()
    prep,scope,retained,_,_,transport=real_host(factory,tmp_path,cap=20,chat=chat)
    prep_temporal=TemporalPreparationHost(client=temporal,task_queue='mf-connected-prep-'+uuid4().hex,
                                       trusted_host=prep,allow_dispatch=lambda:True)
    loop=asyncio.get_running_loop();prep.scheduler=prep_temporal.scheduler_for(loop)
    host=launch_host(prep,factory)
    created=[]
    def construct(native_request):
        supervisor=host.supervisor_factory(native_request);created.append(supervisor);return supervisor
    native=TemporalNativeHost(client=temporal,task_queue='mf-connected-oasis-'+uuid4().hex,
        trusted_principal='owner',supervisor_factory=construct,allow_dispatch=lambda:True)
    host.attach_temporal(native,temporal_bridge(native,loop))
    row=None
    try:
        async with prep_temporal.worker(),native.worker():
            payload=request(retained)
            payload['options'].update(types=['Person','Organization'],max_agents=2,max_rounds=1)
            planned=await asyncio.to_thread(prep.plan,payload)
            await asyncio.to_thread(prep.start,preparation_reference(planned))
            prep_row=prep.store.get('owner',planned['operation_id'])
            await asyncio.wait_for(temporal.get_workflow_handle(prep_row.dispatch.workflow_id).result(),60)
            ready=await asyncio.to_thread(prep.status,preparation_reference(planned))
            assert ready['state']=='ready' and len(ready['actors'])==2 and ready['simulation_executed'] is False
            root=prep.root/ready['receipt']['simulation_id']
            original={file['name']:(root/file['name']).read_bytes() for file in ready['receipt']['files']}
            profiles=json.loads(original['reddit_profiles.json'])
            assert len(profiles)==2 and all(profile['age']==32 and profile['country']=='MY' for profile in profiles)
            dto=await asyncio.to_thread(host.plan,declaration(planned))
            assert dto['preparation']['artifact_sha256']==ready['receipt']['artifact_sha256']
            assert not created and not (root/'.native_prepared_start_claim').exists()
            await asyncio.to_thread(host.start,reference(dto))
            row=host.store.get('owner',dto['request']['run_id'])
            receipt=await asyncio.wait_for(native.result(row.request,NativeWorkflowRef(**row.workflow)),180)
            assert receipt.outcome=='completed'
            actual=NativeRunStore(factory).get('owner',row.run_id)
            assert actual.receipt==receipt and actual.request==row.request
            final=await asyncio.to_thread(host.status,reference(dto))
            assert final['state']=='completed' and final['receipt']==receipt.to_wire()
            assert NativeLaunchStore(factory).get('owner',row.run_id).receipt==receipt.to_wire()
            with factory() as conn:reservation=host.budget._row(conn,host.account_id,row.run_id)
            assert type(reservation.receipt) is NativeBudgetReceipt and reservation.receipt.native_receipt==receipt
            assert host.budget.status('owner',host.account_id).accounted_ceiling_microusd==8
            for platform in ('twitter','reddit'):
                with sqlite3.connect(root/(platform+'_simulation.db')) as db:
                    assert db.execute('SELECT COUNT(*) FROM user').fetchone()[0]==2
                    posts=[value[0] for value in db.execute('SELECT content FROM post')]
                    assert any(post.startswith('Scripted connected native post ') for post in posts)
                lines=(root/platform/'actions.jsonl').read_text(encoding='utf-8').splitlines()
                assert lines and all(type(json.loads(line)) is dict for line in lines)
            assert all((root/name).read_bytes()==data for name,data in original.items())
            assert (root/'.native_prepared_start_claim').is_file()
            local=await native.retry_cleanup(row.request)
            assert not local.cleanup_pending and not local.owner_thread_alive
            recovered=await asyncio.to_thread(host.start,reference(dto))
            assert recovered['receipt']==receipt.to_wire() and len(created)==1
            assert all((root/name).read_bytes()==data for name,data in original.items())
    finally:
        for supervisor in created:assert await asyncio.to_thread(supervisor.close,20)
        if row is not None:
            local=await native.retry_cleanup(row.request)
            assert not local.cleanup_pending and not local.owner_thread_alive


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel_in_flight', [False, True], ids=['completed', 'cancelled'])
async def test_generated_twitter_checkpoint_and_continuation_through_protected_http(
        factory, tmp_path, monkeypatch, cancel_in_flight):
    from app.services.durable_native_launch_host import DurableNativeLaunchHost
    from app.services.native_launch_models import BoundedNativeModelFactory
    from app.services.native_observations_host import NativeObservationsHost
    from native_twitter_fresh_fixture import (
        _DB_LIMIT, _LOG_LIMIT, _PROFILE_LIMIT, _RECEIPT_LIMIT, _RESTORE_OUTPUTS,
        _captured_checkpoint, _captured_owners, _events, _gated_checkpoint_twitter_backends,
        _restored_wait_marker,
    )
    from nexaweave_execution.native_owned_binding import _manifest
    from nexaweave_execution.native_run_contracts import NativeRunDenied, RunState
    from nexaweave_execution.native_twitter_checkpoint_binding import (
        NativeRestoredTwitterFactory, NativeTwitterCaptureFactory,
    )
    from nexaweave_storage.store import ProjectStore
    from test_durable_native_launch import LIMITS
    from test_native_round_capture import _artifact, _native_rows, _read_bounded

    monkeypatch.chdir(tmp_path)
    migrate_all(factory)
    deadline = monotonic() + 240

    def remaining(limit):
        seconds = min(limit, deadline - monotonic() - 20)
        assert seconds > 0, 'connected checkpoint deadline reached its cleanup reserve'
        return seconds

    temporal = await loopback_client()
    prep, scope, retained, _, _, _ = real_host(factory, tmp_path, cap=20, chat=CheckpointConnectedChat())
    prep_temporal = TemporalPreparationHost(client=temporal, task_queue='mf-checkpoint-prep-' + uuid4().hex,
                                            trusted_host=prep, allow_dispatch=lambda: True)
    loop = asyncio.get_running_loop()
    prep.scheduler = prep_temporal.scheduler_for(loop)
    backends = _gated_checkpoint_twitter_backends if cancel_in_flight else _scripted_checkpoint_twitter_backends
    model = BoundedNativeModelFactory(('twitter',), dict(LIMITS), backends, cooperative_transport=True)
    host = DurableNativeLaunchHost(preparation_host=prep, connection_factory=factory, runtime_sha256='c' * 64,
        model_label='scripted checkpoint', limits=LIMITS, model_factory=model, account_id=prep.account_id,
        ceiling_microusd=4, authorize=lambda: True)
    created = {}
    exited = {}
    reaped = {}

    def construct(request):
        supervisor = host.supervisor_factory(request)
        assert request.run_id not in created
        created[request.run_id] = supervisor
        driver = supervisor.coordinator.driver
        genuine_observe = driver.observe

        def observe(req, attempt, child):
            result = genuine_observe(req, attempt, child)
            if result.status == 'completed':
                owned = driver._owned
                assert owned is not None and owned.child == child
                assert owned.process.pid == child.process_id and owned.process.exitcode == 0
                assert req.run_id not in exited
                exited[req.run_id] = (child, result.receipt)
            return result

        driver.observe = observe
        if cancel_in_flight and request.source_run_id is not None:
            genuine_cancel = driver.cancel

            def cancel(req, attempt, child):
                owned = driver._owned
                assert owned is not None and owned.request == req
                assert owned.child == child and owned.attempt == attempt
                assert owned.sent_go and owned.process.pid == child.process_id and owned.process.is_alive()
                result = genuine_cancel(req, attempt, child)
                assert result.status == 'cancelled' and not owned.process.is_alive()
                assert owned.process.exitcode is not None
                assert req.run_id not in reaped
                reaped[req.run_id] = owned, result.receipt
                return result

            driver.cancel = cancel
        return supervisor

    native = TemporalNativeHost(client=temporal, task_queue='mf-checkpoint-oasis-' + uuid4().hex,
        trusted_principal='owner', supervisor_factory=construct, allow_dispatch=lambda: True)
    temporal_call = temporal_bridge(native, loop)
    dispatched = []

    def call(method, request, ref):
        if method == 'start':
            dispatched.append(request.run_id)
        return temporal_call(method, request, ref)

    host.attach_temporal(native, call)
    observations = NativeObservationsHost(launch_host=host)
    post, observations_page = _checkpoint_http(monkeypatch, host, observations)
    reader_roots = []
    real_page = observations.reader.page

    @contextmanager
    def traced_page(root, *args, **kwargs):
        reader_roots.append(root)
        with real_page(root, *args, **kwargs) as result:
            yield result

    monkeypatch.setattr(observations.reader, 'page', traced_page)

    async def read_page(ref, *, offset=0, refused=()):
        payload = {**ref, 'platform': 'twitter', 'offset': offset, 'limit': 3}
        return await asyncio.wait_for(observations_page(payload, refused=refused), remaining(20))

    async def settled_launch(dto, row, outcome):
        ref = _checkpoint_reference(dto)
        receipt = await asyncio.wait_for(native.result(row.request, NativeWorkflowRef(**row.workflow)), remaining(180))
        assert receipt.outcome == outcome
        cleanup = await asyncio.wait_for(native.retry_cleanup(row.request), remaining(20))
        assert not cleanup.cleanup_pending and not cleanup.owner_thread_alive
        final = await asyncio.wait_for(post('status', ref), remaining(30))
        saved = NativeRunStore(factory).get('owner', row.run_id)
        assert saved.state == RunState(outcome) and saved.request == row.request and saved.receipt == receipt
        assert final['state'] == outcome and final['receipt'] == receipt.to_wire()
        assert final['checkpoint'] == dto['checkpoint'] and _checkpoint_reference(final) == ref
        assert final['cleanup'] == {'known': True, 'pending': False, 'owner_thread_alive': False}
        settled = host.store.get('owner', row.run_id)
        assert settled.workflow == row.workflow and settled.receipt == receipt.to_wire()
        with factory() as conn:
            reservation = host.budget._row(conn, host.account_id, row.run_id)
        assert reservation.attempt_id == settled.budget_attempt_id == row.budget_attempt_id
        assert type(reservation.receipt) is NativeBudgetReceipt
        assert reservation.receipt.attempt_id == reservation.attempt_id
        assert reservation.receipt.launch_sha256 == settled.launch_sha256
        assert reservation.receipt.native_receipt == receipt
        driver = created[row.run_id].coordinator.driver
        assert driver._closed and driver._owned is None
        return saved, driver._factory, reservation

    async def launch(dto):
        ref = _checkpoint_reference(dto)
        await asyncio.wait_for(post('start', ref), remaining(30))
        row = host.store.get('owner', ref['launch_id'])
        saved, driver_factory, reservation = await settled_launch(dto, row, 'completed')
        assert exited[row.run_id] == (saved.child, saved.receipt)
        return saved, driver_factory, reservation

    async def cancel_restored(dto, producer):
        ref = _checkpoint_reference(dto)
        start = await asyncio.wait_for(post('start', ref), remaining(30))
        row = host.store.get('owner', ref['launch_id'])
        assert row.dispatch_claimed and row.workflow is not None and row.budget_attempt_id is not None
        assert row.request.source_run_id == producer.request.run_id
        assert dispatched == [producer.request.run_id, row.run_id]

        async def wait_for_gate():
            while True:
                supervisor = created.get(row.run_id)
                if supervisor is not None:
                    continuation = supervisor.coordinator.driver._factory
                    marker = _restored_wait_marker(Path(continuation.child_dir))
                    try:
                        with marker.open('rb') as stream:
                            token = stream.read(32)
                    except FileNotFoundError:
                        token = b''
                    if token:
                        return token
                await asyncio.sleep(0.025)

        token = await asyncio.wait_for(wait_for_gate(), remaining(180))
        active = NativeRunStore(factory).get('owner', row.run_id)
        assert active.state == RunState.running and active.request == row.request
        assert active.attempt_id is not None and active.child is not None and active.receipt is None
        assert not active.cancel_requested
        assert active.child.process_id not in {os.getpid(), producer.child.process_id}
        assert token == f'{active.child.process_id}\n'.encode('ascii')

        cancelled = await asyncio.wait_for(post('cancel', ref), remaining(10))
        assert cancelled['cancel_requested'] and _checkpoint_reference(cancelled) == ref
        saved, continuation, reservation = await settled_launch(dto, row, 'cancelled')
        assert saved.cancel_requested and saved.attempt_id == active.attempt_id and saved.child == active.child
        assert saved.receipt.run_id == row.run_id and saved.receipt.attempt_id == active.attempt_id
        assert saved.receipt.instance_id == active.child.instance_id
        assert saved.receipt.request_fingerprint == row.request.fingerprint
        owned, receipt = reaped[row.run_id]
        assert owned.child == active.child and receipt == saved.receipt
        assert owned.pipe.closed and not owned.reader.thread.is_alive()
        assert set(reaped) == {row.run_id} and set(exited) == {producer.request.run_id}

        for response in (start, cancelled):
            assert set(response) == set(dto) and response['checkpoint'] == dto['checkpoint']
            assert Path(continuation.child_dir).name not in json.dumps(response)
        return saved, continuation, reservation

    async def refuse_competing(dto):
        competing_ref = _checkpoint_reference(dto)
        await post('start', competing_ref, status=409, code='conflict')
        declined = host.store.get('owner', competing_ref['launch_id'])
        assert not declined.dispatch_claimed and declined.workflow is None and declined.budget_attempt_id is None
        with pytest.raises(NativeRunDenied):
            NativeRunStore(factory).get('owner', declined.run_id)

    try:
        async with prep_temporal.worker(), native.worker():
            payload = request(retained)
            payload['options'].update(platforms=['twitter'], types=['Person', 'Organization'], max_agents=2, max_rounds=1)
            planned = await asyncio.to_thread(prep.plan, payload)
            await asyncio.to_thread(prep.start, preparation_reference(planned))
            prep_row = prep.store.get('owner', planned['operation_id'])
            await asyncio.wait_for(temporal.get_workflow_handle(prep_row.dispatch.workflow_id).result(), remaining(60))
            ready = await asyncio.to_thread(prep.status, preparation_reference(planned))
            assert ready['state'] == 'ready' and len(ready['actors']) == 2 and ready['simulation_executed'] is False
            root = prep.root / ready['receipt']['simulation_id']
            originals = {file['name']: _read_bounded(root / file['name'], _PROFILE_LIMIT)
                         for file in ready['receipt']['files']}
            config = json.loads(originals['simulation_config.json'])
            assert len(config['agent_configs']) == 2 and len(config['event_config']['initial_posts']) == 2
            assert config['time_config']['total_simulation_hours'] == 2
            assert config['time_config']['minutes_per_round'] == 60
            assert host.budget.status('owner', host.account_id).accounted_ceiling_microusd == 4

            capture_dto = await post('plan', {**declaration(planned), 'schema_version': 2,
                                               'checkpoint': {'mode': 'capture'}})
            assert capture_dto['checkpoint'] == {'mode': 'capture'} and capture_dto['request']['schema_version'] == 1
            assert capture_dto['request']['platforms'] == ['twitter'] and capture_dto['request']['max_rounds'] == 1
            assert capture_dto['preparation']['artifact_sha256'] == ready['receipt']['artifact_sha256']
            assert not created and not (root / '.native_prepared_start_claim').exists()
            producer, capture, capture_budget = await launch(capture_dto)
            assert type(capture) is NativeTwitterCaptureFactory
            producer_pid = producer.child.process_id
            assert producer_pid != os.getpid()
            checkpoint = Path(capture.checkpoint_dir)
            lineage_path = Path(capture.lineage_dir) / 'lineage.json'
            assert checkpoint.parent == root.parent and lineage_path.parent.parent == root.parent
            assert len({root, checkpoint, lineage_path.parent}) == 3
            lineage_raw = _read_bounded(lineage_path, _RECEIPT_LIMIT)
            evidence = sha256(lineage_raw).hexdigest()
            assert evidence == producer.receipt.evidence_sha256
            lineage = json.loads(lineage_raw)
            assert lineage['producer_pid'] == producer_pid and lineage['anchors'] == capture.anchors
            assert lineage['anchors']['run_id'] == str(producer.request.run_id)
            assert lineage['anchors']['project_id'] == str(scope.project_id)
            assert lineage['anchors']['simulation_id'] == root.name
            assert lineage['prepared'] == {
                'artifact_sha256': producer.request.artifact_sha256,
                'config_sha256': sha256(originals['simulation_config.json']).hexdigest(),
                'profile_sha256': sha256(originals['twitter_profiles.csv']).hexdigest(),
                'source_grounding_sha256': sha256(originals['source_grounding.json']).hexdigest(),
            }
            database, memory, log, manifest, _, limits, pins = _captured_checkpoint(
                checkpoint, lineage, originals['simulation_config.json'],
            )
            _, frames = zip(*_captured_owners(memory, lineage['owners']), strict=True)
            producer_db, producer_log = root / 'twitter_simulation.db', root / 'twitter' / 'actions.jsonl'
            limits.update({producer_db: _DB_LIMIT, producer_log: _LOG_LIMIT, lineage_path: _RECEIPT_LIMIT})
            for path in (producer_db, producer_log, lineage_path):
                pins[path] = _artifact(path, limits[path])
            source_rows = _native_rows(database)
            assert _native_rows(producer_db) == source_rows
            _, old_trace, old_posts, _ = source_rows
            assert len(old_posts) == 3 and old_posts[-1][2] == _checkpoint_post(producer_pid, None)
            prefix = _read_bounded(log, _LOG_LIMIT)
            assert _read_bounded(producer_log, _LOG_LIMIT).startswith(prefix)
            assert _events(prefix)[-1]['event_type'] == 'round_end'
            assert host.budget.status('owner', host.account_id).accounted_ceiling_microusd == 8

            checkpoint_intent = {'mode': 'continue', 'source_run_id': str(producer.request.run_id),
                                 'checkpoint_sha256': evidence}
            continue_dto, competing_dto = [await post('plan', {**declaration(planned), 'schema_version': 2,
                                                               'checkpoint': checkpoint_intent}) for _ in range(2)]
            assert continue_dto['checkpoint'] == checkpoint_intent
            assert continue_dto['request']['schema_version'] == 2
            assert continue_dto['request']['source_run_id'] == checkpoint_intent['source_run_id']
            assert continue_dto['request']['checkpoint_sha256'] == evidence
            # A missing receipt must leave the same continuation admissible.
            parked_lineage = lineage_path.with_suffix('.fixture')
            lineage_path.rename(parked_lineage)
            try:
                continue_ref = _checkpoint_reference(continue_dto)
                await post('start', continue_ref, status=409, code='conflict')
                unclaimed = host.store.get('owner', continue_ref['launch_id'])
                assert unclaimed.state == 'planned' and not unclaimed.dispatch_claimed
                assert unclaimed.budget_attempt_id is None
                assert dispatched == [producer.request.run_id]
                assert host.budget.status('owner', host.account_id).accounted_ceiling_microusd == 8
            finally:
                parked_lineage.rename(lineage_path)
            if cancel_in_flight:
                restored, continuation, restore_budget = await cancel_restored(continue_dto, producer)
            else:
                restored, continuation, restore_budget = await launch(continue_dto)
            assert type(continuation) is NativeRestoredTwitterFactory
            assert restored.request.source_run_id == producer.request.run_id
            assert restored.child.process_id not in {os.getpid(), producer_pid}
            assert restore_budget.attempt_id != capture_budget.attempt_id
            assert continuation.expected_producer_pid == producer_pid
            assert continuation.expected_lineage_sha256 == evidence
            child_dir = Path(continuation.child_dir)
            assert child_dir.parent == root.parent and child_dir not in {root, checkpoint, lineage_path.parent}
            assert {path.name for path in islice(child_dir.iterdir(), 3)} == set(_RESTORE_OUTPUTS)
            child_db, child_log = child_dir / 'native.sqlite3', child_dir / 'actions.jsonl'
            if cancel_in_flight:
                assert not child_db.samefile(database) and not child_db.samefile(producer_db)
                assert sha256(_read_bounded(child_db, _DB_LIMIT)).hexdigest() == manifest['database_sha256']
                assert _read_bounded(child_log, _LOG_LIMIT) == prefix
                await read_page(continue_ref, refused=('conflict',))
                assert not reader_roots
                accounted = host.budget.status('owner', host.account_id).accounted_ceiling_microusd
                assert 8 <= accounted <= 12
                for method in ('start', 'status'):
                    replay = await asyncio.wait_for(post(method, continue_ref), remaining(30))
                    assert set(replay) == set(continue_dto) and replay['cancel_requested']
                    assert replay['state'] == 'cancelled' and replay['receipt'] == restored.receipt.to_wire()
                    assert _checkpoint_reference(replay) == continue_ref and replay['checkpoint'] == checkpoint_intent
                    assert replay['cleanup'] == {'known': True, 'pending': False, 'owner_thread_alive': False}
                    assert child_dir.name not in json.dumps(replay)
                await asyncio.wait_for(refuse_competing(competing_dto), remaining(30))
                assert dispatched == [producer.request.run_id, restored.request.run_id]
                assert set(created) == set(dispatched)
                with factory() as conn:
                    assert host.budget._row(conn, host.account_id, producer.request.run_id) == capture_budget
                    assert host.budget._row(conn, host.account_id, restored.request.run_id) == restore_budget
                assert host.budget.status('owner', host.account_id).accounted_ceiling_microusd == accounted
                assert {path: _artifact(path, limit) for path, limit in limits.items()} == pins
                assert {name: _read_bounded(root / name, _PROFILE_LIMIT) for name in originals} == originals
                assert (root / '.native_prepared_start_claim').is_file() and not (root / 'reddit_simulation.db').exists()
                assert monotonic() < deadline
                return
            assert _manifest(child_dir, _RESTORE_OUTPUTS, _DB_LIMIT) == restored.receipt.evidence_sha256
            child_pins = {child_db: _artifact(child_db, _DB_LIMIT), child_log: _artifact(child_log, _LOG_LIMIT)}
            assert sum(pin[0] for pin in (*pins.values(), *child_pins.values())) <= 160 * 1024 * 1024
            assert not child_db.samefile(database) and not child_db.samefile(producer_db)
            _, trace, posts, recommendations = _native_rows(child_db)
            assert posts[:len(old_posts)] == old_posts and trace[:len(old_trace)] == old_trace
            (post_id, owner, content, tick), = posts[len(old_posts):]
            assert post_id > old_posts[-1][0] and content == _checkpoint_post(restored.child.process_id, frames)
            assert type(tick) is int and tick == manifest['engine_clock']['after_step']
            (_, trace_owner, _, info, trace_tick), = (row for row in trace[len(old_trace):] if row[2] == 'create_post')
            assert trace_owner == owner and trace_tick == tick
            assert json.loads(info)['post_id'] == post_id and json.loads(info)['content'] == content
            assert len(recommendations) == 4 and all(value != post_id for _, value in recommendations)
            child_raw_log = _read_bounded(child_log, _LOG_LIMIT)
            assert child_raw_log.startswith(prefix)
            first, action, last = _events(child_raw_log[len(prefix):])
            assert first == {'round': 2, 'event_type': 'round_start', 'simulated_hour': 1}
            assert action['action_type'] == 'CREATE_POST' and action['success'] is True
            assert action['agent_id'] == owner and action['action_args']['content'] == content
            assert action['action_args']['post_id'] == post_id
            assert last == {'round': 2, 'event_type': 'round_end', 'actions_count': 1}

            for dto, saved in ((capture_dto, producer), (continue_dto, restored)):
                ref = _checkpoint_reference(dto)
                assert (await post('start', ref))['receipt'] == saved.receipt.to_wire()
                assert (await post('status', ref))['receipt'] == saved.receipt.to_wire()
            await refuse_competing(competing_dto)
            assert dispatched == [producer.request.run_id, restored.request.run_id]
            assert set(created) == set(exited) == set(dispatched)
            with factory() as conn:
                assert host.budget._row(conn, host.account_id, producer.request.run_id) == capture_budget
                assert host.budget._row(conn, host.account_id, restored.request.run_id) == restore_budget
            assert host.budget.status('owner', host.account_id).accounted_ceiling_microusd == 12
            assert {path: _artifact(path, limit) for path, limit in limits.items()} == pins
            assert {child_db: _artifact(child_db, _DB_LIMIT), child_log: _artifact(child_log, _LOG_LIMIT)} == child_pins
            assert {name: _read_bounded(root / name, _PROFILE_LIMIT) for name in originals} == originals
            assert (root / '.native_prepared_start_claim').is_file() and not (root / 'reddit_simulation.db').exists()
            assert monotonic() < deadline

            run_ids = [producer.request.run_id, restored.request.run_id]
            native_before = [host.native.get('owner', run_id) for run_id in run_ids]
            launches_before = [host.store.get('owner', run_id) for run_id in run_ids]
            capture_ref = _checkpoint_reference(capture_dto)
            crossed = {**continue_ref, 'launch_sha256': capture_ref['launch_sha256']}
            assert crossed['launch_sha256'] != continue_ref['launch_sha256']
            await read_page(capture_ref, refused=('conflict',))
            await read_page(crossed, refused=('conflict', 'not_found'))
            assert not reader_roots

            offset = len(_events(prefix))
            observed = await read_page(continue_ref, offset=offset)
            assert reader_roots == [child_dir]
            assert observed['schema_version'] == 2 and observed['platform'] == 'twitter'
            assert observed['offset'] == offset and observed['limit'] == 3
            observed_launch = observed['launch']
            assert _checkpoint_reference(observed_launch) == continue_ref
            assert observed_launch['state'] == 'completed' and observed_launch['request'] == continue_dto['request']
            assert observed_launch['checkpoint'] == checkpoint_intent and not observed_launch['cancel_requested']
            assert observed_launch['receipt'] == restored.receipt.to_wire()
            assert observed_launch['cleanup'] == {'known': False, 'pending': None, 'owner_thread_alive': None}
            assert observed['manifest'] == {
                'schema_version': 1,
                'files': [{'name': path.name, 'size': child_pins[path][0], 'sha256': child_pins[path][1]}
                          for path in (child_db, child_log)],
            }
            raw_lines = child_raw_log[len(prefix):].splitlines()
            assert len(raw_lines) == 3 and observed['total_records'] == offset + 3 and observed['next_offset'] is None
            assert observed['records'] == [
                {'index': index, 'record_sha256': sha256(raw).hexdigest(), 'raw_json': raw.decode('utf-8')}
                for index, raw in enumerate(raw_lines, offset)
            ]
            assert json.loads(observed['records'][1]['raw_json'])['action_args']['content'] == content
            all_events = _events(child_raw_log)
            action_records = [record for record in all_events if type(record.get('action_type')) is str]
            assert observed['counts'] == {
                'event_records': sum(type(record.get('event_type')) is str for record in all_events),
                'action_records': len(action_records),
                'successful_action_records': sum(record.get('success') is True for record in action_records),
                'failed_action_records': sum(record.get('success') is False for record in action_records),
            }
            public = json.dumps(observed)
            for private in (child_dir.name, checkpoint.name, lineage_path.parent.name,
                            '"process_id"', '"owner_id"'):
                assert private not in public

            # A completed receipt can coexist with cancellation intent on either durable row.
            completed_launch, completed_native = launches_before[-1], native_before[-1]
            assert completed_launch.state == 'completed' and completed_native.state == RunState.completed
            assert completed_launch.cancel_requested is completed_native.cancel_requested is False
            for store, baseline in ((host.store, completed_launch), (host.native, completed_native)):
                genuine_get = store.get
                overridden = []

                def get_with_cancel_requested(*args, **kwargs):
                    row = genuine_get(*args, **kwargs)
                    if row.request.run_id != restored.request.run_id:
                        return row
                    assert row == baseline
                    overridden.append(row)
                    return replace(row, cancel_requested=True)

                with monkeypatch.context() as cancel_flag:
                    cancel_flag.setattr(store, 'get', get_with_cancel_requested)
                    await read_page(continue_ref, offset=offset, refused=('conflict',))
                assert overridden == [baseline] and reader_roots == [child_dir]

            cancelled_reply = dict(observed, launch=dict(observed_launch, cancel_requested=True))
            received = []

            def injected_page(payload):
                received.append(payload)
                return cancelled_reply

            with monkeypatch.context() as reply_probe:
                reply_probe.setattr(observations, 'page', injected_page)
                await read_page(continue_ref, offset=offset, refused=('invalid_reply',))
            assert received == [{**continue_ref, 'platform': 'twitter', 'offset': offset, 'limit': 3}]
            assert reader_roots == [child_dir]

            # Keep valid JSON, file size and path while breaking the receipt hash.
            byte_offset = child_raw_log.index(b'round_start', len(prefix))
            tampered = child_raw_log[:byte_offset] + b'R' + child_raw_log[byte_offset + 1:]
            try:
                with child_log.open('r+b') as stream:
                    stream.seek(byte_offset)
                    assert stream.write(b'R') == 1
                assert len(tampered) == len(child_raw_log)
                assert _read_bounded(child_log, _LOG_LIMIT) == tampered
                await read_page(continue_ref, offset=offset, refused=('evidence_invalid',))
            finally:
                with child_log.open('r+b') as stream:
                    stream.seek(byte_offset)
                    assert stream.write(b'r') == 1
            assert _read_bounded(child_log, _LOG_LIMIT) == child_raw_log
            assert reader_roots == [child_dir, child_dir]

            projects = ProjectStore(factory)
            project = projects.get('owner', scope.project_id)
            assert project.revision == restored.request.project_revision
            advanced = []

            @contextmanager
            def revoke_during_read(output, *args, **kwargs):
                with traced_page(output, *args, **kwargs) as result:
                    # The real reader has verified the hashes and still owns its descriptors.
                    assert result['manifest'] == observed['manifest'] and result['records'] == observed['records']
                    revised = projects.update(
                        'owner', scope.project_id, project.revision, project.snapshot, project.evidence,
                    )
                    assert revised.revision == project.revision + 1
                    advanced.append(revised)
                    yield result

            stale_refusal = ('conflict', 'not_found')
            with monkeypatch.context() as mid_read:
                mid_read.setattr(observations.reader, 'page', revoke_during_read)
                await read_page(continue_ref, offset=offset, refused=stale_refusal)
            assert len(advanced) == 1 and projects.get('owner', scope.project_id) == advanced[0]
            await read_page(continue_ref, offset=offset, refused=stale_refusal)
            assert reader_roots == [child_dir, child_dir, child_dir]

            assert dispatched == run_ids
            assert set(created) == set(exited) == set(dispatched) and not reaped
            assert [host.native.get('owner', run_id) for run_id in run_ids] == native_before
            assert [host.store.get('owner', run_id) for run_id in run_ids] == launches_before
            with factory() as conn:
                assert host.budget._row(conn, host.account_id, producer.request.run_id) == capture_budget
                assert host.budget._row(conn, host.account_id, restored.request.run_id) == restore_budget
            assert host.budget.status('owner', host.account_id).accounted_ceiling_microusd == 12
            assert monotonic() < deadline
    finally:
        for supervisor in created.values():
            assert await asyncio.to_thread(supervisor.close, 20)

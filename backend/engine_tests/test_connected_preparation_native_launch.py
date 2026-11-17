"""Actual new U07c publication → PG/Temporal → owned both-platform OASIS.

Scripted inherited rich persona/config and CAMEL responses, zero paid calls.
No replacement prepared files or unrelated experiment-manifest run.
"""
import asyncio
import json
import os
import sqlite3
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


def _checkpoint_http(monkeypatch, host):
    from app import create_app
    from app.services.knowledge_read_facade import ReadHostSettings
    from app.services.native_launch_facade import NativeLaunchFacade

    monkeypatch.setenv('NEXAWEAVE_APP_MODE', 'research_local')
    monkeypatch.delenv('FLASK_HOST', raising=False)
    monkeypatch.delenv('NEXAWEAVE_ALLOWED_ORIGINS', raising=False)
    settings = ReadHostSettings('python', 'read_bootstrap.py', '0123456789abcdef' * 4,
                                'owner', host.display_graph_id, host.scope_dto, {})
    monkeypatch.setattr(ReadHostSettings, 'from_config', classmethod(lambda _cls, _config: settings))

    client = create_app(native_launch_facade=NativeLaunchFacade(settings, host=host)).test_client()
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

    return post


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
async def test_generated_twitter_checkpoint_and_continuation_through_protected_http(factory, tmp_path, monkeypatch):
    from app.services.durable_native_launch_host import DurableNativeLaunchHost
    from app.services.native_launch_models import BoundedNativeModelFactory
    from native_twitter_fresh_fixture import (
        _DB_LIMIT, _LOG_LIMIT, _PROFILE_LIMIT, _RECEIPT_LIMIT, _RESTORE_OUTPUTS,
        _captured_checkpoint, _captured_owners, _events,
    )
    from nexaweave_execution.native_owned_binding import _manifest
    from nexaweave_execution.native_run_contracts import NativeRunDenied, RunState
    from nexaweave_execution.native_twitter_checkpoint_binding import (
        NativeRestoredTwitterFactory, NativeTwitterCaptureFactory,
    )
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
    model = BoundedNativeModelFactory(('twitter',), dict(LIMITS), _scripted_checkpoint_twitter_backends,
                                      cooperative_transport=True)
    host = DurableNativeLaunchHost(preparation_host=prep, connection_factory=factory, runtime_sha256='c' * 64,
        model_label='scripted checkpoint', limits=LIMITS, model_factory=model, account_id=prep.account_id,
        ceiling_microusd=4, authorize=lambda: True)
    created = {}
    exited = {}

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
    post = _checkpoint_http(monkeypatch, host)

    async def launch(dto):
        ref = _checkpoint_reference(dto)
        await asyncio.wait_for(post('start', ref), remaining(30))
        row = host.store.get('owner', ref['launch_id'])
        receipt = await asyncio.wait_for(native.result(row.request, NativeWorkflowRef(**row.workflow)), remaining(180))
        assert receipt.outcome == 'completed'
        cleanup = await asyncio.wait_for(native.retry_cleanup(row.request), remaining(20))
        assert not cleanup.cleanup_pending and not cleanup.owner_thread_alive
        final = await asyncio.wait_for(post('status', ref), remaining(30))
        saved = NativeRunStore(factory).get('owner', row.run_id)
        assert saved.state == RunState.completed and saved.request == row.request and saved.receipt == receipt
        assert exited[row.run_id] == (saved.child, receipt)
        assert final['state'] == 'completed' and final['receipt'] == receipt.to_wire()
        assert final['checkpoint'] == dto['checkpoint'] and _checkpoint_reference(final) == ref
        assert final['cleanup'] == {'known': True, 'pending': False, 'owner_thread_alive': False}
        settled = host.store.get('owner', row.run_id)
        assert settled.workflow == row.workflow and settled.receipt == receipt.to_wire()
        with factory() as conn:
            reservation = host.budget._row(conn, host.account_id, row.run_id)
        assert reservation.attempt_id == settled.budget_attempt_id
        assert type(reservation.receipt) is NativeBudgetReceipt
        assert reservation.receipt.attempt_id == reservation.attempt_id
        assert reservation.receipt.launch_sha256 == settled.launch_sha256
        assert reservation.receipt.native_receipt == receipt
        driver = created[row.run_id].coordinator.driver
        assert driver._closed and driver._owned is None
        return saved, driver._factory, reservation

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
            assert _manifest(child_dir, _RESTORE_OUTPUTS, _DB_LIMIT) == restored.receipt.evidence_sha256
            child_db, child_log = child_dir / 'native.sqlite3', child_dir / 'actions.jsonl'
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
            competing_ref = _checkpoint_reference(competing_dto)
            await post('start', competing_ref, status=409, code='conflict')
            declined = host.store.get('owner', competing_ref['launch_id'])
            assert not declined.dispatch_claimed and declined.workflow is None and declined.budget_attempt_id is None
            with pytest.raises(NativeRunDenied):
                NativeRunStore(factory).get('owner', declined.run_id)
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
    finally:
        for supervisor in created.values():
            assert await asyncio.to_thread(supervisor.close, 20)

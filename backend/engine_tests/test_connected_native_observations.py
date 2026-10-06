"""Fresh inherited publication and real PG/Temporal/OASIS, then completed read pages.

Graph projections use the existing source-grounded fixture. Main separately owns
actual Neo/browser qualification. No handcrafted output qualifies native completion.
"""
import asyncio
import hashlib
import json
from uuid import uuid4
import pytest

pytestmark = [pytest.mark.postgres, pytest.mark.native_observations_engine]


@pytest.fixture(scope='module')
def factory():
    from test_native_launch_store import factory as guarded_factory
    return guarded_factory.__wrapped__()


@pytest.mark.asyncio
async def test_fresh_two_platform_completed_outputs_read_without_models_or_budget_mutation(factory, tmp_path, monkeypatch):
    # Keep native and CAMEL-bearing imports out of generic collection/spawn entry.
    from mirofish_execution.temporal_preparation_host import TemporalPreparationHost
    from mirofish_execution.temporal_native_host import TemporalNativeHost, NativeWorkflowRef
    from mirofish_execution.native_run_store import NativeRunStore
    from mirofish_execution.native_launch_store import NativeLaunchStore
    from test_connected_preparation_native_launch import RichConnectedChat
    from test_preparation_store import real_host, request, reference as preparation_reference
    from test_native_launch_store import migrate_all, launch_host, declaration
    from test_native_launch_api import reference
    from test_temporal_connected_launch import loopback_client, temporal_bridge
    from app.services.native_observations_host import NativeObservationsHost
    from app.services.native_observations_facade import NativeObservationsFacade
    from app.services.native_observations_client import NativeObservationsError, digest
    from app.services.knowledge_read_facade import ReadHostSettings
    from test_native_observations_api import payload

    monkeypatch.chdir(tmp_path)
    migrate_all(factory)
    temporal = await loopback_client()
    chat = RichConnectedChat()
    prep, scope, retained, _, _, _ = real_host(factory, tmp_path, cap=20, chat=chat)
    prep_temporal = TemporalPreparationHost(client=temporal, task_queue='mf-obs-prep-' + uuid4().hex,
                                           trusted_host=prep, allow_dispatch=lambda: True)
    loop = asyncio.get_running_loop(); prep.scheduler = prep_temporal.scheduler_for(loop)
    launch = launch_host(prep, factory)
    created = []
    def construct(native_request):
        supervisor = launch.supervisor_factory(native_request); created.append(supervisor); return supervisor
    native = TemporalNativeHost(client=temporal, task_queue='mf-obs-oasis-' + uuid4().hex,
        trusted_principal='owner', supervisor_factory=construct, allow_dispatch=lambda: True)
    launch.attach_temporal(native, temporal_bridge(native, loop))
    row = None
    try:
        async with prep_temporal.worker(), native.worker():
            declaration_payload = request(retained)
            declaration_payload['options'].update(types=['Person', 'Organization'], max_agents=2, max_rounds=1)
            planned = await asyncio.to_thread(prep.plan, declaration_payload)
            await asyncio.to_thread(prep.start, preparation_reference(planned))
            prep_row = prep.store.get('owner', planned['operation_id'])
            await asyncio.wait_for(temporal.get_workflow_handle(prep_row.dispatch.workflow_id).result(), 60)
            ready = await asyncio.to_thread(prep.status, preparation_reference(planned))
            assert ready['state'] == 'ready' and len(ready['actors']) == 2
            root = prep.root / ready['receipt']['simulation_id']
            inputs = {file['name']: (root / file['name']).read_bytes() for file in ready['receipt']['files']}
            dto = await asyncio.to_thread(launch.plan, declaration(planned))
            await asyncio.to_thread(launch.start, reference(dto))
            row = launch.store.get('owner', dto['request']['run_id'])
            receipt = await asyncio.wait_for(native.result(row.request, NativeWorkflowRef(**row.workflow)), 180)
            assert receipt.outcome == 'completed'
            final = await asyncio.to_thread(launch.status, reference(dto))  # Main fixture settles before read-only phase.
            assert final['state'] == 'completed' and final['receipt'] == receipt.to_wire()
            local = await native.retry_cleanup(row.request)
            assert not local.cleanup_pending and not local.owner_thread_alive and len(created) == 1
            outputs = {name: (root / name).read_bytes() for platform in ('twitter', 'reddit')
                       for name in (platform + '_simulation.db', platform + '/actions.jsonl')}
            launch_before = NativeLaunchStore(factory).get('owner', row.run_id)
            native_before = NativeRunStore(factory).get('owner', row.run_id)
            budget_before = launch.budget.status('owner', launch.account_id)
            chat_calls = len(chat.calls)
            def forbidden(*args, **kwargs): pytest.fail('completed observations called a model, dispatch, runtime or ledger mutation')
            launch.authorize = lambda: False
            prep.authorize = lambda: False
            launch.model_factory = None
            prep.chat_client_factory = forbidden
            launch._recover = launch._dto = launch.start = launch.cancel = launch.status = forbidden
            launch.temporal_call = prep.scheduler = forbidden
            # The retained supervisor shares this ledger. Guards cover the entire
            # observations phase, then restore before legitimate final cleanup,
            # including when a read or preservation assertion raises.
            with monkeypatch.context() as read_phase:
                for name in ('reserve_native', 'settle_native', 'start', 'mark_uncertain', 'release_undispatched'):
                    read_phase.setattr(launch.budget, name, forbidden, raising=False)
                observations = NativeObservationsHost(launch_host=launch)
                settings = ReadHostSettings('python', 'read_bootstrap.py', 'fixture-token', 'owner', launch.display_graph_id, launch.scope_dto, {})
                facade = NativeObservationsFacade(settings, host=observations)
                for platform in ('twitter', 'reddit'):
                    physical = outputs[platform + '/actions.jsonl'].split(b'\n')
                    if physical and physical[-1] == b'': physical.pop()
                    raw_lines = [line[:-1] if line.endswith(b'\r') else line for line in physical]
                    assert raw_lines
                    offset, all_records, first = 0, [], None
                    while True:
                        request_payload = payload(final, platform=platform, offset=offset, limit=3)
                        page = await asyncio.to_thread(facade.execute, launch.display_graph_id, request_payload)
                        assert page['launch']['receipt'] == receipt.to_wire()
                        assert not page['launch']['authorization']['model_calls_enabled']
                        assert page['total_records'] == len(raw_lines)
                        assert digest(page['manifest']) == receipt.evidence_sha256
                        if first is None: first = page
                        assert page['counts'] == first['counts'] and page['manifest'] == first['manifest']
                        for record in page['records']:
                            raw = raw_lines[record['index']]
                            assert record['raw_json'].encode('utf-8') == raw
                            assert record['record_sha256'] == hashlib.sha256(raw).hexdigest()
                        all_records.extend(page['records'])
                        if page['next_offset'] is None: break
                        assert page['next_offset'] > offset
                        offset = page['next_offset']
                    assert [record['index'] for record in all_records] == list(range(len(raw_lines)))
                    parsed = [json.loads(raw) for raw in raw_lines]
                    assert first['counts'] == {
                        'event_records': sum(type(value.get('event_type')) is str for value in parsed),
                        'action_records': sum(type(value.get('action_type')) is str for value in parsed),
                        'successful_action_records': sum(type(value.get('action_type')) is str and value.get('success') is True for value in parsed),
                        'failed_action_records': sum(type(value.get('action_type')) is str and value.get('success') is False for value in parsed)}
                    empty = await asyncio.to_thread(facade.execute, launch.display_graph_id, payload(final, platform=platform, offset=len(raw_lines)))
                    assert empty['records'] == [] and empty['next_offset'] is None and empty['counts'] == first['counts']
                    with pytest.raises(NativeObservationsError) as invalid:
                        await asyncio.to_thread(facade.execute, launch.display_graph_id, payload(final, platform=platform, offset=len(raw_lines) + 1))
                    assert invalid.value.code == 'invalid_request'
                assert NativeLaunchStore(factory).get('owner', row.run_id) == launch_before
                assert NativeRunStore(factory).get('owner', row.run_id) == native_before
                assert launch.budget.status('owner', launch.account_id) == budget_before
                assert len(chat.calls) == chat_calls and len(created) == 1
                assert all((root / name).read_bytes() == data for name, data in {**inputs, **outputs}.items())
    finally:
        for supervisor in created: assert await asyncio.to_thread(supervisor.close, 20)
        if row is not None:
            local = await native.retry_cleanup(row.request)
            assert not local.cleanup_pending and not local.owner_thread_alive

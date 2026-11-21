"""Owned ordinary browser proof for one-platform preparation and native runs."""
import asyncio
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4

import pytest

import test_connected_native_report as shared
from app.services.durable_native_launch_host import DurableNativeLaunchHost
from app.services.native_launch_models import BoundedNativeModelFactory
from nexaweave_execution.native_launch_contracts import NativeBudgetReceipt
from nexaweave_execution.native_launch_store import NativeLaunchStore
from nexaweave_execution.native_run_store import NativeRunStore
from nexaweave_execution.preparation_contracts import PreparedBudgetReceipt
from nexaweave_execution.temporal_native_host import NativeWorkflowRef, TemporalNativeHost
from nexaweave_execution.temporal_preparation_host import TemporalPreparationHost
from nexaweave_storage import SourceStore
from research_browser_fixture import ResearchBrowserSession
from test_connected_preparation_native_launch import CheckpointConnectedChat, RichConnectedChat
from test_durable_native_launch import LIMITS, scripted_native_backends
from test_native_launch_store import declaration, factory, migrate_all
from test_preparation_store import real_host, request
from test_temporal_connected_launch import loopback_client, temporal_bridge


pytestmark = [pytest.mark.postgres, pytest.mark.connected_report_temporal]
_single_browser_platform = None
_fixture_phase_sink = None
_ARTIFACT_BYTES = 2097152


def twitter_backends(*, timeout_seconds, max_tokens, max_retries):
    """Use the existing scripted child transport with the exact requested platform."""
    models = scripted_native_backends(timeout_seconds=timeout_seconds, max_tokens=max_tokens, max_retries=max_retries)
    return {'twitter': models['twitter']}


def reddit_backends(*, timeout_seconds, max_tokens, max_retries):
    models = scripted_native_backends(timeout_seconds=timeout_seconds, max_tokens=max_tokens, max_retries=max_retries)
    return {'reddit': models['reddit']}


def _read_artifact(path, limit):
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    assert 0 < len(raw) <= limit
    return raw


def _check_outputs(root, inputs, platform, *, capture=False):
    assert all(_read_artifact(root / name, len(raw)) == raw for name, raw in inputs.items())
    excluded = ('reddit_profiles.json', 'reddit_simulation.db', 'reddit') if platform == 'twitter' else (
        'twitter_profiles.csv', 'twitter_simulation.db', 'twitter')
    for name in excluded:
        path = root / name
        assert not path.exists() and not path.is_symlink()
    database = root / f'{platform}_simulation.db'
    assert 0 < database.stat().st_size <= _ARTIFACT_BYTES
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        assert db.execute('SELECT COUNT(*) FROM user').fetchone()[0] == 2
        prefix = 'Scripted connected checkpoint post %' if capture else 'Scripted connected native post %'
        assert db.execute('SELECT 1 FROM post WHERE content LIKE ? LIMIT 1', (prefix,)).fetchone()
    actions = _read_artifact(root / platform / 'actions.jsonl', _ARTIFACT_BYTES).splitlines()
    assert actions and all(type(json.loads(line)) is dict for line in actions)
    assert (root / '.native_prepared_start_claim').is_file()


@pytest.mark.asyncio
async def test_twitter_only_suggest_ready_and_native_workbench(factory, tmp_path, monkeypatch):
    await _run_single_platform_workbench(factory, tmp_path, monkeypatch, 'twitter')


@pytest.mark.asyncio
async def test_reddit_only_suggest_ready_and_native_workbench(factory, tmp_path, monkeypatch):
    await _run_single_platform_workbench(factory, tmp_path, monkeypatch, 'reddit')


@pytest.mark.asyncio
async def test_twitter_capture_and_fixed_fork_workbench(factory, tmp_path, monkeypatch):
    await _run_single_platform_workbench(factory, tmp_path, monkeypatch, 'twitter', fork=True)


async def _run_single_platform_workbench(factory, tmp_path, monkeypatch, platform, *, fork=False):
    mode = 'twitter-fork' if fork else platform
    assert _single_browser_platform == mode, f'select this case through --{mode}-browser'
    monkeypatch.setattr(shared, '_actual_graph_journey', True)
    monkeypatch.setattr(shared, '_research_browser_journey', True)
    monkeypatch.setattr(shared, '_fixture_phase_sink', _fixture_phase_sink)
    phases = shared.FixturePhases(f'workbench-{mode}-ordinary')
    phases.start('setup')
    monkeypatch.chdir(tmp_path)
    migrate_all(factory)
    temporal = await loopback_client()
    input_artifacts = tmp_path / 'native-inputs'
    input_artifacts.mkdir()
    chat = CheckpointConnectedChat() if fork else RichConnectedChat()
    prep, source_scope, _, _, _, _ = real_host(factory, input_artifacts, cap=12, chat=chat, ceiling=2)
    with shared._actual_graph_sources(factory, tmp_path, monkeypatch, prep, source_scope) as selected:
        loop = asyncio.get_running_loop()
        preparation = TemporalPreparationHost(client=temporal, task_queue='mf-workbench-prep-' + uuid4().hex,
                                              trusted_host=prep, allow_dispatch=lambda: True)
        prep.scheduler = preparation.scheduler_for(loop)
        platform_backends = twitter_backends if platform == 'twitter' else reddit_backends
        if fork:
            from native_twitter_fresh_fixture import _scripted_checkpoint_twitter_backends
            platform_backends = _scripted_checkpoint_twitter_backends
        model = BoundedNativeModelFactory((platform,), dict(LIMITS), platform_backends, cooperative_transport=True)
        native_host = DurableNativeLaunchHost(
            preparation_host=prep, connection_factory=factory, runtime_sha256='c' * 64,
            model_label='scripted', limits=LIMITS, model_factory=model, account_id=prep.account_id,
            ceiling_microusd=2, authorize=lambda: True)
        owners = []

        def construct(native_request):
            owner = native_host.supervisor_factory(native_request)
            owners.append(owner)
            return owner

        native = TemporalNativeHost(client=temporal, task_queue='mf-workbench-native-' + uuid4().hex,
                                    trusted_principal='owner', supervisor_factory=construct, allow_dispatch=lambda: True)
        temporal_call = temporal_bridge(native, loop)
        dispatches = []

        def record_dispatch(method, request, ref):
            if method == 'start':
                dispatches.append(request.run_id)
            return temporal_call(method, request, ref)

        native_host.attach_temporal(native, record_dispatch if fork else temporal_call)
        launched = []
        report_log = tmp_path / 'actual-report-sdk-requests.jsonl'
        browser = ResearchBrowserSession(tmp_path, prep, native_host, None, selected, report_log,
                                         single_platform=platform, fork=fork)
        try:
            phases.end('setup')
            async with preparation.worker(), native.worker(), browser:
                browser.ordered_sources()
                await asyncio.to_thread(shared._browse_saved_pdf, browser, factory, prep, source_scope, selected)
                phases.start('preparation-plan-start')
                payload = dict(request(selected.records[0]), schema_version=2, source_revisions=selected.revisions[:])
                payload['options'].update(platforms=[platform], types=['Person', 'Organization'], max_agents=2,
                                          seed=7, max_rounds=1,
                                          simulation_requirement=f'Study the retained evidence. Use {platform.title()}.')
                planned = await asyncio.to_thread(browser.perform, 'preparation-plan', payload)
                assert planned['state'] == 'planned' and len(planned['actors']) == 2 and not owners
                await asyncio.to_thread(browser.perform, 'preparation-start')
                prep_row = prep.store.get('owner', planned['operation_id'])
                phases.end('preparation-plan-start')

                phases.start('preparation-result')
                await asyncio.wait_for(temporal.get_workflow_handle(prep_row.dispatch.workflow_id).result(), 60)
                ready = await asyncio.to_thread(browser.perform, 'preparation-ready')
                source_fields = ('source_revision', 'source_name', 'source_sha256')
                assert ready['state'] == 'ready' and ready['simulation_executed'] is False
                assert ready['operation_id'] == planned['operation_id'] and ready['plan_sha256'] == planned['plan_sha256']
                assert ready['scope'] == planned['scope'] and ready['project_revision'] == planned['project_revision']
                assert ready['options'] == planned['options'] == payload['options']
                assert ready['sources'] == [{key: source[key] for key in source_fields} for source in selected.snapshots]
                assert ready['source'] == ready['sources'][0]
                assert prep_row.frozen['sources'] == selected.snapshots
                prepared = ready['receipt']
                root = prep.root / prepared['simulation_id']
                files = prepared['files']
                profile_name = 'twitter_profiles.csv' if platform == 'twitter' else 'reddit_profiles.json'
                assert [item['name'] for item in files] == [
                    'state.json', 'simulation_config.json', 'source_grounding.json', profile_name]
                inputs = {}
                for entry in files:
                    name = entry['name']
                    raw = _read_artifact(root / name, _ARTIFACT_BYTES)
                    assert (len(raw), sha256(raw).hexdigest()) == (entry['size'], entry['sha256'])
                    inputs[name] = raw
                if platform == 'reddit':
                    profiles = json.loads(inputs[profile_name])
                    assert len(profiles) == 2 and all(
                        profile['age'] == 32 and profile['country'] == 'MY' for profile in profiles)
                with factory() as conn:
                    reservation = prep.budget._row(conn, prep.account_id, UUID(planned['operation_id']))
                assert type(reservation.receipt) is PreparedBudgetReceipt
                assert reservation.ceiling_microusd == 2 and reservation.state.value == 'settled'
                assert reservation.receipt.operation_id == UUID(planned['operation_id'])
                assert reservation.receipt.attempt_id == reservation.attempt_id
                assert reservation.receipt.fingerprint == reservation.fingerprint
                assert reservation.receipt.artifact_sha256 == prepared['artifact_sha256']
                shared._assert_accounted(prep, 6)
                assert not owners and not (root / '.native_prepared_start_claim').exists()
                phases.end('preparation-result')

                phases.start('native-plan-start')
                started = await asyncio.to_thread(browser.perform, 'native-start', declaration(planned))
                binding = dict(operation_id=ready['operation_id'], plan_sha256=ready['plan_sha256'],
                               simulation_id=prepared['simulation_id'], artifact_sha256=prepared['artifact_sha256'])
                assert all(started['preparation'][key] == value for key, value in binding.items())
                wire = started['request']
                assert wire['platforms'] == [platform] and wire['seed'] == 7 and wire['max_rounds'] == 1
                assert started['ceiling_microusd'] == '2'
                native_row = native_host.store.get('owner', wire['run_id'])
                launched.append(native_row.request)
                assert native_row.frozen['identity']['preparation'] == started['preparation']
                assert native_row.request.artifact_sha256 == binding['artifact_sha256']
                assert native_row.request.simulation_id == binding['simulation_id']
                phases.end('native-plan-start')

                phases.start('native-result')
                receipt = await asyncio.wait_for(native.result(native_row.request, NativeWorkflowRef(**native_row.workflow)), 180)
                assert receipt.outcome == 'completed' and len(owners) == 1
                if fork:
                    await _require_native_closed(native, native_row.request)
                final = await asyncio.to_thread(browser.perform, 'native-ready')
                assert final['state'] == 'completed' and final['receipt'] == receipt.to_wire()
                assert final['request'] == wire and final['preparation'] == started['preparation']
                assert final['launch_sha256'] == started['launch_sha256']
                assert receipt.run_id == native_row.run_id and receipt.request_fingerprint == native_row.request.fingerprint
                saved = NativeRunStore(factory).get('owner', native_row.run_id)
                settled = NativeLaunchStore(factory).get('owner', native_row.run_id)
                assert saved.request == native_row.request and saved.receipt == receipt
                assert settled.receipt == final['receipt'] and settled.launch_sha256 == final['launch_sha256']
                with factory() as conn:
                    reservation = native_host.budget._row(conn, prep.account_id, native_row.run_id)
                assert type(reservation.receipt) is NativeBudgetReceipt and reservation.ceiling_microusd == 2
                assert reservation.state.value == 'settled' and reservation.receipt.native_receipt == receipt
                assert reservation.receipt.operation_id == native_row.run_id
                assert reservation.receipt.attempt_id == reservation.attempt_id
                assert reservation.receipt.launch_sha256 == final['launch_sha256']
                assert reservation.receipt.fingerprint == reservation.fingerprint
                assert reservation.attempt_id == settled.budget_attempt_id
                shared._assert_accounted(prep, 8)
                _check_outputs(root, inputs, platform, capture=fork)
                assert prep.store.get('owner', planned['operation_id']).frozen['sources'] == selected.snapshots
                sources = SourceStore(factory)
                for record in selected.records:
                    assert sources.get_source('owner', source_scope.project_id, record.source_revision) == record
                original = sources.get_original('owner', source_scope.project_id, selected.records[0].source_revision)
                assert original.source == selected.records[0] and original.content == selected.pdf
                assert not report_log.exists()
                phases.end('native-result')
                if fork:
                    phases.start('native-fork')
                    await _browser_fork(browser, native, native_host, owners, dispatches, launched, root, final)
                    shared._assert_accounted(prep, 10)
                    phases.end('native-fork')
            assert browser.closed and browser.last_proof == dict(
                context_closed=True, browser_closed=True, private_tree_removed=True)
        finally:
            for owner in owners:
                assert await asyncio.to_thread(owner.close, 20)
            for launch_request in launched:
                await _require_native_closed(native, launch_request)
        phases.observed_closed()


async def _require_native_closed(native, request):
    local = await native.retry_cleanup(request)
    assert not local.cleanup_pending and not local.owner_thread_alive


async def _browser_fork(browser, native, host, owners, dispatches, launched, root, parent):
    from native_twitter_fresh_fixture import (
        _DB_LIMIT, _LOG_LIMIT, _PREPARED, _PROFILE_LIMIT, _RECEIPT_LIMIT, _RESTORE_OUTPUTS, _captured_checkpoint,
    )
    from nexaweave_execution.budget import BudgetDenied, ReservationState
    from nexaweave_execution.native_owned_binding import _manifest
    from nexaweave_execution.native_run_contracts import NativeRunDenied
    from nexaweave_execution.native_twitter_checkpoint_binding import (
        NativeRestoredTwitterFactory, NativeTwitterCaptureFactory,
    )
    from test_native_round_capture import _artifact, _read_bounded

    parent_id = UUID(parent['request']['run_id'])
    assert parent['schema_version'] == 2 and parent['checkpoint'] == {'mode': 'capture'}
    assert dispatches == [parent_id] and len(owners) == 1
    assert parent['cleanup'] == {'known': True, 'pending': False, 'owner_thread_alive': False}
    parent_launch = host.store.get('owner', parent_id)
    parent_run = host.native.get('owner', parent_id)
    driver = owners[0].coordinator.driver
    assert driver._closed and driver._owned is None
    capture = driver._factory
    assert type(capture) is NativeTwitterCaptureFactory
    lineage_path = Path(capture.lineage_dir) / 'lineage.json'
    lineage = _read_bounded(lineage_path, _RECEIPT_LIMIT)
    assert sha256(lineage).hexdigest() == parent_run.receipt.evidence_sha256
    checkpoint = Path(capture.checkpoint_dir)
    _, _, _, manifest, _, limits, _ = _captured_checkpoint(
        checkpoint, json.loads(lineage), _read_bounded(root / 'simulation_config.json', _PROFILE_LIMIT))
    limits.update({lineage_path: _RECEIPT_LIMIT,
                   root / 'twitter_simulation.db': _DB_LIMIT,
                   root / 'twitter' / 'actions.jsonl': _LOG_LIMIT})
    limits.update({root / name: _PROFILE_LIMIT for name in _PREPARED})
    pins = {path: _artifact(path, limit)[:2] for path, limit in limits.items()}
    account = host.budget.status('owner', host.account_id)

    reviewed = await asyncio.to_thread(browser.perform, 'native-fork-review')
    child_id = UUID(reviewed['request']['run_id'])
    assert child_id != parent_id and reviewed['launch_sha256'] != parent['launch_sha256']
    assert reviewed['checkpoint']['intervention'] == {
        'kind': 'swap_active_actor_v1',
        'manifest_sha256': pins[checkpoint / 'manifest.json'][1],
        'runner_state_sha256': manifest['runner_state']['sha256'],
    }
    planned = host.store.get('owner', child_id)
    assert planned.state == 'planned' and not planned.dispatch_claimed
    assert planned.workflow is None and planned.budget_attempt_id is None
    with pytest.raises(NativeRunDenied):
        host.native.get('owner', child_id)
    with pytest.raises(BudgetDenied):
        host.budget.native_reservation('owner', host.account_id, planned.request, planned.launch_sha256)
    child_root = root.parent / ('native_child_' + child_id.hex)
    assert not child_root.exists() and not child_root.is_symlink()
    assert host.budget.status('owner', host.account_id) == account
    assert dispatches == [parent_id] and len(owners) == 1

    started = await asyncio.to_thread(browser.perform, 'native-fork-start')
    assert started['request'] == reviewed['request'] and started['checkpoint'] == reviewed['checkpoint']
    row = host.store.get('owner', child_id)
    launched.append(row.request)
    assert row.dispatch_claimed and row.workflow is not None and row.budget_attempt_id is not None
    receipt = await asyncio.wait_for(native.result(row.request, NativeWorkflowRef(**row.workflow)), 180)
    await _require_native_closed(native, row.request)
    child, reread = await asyncio.to_thread(browser.perform, 'native-fork-ready')
    child_run = host.native.get('owner', child_id)
    child_launch = host.store.get('owner', child_id)
    assert dispatches == [parent_id, child_id] and len(owners) == 2
    assert child['state'] == receipt.outcome == 'completed' and receipt.run_id == child_id
    assert child['receipt'] == receipt.to_wire() == child_launch.receipt
    assert child['checkpoint'] == reviewed['checkpoint'] and child['cleanup'] == parent['cleanup']
    assert child_run.request == planned.request == row.request and child_run.receipt == receipt
    assert child_run.child.process_id != parent_run.child.process_id
    assert child_run.request.source_run_id == parent_id and child_run.request.schema_version == 3
    assert receipt.instance_id == child_run.child.instance_id != parent_run.child.instance_id
    assert receipt.evidence_sha256 != parent_run.receipt.evidence_sha256
    driver = owners[1].coordinator.driver
    assert driver._closed and driver._owned is None
    assert type(driver._factory) is NativeRestoredTwitterFactory
    assert Path(driver._factory.child_dir) == child_root
    child_db, child_log = (child_root / name for name in _RESTORE_OUTPUTS)
    child_size = _artifact(child_db, _DB_LIMIT)[0] + _artifact(child_log, _LOG_LIMIT)[0]
    assert sum(size for size, _ in pins.values()) + child_size <= 160 * 1024 * 1024
    assert _manifest(child_root, _RESTORE_OUTPUTS, _DB_LIMIT) == receipt.evidence_sha256
    reservation = host.budget.native_reservation('owner', host.account_id, row.request, row.launch_sha256)
    assert reservation.state == ReservationState.settled and reservation.attempt_id == row.budget_attempt_id
    assert reservation.receipt.native_receipt == receipt

    for field in ('request', 'launch_sha256', 'checkpoint', 'preparation', 'receipt', 'cleanup'):
        assert reread[field] == parent[field]
    assert host.store.get('owner', parent_id) == parent_launch
    assert host.native.get('owner', parent_id) == parent_run
    assert {path: _artifact(path, limit)[:2] for path, limit in limits.items()} == pins

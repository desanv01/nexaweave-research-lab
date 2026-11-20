"""Owned ordinary browser proof for one-platform preparation and native runs."""
import asyncio
from contextlib import closing
from hashlib import sha256
import json
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
from test_connected_preparation_native_launch import RichConnectedChat
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


def _check_outputs(root, inputs, platform):
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
        assert db.execute("SELECT 1 FROM post WHERE content LIKE 'Scripted connected native post %' LIMIT 1").fetchone()
    actions = _read_artifact(root / platform / 'actions.jsonl', _ARTIFACT_BYTES).splitlines()
    assert actions and all(type(json.loads(line)) is dict for line in actions)
    assert (root / '.native_prepared_start_claim').is_file()


@pytest.mark.asyncio
async def test_twitter_only_suggest_ready_and_native_workbench(factory, tmp_path, monkeypatch):
    await _run_single_platform_workbench(factory, tmp_path, monkeypatch, 'twitter')


@pytest.mark.asyncio
async def test_reddit_only_suggest_ready_and_native_workbench(factory, tmp_path, monkeypatch):
    await _run_single_platform_workbench(factory, tmp_path, monkeypatch, 'reddit')


async def _run_single_platform_workbench(factory, tmp_path, monkeypatch, platform):
    assert _single_browser_platform == platform, f'select this case through --{platform}-browser'
    monkeypatch.setattr(shared, '_actual_graph_journey', True)
    monkeypatch.setattr(shared, '_research_browser_journey', True)
    monkeypatch.setattr(shared, '_fixture_phase_sink', _fixture_phase_sink)
    phases = shared.FixturePhases(f'workbench-{platform}-ordinary')
    phases.start('setup')
    monkeypatch.chdir(tmp_path)
    migrate_all(factory)
    temporal = await loopback_client()
    input_artifacts = tmp_path / 'native-inputs'
    input_artifacts.mkdir()
    prep, source_scope, _, _, _, _ = real_host(factory, input_artifacts, cap=12, chat=RichConnectedChat(), ceiling=2)
    with shared._actual_graph_sources(factory, tmp_path, monkeypatch, prep, source_scope) as selected:
        loop = asyncio.get_running_loop()
        preparation = TemporalPreparationHost(client=temporal, task_queue='mf-workbench-prep-' + uuid4().hex,
                                              trusted_host=prep, allow_dispatch=lambda: True)
        prep.scheduler = preparation.scheduler_for(loop)
        platform_backends = twitter_backends if platform == 'twitter' else reddit_backends
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
        native_host.attach_temporal(native, temporal_bridge(native, loop))
        native_row = None
        report_log = tmp_path / 'actual-report-sdk-requests.jsonl'
        browser = ResearchBrowserSession(tmp_path, prep, native_host, None, selected, report_log,
                                         single_platform=platform)
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
                assert native_row.frozen['identity']['preparation'] == started['preparation']
                assert native_row.request.artifact_sha256 == binding['artifact_sha256']
                assert native_row.request.simulation_id == binding['simulation_id']
                phases.end('native-plan-start')

                phases.start('native-result')
                receipt = await asyncio.wait_for(native.result(native_row.request, NativeWorkflowRef(**native_row.workflow)), 180)
                assert receipt.outcome == 'completed' and len(owners) == 1
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
                _check_outputs(root, inputs, platform)
                assert prep.store.get('owner', planned['operation_id']).frozen['sources'] == selected.snapshots
                sources = SourceStore(factory)
                for record in selected.records:
                    assert sources.get_source('owner', source_scope.project_id, record.source_revision) == record
                original = sources.get_original('owner', source_scope.project_id, selected.records[0].source_revision)
                assert original.source == selected.records[0] and original.content == selected.pdf
                assert not report_log.exists()
                phases.end('native-result')
            assert browser.closed and browser.last_proof == dict(
                context_closed=True, browser_closed=True, private_tree_removed=True)
        finally:
            for owner in owners:
                assert await asyncio.to_thread(owner.close, 20)
            if native_row is not None:
                local = await native.retry_cleanup(native_row.request)
                assert not local.cleanup_pending and not local.owner_thread_alive
        phases.observed_closed()

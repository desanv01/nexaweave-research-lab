"""Opt-in prepared OASIS/CAMEL + real Temporal + disposable PostgreSQL.

Main supplies locked native/Temporal dependencies and the guarded test server.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import replace
from threading import Event
from uuid import uuid4

import pytest

from native_temporal_offline import offline_models, prepared

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def connection_factory():
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    from nexaweave_execution.native_run_store import migrate_native_runs
    from nexaweave_storage import migrate

    if os.getenv("PROJECT_STORE_POSTGRES_INTEGRATION") != "1":
        pytest.skip("disposable PostgreSQL integration disabled")
    dsn = os.getenv("PROJECT_STORE_POSTGRES_TEST_DSN")
    pg_keys = ("PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER",
               "PGPASSWORD", "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE")
    if not dsn or any(os.getenv(key) for key in pg_keys):
        pytest.fail("approved fixture DSN required")
    settings = conninfo_to_dict(dsn)
    if (settings.get("host") != "127.0.0.1" or settings.get("port") != "15432"
            or settings.get("dbname") != "mirofish_operations_test"
            or settings.get("user") != "mirofish_fixture"
            or not settings.get("password")
            or set(settings) - {"host", "port", "dbname", "user", "password", "connect_timeout"}):
        pytest.fail("not the approved disposable fixture")

    def connect():
        return psycopg.connect(dsn, connect_timeout=3)

    with connect() as connection:
        migrate(connection)
        migrate_native_runs(connection)
    return connect


async def _client():
    from temporalio.client import Client

    if os.getenv("TEMPORAL_EXECUTION_INTEGRATION") != "1":
        pytest.skip("explicit disposable Temporal integration disabled")
    if os.getenv("TEMPORAL_TEST_ADDRESS") != "127.0.0.1:17233":
        pytest.fail("approved loopback Temporal address required")
    return await Client.connect("127.0.0.1:17233")


def _snapshot():
    return {"project_id": "proj_1", "name": "Native Temporal fixture",
        "status": "created", "created_at": "2026-09-29T00:00:00",
        "updated_at": "2026-09-29T00:00:00", "files": [],
        "total_text_length": 0, "ontology": None, "analysis_summary": None,
        "graph_id": None, "graph_build_task_id": None,
        "zep_batch_id": "inert", "zep_batch_operation_id": None,
        "simulation_requirement": None, "chunk_size": 500,
        "chunk_overlap": 50, "error": None}


def _prepared_binding(connection_factory, tmp_path):
    from nexaweave_execution.native_owned_binding import NativeOwnedSessionFactory, _manifest
    from nexaweave_execution.native_run_contracts import NativeRunRequest
    from nexaweave_storage import ProjectStore

    root = prepared(tmp_path)
    project_id = uuid4()
    ProjectStore(connection_factory).create("owner", uuid4(), project_id,
                                            "proj_1", _snapshot())
    names = ("state.json", "simulation_config.json", "source_grounding.json",
             "twitter_profiles.csv", "reddit_profiles.json")
    frozen = {name: (root / name).read_bytes() for name in names}
    binding = NativeOwnedSessionFactory(str(root), "graph-fixture", "sim-fixture",
        "owner", str(project_id), 1, ("twitter", "reddit"), 7, 1,
        "b" * 64, offline_models)
    request = NativeRunRequest.from_wire(dict(schema_version=1, principal="owner",
        project_id=project_id, project_revision=1, simulation_id="sim-fixture",
        run_id=uuid4(), artifact_sha256=_manifest(root, names, 2 * 1024 * 1024),
        runtime_sha256="b" * 64, platforms=("twitter", "reddit"),
        seed=7, max_rounds=1))
    return root, request, binding, frozen


def _host(client, connection_factory, request, binding, *, allow=True,
          native_allow=True):
    from app.services.temporal_prepared_host import TemporalPreparedHost

    return TemporalPreparedHost(client=client,
        task_queue="mirofish-prepared-" + uuid4().hex,
        trusted_principal="owner", connection_factory=connection_factory,
        bindings={request: binding},
        allow_dispatch=(lambda: True) if allow else None,
        native_dispatch_allowed=(lambda _: True) if native_allow else None,
        native_options={"lease_seconds": 240, "poll_seconds": 0.2,
            "call_budget_seconds": 20, "handshake_seconds": 10,
            "join_seconds": 3, "go_timeout_seconds": 30})


async def _clean(host, request):
    deadline = asyncio.get_running_loop().time() + 25
    while True:
        local = await host.retry_cleanup(request.to_wire())
        if not local.cleanup_pending and not local.owner_thread_alive:
            assert local.inflight_calls == 0
            return
        if asyncio.get_running_loop().time() >= deadline:
            pytest.fail("retained native owner cleanup not proven")
        await asyncio.sleep(0.1)


@asynccontextmanager
async def _failure_cleanup(host, request, retained):
    """Keep the owning worker alive while failure cleanup targets this run."""
    try:
        yield
    except BaseException as original:
        ref = retained.get("ref")
        if ref is not None:
            for operation in (
                host.cancel(request.to_wire(), ref),
                _clean(host, request),
            ):
                try:
                    await asyncio.wait_for(operation, 30)
                except BaseException as cleanup_error:
                    original.add_note(
                        f"retained native cleanup failed: {type(cleanup_error).__name__}")
        raise


@pytest.mark.asyncio
async def test_actual_both_platforms_temporal_pg_replay_and_fresh_result(
        connection_factory, tmp_path, monkeypatch):
    from nexaweave_execution.native_process_driver import NativeProcessDriver
    from nexaweave_execution.native_run_contracts import RunState
    from nexaweave_execution.native_run_store import NativeRunStore
    from nexaweave_execution.temporal_native_host import NativeTemporalHostError
    from nexaweave_execution.temporal_native_workflow import NativeExecutionWorkflow
    from temporalio.worker import Replayer

    client = await _client()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NEXAWEAVE_NATIVE_TEST_OFFLINE", "1")
    root, request, binding, frozen = _prepared_binding(connection_factory, tmp_path)
    host = _host(client, connection_factory, request, binding)
    store = NativeRunStore(connection_factory)
    trace = []
    launch = NativeProcessDriver.launch
    observe = NativeProcessDriver.observe

    def traced_launch(driver, req, attempt):
        row = store.get("owner", request.run_id)
        trace.append(("launch", row.state, row.attempt_id == attempt,
                      row.child is None))
        return launch(driver, req, attempt)

    def traced_observe(driver, req, attempt, child):
        if not any(item[0] == "go" for item in trace):
            row = store.get("owner", request.run_id)
            trace.append(("go", row.state, row.attempt_id == attempt,
                          row.child == child))
        return observe(driver, req, attempt, child)

    monkeypatch.setattr(NativeProcessDriver, "launch", traced_launch)
    monkeypatch.setattr(NativeProcessDriver, "observe", traced_observe)
    wire = request.to_wire()
    retained = {}
    async with host.worker(), _failure_cleanup(host, request, retained):
        ref = await host.start(wire)
        retained["ref"] = ref
        receipt = await asyncio.wait_for(host.result(wire, ref), 180)
        assert receipt.outcome == "completed" and receipt.run_id == request.run_id
        assert trace == [("launch", RunState.starting, True, True),
                         ("go", RunState.running, True, True)]
        row = store.get("owner", request.run_id)
        assert row.state == RunState.completed and row.receipt == receipt
        assert row.fingerprint == request.fingerprint
        temporal_status = await host.status(wire, ref)
        assert temporal_status.temporal_status == "completed"
        assert temporal_status.native_authoritative is False
        for platform in ("twitter", "reddit"):
            with sqlite3.connect(root / f"{platform}_simulation.db") as conn:
                assert conn.execute("SELECT COUNT(*) FROM user").fetchone()[0] == 2
                posts = [item[0] for item in conn.execute("SELECT content FROM post")]
                assert any(item.startswith("Synthetic native post ") for item in posts)
            assert (root / platform / "actions.jsonl").is_file()
        assert (root / ".native_prepared_start_claim").is_file()
        assert all((root / name).read_bytes() == data for name, data in frozen.items())
        with pytest.raises(NativeTemporalHostError) as duplicate:
            await host.start(wire)
        assert duplicate.value.code == "workflow_exists"
        history = await client.get_workflow_handle(ref.workflow_id,
            run_id=ref.temporal_run_id).fetch_history()
        await Replayer(workflows=[NativeExecutionWorkflow]).replay_workflow(history)
        fresh = _host(client, connection_factory, request, binding)
        assert await fresh.result(wire, ref) == receipt
        assert trace == [("launch", RunState.starting, True, True),
                         ("go", RunState.running, True, True)]
        assert all((root / name).read_bytes() == data for name, data in frozen.items())
        await _clean(host, request)


@pytest.mark.asyncio
async def test_disabled_foreign_and_missing_revision_never_reach_binding(
        connection_factory, tmp_path, monkeypatch):
    from nexaweave_execution.native_run_contracts import NativeRunDenied
    from nexaweave_execution.native_run_store import NativeRunStore
    from nexaweave_execution.temporal_native_host import NativeTemporalHostError

    client = await _client()
    monkeypatch.chdir(tmp_path)
    root, request, binding, _ = _prepared_binding(connection_factory, tmp_path)
    disabled = _host(client, connection_factory, request, binding, allow=False)
    calls = []
    disabled._bind = lambda value: calls.append(value)
    with pytest.raises(NativeTemporalHostError) as off:
        await disabled.start(request.to_wire())
    assert off.value.code == "dispatch_disabled" and calls == []
    enabled = _host(client, connection_factory, request, binding)
    enabled._bind = lambda value: calls.append(value)
    with pytest.raises(NativeTemporalHostError) as foreign:
        await enabled.start({**request.to_wire(), "principal": "foreign"})
    assert foreign.value.code == "native_run_denied" and calls == []

    native_off = _host(client, connection_factory, request, binding,
                       native_allow=False)
    async with native_off.worker():
        off_ref = await native_off.start(request.to_wire())
        with pytest.raises(NativeTemporalHostError) as native_denied:
            await asyncio.wait_for(native_off.result(request.to_wire(), off_ref), 30)
        assert native_denied.value.code == "native_run_denied"
        await _clean(native_off, request)
        assert NativeRunStore(connection_factory).get("owner", request.run_id).state == "declared"
        assert not (root / ".native_prepared_start_claim").exists()

    missing = replace(request, project_revision=2, run_id=uuid4())
    wrong_binding = replace(binding, project_revision=2)
    denied = _host(client, connection_factory, missing, wrong_binding)
    denied._bind = lambda value: calls.append(value)
    # Removing an artifact makes accidental early validation observable. The
    # missing exact revision must be rejected by PostgreSQL first.
    (root / "source_grounding.json").unlink()
    async with denied.worker():
        ref = await denied.start(missing.to_wire())
        with pytest.raises(NativeTemporalHostError) as unavailable:
            await asyncio.wait_for(denied.result(missing.to_wire(), ref), 30)
        assert unavailable.value.code == "native_run_denied"
        assert calls == []
        assert not (root / ".native_prepared_start_claim").exists()
        with pytest.raises(NativeRunDenied):
            NativeRunStore(connection_factory).get("owner", missing.run_id)


@pytest.mark.asyncio
async def test_cancel_during_authorized_binding_is_prelaunch_intent(
        connection_factory, tmp_path, monkeypatch):
    from nexaweave_execution.native_run_store import NativeRunStore
    from nexaweave_execution.temporal_native_host import NativeTemporalHostError

    client = await _client()
    monkeypatch.chdir(tmp_path)
    root, request, binding, _ = _prepared_binding(connection_factory, tmp_path)
    host = _host(client, connection_factory, request, binding)
    store = NativeRunStore(connection_factory)
    entered, release = Event(), Event()
    select = host._bind

    def gated(value):
        row = store.get("owner", request.run_id)
        assert row.request == request and row.state == "declared"
        entered.set()
        assert release.wait(15)
        return select(value)

    host._bind = gated
    wire = request.to_wire()
    try:
        async with host.worker():
            ref = await host.start(wire)
            assert await asyncio.to_thread(entered.wait, 10)
            intent = await host.cancel(wire, ref)
            assert intent.temporal_status == "cancel_requested"
            durable = store.get("owner", request.run_id)
            assert durable.cancel_requested and durable.attempt_id is None
            assert durable.receipt is None
            release.set()
            with pytest.raises(NativeTemporalHostError) as stopped:
                await asyncio.wait_for(host.result(wire, ref), 30)
            assert stopped.value.code == "native_cancel_pending"
            await _clean(host, request)
            row = store.get("owner", request.run_id)
            assert row.cancel_requested and row.attempt_id is None
            assert row.receipt is None
            assert not (root / ".native_prepared_start_claim").exists()
            assert not any(root.glob("*_simulation.db"))
    finally:
        release.set()


@pytest.mark.asyncio
async def test_prior_uncertain_attempt_never_relaunches_native(
        connection_factory, tmp_path, monkeypatch):
    from nexaweave_execution.native_run_contracts import RunState
    from nexaweave_execution.native_run_store import NativeRunStore
    from nexaweave_execution.temporal_native_host import NativeTemporalHostError

    client = await _client()
    monkeypatch.chdir(tmp_path)
    root, request, binding, _ = _prepared_binding(connection_factory, tmp_path)
    store = NativeRunStore(connection_factory)
    store.register(request)
    old_owner = uuid4()
    claim = store.claim_start("owner", request.run_id, old_owner, 20)
    store.mark_uncertain("owner", request.run_id, claim.attempt_id, old_owner)
    host = _host(client, connection_factory, request, binding)
    wire = request.to_wire()
    async with host.worker():
        ref = await host.start(wire)
        with pytest.raises(NativeTemporalHostError) as fenced:
            await asyncio.wait_for(host.result(wire, ref), 30)
        assert fenced.value.code == "native_run_uncertain"
        assert store.get("owner", request.run_id).state == RunState.uncertain
        assert not (root / ".native_prepared_start_claim").exists()
        assert not any(root.glob("*_simulation.db"))
        await _clean(host, request)

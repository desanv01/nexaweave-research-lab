"""Opt-in real prepared OASIS/CAMEL run under disposable PostgreSQL authority.

Main runs this suite with the locked native engine and guarded fixture DSN.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import replace
from threading import Event
from uuid import uuid4

import pytest

from offline_fixture import offline_models, prepared

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def connection_factory():
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    from mirofish_execution.native_run_store import migrate_native_runs
    from mirofish_storage import migrate

    if os.getenv("PROJECT_STORE_POSTGRES_INTEGRATION") != "1":
        pytest.skip("disposable PostgreSQL integration disabled")
    dsn = os.getenv("PROJECT_STORE_POSTGRES_TEST_DSN")
    pg_keys = ("PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER",
               "PGPASSWORD", "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE")
    if not dsn or any(os.getenv(key) for key in pg_keys):
        pytest.fail("approved fixture DSN required")
    settings = conninfo_to_dict(dsn)
    if (settings.get("host") != "127.0.0.1"
            or settings.get("port") != "15432"
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


def _snapshot():
    return {"project_id": "proj_1", "name": "Native fixture", "status": "created",
        "created_at": "2026-09-29T00:00:00", "updated_at": "2026-09-29T00:00:00",
        "files": [], "total_text_length": 0, "ontology": None,
        "analysis_summary": None, "graph_id": None, "graph_build_task_id": None,
        "zep_batch_id": "inert", "zep_batch_operation_id": None,
        "simulation_requirement": None, "chunk_size": 500, "chunk_overlap": 50,
        "error": None}


def _prepared_host(connection_factory, tmp_path, *, dispatch=True):
    from app.services.native_prepared_host import NativePreparedHost
    from mirofish_execution.native_owned_binding import NativeOwnedSessionFactory, _manifest
    from mirofish_execution.native_run_contracts import NativeRunRequest
    from mirofish_storage import ProjectStore

    root = prepared(tmp_path)
    project_id = uuid4()
    ProjectStore(connection_factory).create("owner", uuid4(), project_id,
                                            "proj_1", _snapshot())
    names = ("state.json", "simulation_config.json", "source_grounding.json",
             "twitter_profiles.csv", "reddit_profiles.json")
    original = {name: (root / name).read_bytes() for name in names}
    binding = NativeOwnedSessionFactory(str(root), "graph-fixture", "sim-fixture",
        "owner", str(project_id), 1, ("twitter", "reddit"), 7, 1,
        "b" * 64, offline_models)
    request = NativeRunRequest.from_wire(dict(schema_version=1, principal="owner",
        project_id=project_id, project_revision=1, simulation_id="sim-fixture",
        run_id=uuid4(), artifact_sha256=_manifest(root, names, 2 * 1024 * 1024),
        runtime_sha256="b" * 64, platforms=("twitter", "reddit"), seed=7,
        max_rounds=1))
    def host(req=request, *, allowed=dispatch):
        return NativePreparedHost(principal="owner", request=req,
            session_factory=binding, connection_factory=connection_factory,
            dispatch_allowed=(lambda _: True) if allowed else None,
            lease_seconds=240, poll_seconds=0.2, call_budget_seconds=20,
            handshake_seconds=10, go_timeout_seconds=30, join_seconds=3)
    return root, binding, request, original, host


def _close(host):
    assert host.close(20), "owned child cleanup did not finish"
    assert host.driver._owned is None and host.driver._closed


@pytest.mark.parametrize("bounds", [
    {"handshake_seconds": 10, "join_seconds": 2, "call_budget_seconds": 15},
    {"handshake_seconds": 1, "observe_seconds": 10, "join_seconds": 2,
     "call_budget_seconds": 15},
    {"handshake_seconds": 1, "grace_seconds": 10, "join_seconds": 2,
     "call_budget_seconds": 13},
    {"handshake_seconds": float("nan")},
    {"join_seconds": float("inf")},
    {"call_budget_seconds": float("nan")},
    {"lease_seconds": 20, "call_budget_seconds": 20},
])
def test_host_rejects_unpromised_or_nonfinite_driver_bounds(bounds, tmp_path):
    from app.services.native_prepared_host import NativePreparedHost
    from mirofish_execution.native_owned_binding import NativeOwnedSessionFactory
    from mirofish_execution.native_run_contracts import InvalidNativeRun, NativeRunRequest

    project = uuid4()
    binding = NativeOwnedSessionFactory(str(tmp_path / "unread"), "graph-fixture",
        "sim-fixture", "owner", str(project), 1, ("twitter", "reddit"),
        7, 1, "b" * 64, offline_models)
    request = NativeRunRequest.from_wire(dict(schema_version=1, principal="owner",
        project_id=project, project_revision=1, simulation_id="sim-fixture",
        run_id=uuid4(), artifact_sha256="a" * 64, runtime_sha256="b" * 64,
        platforms=("twitter", "reddit"), seed=7, max_rounds=1))
    with pytest.raises(InvalidNativeRun):
        NativePreparedHost(principal="owner", request=request,
            session_factory=binding, connection_factory=lambda: None, **bounds)
    assert not (tmp_path / "unread").exists()


def test_both_platforms_durable_attachment_and_exact_terminal_recovery(
        connection_factory, tmp_path, monkeypatch):
    from mirofish_execution.native_run_contracts import RunState
    from mirofish_execution.native_run_store import NativeRunStore

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MIROFISH_NATIVE_TEST_OFFLINE", "1")
    root, _, request, original, new_host = _prepared_host(connection_factory, tmp_path)
    host = new_host()
    attached_before_go = []
    observe = host.driver.observe

    def observed(req, attempt, child):
        if not attached_before_go:
            saved = NativeRunStore(connection_factory).get("owner", request.run_id)
            attached_before_go.append(saved.state == RunState.running
                and saved.attempt_id == attempt and saved.child == child)
        return observe(req, attempt, child)

    host.driver.observe = observed
    try:
        begun = host.start(35)
        assert begun.run_state in (RunState.running, RunState.completed)
        result = host.wait(180)
        assert attached_before_go == [True]
        assert result.run_state == RunState.completed and result.receipt is not None
        saved = NativeRunStore(connection_factory).get("owner", request.run_id)
        assert saved.state == RunState.completed and saved.receipt == result.receipt
        assert saved.fingerprint == request.fingerprint
        for platform in ("twitter", "reddit"):
            with sqlite3.connect(root / f"{platform}_simulation.db") as connection:
                assert connection.execute("SELECT COUNT(*) FROM user").fetchone()[0] == 2
                posts = [row[0] for row in connection.execute("SELECT content FROM post")]
                assert any(post.startswith("Synthetic native post ") for post in posts)
            assert (root / platform / "actions.jsonl").is_file()
        assert (root / ".native_prepared_start_claim").is_file()
        assert all((root / name).read_bytes() == data for name, data in original.items())
    finally:
        _close(host)

    evidence = {item.name: item.stat().st_mtime_ns for item in root.iterdir()}
    fresh = new_host()
    try:
        recovered = fresh.start(10)
        assert recovered.run_state == RunState.completed
        assert recovered.receipt == saved.receipt
        assert fresh.driver._spent is False and fresh.driver._owned is None
        assert evidence == {item.name: item.stat().st_mtime_ns for item in root.iterdir()}
        with pytest.raises(Exception) as repeated:
            fresh.start(1)
        assert getattr(repeated.value, "code", None) == "native_supervisor_busy"
    finally:
        _close(fresh)


def test_dispatch_off_and_foreign_revision_denied_before_artifacts(
        connection_factory, tmp_path, monkeypatch):
    from app.services.native_prepared_host import NativePreparedHost
    from mirofish_execution.native_run_store import NativeRunStore

    monkeypatch.chdir(tmp_path)
    root, binding, request, _, new_host = _prepared_host(connection_factory, tmp_path)
    host = new_host(allowed=False)
    try:
        stopped = host.start(10)
        assert stopped.error_code == "native_run_denied"
        assert NativeRunStore(connection_factory).get("owner", request.run_id).state == "declared"
        assert not (root / ".native_prepared_start_claim").exists()
    finally:
        _close(host)

    # Make the path unreadable by removing only this fixture's artifact. Store
    # denial must precede any factory validation or native side effect.
    (root / "source_grounding.json").unlink()
    for wrong, bound in (
        (replace(request, principal="foreign", run_id=uuid4()),
         replace(binding, principal="foreign")),
        (replace(request, project_id=uuid4(), run_id=uuid4()), None),
        (replace(request, project_revision=2, run_id=uuid4()),
         replace(binding, project_revision=2)),
    ):
        if bound is None:
            bound = replace(binding, project_id=str(wrong.project_id))
        denied = NativePreparedHost(principal=wrong.principal, request=wrong,
            session_factory=bound, connection_factory=connection_factory,
            dispatch_allowed=lambda _: True, lease_seconds=240)
        try:
            outcome = denied.start(10)
            assert outcome.error_code == "native_run_denied"
            assert not (root / ".native_prepared_start_claim").exists()
        finally:
            _close(denied)
    from mirofish_execution.native_run_contracts import NativeRunDenied
    with pytest.raises(NativeRunDenied):
        NativePreparedHost(principal="owner", request=replace(request,
            project_revision=2, run_id=uuid4()), session_factory=binding,
            connection_factory=connection_factory, dispatch_allowed=lambda _: True)


@pytest.mark.parametrize("change", ["artifact", "runtime", "changed-input"])
def test_mismatch_fences_without_native_side_effects(connection_factory, tmp_path,
                                                      monkeypatch, change):
    from mirofish_execution.native_run_contracts import RunState
    from mirofish_execution.native_run_store import NativeRunStore

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MIROFISH_NATIVE_TEST_OFFLINE", "1")
    root, _, request, original, new_host = _prepared_host(connection_factory, tmp_path)
    wrong = (replace(request, artifact_sha256="a" * 64) if change == "artifact"
             else replace(request, runtime_sha256="c" * 64) if change == "runtime"
             else request)
    if change == "changed-input":
        path = root / "source_grounding.json"
        path.write_bytes(path.read_bytes() + b"\n")
        original[path.name] = path.read_bytes()
    host = new_host(wrong)
    try:
        outcome = host.start(35)
        assert outcome.run_state == RunState.uncertain
        assert NativeRunStore(connection_factory).get("owner", wrong.run_id).state == RunState.uncertain
        assert not (root / ".native_prepared_start_claim").exists()
        assert not any(root.glob("*_simulation.db"))
        assert all((root / name).read_bytes() == data for name, data in original.items())
    finally:
        _close(host)
    retry = new_host(wrong)
    try:
        result = retry.start(10)
        assert result.run_state == RunState.uncertain
        assert retry.driver._spent is False
    finally:
        _close(retry)


def test_cancel_during_dispatch_gate_prevents_claim_and_native_launch(
        connection_factory, tmp_path, monkeypatch):
    from app.services.native_prepared_host import NativePreparedHost
    from mirofish_execution.native_run_store import NativeRunStore
    from mirofish_execution.native_run_supervisor import SupervisorWaitTimeout

    monkeypatch.chdir(tmp_path)
    root, binding, request, _, _ = _prepared_host(connection_factory, tmp_path)
    entered, release = Event(), Event()

    def dispatch(_):
        entered.set()
        return release.wait(10)

    host = NativePreparedHost(principal="owner", request=request,
        session_factory=binding, connection_factory=connection_factory,
        dispatch_allowed=dispatch, lease_seconds=240)
    try:
        with pytest.raises(SupervisorWaitTimeout):
            host.start(0.2)
        assert entered.wait(5)
        assert host.request_cancel().cancel_requested
        release.set()
        result = host.wait(10)
        assert result.cancel_requested
        saved = NativeRunStore(connection_factory).get("owner", request.run_id)
        assert saved.cancel_requested and saved.attempt_id is None
        assert not (root / ".native_prepared_start_claim").exists()
        assert not any(root.glob("*_simulation.db"))
        assert host.driver._spent is False
    finally:
        release.set()
        _close(host)

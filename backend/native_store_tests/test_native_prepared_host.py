"""Opt-in real prepared OASIS/CAMEL run under disposable PostgreSQL authority.

Main runs this suite with the locked native engine and guarded fixture DSN.
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import replace
from hashlib import sha256
from itertools import islice
from threading import Event
from time import monotonic
from uuid import uuid4

import pytest

from offline_fixture import offline_models, prepared

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
    from nexaweave_execution.native_owned_binding import NativeOwnedSessionFactory, _manifest
    from nexaweave_execution.native_run_contracts import NativeRunRequest
    from nexaweave_storage import ProjectStore

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


def _checkpoint_wait(deadline, limit):
    remaining = min(limit, deadline - monotonic() - 20)
    assert remaining > 0, "continuation deadline reached its cleanup reserve"
    return remaining


def _completed_checkpoint_host(host, deadline):
    from nexaweave_execution.native_run_contracts import RunState
    from nexaweave_execution.native_run_supervisor import SupervisorBusy

    observed_terminal = []
    observe = host.driver.observe

    def observe_child(request, attempt, child):
        result = observe(request, attempt, child)
        if result.status == "completed":
            owned = host.driver._owned
            assert owned is not None and owned.child == child
            assert owned.process.pid == child.process_id and owned.process.exitcode == 0
            observed_terminal.append((attempt, child, result.receipt))
        return result

    host.driver.observe = observe_child
    try:
        started = host.start(_checkpoint_wait(deadline, 35))
        assert started.run_state in (RunState.running, RunState.completed)
        finished = host.wait(_checkpoint_wait(deadline, 180))
        assert finished.run_state == RunState.completed and finished.error_code is None
        saved = host.store.get(host.request.principal, host.request.run_id)
        assert saved.state == RunState.completed and saved.request == host.request
        assert saved.fingerprint == host.request.fingerprint
        assert saved.child is not None and saved.receipt is not None and saved.receipt == finished.receipt
        assert saved.lease_until is None and not saved.cancel_requested
        assert observed_terminal == [(saved.attempt_id, saved.child, saved.receipt)]
        with pytest.raises(SupervisorBusy):
            host.start(_checkpoint_wait(deadline, 1))
        assert host.driver._spent
    finally:
        _close(host)
    assert monotonic() < deadline
    return saved


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
    from nexaweave_execution.native_owned_binding import NativeOwnedSessionFactory
    from nexaweave_execution.native_run_contracts import InvalidNativeRun, NativeRunRequest

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
    from nexaweave_execution.native_run_contracts import RunState
    from nexaweave_execution.native_run_store import NativeRunStore

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NEXAWEAVE_NATIVE_TEST_OFFLINE", "1")
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


def test_trusted_host_consumes_completed_twitter_checkpoint_once(
        connection_factory, tmp_path, monkeypatch):
    from app.services.native_prepared_host import NativePreparedHost
    from native_twitter_fresh_fixture import (
        _DB_LIMIT, _LOG_LIMIT, _NEXT_POST, _PROFILE_LIMIT, _RECEIPT_LIMIT, _RESTORE_OUTPUTS,
        _RestoredModels, _captured_checkpoint, _captured_owners, _check_binding, _events,
        _owned_factory, _prepared_twitter, _producer_models,
    )
    from nexaweave_execution import native_process_driver
    from nexaweave_execution.native_owned_binding import _manifest
    from nexaweave_execution.native_run_contracts import NativeRunDenied, NativeRunRequest, RunState
    from nexaweave_execution.native_run_store import NativeRunStore
    from nexaweave_execution.native_twitter_checkpoint_binding import (
        NativeRestoredTwitterFactory, NativeTwitterCaptureFactory,
    )
    from nexaweave_storage import ProjectStore
    from test_native_round_capture import _artifact, _native_rows, _read_bounded

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NEXAWEAVE_NATIVE_TEST_OFFLINE", "1")
    deadline = monotonic() + 180
    project = uuid4()
    ProjectStore(connection_factory).create("owner", uuid4(), project, "proj_1", _snapshot())
    original = NativeRunRequest.from_wire(dict(
        schema_version=1, principal="owner", project_id=project, project_revision=1,
        simulation_id="sim-fixture", run_id=uuid4(), artifact_sha256="a" * 64,
        runtime_sha256="b" * 64, platforms=("twitter",), seed=7, max_rounds=1,
    ))
    root, prepared_inputs, prepared_binding, capture_request = _prepared_twitter(
        tmp_path / "producer", original, 1, _producer_models,
    )
    checkpoint = root.parent / "checkpoint"
    lineage_dir = tmp_path / "lineage"
    lineage_path = lineage_dir / "lineage.json"
    child_dir = tmp_path / "restored"
    probe_path = tmp_path / "restored-memory-probe.json"
    anchors = {
        "graph_id": "graph-fixture", "simulation_id": capture_request.simulation_id,
        "run_id": str(capture_request.run_id), "branch_id": str(uuid4()),
        "project_id": str(project), "project_revision": capture_request.project_revision,
    }

    def new_host(request, binding):
        return NativePreparedHost(principal="owner", request=request, session_factory=binding,
            connection_factory=connection_factory, dispatch_allowed=lambda _: True,
            lease_seconds=240, poll_seconds=0.2, call_budget_seconds=20,
            handshake_seconds=10, go_timeout_seconds=30, join_seconds=3)

    capture = NativeTwitterCaptureFactory(prepared_binding, str(checkpoint), str(lineage_dir), anchors)
    produced = _completed_checkpoint_host(new_host(capture_request, capture), deadline)
    assert produced.receipt.run_id == capture_request.run_id
    producer_pid = produced.child.process_id
    assert producer_pid != os.getpid() and not child_dir.exists()
    lineage_raw = _read_bounded(lineage_path, _RECEIPT_LIMIT)
    checkpoint_digest = sha256(lineage_raw).hexdigest()
    assert checkpoint_digest == produced.receipt.evidence_sha256
    lineage = json.loads(lineage_raw)
    _check_binding(lineage, producer_pid, anchors, prepared_inputs, capture_request.artifact_sha256)
    database, memory, log, manifest, _, limits, pins = _captured_checkpoint(
        checkpoint, lineage, prepared_inputs["simulation_config.json"],
    )
    producer_db = root / "twitter_simulation.db"
    producer_log = root / "twitter" / "actions.jsonl"
    limits.update({producer_db: _DB_LIMIT, producer_log: _LOG_LIMIT, lineage_path: _RECEIPT_LIMIT})
    for path in (producer_db, producer_log, lineage_path):
        pins[path] = _artifact(path, limits[path])
    checkpoint_rows = _native_rows(database)
    _, old_trace, old_posts, _ = checkpoint_rows
    assert _native_rows(producer_db) == checkpoint_rows
    prefix = _read_bounded(log, _LOG_LIMIT)
    assert _read_bounded(producer_log, _LOG_LIMIT).startswith(prefix)
    assert _events(prefix)[-1]["event_type"] == "round_end" and len(old_posts) == 3
    _, expected_frames = zip(*_captured_owners(memory, lineage["owners"]), strict=True)

    request = replace(capture_request, schema_version=2, run_id=uuid4(),
                      source_run_id=produced.receipt.run_id, checkpoint_sha256=checkpoint_digest)
    restore = NativeRestoredTwitterFactory(
        _owned_factory(root, request, _RestoredModels(expected_frames, str(probe_path))),
        str(checkpoint), str(lineage_dir), str(child_dir), checkpoint_digest, producer_pid,
        str(produced.receipt.run_id), None,
    )
    store = NativeRunStore(connection_factory)

    def unexpected_dispatch(*_args, **_kwargs):
        pytest.fail("checkpoint refusal or recovery reached native dispatch")

    with monkeypatch.context() as preflight:
        preflight.setattr(native_process_driver, "NativeProcessDriver", unexpected_dispatch)
        preflight.setattr(NativeRestoredTwitterFactory, "validate", unexpected_dispatch)
        bad_digest = "0" * 64 if checkpoint_digest != "0" * 64 else "1" * 64
        for invalid in (
            replace(request, run_id=uuid4(), checkpoint_sha256=bad_digest),
            replace(request, run_id=uuid4(), source_run_id=uuid4()),
        ):
            with pytest.raises(NativeRunDenied):
                new_host(invalid, restore)
            with pytest.raises(NativeRunDenied):
                store.get("owner", invalid.run_id)
    assert not child_dir.exists() and not probe_path.exists()

    restored = _completed_checkpoint_host(new_host(request, restore), deadline)
    assert restored.child.process_id not in {os.getpid(), producer_pid}
    assert restored.receipt.run_id == request.run_id and restored.attempt_id != produced.attempt_id
    assert restored.request.source_run_id == produced.receipt.run_id
    assert restored.request.checkpoint_sha256 == produced.receipt.evidence_sha256
    assert {path.name for path in islice(child_dir.iterdir(), 3)} == set(_RESTORE_OUTPUTS)
    assert _manifest(child_dir, _RESTORE_OUTPUTS, _DB_LIMIT) == restored.receipt.evidence_sha256
    child_db = child_dir / "native.sqlite3"
    child_log = child_dir / "actions.jsonl"
    output_pins = {child_db: _artifact(child_db, _DB_LIMIT), child_log: _artifact(child_log, _LOG_LIMIT),
                   probe_path: _artifact(probe_path, _RECEIPT_LIMIT)}
    assert sum(item[0] for item in (*pins.values(), *output_pins.values())) <= 160 * 1024 * 1024
    assert not child_db.samefile(database) and not child_db.samefile(producer_db)
    _, trace, posts, recommendations = _native_rows(child_db)
    assert trace[:len(old_trace)] == old_trace and posts[:len(old_posts)] == old_posts
    (post_id, owner, content, tick), = posts[len(old_posts):]
    assert post_id > old_posts[-1][0] and content == _NEXT_POST
    assert tick == manifest["engine_clock"]["after_step"]
    (_, trace_owner, _, info, trace_tick), = (
        row for row in trace[len(old_trace):] if row[2] == "create_post"
    )
    assert trace_owner == owner and trace_tick == tick
    assert json.loads(info)["post_id"] == post_id and json.loads(info)["content"] == content
    assert len(recommendations) == 4 and all(recommended != post_id for _, recommended in recommendations)
    proof = json.loads(_read_bounded(probe_path, _RECEIPT_LIMIT))
    assert proof == {"pid": restored.child.process_id, "frames": [list(frame) for frame in expected_frames],
                     "step": {"agent_id": owner, "post_id": post_id, "after_step": tick + 1}}
    staged_log = _read_bounded(child_log, _LOG_LIMIT)
    assert staged_log.startswith(prefix)
    first, action, last = _events(staged_log[len(prefix):])
    assert first == {"round": 2, "event_type": "round_start", "simulated_hour": 1}
    assert action["action_type"] == "CREATE_POST" and action["success"] is True
    assert action["agent_id"] == owner and action["agent_name"] == lineage["owners"][owner]["name"]
    assert action["action_args"]["post_id"] == post_id and action["action_args"]["content"] == content
    assert last == {"round": 2, "event_type": "round_end", "actions_count": 1}

    replay = new_host(request, restore)
    replay.driver.launch = unexpected_dispatch
    try:
        recovered = replay.start(_checkpoint_wait(deadline, 10))
        assert recovered.run_state == RunState.completed and recovered.receipt == restored.receipt
        assert recovered.error_code is None and not replay.driver._spent
        assert store.get("owner", request.run_id) == restored
    finally:
        _close(replay)

    competing_dir = tmp_path / "competing-restore"
    competing = replace(request, run_id=uuid4())
    competitor = new_host(competing, replace(restore, child_dir=str(competing_dir)))
    competitor.driver.launch = unexpected_dispatch
    try:
        refused = competitor.start(_checkpoint_wait(deadline, 10))
        assert refused.error_code == "native_run_conflict" and not competitor.driver._spent
    finally:
        _close(competitor)
    with pytest.raises(NativeRunDenied):
        store.get("owner", competing.run_id)
    assert not competing_dir.exists()
    assert store.get("owner", capture_request.run_id) == produced
    assert {path: _artifact(path, limit) for path, limit in limits.items()} == pins
    assert {child_db: _artifact(child_db, _DB_LIMIT), child_log: _artifact(child_log, _LOG_LIMIT),
            probe_path: _artifact(probe_path, _RECEIPT_LIMIT)} == output_pins
    assert {name: _read_bounded(root / name, _PROFILE_LIMIT) for name in prepared_inputs} == prepared_inputs
    assert monotonic() < deadline


def test_dispatch_off_and_foreign_revision_denied_before_artifacts(
        connection_factory, tmp_path, monkeypatch):
    from app.services.native_prepared_host import NativePreparedHost
    from nexaweave_execution.native_run_store import NativeRunStore

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
    from nexaweave_execution.native_run_contracts import NativeRunDenied
    with pytest.raises(NativeRunDenied):
        NativePreparedHost(principal="owner", request=replace(request,
            project_revision=2, run_id=uuid4()), session_factory=binding,
            connection_factory=connection_factory, dispatch_allowed=lambda _: True)


@pytest.mark.parametrize("change", ["artifact", "runtime", "changed-input"])
def test_mismatch_fences_without_native_side_effects(connection_factory, tmp_path,
                                                      monkeypatch, change):
    from nexaweave_execution.native_run_contracts import RunState
    from nexaweave_execution.native_run_store import NativeRunStore

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NEXAWEAVE_NATIVE_TEST_OFFLINE", "1")
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
    from nexaweave_execution.native_run_store import NativeRunStore
    from nexaweave_execution.native_run_supervisor import SupervisorWaitTimeout

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

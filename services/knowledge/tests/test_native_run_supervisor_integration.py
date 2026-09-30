"""Opt-in loopback PostgreSQL with an owned offline spawned child."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest

from mirofish_execution.native_process_driver import NativeProcessDriver
from mirofish_execution.native_run_contracts import NativeRunRequest, RunState
from mirofish_execution.native_run_coordinator import NativeRunCoordinator
from mirofish_execution.native_run_store import NativeRunStore, migrate_native_runs
from mirofish_execution.native_run_supervisor import NativeRunSupervisor
from mirofish_storage import ProjectStore
from test_project_store import snapshot
from test_project_store_postgres import factory  # exact guarded port 15432 fixture

pytestmark = pytest.mark.postgres


@dataclass
class GateSession:
    started: str
    release: str
    finished: str

    def start(self):
        Path(self.started).write_text("started", encoding="ascii")
        deadline = time.monotonic() + 40
        while not Path(self.release).exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("fixture_gate_timeout")
            time.sleep(0.05)
        Path(self.finished).write_text("finished", encoding="ascii")

    def close(self):
        pass


@dataclass
class GateFactory:
    started: str
    release: str
    finished: str

    def validate(self, request):
        assert request.simulation_id == "sim_1"

    def create_session(self, request):
        return GateSession(self.started, self.release, self.finished)

    def evidence(self, request):
        return hashlib.sha256(Path(self.finished).read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def native_schema(factory):
    with factory() as conn:
        migrate_native_runs(conn)


def owned(factory):
    project_id = uuid4()
    ProjectStore(factory).create("owner", uuid4(), project_id, "proj_1", snapshot())
    return NativeRunRequest.from_wire(dict(schema_version=1, principal="owner",
        project_id=project_id, project_revision=1, simulation_id="sim_1",
        run_id=uuid4(), artifact_sha256="a" * 64, runtime_sha256="b" * 64,
        platforms=("twitter",), seed=7, max_rounds=1))


def host(factory, request, tmp_path):
    paths = tuple(str(tmp_path / name) for name in ("started", "release", "finished"))
    driver = NativeProcessDriver(GateFactory(*paths), observe_seconds=0.02,
                                 grace_seconds=0.1, join_seconds=0.2,
                                 go_timeout_seconds=30)
    coordinator = NativeRunCoordinator("owner", NativeRunStore(factory), driver,
        dispatch_allowed=lambda _: True, lease_seconds=20)
    supervisor = NativeRunSupervisor(request, coordinator, poll_seconds=0.1,
                                     call_budget_seconds=5)
    return supervisor, driver, tuple(Path(path) for path in paths)


def await_file(path: Path, seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    pytest.fail("offline fixture did not reach bounded marker")


def test_heartbeat_beyond_lease_terminal_recovery_and_cleanup(factory, tmp_path):
    request = owned(factory)
    supervisor, driver, (started, release, finished) = host(factory, request, tmp_path)
    try:
        assert supervisor.start(10).run_state == RunState.running
        await_file(started)
        # A real DB-clock interval longer than the 20-second lease proves that
        # the background owner, rather than a caller polling, renewed it.
        assert not Event().wait(21)
        live = NativeRunStore(factory).get("owner", request.run_id)
        assert live.state == RunState.running and live.receipt is None
        release.write_text("go", encoding="ascii")
        result = supervisor.wait(10)
        assert result.run_state == RunState.completed and result.receipt is not None
        assert finished.read_text(encoding="ascii") == "finished"
        assert supervisor.close(10)
        assert driver._closed and driver._owned is None
        fresh_driver = NativeProcessDriver(GateFactory(str(tmp_path / "other-start"),
            str(tmp_path / "other-release"), str(tmp_path / "other-finish")))
        fresh = NativeRunCoordinator("owner", NativeRunStore(factory), fresh_driver,
                                     dispatch_allowed=lambda _: True)
        try:
            assert fresh.start(request).receipt == result.receipt
            assert not (tmp_path / "other-start").exists()
        finally:
            fresh_driver.close()
    finally:
        release.write_text("release", encoding="ascii")
        supervisor.close(10)


def test_independent_cancellation_is_consumed_by_owner(factory, tmp_path):
    request = owned(factory)
    supervisor, driver, (started, release, finished) = host(factory, request, tmp_path)
    try:
        assert supervisor.start(10).run_state == RunState.running
        await_file(started)
        other_driver = NativeProcessDriver(GateFactory(str(tmp_path / "other-start"),
            str(tmp_path / "other-release"), str(tmp_path / "other-finish")))
        other = NativeRunCoordinator("owner", NativeRunStore(factory), other_driver,
                                     dispatch_allowed=lambda _: True)
        try:
            intent = other.request_cancel(request.run_id)
            assert intent.cancel_requested and intent.state == RunState.running
            result = supervisor.wait(10)
            assert result.run_state == RunState.cancelled
            assert result.receipt is not None and result.receipt.outcome == "cancelled"
            assert not finished.exists()
            assert not (tmp_path / "other-start").exists()
        finally:
            other_driver.close()
        assert supervisor.close(10)
        assert driver._closed and driver._owned is None
    finally:
        release.write_text("release", encoding="ascii")
        supervisor.close(10)


def test_db_clock_expiry_fences_without_restart(factory, tmp_path):
    request = owned(factory)
    supervisor, driver, (started, release, finished) = host(factory, request, tmp_path)
    try:
        assert supervisor.start(10).run_state == RunState.running
        await_file(started)
        with factory() as conn:
            conn.execute("UPDATE mf_native_execution.runs SET "
                         "lease_until=clock_timestamp()-interval '1 second' WHERE run_id=%s",
                         (request.run_id,))
        result = supervisor.wait(10)
        assert result.run_state == RunState.uncertain
        assert result.receipt is None
        assert supervisor.close(10)
        assert driver._closed and driver._owned is None
        assert NativeRunStore(factory).get("owner", request.run_id).state == RunState.uncertain
        assert not finished.exists()
    finally:
        release.write_text("release", encoding="ascii")
        supervisor.close(10)

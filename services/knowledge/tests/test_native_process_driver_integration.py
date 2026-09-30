"""Opt-in guarded PostgreSQL ownership with an actual spawned owned child."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from mirofish_execution.native_process_driver import NativeProcessDriver
from mirofish_execution.native_run_contracts import NativeRunBusy, NativeRunRequest, RunState
from mirofish_execution.native_run_coordinator import NativeRunCoordinator
from mirofish_execution.native_run_store import NativeRunStore, migrate_native_runs
from mirofish_storage import ProjectStore
from test_native_process_driver import FileFactory
from test_project_store import snapshot
from test_project_store_postgres import factory  # guarded port 15432

pytestmark = pytest.mark.postgres


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


def test_durable_attach_go_terminal_and_recovery(factory, tmp_path):
    request = owned(factory)
    driver = NativeProcessDriver(FileFactory(str(tmp_path / "started")))
    store = NativeRunStore(factory)
    host = NativeRunCoordinator("owner", store, driver,
                                dispatch_allowed=lambda _: True)
    try:
        running = host.start(request)
        assert running.state == RunState.running
        assert store.get("owner", request.run_id).state == RunState.running
        assert not (tmp_path / "started").exists()  # first observe sends go
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = host.observe(request.run_id)
            if result.state != RunState.running:
                break
            time.sleep(0.02)
        else:
            pytest.fail("bounded coordinator observation timed out")
        assert result.state == RunState.completed and result.receipt is not None
        assert NativeRunStore(factory).get("owner", request.run_id).receipt == result.receipt
        new_host = NativeRunCoordinator("owner", NativeRunStore(factory),
                                        NativeProcessDriver(FileFactory(str(tmp_path / "other"))),
                                        dispatch_allowed=lambda _: True)
        assert new_host.start(request).receipt == result.receipt
        assert not (tmp_path / "other").exists()
    finally:
        driver.close()


def test_durable_cancel_before_go(factory, tmp_path):
    request = owned(factory)
    marker = tmp_path / "cancelled-before-start"
    driver = NativeProcessDriver(FileFactory(str(marker)))
    host = NativeRunCoordinator("owner", NativeRunStore(factory), driver,
                                dispatch_allowed=lambda _: True)
    try:
        running = host.start(request)
        assert running.state == RunState.running
        final = host.request_cancel(request.run_id)
        assert final.state == RunState.cancelled
        assert final.receipt.outcome == "cancelled"
        assert not marker.exists()
    finally:
        driver.close()


def test_competing_coordinators_launch_one_owned_child(factory, tmp_path):
    request = owned(factory)
    gate = Barrier(2)

    class RacingStore(NativeRunStore):
        def register(self, value):
            record = super().register(value)
            gate.wait(timeout=5)
            return record

    marker = tmp_path / "one-launch"
    driver = NativeProcessDriver(FileFactory(str(marker)))
    hosts = [NativeRunCoordinator("owner", RacingStore(factory), driver,
                                   dispatch_allowed=lambda _: True)
             for _ in range(2)]

    def start(host):
        try:
            return host, host.start(request)
        except NativeRunBusy:
            return host, None

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(start, hosts))
        winners = [(host, record) for host, record in results if record is not None]
        assert len(winners) == 1
        winner, running = winners[0]
        assert running.state == RunState.running
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            settled = winner.observe(request.run_id)
            if settled.state != RunState.running:
                break
            time.sleep(0.02)
        else:
            pytest.fail("bounded competing-owner observation timed out")
        assert settled.state == RunState.completed
        assert marker.read_text(encoding="ascii") == "started"
    finally:
        driver.close()

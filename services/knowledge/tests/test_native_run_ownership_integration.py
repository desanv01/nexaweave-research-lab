"""Opt-in, disposable PostgreSQL ownership and one-shot launch qualification."""
from __future__ import annotations

import hashlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from mirofish_execution.native_run_contracts import (NativeChildIdentity, NativeRunBusy,
    NativeRunConflict, NativeRunDenied, NativeRunReceipt, NativeRunRequest,
    NativeRunMigrationMismatch, NativeRunUnavailable, NativeRunUncertain, RunState)
from mirofish_execution.native_run_coordinator import NativeObservation, NativeRunCoordinator
from mirofish_execution.native_run_store import NativeRunStore, migrate_native_runs
from mirofish_storage import ProjectStore
from test_project_store import snapshot
from test_project_store_postgres import factory  # guarded port 15432 fixture

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def native_schema(factory):
    with factory() as conn:
        migrate_native_runs(conn)
        migrate_native_runs(conn)


def owned(factory, *, simulation_id="sim_1"):
    project_id = uuid4()
    ProjectStore(factory).create("owner", uuid4(), project_id, "proj_1", snapshot())
    request = NativeRunRequest.from_wire(dict(schema_version=1, principal="owner",
        project_id=project_id, project_revision=1, simulation_id=simulation_id,
        run_id=uuid4(), artifact_sha256="a" * 64, runtime_sha256="b" * 64,
        platforms=("twitter",), seed=7, max_rounds=2))
    return request


def receipt(request, attempt_id, child, outcome="completed"):
    return NativeRunReceipt.from_wire(dict(run_id=request.run_id, attempt_id=attempt_id,
        instance_id=child.instance_id, request_fingerprint=request.fingerprint,
        outcome=outcome, evidence_sha256="e" * 64))


class FakeDriver:
    def __init__(self, store):
        self.store = store
        self.launches = 0
        self.cancels = 0
        self.children = {}
        self.fail_launch = False

    def launch(self, request, attempt_id):
        # This fails if the coordinator moves the durable transition after launch.
        prior = self.store.get(request.principal, request.run_id)
        assert prior.state == RunState.starting and prior.attempt_id == attempt_id
        self.launches += 1
        if self.fail_launch:
            raise RuntimeError("private launch detail")
        child = NativeChildIdentity(uuid4(), 12345, "c" * 64)
        self.children[request.run_id] = child
        return child

    def observe(self, request, attempt_id, child):
        return NativeObservation("completed", receipt(request, attempt_id, child))

    def cancel(self, request, attempt_id, child):
        self.cancels += 1
        return NativeObservation("cancelled", receipt(request, attempt_id, child, "cancelled"))


def coordinator(factory, driver, **kwargs):
    return NativeRunCoordinator("owner", NativeRunStore(factory), driver,
                                dispatch_allowed=lambda _request: True, **kwargs)


def test_registration_pinned_revision_idempotence_and_isolation(factory):
    request = owned(factory)
    store = NativeRunStore(factory)
    first = store.register(request)
    assert first.state == RunState.declared
    assert NativeRunStore(factory).register(request) == first
    with pytest.raises(NativeRunConflict):
        store.register(replace(request, runtime_sha256="f" * 64))
    with pytest.raises(NativeRunConflict):
        store.register(replace(request, run_id=uuid4()))
    with pytest.raises(NativeRunDenied):
        store.register(replace(request, principal="other", run_id=uuid4()))
    with pytest.raises(NativeRunDenied):
        store.register(replace(request, project_revision=2, run_id=uuid4()))
    with pytest.raises(NativeRunDenied):
        store.get("other", request.run_id)


def test_pinned_old_revision_remains_eligible_after_project_update(factory):
    request = owned(factory)
    changed = snapshot()
    changed["name"] = "new revision"
    ProjectStore(factory).update("owner", request.project_id, 1, changed)
    assert ProjectStore(factory).get("owner", request.project_id).revision == 2
    assert NativeRunStore(factory).register(request).request.project_revision == 1


def test_dispatch_defaults_closed_and_catalog_drift_is_rejected(factory):
    request = owned(factory)
    store = NativeRunStore(factory)
    driver = FakeDriver(store)
    host = NativeRunCoordinator("owner", store, driver)
    with pytest.raises(NativeRunDenied):
        host.start(request)
    assert store.get("owner", request.run_id).state == RunState.declared
    assert driver.launches == 0
    with factory() as conn:
        with conn.transaction():
            conn.execute("CREATE TABLE mf_native_execution.unexpected(id integer)")
            with pytest.raises(NativeRunMigrationMismatch):
                migrate_native_runs(conn)
            conn.execute("DROP TABLE mf_native_execution.unexpected")


def test_concurrent_claim_is_one_launch_and_terminal_recovery(factory):
    request = owned(factory)
    store = NativeRunStore(factory)
    driver = FakeDriver(store)
    both_registered = Barrier(2)
    class RacingStore(NativeRunStore):
        def register(self, request):
            record = super().register(request)
            both_registered.wait(timeout=10)
            return record
    hosts = [NativeRunCoordinator("owner", RacingStore(factory), driver,
                                  dispatch_allowed=lambda _: True) for _ in range(2)]
    def start(host):
        try:
            return host, host.start(request)
        except NativeRunBusy:
            return host, None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(start, hosts))
    assert driver.launches == 1
    winner, running = next(pair for pair in results if pair[1] is not None)
    assert running.state == RunState.running
    with pytest.raises(NativeRunConflict):
        store.heartbeat("owner", request.run_id, uuid4(), winner.owner_id)
    with pytest.raises(NativeRunConflict):
        store.attach("owner", request.run_id, running.attempt_id, uuid4(), running.child)
    terminal = winner.observe(request.run_id)
    assert terminal.state == RunState.completed and terminal.receipt is not None
    assert NativeRunStore(factory).get("owner", request.run_id).receipt == terminal.receipt
    assert coordinator(factory, driver).start(request).receipt == terminal.receipt
    assert driver.launches == 1
    assert store.settle("owner", request.run_id, terminal.attempt_id,
                        winner.owner_id, terminal.receipt) == terminal
    with pytest.raises(NativeRunConflict):
        store.settle("owner", request.run_id, terminal.attempt_id, winner.owner_id,
                     replace(terminal.receipt, evidence_sha256="d" * 64))


def test_expiry_fences_forever_and_wrong_receipt_fails(factory):
    request = owned(factory)
    store = NativeRunStore(factory)
    driver = FakeDriver(store)
    host = coordinator(factory, driver)
    running = host.start(request)
    with pytest.raises(NativeRunConflict):
        store.settle("owner", request.run_id, running.attempt_id, host.owner_id,
                     replace(receipt(request, running.attempt_id, running.child),
                             instance_id=uuid4()))
    with factory() as conn:
        conn.execute("UPDATE mf_native_execution.runs SET lease_until=clock_timestamp()-interval '1 second' "
                     "WHERE run_id=%s", (request.run_id,))
    with pytest.raises(NativeRunUncertain):
        store.heartbeat("owner", request.run_id, running.attempt_id, host.owner_id)
    assert store.reconcile_expired("owner", request.run_id).state == RunState.uncertain
    with pytest.raises(NativeRunUncertain):
        store.settle("owner", request.run_id, running.attempt_id, host.owner_id,
                     receipt(request, running.attempt_id, running.child))
    with pytest.raises(NativeRunUncertain):
        host.start(request)
    assert driver.launches == 1


def test_cancel_during_launch_and_ambiguous_launch(factory):
    request = owned(factory)
    store = NativeRunStore(factory)
    class RacingDriver(FakeDriver):
        def launch(self, request, attempt_id):
            self.store.request_cancel("owner", request.run_id)
            return super().launch(request, attempt_id)
    driver = RacingDriver(store)
    host = coordinator(factory, driver)
    cancelled = host.start(request)
    assert cancelled.cancel_requested and cancelled.state == RunState.cancelled
    assert driver.cancels == 1 and cancelled.receipt.outcome == "cancelled"

    failed_request = owned(factory, simulation_id="sim_failed")
    failing = FakeDriver(store)
    failing.fail_launch = True
    failed_host = coordinator(factory, failing)
    with pytest.raises(NativeRunUncertain, match="^native_run_uncertain$"):
        failed_host.start(failed_request)
    assert store.get("owner", failed_request.run_id).state == RunState.uncertain
    with pytest.raises(NativeRunUncertain):
        failed_host.start(failed_request)
    assert failing.launches == 1


def test_lost_attach_ack_is_fenced(factory):
    request = owned(factory)
    class LostAckStore(NativeRunStore):
        def attach(self, *args, **kwargs):
            super().attach(*args, **kwargs)
            raise NativeRunUnavailable()
    store = LostAckStore(factory)
    driver = FakeDriver(store)
    host = NativeRunCoordinator("owner", store, driver, dispatch_allowed=lambda _: True)
    with pytest.raises(NativeRunUncertain):
        host.start(request)
    assert NativeRunStore(factory).get("owner", request.run_id).state == RunState.uncertain
    assert driver.launches == 1


def test_lost_claim_ack_spends_launch_permission(factory):
    request = owned(factory)
    class LostClaimStore(NativeRunStore):
        def claim_start(self, *args, **kwargs):
            super().claim_start(*args, **kwargs)
            raise NativeRunUnavailable()
    store = LostClaimStore(factory)
    driver = FakeDriver(store)
    host = NativeRunCoordinator("owner", store, driver, dispatch_allowed=lambda _: True)
    with pytest.raises(NativeRunUnavailable):
        host.start(request)
    assert store.get("owner", request.run_id).state == RunState.starting
    with pytest.raises(NativeRunBusy):
        host.start(request)
    assert driver.launches == 0


def test_starting_expiry_before_external_call_prevents_launch(factory):
    request = owned(factory)
    class ExpiredClaimStore(NativeRunStore):
        def claim_start(self, *args, **kwargs):
            record = super().claim_start(*args, **kwargs)
            with factory() as conn:
                conn.execute("UPDATE mf_native_execution.runs SET "
                             "lease_until=clock_timestamp()-interval '1 second' WHERE run_id=%s",
                             (record.request.run_id,))
            return record
    store = ExpiredClaimStore(factory)
    driver = FakeDriver(store)
    host = NativeRunCoordinator("owner", store, driver, dispatch_allowed=lambda _: True)
    with pytest.raises(NativeRunUncertain):
        host.start(request)
    assert store.get("owner", request.run_id).state == RunState.uncertain
    assert driver.launches == 0


def test_non_owner_cancellation_is_serviced_by_active_owner(factory):
    request = owned(factory)
    store = NativeRunStore(factory)
    class PendingCancelDriver(FakeDriver):
        def cancel(self, request, attempt_id, child):
            self.cancels += 1
            if self.cancels == 1:
                return NativeObservation("running")
            return NativeObservation("cancelled", receipt(request, attempt_id, child, "cancelled"))
    driver = PendingCancelDriver(store)
    owner = coordinator(factory, driver)
    running = owner.start(request)
    other = coordinator(factory, driver)
    pending = other.request_cancel(request.run_id)
    assert pending.state == RunState.running and pending.cancel_requested
    assert driver.cancels == 0
    still_running = owner.observe(request.run_id)
    assert still_running.state == RunState.running and still_running.cancel_requested
    assert still_running.attempt_id == running.attempt_id and driver.cancels == 1
    final = owner.observe(request.run_id)
    assert final.state == RunState.cancelled and driver.cancels == 2


def test_null_receipt_field_rejected_by_sql(factory):
    request = owned(factory)
    store = NativeRunStore(factory)
    driver = FakeDriver(store)
    host = coordinator(factory, driver)
    running = host.start(request)
    malformed = receipt(request, running.attempt_id, running.child).to_wire()
    malformed["evidence_sha256"] = None
    with factory() as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute("UPDATE mf_native_execution.runs SET state='completed',"
                             "receipt=%s,lease_until=NULL WHERE run_id=%s",
                             (Jsonb(malformed), request.run_id))
    assert store.get("owner", request.run_id).state == RunState.running


@pytest.mark.parametrize("status", ["unknown", "absent"])
def test_unknown_child_observation_is_not_completion(factory, status):
    request = owned(factory)
    store = NativeRunStore(factory)
    class UnknownDriver(FakeDriver):
        def observe(self, request, attempt_id, child):
            return NativeObservation(status)
    driver = UnknownDriver(store)
    host = coordinator(factory, driver)
    host.start(request)
    with pytest.raises(NativeRunUncertain):
        host.observe(request.run_id)
    assert store.get("owner", request.run_id).state == RunState.uncertain
    assert driver.launches == 1


def test_launch_that_outlives_lease_cannot_attach_or_restart(factory):
    request = owned(factory)
    store = NativeRunStore(factory)
    class SlowDriver(FakeDriver):
        def launch(self, request, attempt_id):
            child = super().launch(request, attempt_id)
            with factory() as conn:
                conn.execute("UPDATE mf_native_execution.runs SET "
                             "lease_until=clock_timestamp()-interval '1 second' WHERE run_id=%s",
                             (request.run_id,))
            return child
    driver = SlowDriver(store)
    host = coordinator(factory, driver)
    with pytest.raises(NativeRunUncertain):
        host.start(request)
    saved = NativeRunStore(factory).get("owner", request.run_id)
    assert saved.state == RunState.uncertain and saved.child is None
    with pytest.raises(NativeRunUncertain):
        coordinator(factory, driver).start(request)
    assert driver.launches == 1  # External launch happened; no termination is claimed.


def test_late_settlement_itself_commits_expiry_fence(factory):
    request = owned(factory)
    store = NativeRunStore(factory)
    host = coordinator(factory, FakeDriver(store))
    running = host.start(request)
    with factory() as conn:
        conn.execute("UPDATE mf_native_execution.runs SET "
                     "lease_until=clock_timestamp()-interval '1 second' WHERE run_id=%s",
                     (request.run_id,))
    with pytest.raises(NativeRunUncertain):
        store.settle("owner", request.run_id, running.attempt_id, host.owner_id,
                     receipt(request, running.attempt_id, running.child))
    saved = NativeRunStore(factory).get("owner", request.run_id)
    assert saved.state == RunState.uncertain and saved.receipt is None


def test_owned_child_process_cancel(factory, tmp_path):
    request = owned(factory)
    store = NativeRunStore(factory)
    class OwnedChildDriver(FakeDriver):
        def __init__(self, store):
            super().__init__(store)
            self.process = None

        def launch(self, request, attempt_id):
            assert self.store.get("owner", request.run_id).state == RunState.starting
            self.launches += 1
            self.process = subprocess.Popen(
                [sys.executable, "-c", "import sys; sys.stdin.read()"],
                cwd=tmp_path, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            instance = uuid4()
            fingerprint = hashlib.sha256(f"{instance}:{self.process.pid}".encode()).hexdigest()
            return NativeChildIdentity(instance, self.process.pid, fingerprint)

        def cancel(self, request, attempt_id, child):
            assert self.process is not None and self.process.pid == child.process_id
            self.process.terminate()  # The handle was created by this test.
            self.process.communicate(timeout=5)
            return NativeObservation("cancelled", receipt(request, attempt_id, child, "cancelled"))

    driver = OwnedChildDriver(store)
    try:
        host = coordinator(factory, driver)
        running = host.start(request)
        assert running.state == RunState.running and driver.process.poll() is None
        final = host.request_cancel(request.run_id)
        assert final.state == RunState.cancelled and final.receipt is not None
    finally:
        if driver.process is not None and driver.process.poll() is None:
            driver.process.terminate()
            driver.process.communicate(timeout=5)

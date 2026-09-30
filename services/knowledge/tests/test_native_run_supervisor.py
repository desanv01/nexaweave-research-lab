"""Controlled one-owner thread, wait, cancellation and cleanup source tests."""
from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from mirofish_execution import native_run_supervisor as supervisor_module
from mirofish_execution.native_run_contracts import (InvalidNativeRun,
    NativeChildIdentity, NativeObservation, NativeRunBusy, NativeRunDenied,
    NativeRunReceipt, NativeRunRequest, NativeRunUncertain, RunState)
from mirofish_execution.native_run_coordinator import NativeRunCoordinator
from mirofish_execution.native_run_store import NativeRunRecord, NativeRunStore
from mirofish_execution.native_run_supervisor import (NativeRunSupervisor,
    SupervisorBusy, SupervisorClosed, SupervisorThreadUnavailable,
    SupervisorWaitTimeout)

_HOSTS = []


@pytest.fixture(autouse=True)
def release_test_owned_threads():
    yield
    while _HOSTS:
        supervisor, driver = _HOSTS.pop()
        driver.allow_launch.set()
        driver.allow_close.set()
        supervisor.close(5)


def request():
    return NativeRunRequest.from_wire(dict(schema_version=1, principal="owner",
        project_id=uuid4(), project_revision=1, simulation_id="sim_1",
        run_id=uuid4(), artifact_sha256="a" * 64, runtime_sha256="b" * 64,
        platforms=("twitter",), seed=7, max_rounds=1))


def record(req, state=RunState.declared, **changes):
    now = datetime.now(timezone.utc)
    value = NativeRunRecord(req, req.fingerprint, state, False, None, None,
                            None, None, None, now, now)
    return replace(value, **changes)


class MemoryStore(NativeRunStore):
    """Test-only bounded record seam; production authority is PostgreSQL."""

    def __init__(self, req):
        self.value = record(req)
        self.lock = threading.Lock()

    def register(self, req):
        with self.lock:
            if req != self.value.request:
                raise NativeRunDenied()
            return self.value

    def reconcile_expired(self, principal, run_id):
        with self.lock:
            if principal != self.value.request.principal or run_id != self.value.request.run_id:
                raise NativeRunDenied()
            return self.value

    def request_cancel(self, principal, run_id):
        with self.lock:
            if principal != self.value.request.principal or run_id != self.value.request.run_id:
                raise NativeRunDenied()
            self.value = replace(self.value, cancel_requested=True)
            return self.value

    def mark_uncertain(self, principal, run_id, attempt_id, owner_id):
        with self.lock:
            if self.value.attempt_id != attempt_id or self.value.owner_id != owner_id:
                raise NativeRunDenied()
            self.value = replace(self.value, state=RunState.uncertain)
            return self.value


class ControlledDriver:
    def __init__(self):
        self.launch_entered = threading.Event()
        self.allow_launch = threading.Event()
        self.finish = threading.Event()
        self.close_entered = threading.Event()
        self.allow_close = threading.Event()
        self.allow_close.set()
        self.first_close_failed = threading.Event()
        self.fail_first_close = False
        self.unknown = False
        self.launches = 0
        self.observations = 0
        self.cancels = 0
        self.closes = 0
        self.owner_thread = None
        self.child = NativeChildIdentity(uuid4(), 12345, "c" * 64)

    def _serial(self):
        current = threading.get_ident()
        if self.owner_thread is None:
            self.owner_thread = current
        assert self.owner_thread == current

    def launch(self, req, attempt):
        self._serial()
        self.launches += 1
        self.launch_entered.set()
        assert self.allow_launch.wait(5)
        return self.child

    def _receipt(self, req, attempt, outcome):
        return NativeRunReceipt(req.run_id, attempt, self.child.instance_id,
                                req.fingerprint, outcome, "e" * 64)

    def observe(self, req, attempt, child):
        self._serial()
        self.observations += 1
        if self.unknown:
            return NativeObservation("unknown")
        if self.finish.is_set():
            return NativeObservation("completed", self._receipt(req, attempt, "completed"))
        return NativeObservation("running")

    def cancel(self, req, attempt, child):
        self._serial()
        self.cancels += 1
        return NativeObservation("cancelled", self._receipt(req, attempt, "cancelled"))

    def close(self):
        self._serial()
        self.closes += 1
        self.close_entered.set()
        assert self.allow_close.wait(5)
        if self.fail_first_close and self.closes == 1:
            self.first_close_failed.set()
            return False
        return True


class ScriptedCoordinator(NativeRunCoordinator):
    def __init__(self, req, store, driver, *, dispatch=True):
        super().__init__(req.principal, store, driver, lease_seconds=30,
                         dispatch_allowed=(lambda _: True) if dispatch else None)
        self.starts = 0

    def start(self, req):
        self.starts += 1
        prior = self.store.register(req)
        if prior.state in (RunState.completed, RunState.failed, RunState.cancelled):
            return prior
        if self.dispatch_allowed is None:
            raise NativeRunDenied()
        if prior.state != RunState.declared or prior.cancel_requested:
            raise NativeRunBusy()
        attempt = uuid4()
        with self.store.lock:
            self.store.value = replace(self.store.value, state=RunState.starting,
                                       attempt_id=attempt, owner_id=self.owner_id)
        child = self.driver.launch(req, attempt)
        with self.store.lock:
            self.store.value = replace(self.store.value, state=RunState.running,
                                       child=child)
            running = self.store.value
        if running.cancel_requested:
            return self.observe(req.run_id)
        return running

    def observe(self, run_id):
        prior = self.store.reconcile_expired(self.principal, run_id)
        if prior.state == RunState.uncertain:
            raise NativeRunUncertain()
        observed = (self.driver.cancel(prior.request, prior.attempt_id, prior.child)
                    if prior.cancel_requested else
                    self.driver.observe(prior.request, prior.attempt_id, prior.child))
        if observed.status in ("unknown", "absent"):
            self.store.mark_uncertain(self.principal, run_id, prior.attempt_id,
                                      self.owner_id)
            raise NativeRunUncertain()
        if observed.receipt is not None:
            with self.store.lock:
                self.store.value = replace(self.store.value,
                    state=RunState(observed.status), receipt=observed.receipt)
                return self.store.value
        return prior


def host(*, dispatch=True):
    req = request()
    store = MemoryStore(req)
    driver = ControlledDriver()
    driver.allow_launch.set()
    coordinator = ScriptedCoordinator(req, store, driver, dispatch=dispatch)
    supervisor = NativeRunSupervisor(req, coordinator, poll_seconds=0.05)
    _HOSTS.append((supervisor, driver))
    return req, store, driver, supervisor


def test_concurrent_one_start_and_owner_serialization():
    req, store, driver, supervisor = host()
    driver.allow_launch.clear()
    result = []
    caller = threading.Thread(target=lambda: result.append(supervisor.start(5)))
    caller.start()
    assert driver.launch_entered.wait(5)
    with pytest.raises(SupervisorBusy):
        supervisor.start(0.1)
    assert supervisor.request_cancel().cancel_requested
    driver.allow_launch.set()
    caller.join(5)
    assert not caller.is_alive()
    assert result[0].run_state == RunState.cancelled
    assert supervisor.wait(5).receipt is not None
    assert driver.launches == 1 and driver.cancels == 1
    assert supervisor.close(5)
    assert driver.closes == 1


def test_default_off_timeout_and_no_relaunch():
    req, store, driver, supervisor = host(dispatch=False)
    result = supervisor.start(5)
    assert result.error_code == "native_run_denied" and driver.launches == 0
    assert supervisor.close(5)
    with pytest.raises(SupervisorClosed):
        supervisor.start(1)

    req, store, driver, supervisor = host()
    driver.allow_launch.clear()
    with pytest.raises(SupervisorWaitTimeout):
        supervisor.start(0.01)
    assert driver.launch_entered.wait(5)
    with pytest.raises(SupervisorBusy):
        supervisor.start(1)
    driver.allow_launch.set()
    driver.finish.set()
    assert supervisor.wait(5).run_state == RunState.completed
    assert driver.launches == 1
    assert supervisor.close(5)


def test_cancel_before_go_and_wrong_principal():
    req, store, driver, supervisor = host()
    with pytest.raises(NativeRunDenied):
        NativeRunSupervisor(replace(req, principal="other"), supervisor.coordinator)
    supervisor.request_cancel()
    stopped = supervisor.start(5)
    assert stopped.phase == "stopped_before_launch" and stopped.cancel_requested
    assert driver.launches == 0
    assert supervisor.close(5)


def test_unknown_observation_fences_and_close_wait_is_bounded():
    req, store, driver, supervisor = host()
    driver.unknown = True
    driver.allow_close.clear()
    supervisor.start(5)
    assert supervisor.wait(5).run_state == RunState.uncertain
    assert driver.close_entered.wait(5)
    assert supervisor.status().cleanup_pending
    assert supervisor.close(0.01) is False
    assert supervisor.status().thread_alive
    driver.allow_close.set()
    assert supervisor.close(5)
    assert driver.launches == 1


def test_failed_cleanup_retries_on_later_close():
    req, store, driver, supervisor = host()
    driver.fail_first_close = True
    driver.finish.set()
    supervisor.start(5)
    assert supervisor.wait(5).run_state == RunState.completed
    assert driver.first_close_failed.wait(5)
    with supervisor._condition:
        assert supervisor._condition.wait_for(lambda: supervisor._cleanup_pending, timeout=5)
    assert supervisor.status().cleanup_pending
    assert supervisor.close(5)
    assert driver.closes == 2 and not supervisor.status().cleanup_pending


def test_exact_terminal_recovery_never_launches():
    req, store, driver, supervisor = host()
    receipt = NativeRunReceipt(req.run_id, uuid4(), uuid4(), req.fingerprint,
                               "completed", "e" * 64)
    store.value = replace(store.value, state=RunState.completed, receipt=receipt)
    assert supervisor.start(5).receipt == receipt
    assert supervisor.wait(5).receipt == receipt
    assert driver.launches == 0
    assert supervisor.close(5)


def test_thread_start_failure_spends_launch_and_later_close_only_cleans(monkeypatch):
    req, store, driver, supervisor = host()
    original = supervisor_module.threading.Thread.start
    def fail_start(self):
        raise RuntimeError("private thread-start detail")
    monkeypatch.setattr(supervisor_module.threading.Thread, "start", fail_start)
    with pytest.raises(SupervisorThreadUnavailable,
                       match="^native_supervisor_thread_unavailable$"):
        supervisor.start(1)
    assert supervisor.wait(1).error_code == "native_supervisor_thread_unavailable"
    assert supervisor.status().cleanup_pending
    with pytest.raises(SupervisorBusy):
        supervisor.start(1)
    assert driver.launches == driver.closes == 0
    monkeypatch.setattr(supervisor_module.threading.Thread, "start", original)
    assert supervisor.close(5)
    assert driver.launches == 0 and driver.closes == 1


def test_thread_start_after_launch_then_raise_is_cleanup_only(monkeypatch):
    req, store, driver, supervisor = host()
    original = supervisor_module.threading.Thread.start
    def start_then_raise(self):
        original(self)
        raise RuntimeError("private ambiguous thread-start detail")
    monkeypatch.setattr(supervisor_module.threading.Thread, "start", start_then_raise)
    with pytest.raises(SupervisorThreadUnavailable):
        supervisor.start(1)
    monkeypatch.setattr(supervisor_module.threading.Thread, "start", original)
    assert supervisor.wait(1).error_code == "native_supervisor_thread_unavailable"
    assert supervisor.close(5)
    assert driver.launches == 0 and driver.closes == 1
    with pytest.raises(SupervisorClosed):
        supervisor.start(1)


def test_thread_constructor_failure_then_close_before_start_retry(monkeypatch):
    req, store, driver, supervisor = host()
    original = supervisor_module.threading.Thread
    def fail_constructor(*args, **kwargs):
        raise RuntimeError("private thread-constructor detail")
    monkeypatch.setattr(supervisor_module.threading, "Thread", fail_constructor)
    assert supervisor.close(0.1) is False
    assert supervisor.status().cleanup_pending
    assert supervisor.status().error_code == "native_supervisor_thread_unavailable"
    with pytest.raises(SupervisorClosed):
        supervisor.start(1)
    monkeypatch.setattr(supervisor_module.threading, "Thread", original)
    assert supervisor.close(5)
    assert driver.launches == 0 and driver.closes == 1


def test_thread_constructor_failure_from_start_never_relaunches(monkeypatch):
    req, store, driver, supervisor = host()
    original = supervisor_module.threading.Thread
    monkeypatch.setattr(supervisor_module.threading, "Thread",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("private constructor")))
    with pytest.raises(SupervisorThreadUnavailable):
        supervisor.start(1)
    assert supervisor.wait(1).error_code == "native_supervisor_thread_unavailable"
    with pytest.raises(SupervisorBusy):
        supervisor.start(1)
    monkeypatch.setattr(supervisor_module.threading, "Thread", original)
    assert supervisor.close(5)
    assert driver.launches == 0 and driver.closes == 1


def test_close_before_start_thread_start_failure_retries(monkeypatch):
    req, store, driver, supervisor = host()
    original = supervisor_module.threading.Thread.start
    monkeypatch.setattr(supervisor_module.threading.Thread, "start",
                        lambda self: (_ for _ in ()).throw(RuntimeError("private start")))
    assert supervisor.close(0.1) is False
    assert supervisor.status().cleanup_pending
    monkeypatch.setattr(supervisor_module.threading.Thread, "start", original)
    assert supervisor.close(5)
    assert driver.launches == 0 and driver.closes == 1


def test_close_before_start_is_cleanup_only():
    req, store, driver, supervisor = host()
    assert supervisor.close(5)
    assert driver.launches == 0 and driver.closes == 1
    assert supervisor.status().phase == "closed_before_start"
    with pytest.raises(SupervisorClosed):
        supervisor.start(1)


def test_bounded_waits_do_not_add_slow_durable_status_read():
    req, store, driver, supervisor = host()
    caller_thread = threading.get_ident()
    status_entered = threading.Event()
    release_status = threading.Event()
    unexpected_caller_reads = []
    original = store.reconcile_expired
    def slow_status(principal, run_id):
        if threading.get_ident() == caller_thread:
            unexpected_caller_reads.append(1)
            raise AssertionError("caller performed durable status read")
        if threading.current_thread().name == "explicit-slow-status":
            status_entered.set()
            assert release_status.wait(5)
        return original(principal, run_id)
    store.reconcile_expired = slow_status
    driver.finish.set()
    assert supervisor.start(5).run_state in (RunState.running, RunState.completed)
    assert supervisor.wait(5).run_state == RunState.completed
    results = []
    reader = threading.Thread(name="explicit-slow-status",
                              target=lambda: results.append(supervisor.status()))
    reader.start()
    try:
        assert status_entered.wait(5)
        assert supervisor.close(0.1)
        assert not unexpected_caller_reads
    finally:
        release_status.set()
        reader.join(5)
    assert not reader.is_alive() and results[0].run_state == RunState.completed


@pytest.mark.parametrize("value", [True, 0, -1, float("nan"), float("inf")])
def test_invalid_wait_and_poll_values(value):
    req, store, driver, supervisor = host()
    with pytest.raises(InvalidNativeRun):
        supervisor.start(value)
    with pytest.raises(InvalidNativeRun):
        supervisor.wait(value)
    with pytest.raises(InvalidNativeRun):
        supervisor.close(value)
    with pytest.raises(InvalidNativeRun):
        NativeRunSupervisor(req, supervisor.coordinator, poll_seconds=value)

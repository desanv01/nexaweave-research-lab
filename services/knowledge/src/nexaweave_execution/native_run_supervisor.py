"""One trusted background owner for a single native run and its driver cleanup.

Only the owner thread invokes coordinator start/observe or the injected driver's
close. Caller threads may write cancellation intent and read durable status.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass

from .native_run_contracts import (InvalidNativeRun, NativeRunDenied, NativeRunError,
    NativeRunReceipt, NativeRunRequest, NativeRunUncertain, RunState)
from .native_run_coordinator import NativeRunCoordinator
from .native_run_store import NativeRunRecord


class SupervisorError(RuntimeError):
    code = "native_supervisor_error"

    def __init__(self):
        super().__init__(self.code)


class SupervisorBusy(SupervisorError):
    code = "native_supervisor_busy"


class SupervisorClosed(SupervisorError):
    code = "native_supervisor_closed"


class SupervisorWaitTimeout(SupervisorError):
    code = "native_supervisor_wait_timeout"


class SupervisorThreadUnavailable(SupervisorError):
    code = "native_supervisor_thread_unavailable"


@dataclass(frozen=True)
class SupervisorStatus:
    phase: str
    run_state: RunState | None
    cancel_requested: bool
    cleanup_pending: bool
    thread_alive: bool
    receipt: NativeRunReceipt | None
    error_code: str | None


def _seconds(value: object, *, maximum: float = 300.0) -> float:
    if (type(value) not in (int, float) or not 0 < value <= maximum
            or not math.isfinite(value)):
        raise InvalidNativeRun()
    return float(value)


class NativeRunSupervisor:
    """Own one request, one coordinator owner token, and one driver for life.

    start() is permanently one-shot, including after caller timeout or close.
    wait() waits for a terminal/uncertain local outcome, not child cleanup.
    close() requests cancellation and bounded cleanup; False retains the live
    owner thread and permits a later close() to retry the same driver's cleanup.
    """

    def __init__(self, request: NativeRunRequest, coordinator: NativeRunCoordinator,
                 *, poll_seconds: float = 1.0, call_budget_seconds: float = 5.0):
        request = NativeRunRequest.from_wire(request)
        if (not isinstance(coordinator, NativeRunCoordinator)
                or request.principal != coordinator.principal
                or not callable(getattr(coordinator.driver, "close", None))):
            raise NativeRunDenied()
        poll = _seconds(poll_seconds, maximum=30.0)
        call_budget = _seconds(call_budget_seconds, maximum=30.0)
        lease = coordinator.lease_seconds
        # Two bounded DB transactions plus the driver's promised longest call
        # must leave room for the next heartbeat. This is a host contract, not
        # runtime preemption of an arbitrary blocking driver.
        if (lease < 20 or poll > lease / 5
                or poll + call_budget + 10 > lease * 0.8):
            raise InvalidNativeRun()
        self.request = request
        self.coordinator = coordinator
        self.poll_seconds = poll
        self.call_budget_seconds = call_budget
        self._condition = threading.Condition()
        self._wake = threading.Event()
        self._retry_cleanup = threading.Event()
        self._started = threading.Event()
        self._outcome = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_spent = False
        self._cleanup_only = False
        self._close_requested = False
        self._cancel_requested = False
        self._cleanup_pending = False
        self._cleanup_done = False
        self._phase = "new"
        self._record: NativeRunRecord | None = None
        self._error_code: str | None = None

    def _publish(self, *, phase: str | None = None,
                 record: NativeRunRecord | None = None, error_code: str | None = None) -> None:
        with self._condition:
            if phase is not None:
                self._phase = phase
            if record is not None:
                self._record = record
            if error_code is not None:
                self._error_code = error_code
            self._condition.notify_all()

    def _spawn_locked(self) -> None:
        admitted = threading.Event()
        thread: threading.Thread | None = None
        try:
            thread = threading.Thread(target=self._owner_main, args=(admitted,),
                                      daemon=False, name="native-run-owner")
            thread.start()
        except BaseException as error:
            # Construction may fail, or a custom start may launch then raise.
            # The gate keeps any live thread on the cleanup-only path.
            self._cleanup_only = True
            self._thread = thread if thread is not None and thread.is_alive() else None
            self._phase = "error"
            self._error_code = SupervisorThreadUnavailable.code
            self._cleanup_pending = True
            self._started.set()
            self._outcome.set()
            self._condition.notify_all()
            admitted.set()
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise SupervisorThreadUnavailable() from None
        self._thread = thread
        admitted.set()

    def start(self, wait_seconds: float = 5.0) -> SupervisorStatus:
        wait_seconds = _seconds(wait_seconds)
        with self._condition:
            if self._close_requested:
                raise SupervisorClosed()
            if self._start_spent:
                raise SupervisorBusy()
            self._start_spent = True
            self._phase = "starting"
            self._spawn_locked()
        if not self._started.wait(wait_seconds):
            raise SupervisorWaitTimeout()
        return self._snapshot(refresh=False)

    def _fence_owned(self) -> None:
        """Best effort after a failed active operation; never reset a run."""
        try:
            record = self.coordinator.store.reconcile_expired(
                self.request.principal, self.request.run_id)
            if (record.owner_id == self.coordinator.owner_id
                    and record.attempt_id is not None
                    and record.state in (RunState.starting, RunState.running)):
                self.coordinator.store.mark_uncertain(
                    self.request.principal, self.request.run_id,
                    record.attempt_id, self.coordinator.owner_id)
        except Exception:
            # The original finite lease retains the one-shot fence until a
            # later explicit reconciliation can persist uncertainty.
            pass

    def _cancel_intent(self) -> None:
        self.coordinator.store.request_cancel(self.request.principal,
                                              self.request.run_id)

    def _run(self) -> None:
        # Registration lets a prelaunch stop persist without claiming a launch.
        record = self.coordinator.store.register(self.request)
        self._publish(record=record)
        with self._condition:
            stopping = self._cancel_requested or self._close_requested
        if stopping:
            self._cancel_intent()
            self._publish(phase="stopped_before_launch")
            self._started.set()
            self._outcome.set()
            return
        record = self.coordinator.start(self.request)
        self._publish(record=record, phase=record.state.value)
        self._started.set()
        if record.state in (RunState.completed, RunState.failed, RunState.cancelled,
                            RunState.uncertain):
            self._outcome.set()
            return
        if record.state != RunState.running:
            raise NativeRunUncertain()
        while record.state == RunState.running:
            with self._condition:
                stopping = self._cancel_requested or self._close_requested
            if stopping:
                self._cancel_intent()
            # The accepted coordinator routes persisted cancellation intent to
            # the same driver's cancel operation on this owner thread.
            record = self.coordinator.observe(self.request.run_id)
            self._publish(record=record, phase=record.state.value)
            if record.state != RunState.running:
                self._outcome.set()
                return
            self._wake.wait(self.poll_seconds)
            self._wake.clear()

    def _cleanup_loop(self) -> BaseException | None:
        interrupt: BaseException | None = None
        while True:
            self._retry_cleanup.clear()
            with self._condition:
                self._cleanup_pending = True
                self._condition.notify_all()
            try:
                cleaned = self.coordinator.driver.close() is True
            except BaseException as error:
                if isinstance(error, (KeyboardInterrupt, SystemExit)) and interrupt is None:
                    interrupt = error
                cleaned = False
            if cleaned:
                with self._condition:
                    self._cleanup_done = True
                    self._cleanup_pending = False
                    self._condition.notify_all()
                return interrupt
            with self._condition:
                self._cleanup_pending = True
                self._condition.notify_all()
            # A failed close retains this non-daemon owner and its child handle.
            # Only a later close() requests another attempt on this same thread.
            self._retry_cleanup.wait()

    def _owner_main(self, admitted: threading.Event) -> None:
        admitted.wait()
        interrupt: BaseException | None = None
        try:
            with self._condition:
                cleanup_only = self._cleanup_only or (self._close_requested and not self._start_spent)
            if cleanup_only:
                if self._error_code is None:
                    self._publish(phase="closed_before_start")
                self._started.set()
                self._outcome.set()
            else:
                self._run()
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                interrupt = error
            code = error.code if isinstance(error, NativeRunError) else "native_supervisor_unavailable"
            self._fence_owned()
            try:
                durable = self.coordinator.store.reconcile_expired(
                    self.request.principal, self.request.run_id)
            except Exception:
                durable = None
            self._publish(phase="error", record=durable, error_code=code)
            self._started.set()
            self._outcome.set()
        finally:
            cleanup_interrupt = self._cleanup_loop()
            if interrupt is None:
                interrupt = cleanup_interrupt
        if interrupt is not None:
            raise interrupt

    def _snapshot(self, *, refresh: bool) -> SupervisorStatus:
        with self._condition:
            phase = self._phase
            record = self._record
            local_cancel = self._cancel_requested or self._close_requested
            pending = self._cleanup_pending
            thread = self._thread
            code = self._error_code
        if refresh and self._start_spent and phase not in ("new", "closed_before_start"):
            try:
                record = self.coordinator.store.reconcile_expired(
                    self.request.principal, self.request.run_id)
            except NativeRunDenied:
                # The owner may not have registered yet. No record is inferred.
                record = None
            except NativeRunError as error:
                code = error.code
            except Exception:
                code = "native_supervisor_unavailable"
        return SupervisorStatus(phase, None if record is None else record.state,
            local_cancel or (record.cancel_requested if record is not None else False),
            pending, thread.is_alive() if thread is not None else False,
            record.receipt if record is not None else None, code)

    def status(self) -> SupervisorStatus:
        """Read durable status; trusted store connection bounds apply."""
        return self._snapshot(refresh=True)

    def wait(self, wait_seconds: float = 30.0) -> SupervisorStatus:
        wait_seconds = _seconds(wait_seconds)
        with self._condition:
            if not self._start_spent:
                raise SupervisorBusy()
        if not self._outcome.wait(wait_seconds):
            raise SupervisorWaitTimeout()
        return self._snapshot(refresh=False)

    def request_cancel(self) -> SupervisorStatus:
        with self._condition:
            self._cancel_requested = True
            active = self._start_spent and self._thread is not None
        self._wake.set()
        if active:
            try:
                self._cancel_intent()
            except NativeRunDenied:
                # The owner thread may not yet have registered; it will persist
                # the already recorded local intent before claiming a launch.
                pass
            except Exception:
                self._publish(error_code="native_supervisor_unavailable")
        return self.status()

    def close(self, wait_seconds: float = 5.0) -> bool:
        wait_seconds = _seconds(wait_seconds)
        with self._condition:
            self._close_requested = True
            self._cancel_requested = True
            if self._thread is None:
                try:
                    self._spawn_locked()  # cleanup-only owner; start is forbidden.
                except SupervisorThreadUnavailable:
                    return False
            thread = self._thread
        self._wake.set()
        self._retry_cleanup.set()
        if threading.current_thread() is thread:
            raise SupervisorBusy()
        thread.join(wait_seconds)
        with self._condition:
            return self._cleanup_done and not thread.is_alive()

"""Trusted native Temporal activity with retained one-shot supervisor ownership."""
from __future__ import annotations

import asyncio
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from .native_run_contracts import (InvalidNativeRun, NativeRunError,
                                   NativeRunRequest, RunState, principal_id)
from .native_run_supervisor import (NativeRunSupervisor, SupervisorError,
                                    SupervisorStatus, SupervisorWaitTimeout)
from .temporal_native_contracts import qualified_receipt

_TERMINAL = frozenset({RunState.completed, RunState.failed, RunState.cancelled})
_SAFE_CODES = frozenset({"invalid_native_run", "native_run_denied", "native_run_conflict",
    "native_run_busy", "native_run_uncertain", "native_run_unavailable",
    "native_run_migration_mismatch", "native_supervisor_busy", "native_supervisor_closed",
    "native_supervisor_thread_unavailable", "native_supervisor_wait_timeout"})


class NativeActivityError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass
class _Entry:
    run_id: UUID
    fingerprint: str
    supervisor: NativeRunSupervisor | None = None
    factory_task: asyncio.Task | None = None
    cleanup_task: asyncio.Task | None = None
    inflight: set[asyncio.Task] = field(default_factory=set)
    stop_requested: bool = False
    binding_invalid: bool = False
    cleanup_proven: bool = False
    error_code: str | None = None


@dataclass(frozen=True)
class LocalNativeStatus:
    run_id: UUID
    admission_spent: bool
    supervisor_present: bool
    cleanup_pending: bool
    owner_thread_alive: bool
    run_state: RunState | None
    receipt_present: bool
    inflight_calls: int
    error_code: str | None


def _finite(value: object, low: float, high: float) -> float:
    if (type(value) not in (int, float) or not low <= value <= high
            or not math.isfinite(value)):
        raise InvalidNativeRun()
    return float(value)


def _failure(error: Exception) -> ApplicationError:
    if isinstance(error, NativeActivityError):
        code = error.code
    elif isinstance(error, (NativeRunError, SupervisorError)):
        code = error.code if error.code in _SAFE_CODES else "native_execution_failed"
    else:
        code = "native_execution_failed"
    return ApplicationError(code, type=code, non_retryable=True)


class NativeExecutionActivities:
    def __init__(self, *, trusted_principal: str,
                 supervisor_factory: Callable[[NativeRunRequest], NativeRunSupervisor],
                 allow_dispatch: Callable[[], bool] | None = None,
                 max_retained: int = 32, heartbeat_seconds: float = 5.0,
                 wait_slice_seconds: float = 5.0, close_wait_seconds: float = 5.0,
                 max_lifetime_seconds: float = 3600.0):
        try:
            self.principal = principal_id(trusted_principal)
            if (not callable(supervisor_factory)
                    or allow_dispatch is not None and not callable(allow_dispatch)
                    or type(max_retained) is not int or not 1 <= max_retained <= 128):
                raise InvalidNativeRun()
            self.heartbeat_seconds = _finite(heartbeat_seconds, 0.2, 10)
            self.wait_slice_seconds = _finite(wait_slice_seconds, 0.2, 10)
            self.close_wait_seconds = _finite(close_wait_seconds, 0.2, 30)
            self.max_lifetime_seconds = _finite(max_lifetime_seconds, 30, 7000)
        except InvalidNativeRun:
            raise NativeActivityError("invalid_activity_configuration") from None
        self._factory = supervisor_factory
        self._allow = allow_dispatch
        self._max = max_retained
        self._lock = threading.Lock()
        self._entries: dict[UUID, _Entry] = {}

    def _admit(self, request: NativeRunRequest) -> _Entry:
        with self._lock:
            prior = self._entries.get(request.run_id)
            if prior is not None:
                raise NativeActivityError("native_run_conflict" if prior.fingerprint != request.fingerprint
                                          else "native_run_busy")
            if len(self._entries) >= self._max:
                raise NativeActivityError("native_registry_full")
            entry = _Entry(request.run_id, request.fingerprint)
            self._entries[request.run_id] = entry
            return entry

    def _entry(self, request: NativeRunRequest) -> _Entry:
        with self._lock:
            entry = self._entries.get(request.run_id)
            if entry is None or entry.fingerprint != request.fingerprint:
                raise NativeActivityError("native_entry_unavailable")
            return entry

    def _track(self, entry: _Entry, task: asyncio.Task) -> asyncio.Task:
        with self._lock:
            entry.inflight.add(task)
        def finished(done: asyncio.Task) -> None:
            self._drain_task(entry, done)
            with self._lock:
                entry.inflight.discard(done)
        task.add_done_callback(finished)
        return task

    def _drain_task(self, entry: _Entry, done: asyncio.Task) -> None:
        try:
            error = done.exception()
        except asyncio.CancelledError:
            error = None
        if error is None or isinstance(error, SupervisorWaitTimeout):
            return
        if isinstance(error, NativeActivityError):
            code = error.code
        elif isinstance(error, (NativeRunError, SupervisorError)):
            code = error.code if error.code in _SAFE_CODES else "native_execution_failed"
        else:
            code = "native_execution_failed"
        with self._lock:
            if entry.error_code is None:
                entry.error_code = code

    def _accept_supervisor(self, entry: _Entry, request: NativeRunRequest,
                           value: object) -> NativeRunSupervisor:
        if not isinstance(value, NativeRunSupervisor):
            raise NativeActivityError("invalid_supervisor")
        with self._lock:
            if entry.supervisor is None:
                entry.supervisor = value
            elif entry.supervisor is not value:
                raise NativeActivityError("native_run_conflict")
            try:
                mismatch = (value.request != request
                            or value.coordinator.principal != self.principal)
            except Exception:
                mismatch = True
            if mismatch:
                entry.stop_requested = True
                entry.binding_invalid = True
                entry.error_code = "invalid_supervisor"
            stopped = entry.stop_requested
        if mismatch:
            self._schedule_cleanup(entry, cancel=False)
            raise NativeActivityError("invalid_supervisor")
        if stopped:
            self._schedule_cleanup(entry, cancel=True)
        return value

    def _factory_done(self, entry: _Entry, request: NativeRunRequest,
                      task: asyncio.Task) -> None:
        try:
            self._accept_supervisor(entry, request, task.result())
        except NativeActivityError as error:
            with self._lock:
                entry.error_code = error.code
        except BaseException:
            with self._lock:
                if entry.error_code is None:
                    entry.error_code = "native_execution_failed"

    async def _call(self, entry: _Entry, function, *args,
                    heartbeat_task: asyncio.Task | None = None):
        task = self._track(entry, asyncio.create_task(asyncio.to_thread(function, *args)))
        if heartbeat_task is None:
            return await asyncio.shield(task)
        done, _ = await asyncio.wait((task, heartbeat_task),
                                     return_when=asyncio.FIRST_COMPLETED)
        if heartbeat_task in done:
            heartbeat_task.result()
            raise NativeActivityError("native_heartbeat_failed")
        return task.result()

    async def _heartbeat(self, request: NativeRunRequest, stop: asyncio.Event) -> None:
        while not stop.is_set():
            activity.heartbeat({"stage": "native_running", "run_id": str(request.run_id)})
            try:
                await asyncio.wait_for(stop.wait(), self.heartbeat_seconds)
            except asyncio.TimeoutError:
                pass

    async def _cleanup(self, entry: _Entry, *, cancel: bool) -> None:
        supervisor = entry.supervisor
        if supervisor is None:
            return
        if cancel:
            try:
                await self._call(entry, supervisor.request_cancel)
            except Exception:
                with self._lock:
                    if entry.error_code is None:
                        entry.error_code = "native_cleanup_pending"
        try:
            cleaned = await self._call(entry, supervisor.close, self.close_wait_seconds)
        except Exception:
            cleaned = False
        with self._lock:
            entry.cleanup_proven = cleaned is True
            if cleaned:
                if entry.error_code == "native_cleanup_pending":
                    entry.error_code = None
            else:
                if entry.error_code is None:
                    entry.error_code = "native_cleanup_pending"

    def _schedule_cleanup(self, entry: _Entry, *, cancel: bool) -> asyncio.Task | None:
        with self._lock:
            entry.stop_requested = entry.stop_requested or cancel
            if entry.binding_invalid:
                cancel = False  # Mismatched handles receive cleanup only.
            prior = entry.cleanup_task
            if prior is not None and not prior.done():
                return prior
            if entry.supervisor is None:
                return None
            entry.cleanup_proven = False
            task = asyncio.create_task(self._cleanup(entry, cancel=cancel))
            entry.cleanup_task = task
            task.add_done_callback(lambda done: self._drain_task(entry, done))
            return task

    @staticmethod
    def _terminal(status: SupervisorStatus, request: NativeRunRequest) -> dict[str, str] | None:
        if status.receipt is not None:
            receipt = qualified_receipt(status.receipt, request)
            if status.run_state not in _TERMINAL or receipt.outcome != status.run_state.value:
                raise NativeActivityError("invalid_receipt")
            return receipt.to_wire()
        if status.run_state in _TERMINAL:
            raise NativeActivityError("invalid_receipt")
        if status.run_state == RunState.uncertain:
            raise NativeActivityError("native_run_uncertain")
        if status.error_code is not None:
            raise NativeActivityError(status.error_code if status.error_code in _SAFE_CODES
                                      else "native_execution_failed")
        if status.phase == "stopped_before_launch":
            raise NativeActivityError("native_cancel_pending")
        return None

    @activity.defn(name="mirofish_native_run_v1")
    async def run_native(self, wire: Any) -> dict[str, Any]:
        entry: _Entry | None = None
        heartbeat_task: asyncio.Task | None = None
        stop = asyncio.Event()
        completed = False
        try:
            request = NativeRunRequest.from_wire(wire)
            if request.principal != self.principal:
                raise NativeActivityError("native_run_denied")
            try:
                allowed = self._allow is not None and self._allow() is True
            except Exception:
                allowed = False
            if not allowed:
                raise NativeActivityError("dispatch_disabled")
            entry = self._admit(request)
            heartbeat_task = asyncio.create_task(self._heartbeat(request, stop))
            factory_task = self._track(entry, asyncio.create_task(
                asyncio.to_thread(self._factory, request)))
            entry.factory_task = factory_task
            factory_task.add_done_callback(
                lambda done: self._factory_done(entry, request, done))
            supervisor = self._accept_supervisor(entry, request,
                await self._await_task(factory_task, heartbeat_task))
            with self._lock:
                stopped_before_start = entry.stop_requested
            if stopped_before_start:
                self._schedule_cleanup(entry, cancel=True)
                raise NativeActivityError("native_cancel_pending")
            deadline = time.monotonic() + self.max_lifetime_seconds
            try:
                status = await self._call(entry, supervisor.start, self.wait_slice_seconds,
                                          heartbeat_task=heartbeat_task)
            except SupervisorWaitTimeout:
                status = None
            while True:
                if heartbeat_task.done():
                    heartbeat_task.result()
                    raise NativeActivityError("native_heartbeat_failed")
                if status is not None:
                    result = self._terminal(status, request)
                    if result is not None:
                        completed = True
                        return result
                if time.monotonic() >= deadline:
                    raise NativeActivityError("native_timeout")
                try:
                    status = await self._call(entry, supervisor.wait, self.wait_slice_seconds,
                                              heartbeat_task=heartbeat_task)
                except SupervisorWaitTimeout:
                    status = None
        except asyncio.CancelledError:
            if entry is not None:
                self._schedule_cleanup(entry, cancel=True)
            raise
        except Exception as error:
            if entry is not None:
                self._schedule_cleanup(entry, cancel=True)
            raise _failure(error) from None
        finally:
            stop.set()
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except (asyncio.CancelledError, Exception):
                    pass
            if completed and entry is not None:
                self._schedule_cleanup(entry, cancel=False)

    async def _await_task(self, task: asyncio.Task, heartbeat_task: asyncio.Task):
        done, _ = await asyncio.wait((task, heartbeat_task),
                                     return_when=asyncio.FIRST_COMPLETED)
        if heartbeat_task in done:
            heartbeat_task.result()
            raise NativeActivityError("native_heartbeat_failed")
        return task.result()

    async def local_status(self, request: NativeRunRequest) -> LocalNativeStatus:
        request = NativeRunRequest.from_wire(request)
        if request.principal != self.principal:
            raise NativeActivityError("native_run_denied")
        entry = self._entry(request)
        with self._lock:
            supervisor = entry.supervisor
            inflight = len(entry.inflight)
            code = entry.error_code
            cleanup_task = entry.cleanup_task
            cleanup_proven = entry.cleanup_proven
            factory_task = entry.factory_task
            stopped = entry.stop_requested
            binding_invalid = entry.binding_invalid
        pending = (cleanup_task is not None or stopped) and not cleanup_proven
        if supervisor is None:
            waiting_factory = factory_task is not None and not factory_task.done()
            return LocalNativeStatus(request.run_id, True, False, waiting_factory,
                                     False,
                                     None, False, inflight, code)
        if binding_invalid:
            # A mismatched handle may only be closed through the retained
            # cleanup path. Its status API is not an authority for this request.
            return LocalNativeStatus(request.run_id, True, True, pending,
                not cleanup_proven, None, False, inflight, "invalid_supervisor")
        try:
            status = await self._call(entry, supervisor.status)
        except Exception:
            return LocalNativeStatus(request.run_id, True, True, True, True,
                                     None, False, inflight, "native_status_unavailable")
        return LocalNativeStatus(request.run_id, True, True,
            pending or status.cleanup_pending,
            status.thread_alive, status.run_state, status.receipt is not None,
            inflight, code or status.error_code)

    async def request_local_cancel(self, request: NativeRunRequest) -> LocalNativeStatus:
        request = NativeRunRequest.from_wire(request)
        if request.principal != self.principal:
            raise NativeActivityError("native_run_denied")
        entry = self._entry(request)
        with self._lock:
            entry.stop_requested = True
            supervisor = entry.supervisor
            binding_invalid = entry.binding_invalid
        if binding_invalid:
            self._schedule_cleanup(entry, cancel=False)
            return await self.local_status(request)
        if supervisor is not None:
            try:
                await self._call(entry, supervisor.request_cancel)
            except Exception:
                with self._lock:
                    if entry.error_code is None:
                        entry.error_code = "native_cleanup_pending"
                self._schedule_cleanup(entry, cancel=True)
        return await self.local_status(request)

    async def retry_cleanup(self, request: NativeRunRequest) -> LocalNativeStatus:
        request = NativeRunRequest.from_wire(request)
        if request.principal != self.principal:
            raise NativeActivityError("native_run_denied")
        entry = self._entry(request)
        with self._lock:
            entry.stop_requested = True
        task = self._schedule_cleanup(entry, cancel=False)
        if task is not None:
            try:
                await asyncio.shield(task)
            except Exception:
                with self._lock:
                    if entry.error_code is None:
                        entry.error_code = "native_cleanup_pending"
        return await self.local_status(request)

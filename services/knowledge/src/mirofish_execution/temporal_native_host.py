"""Trusted native Temporal host; client and supervisor factory are injected."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable
from uuid import UUID

from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.worker import Worker

from .native_run_contracts import (InvalidNativeRun, NativeRunReceipt,
                                   NativeRunRequest, principal_id)
from .native_run_supervisor import NativeRunSupervisor
from .temporal_native_activities import (LocalNativeStatus, NativeActivityError,
                                         NativeExecutionActivities)
from .temporal_native_contracts import native_workflow_id, qualified_receipt
from .temporal_native_workflow import NativeExecutionWorkflow

_QUEUE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,127}\Z")
_STAGES = frozenset({"created", "activity_scheduled", "cancel_requested", "completed", "failed"})
_FAILURE_CODES = frozenset({"invalid_request", "invalid_receipt", "dispatch_disabled",
    "native_run_denied", "native_run_conflict", "native_run_busy", "native_run_uncertain",
    "native_run_unavailable", "native_registry_full", "native_entry_unavailable",
    "invalid_supervisor", "native_execution_failed", "native_timeout",
    "native_cancel_pending", "native_heartbeat_failed", "native_supervisor_busy",
    "native_supervisor_closed", "native_supervisor_thread_unavailable",
    "native_supervisor_wait_timeout", "native_run_migration_mismatch"})


class NativeTemporalHostError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class NativeWorkflowRef:
    workflow_id: str
    temporal_run_id: str
    native_run_id: str


@dataclass(frozen=True)
class NativeWorkflowStatus:
    workflow_id: str
    temporal_run_id: str
    native_run_id: str
    temporal_status: str
    workflow_stage: str | None
    native_authoritative: bool = False


class TemporalNativeHost:
    def __init__(self, *, client: Client, task_queue: str, trusted_principal: str,
                 supervisor_factory: Callable[[NativeRunRequest], NativeRunSupervisor],
                 allow_dispatch: Callable[[], bool] | None = None,
                 max_retained: int = 32, max_concurrent_activities: int = 4,
                 shutdown_grace_seconds: int = 30):
        try:
            principal = principal_id(trusted_principal)
        except InvalidNativeRun:
            raise NativeTemporalHostError("invalid_host_configuration") from None
        if (not isinstance(client, Client) or type(task_queue) is not str
                or not _QUEUE.fullmatch(task_queue) or not callable(supervisor_factory)
                or allow_dispatch is not None and not callable(allow_dispatch)
                or type(max_concurrent_activities) is not int
                or not 1 <= max_concurrent_activities <= 32
                or type(max_retained) is not int or not 1 <= max_retained <= 128
                or max_concurrent_activities > max_retained
                or type(shutdown_grace_seconds) is not int
                or not 1 <= shutdown_grace_seconds <= 120):
            raise NativeTemporalHostError("invalid_host_configuration")
        self._client = client
        self._queue = task_queue
        self._principal = principal
        self._allow = allow_dispatch
        self._concurrency = max_concurrent_activities
        self._grace = shutdown_grace_seconds
        self._activities = NativeExecutionActivities(
            trusted_principal=principal, supervisor_factory=supervisor_factory,
            allow_dispatch=allow_dispatch, max_retained=max_retained)

    def worker(self) -> Worker:
        """Caller must drain/retry retained cleanup before safe process exit."""
        return Worker(self._client, task_queue=self._queue,
                      workflows=[NativeExecutionWorkflow],
                      activities=[self._activities.run_native],
                      max_concurrent_activities=self._concurrency,
                      graceful_shutdown_timeout=timedelta(seconds=self._grace))

    def _request(self, value: object) -> NativeRunRequest:
        try:
            request = NativeRunRequest.from_wire(value)
        except InvalidNativeRun:
            raise NativeTemporalHostError("invalid_request") from None
        if request.principal != self._principal:
            raise NativeTemporalHostError("native_run_denied")
        return request

    def _handle(self, value: object, ref: NativeWorkflowRef):
        request = self._request(value)
        if (not isinstance(ref, NativeWorkflowRef)
                or ref.workflow_id != native_workflow_id(request)
                or ref.native_run_id != str(request.run_id)
                or type(ref.temporal_run_id) is not str):
            raise NativeTemporalHostError("invalid_workflow_ref")
        try:
            if str(UUID(ref.temporal_run_id)) != ref.temporal_run_id:
                raise ValueError
        except ValueError:
            raise NativeTemporalHostError("invalid_workflow_ref") from None
        return request, self._client.get_workflow_handle(ref.workflow_id,
                                                          run_id=ref.temporal_run_id)

    async def start(self, value: object) -> NativeWorkflowRef:
        request = self._request(value)
        try:
            allowed = self._allow is not None and self._allow() is True
        except Exception:
            allowed = False
        if not allowed:
            raise NativeTemporalHostError("dispatch_disabled")
        try:
            handle = await self._client.start_workflow(
                NativeExecutionWorkflow.run, request.to_wire(),
                id=native_workflow_id(request), task_queue=self._queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.FAIL)
            run_id = (getattr(handle, "result_run_id", None)
                      or getattr(handle, "first_execution_run_id", None))
            if type(run_id) is not str or str(UUID(run_id)) != run_id:
                raise NativeTemporalHostError("workflow_identity_unavailable")
            return NativeWorkflowRef(native_workflow_id(request), run_id,
                                     str(request.run_id))
        except asyncio.CancelledError:
            raise
        except NativeTemporalHostError:
            raise
        except Exception as error:
            if type(error).__name__ == "WorkflowAlreadyStartedError":
                raise NativeTemporalHostError("workflow_exists") from None
            raise NativeTemporalHostError("temporal_unavailable") from None

    async def status(self, value: object, ref: NativeWorkflowRef) -> NativeWorkflowStatus:
        request, handle = self._handle(value, ref)
        try:
            description = await handle.describe()
            status = description.status.name.lower()
            if status not in {"running", "completed", "failed", "canceled",
                              "terminated", "timed_out", "continued_as_new"}:
                raise NativeTemporalHostError("invalid_workflow_status")
            if status == "running":
                stage = await handle.query("stage")
                if stage not in _STAGES:
                    raise NativeTemporalHostError("invalid_workflow_status")
            elif status == "completed":
                stage = "completed"
            elif status == "canceled":
                stage = "cancel_requested"
            else:
                stage = "failed"
            return NativeWorkflowStatus(ref.workflow_id, ref.temporal_run_id,
                str(request.run_id), status, stage)
        except asyncio.CancelledError:
            raise
        except NativeTemporalHostError:
            raise
        except Exception:
            raise NativeTemporalHostError("temporal_unavailable") from None

    async def result(self, value: object, ref: NativeWorkflowRef) -> NativeRunReceipt:
        request, handle = self._handle(value, ref)
        try:
            return qualified_receipt(await handle.result(), request)
        except asyncio.CancelledError:
            raise
        except InvalidNativeRun:
            raise NativeTemporalHostError("invalid_workflow_result") from None
        except Exception as error:
            cause = error
            for _ in range(4):
                code = getattr(cause, "type", None)
                if code in _FAILURE_CODES:
                    raise NativeTemporalHostError(code) from None
                cause = getattr(cause, "cause", None)
                if cause is None:
                    break
            raise NativeTemporalHostError("workflow_failed") from None

    async def cancel(self, value: object, ref: NativeWorkflowRef) -> NativeWorkflowStatus:
        request, handle = self._handle(value, ref)
        try:
            await self._activities.request_local_cancel(request)
        except NativeActivityError as error:
            if error.code != "native_entry_unavailable":
                raise NativeTemporalHostError(error.code) from None
            # No retained owner on this host: ask Temporal to cancel the remote
            # activity. This is intent, never native termination proof.
            try:
                await handle.cancel()
            except asyncio.CancelledError:
                raise
            except Exception:
                raise NativeTemporalHostError("temporal_unavailable") from None
        # With a retained local owner, leave the activity running so it can
        # observe and persist an exact cancelled receipt before workflow exit.
        return NativeWorkflowStatus(ref.workflow_id, ref.temporal_run_id,
            str(request.run_id), "cancel_requested", "cancel_requested")

    async def local_status(self, value: object) -> LocalNativeStatus:
        request = self._request(value)
        try:
            return await self._activities.local_status(request)
        except NativeActivityError as error:
            raise NativeTemporalHostError(error.code) from None

    async def retry_cleanup(self, value: object) -> LocalNativeStatus:
        request = self._request(value)
        try:
            return await self._activities.retry_cleanup(request)
        except NativeActivityError as error:
            raise NativeTemporalHostError(error.code) from None

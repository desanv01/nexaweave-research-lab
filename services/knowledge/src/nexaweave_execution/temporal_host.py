"""Trusted Temporal host helpers; caller supplies an already connected client."""
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

from nexaweave_knowledge.contracts import OntologySpec
from nexaweave_knowledge.source_bridge import SourceIngestionBridge

from .budgeted_ingestion import BudgetedIngestion
from .temporal_activities import SourceIngestionActivities
from .temporal_contracts import (InvalidTemporalRequest, TemporalIngestionRequest,
                                 TemporalReceipt)
from .temporal_workflow import SourceIngestionWorkflow

_QUEUE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,127}\Z")
_STAGES = frozenset({"created", "activity_scheduled", "cancel_requested",
                     "completed", "failed"})
_FAILURE_CODES = frozenset({"budget_denied", "budget_busy", "budget_conflict",
    "ingestion_uncertain", "store_unavailable", "source_denied", "source_not_found",
    "invalid_source", "ingestion_busy", "invalid_request", "invalid_receipt",
    "ingestion_failed"})


class TemporalHostError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class WorkflowRef:
    workflow_id: str
    run_id: str


@dataclass(frozen=True)
class WorkflowStatus:
    workflow_id: str
    run_id: str
    temporal_status: str
    workflow_stage: str | None
    budget_authoritative: bool = False


class TemporalIngestionHost:
    def __init__(self, *, client: Client, task_queue: str, trusted_principal: str,
                 bridge: SourceIngestionBridge, service: BudgetedIngestion,
                 resolve_ontology: Callable[[str, UUID], OntologySpec],
                 allow_dispatch: Callable[[], bool] | None = None,
                 max_concurrent_activities: int = 4,
                 shutdown_grace_seconds: int = 30):
        if (not isinstance(client, Client) or type(task_queue) is not str
                or not _QUEUE.fullmatch(task_queue)
                or type(trusted_principal) is not str or not trusted_principal
                or type(max_concurrent_activities) is not int
                or not 1 <= max_concurrent_activities <= 32
                or type(shutdown_grace_seconds) is not int
                or not 1 <= shutdown_grace_seconds <= 120):
            raise TemporalHostError("invalid_host_configuration")
        self._client = client
        self._queue = task_queue
        self._principal = trusted_principal
        self._activities = SourceIngestionActivities(
            trusted_principal=trusted_principal, bridge=bridge, service=service,
            resolve_ontology=resolve_ontology, allow_dispatch=allow_dispatch)
        self._concurrency = max_concurrent_activities
        self._grace = shutdown_grace_seconds
        self._allow = allow_dispatch

    def worker(self) -> Worker:
        """Construct a real Worker; caller controls its bounded lifecycle."""
        return Worker(self._client, task_queue=self._queue,
                      workflows=[SourceIngestionWorkflow],
                      activities=[self._activities.ingest_source],
                      max_concurrent_activities=self._concurrency,
                      graceful_shutdown_timeout=timedelta(seconds=self._grace))

    def _request(self, value: object) -> TemporalIngestionRequest:
        try:
            request = TemporalIngestionRequest.from_wire(value)
        except InvalidTemporalRequest:
            raise TemporalHostError("invalid_request") from None
        if request.principal != self._principal:
            raise TemporalHostError("source_denied")
        return request

    def _handle(self, value: object, ref: WorkflowRef):
        request = self._request(value)
        if (not isinstance(ref, WorkflowRef) or ref.workflow_id != request.workflow_id
                or type(ref.run_id) is not str):
            raise TemporalHostError("invalid_workflow_ref")
        try:
            if str(UUID(ref.run_id)) != ref.run_id:
                raise ValueError
        except ValueError:
            raise TemporalHostError("invalid_workflow_ref") from None
        return request, self._client.get_workflow_handle(ref.workflow_id, run_id=ref.run_id)

    async def start(self, value: object) -> WorkflowRef:
        request = self._request(value)
        try:
            permitted = self._allow is not None and self._allow() is True
        except Exception:
            permitted = False
        if not permitted:
            raise TemporalHostError("dispatch_disabled")
        try:
            handle = await self._client.start_workflow(
                SourceIngestionWorkflow.run, request.to_wire(),
                id=request.workflow_id, task_queue=self._queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.FAIL)
            run_id = (getattr(handle, "result_run_id", None)
                      or getattr(handle, "first_execution_run_id", None))
            if type(run_id) is not str:
                raise TemporalHostError("workflow_identity_unavailable")
            return WorkflowRef(request.workflow_id, run_id)
        except asyncio.CancelledError:
            raise
        except TemporalHostError:
            raise
        except Exception as error:
            if type(error).__name__ == "WorkflowAlreadyStartedError":
                raise TemporalHostError("workflow_exists") from None
            raise TemporalHostError("temporal_unavailable") from None

    async def status(self, value: object, ref: WorkflowRef) -> WorkflowStatus:
        _, handle = self._handle(value, ref)
        try:
            description = await handle.describe()
            status = description.status.name.lower()
            if status not in {"running", "completed", "failed", "canceled",
                              "terminated", "timed_out", "continued_as_new"}:
                raise TemporalHostError("invalid_workflow_status")
            stage = None
            if status == "running":
                stage = await handle.query("stage")
                if stage not in _STAGES:
                    raise TemporalHostError("invalid_workflow_status")
            elif status == "completed":
                stage = "completed"
            elif status == "canceled":
                stage = "cancel_requested"
            else:
                stage = "failed"
            return WorkflowStatus(ref.workflow_id, ref.run_id, status, stage)
        except asyncio.CancelledError:
            raise
        except TemporalHostError:
            raise
        except Exception:
            raise TemporalHostError("temporal_unavailable") from None

    async def result(self, value: object, ref: WorkflowRef) -> TemporalReceipt:
        request, handle = self._handle(value, ref)
        try:
            result = await handle.result()
            return TemporalReceipt.from_wire(result, request)
        except asyncio.CancelledError:
            raise
        except InvalidTemporalRequest:
            raise TemporalHostError("invalid_workflow_result") from None
        except Exception as error:
            cause = error
            for _ in range(4):
                code = getattr(cause, "type", None)
                if code in _FAILURE_CODES:
                    raise TemporalHostError(code) from None
                cause = getattr(cause, "cause", None)
                if cause is None:
                    break
            raise TemporalHostError("workflow_failed") from None

    async def cancel(self, value: object, ref: WorkflowRef) -> WorkflowStatus:
        self._request(value)
        _, handle = self._handle(value, ref)
        try:
            await handle.cancel()
        except asyncio.CancelledError:
            raise
        except Exception:
            raise TemporalHostError("temporal_unavailable") from None
        return WorkflowStatus(ref.workflow_id, ref.run_id, "cancel_requested",
                              "cancel_requested")

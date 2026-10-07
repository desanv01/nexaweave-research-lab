"""Deterministic Temporal orchestration; no stores or providers in this module."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from .temporal_contracts import (InvalidTemporalRequest,
                                     TemporalIngestionRequest, TemporalReceipt)

ACTIVITY_NAME = "mirofish_ingest_source_v1"
START_TO_CLOSE = timedelta(minutes=10)
SCHEDULE_TO_CLOSE = timedelta(minutes=12)
HEARTBEAT_TIMEOUT = timedelta(seconds=30)


@workflow.defn(name="mirofish_source_ingestion_v1")
class SourceIngestionWorkflow:
    def __init__(self) -> None:
        self._stage = "created"

    @workflow.run
    async def run(self, wire: Any) -> dict[str, Any]:
        # Pure revalidation is required even after host admission. No code in
        # workflow opens a connection, resolves an ontology or sees source text.
        try:
            request = TemporalIngestionRequest.from_wire(wire)
        except InvalidTemporalRequest:
            self._stage = "failed"
            raise ApplicationError("invalid_request", type="invalid_request",
                                   non_retryable=True) from None
        self._stage = "activity_scheduled"
        try:
            result = await workflow.execute_activity(
                ACTIVITY_NAME, request.to_wire(),
                start_to_close_timeout=START_TO_CLOSE,
                schedule_to_close_timeout=SCHEDULE_TO_CLOSE,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except asyncio.CancelledError:
            self._stage = "cancel_requested"
            raise
        except Exception:
            # The activity emits only fixed, nonretryable failure codes. Never
            # stringify an exception into workflow history or a query result.
            self._stage = "failed"
            raise
        try:
            validated = TemporalReceipt.from_wire(result, request)
        except InvalidTemporalRequest:
            self._stage = "failed"
            raise ApplicationError("invalid_receipt", type="invalid_receipt",
                                   non_retryable=True) from None
        self._stage = "completed"
        return validated.to_wire()

    @workflow.query(name="stage")
    def stage(self) -> str:
        return self._stage

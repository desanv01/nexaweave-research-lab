"""Deterministic, identifier-only Temporal workflow for one native run."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from .native_run_contracts import InvalidNativeRun, NativeRunRequest
    from .temporal_native_contracts import qualified_receipt

ACTIVITY_NAME = "mirofish_native_run_v1"
START_TO_CLOSE = timedelta(hours=2)
SCHEDULE_TO_CLOSE = timedelta(hours=2, minutes=5)
HEARTBEAT_TIMEOUT = timedelta(seconds=30)


@workflow.defn(name="mirofish_native_execution_v1")
class NativeExecutionWorkflow:
    def __init__(self) -> None:
        self._stage = "created"

    @workflow.run
    async def run(self, wire: Any) -> dict[str, Any]:
        try:
            request = NativeRunRequest.from_wire(wire)
        except InvalidNativeRun:
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
            self._stage = "failed"
            raise
        try:
            receipt = qualified_receipt(result, request)
        except InvalidNativeRun:
            self._stage = "failed"
            raise ApplicationError("invalid_receipt", type="invalid_receipt",
                                   non_retryable=True) from None
        self._stage = "completed"
        return receipt.to_wire()

    @workflow.query(name="stage")
    def stage(self) -> str:
        return self._stage

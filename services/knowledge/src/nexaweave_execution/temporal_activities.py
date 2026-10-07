"""Temporal activity adapter for accepted budgeted retained-source ingestion."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from typing import Any
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from nexaweave_knowledge.contracts import OntologySpec
from nexaweave_knowledge.operations import _receipt
from nexaweave_knowledge.source_bridge import (BridgeConflict, BridgeDenied,
    BridgeInvalid, BridgeNotFound, BridgeUnavailable, BridgeUncertain,
    SourceIngestionBridge)

from .budget import (BudgetBusy, BudgetConflict, BudgetDenied, BudgetUncertain,
                     BudgetUnavailable)
from .budgeted_ingestion import BudgetedIngestion
from .temporal_contracts import (InvalidTemporalRequest, TemporalIngestionRequest,
                                 TemporalReceipt)

_FAILURES = {
    BudgetDenied: "budget_denied", BudgetBusy: "budget_busy",
    BudgetConflict: "budget_conflict", BudgetUncertain: "ingestion_uncertain",
    BudgetUnavailable: "store_unavailable", BridgeDenied: "source_denied",
    BridgeNotFound: "source_not_found", BridgeInvalid: "invalid_source",
    BridgeConflict: "ingestion_busy", BridgeUncertain: "ingestion_uncertain",
    BridgeUnavailable: "store_unavailable", InvalidTemporalRequest: "invalid_request",
}


def _safe_failure(error: Exception) -> ApplicationError:
    code = next((name for kind, name in _FAILURES.items() if isinstance(error, kind)),
                "ingestion_failed")
    return ApplicationError(code, type=code, non_retryable=True)


class SourceIngestionActivities:
    def __init__(self, *, trusted_principal: str, bridge: SourceIngestionBridge,
                 service: BudgetedIngestion,
                 resolve_ontology: Callable[[str, UUID], OntologySpec],
                 allow_dispatch: Callable[[], bool] | None = None,
                 heartbeat_seconds: float = 5.0):
        if (not isinstance(trusted_principal, str) or not trusted_principal
                or not isinstance(bridge, SourceIngestionBridge)
                or not isinstance(service, BudgetedIngestion)
                or not callable(resolve_ontology)
                or allow_dispatch is not None and not callable(allow_dispatch)
                or type(heartbeat_seconds) not in (int, float)
                or not 0 < heartbeat_seconds <= 10):
            raise ValueError("invalid trusted Temporal activity configuration")
        self._principal = trusted_principal
        self._bridge = bridge
        self._service = service
        self._ontology = resolve_ontology
        self._allow = allow_dispatch
        self._heartbeat_seconds = float(heartbeat_seconds)

    @activity.defn(name="mirofish_ingest_source_v1")
    async def ingest_source(self, wire: Any) -> dict[str, Any]:
        stop = asyncio.Event()
        async def heartbeat() -> None:
            while not stop.is_set():
                activity.heartbeat({"stage": "running", "operation_id": request.operation_id})
                try:
                    await asyncio.wait_for(stop.wait(), self._heartbeat_seconds)
                except asyncio.TimeoutError:
                    pass

        try:
            request = TemporalIngestionRequest.from_wire(wire)
            if request.principal != self._principal or self._allow is None or self._allow() is not True:
                raise BudgetDenied()
            heartbeats = asyncio.create_task(heartbeat())
            service_task: asyncio.Task | None = None
            try:
                revision = UUID(request.ontology_revision)
                ontology = await asyncio.to_thread(self._ontology, self._principal, revision)
                if not isinstance(ontology, OntologySpec):
                    raise InvalidTemporalRequest()
                ontology = OntologySpec.model_validate(ontology.model_dump(warnings=False))
                if ontology.revision != revision:
                    raise InvalidTemporalRequest()
                plan = await asyncio.to_thread(self._bridge.plan, self._principal,
                    request.display_graph_id, UUID(request.source_revision),
                    UUID(request.operation_id), ontology)
                if plan.request_fingerprint != request.request_fingerprint:
                    raise InvalidTemporalRequest()
                if heartbeats.done():
                    heartbeats.result()
                    raise RuntimeError("heartbeat_stopped")
                service_task = asyncio.create_task(self._service.ingest(self._principal,
                    request.display_graph_id, UUID(request.source_revision),
                    UUID(request.operation_id), ontology,
                    account_id=UUID(request.account_id),
                    ceiling_microusd=request.ceiling_microusd))
                done, _ = await asyncio.wait((service_task, heartbeats),
                                             return_when=asyncio.FIRST_COMPLETED)
                if heartbeats in done:
                    # Heartbeat cancellation/failure must stop the in-flight
                    # service task; it never authorizes budget release.
                    heartbeats.result()
                    raise RuntimeError("heartbeat_stopped")
                receipt = service_task.result()
                valid = _receipt(receipt, plan.scope, UUID(request.operation_id),
                                 request.request_fingerprint)
                if valid.evidence_ids != plan.source.evidence_ids:
                    raise ValueError("receipt evidence mismatch")
                return TemporalReceipt(1, valid.group_id, str(valid.episode_id),
                    valid.fingerprint, tuple(str(v) for v in valid.evidence_ids)).to_wire()
            finally:
                if service_task is not None and not service_task.done():
                    service_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await service_task
                stop.set()
                heartbeats.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await heartbeats
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise _safe_failure(error) from None

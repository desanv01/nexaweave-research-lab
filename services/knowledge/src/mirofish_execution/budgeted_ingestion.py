"""Trusted host adapter between retained source ingestion and budget admission."""
from __future__ import annotations

import asyncio
from uuid import UUID

from mirofish_knowledge.contracts import OntologySpec
from mirofish_knowledge.ingestion import KnowledgeIngestionCoordinator
from mirofish_knowledge.operations import CompletionReceipt, _receipt
from mirofish_knowledge.source_bridge import IngestionPlan, SourceIngestionBridge

from .budget import (BudgetBusy, BudgetLedger, BudgetUncertain, ReservationState,
                     _money)


class BudgetedIngestion:
    def __init__(self, bridge: SourceIngestionBridge, ledger: BudgetLedger,
                 coordinator: KnowledgeIngestionCoordinator):
        if (not isinstance(bridge, SourceIngestionBridge) or not isinstance(ledger, BudgetLedger)
                or not isinstance(coordinator, KnowledgeIngestionCoordinator)):
            raise TypeError("trusted ingestion components required")
        self._bridge = bridge
        self._ledger = ledger
        self._coordinator = coordinator
        self._cleanup_tasks: set[asyncio.Task] = set()

    def _quarantine(self, principal: str, account_id: UUID, operation_id: UUID,
                    attempt_id: UUID, code: str) -> asyncio.Task:
        """Keep cancellation cleanup alive; started money stays held if DB fails."""
        task = asyncio.create_task(asyncio.to_thread(self._ledger.mark_uncertain,
                                  principal, account_id, operation_id, attempt_id, code))
        self._cleanup_tasks.add(task)
        def finished(done: asyncio.Task) -> None:
            self._cleanup_tasks.discard(done)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)
        return task

    async def _mark_uncertain(self, principal: str, account_id: UUID,
                              operation_id: UUID, attempt_id: UUID, code: str) -> None:
        task = self._quarantine(principal, account_id, operation_id, attempt_id, code)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=15)
        except Exception:
            # The started row remains unavailable even without an uncertainty
            # acknowledgment. Never release it or permit a retry.
            pass

    @staticmethod
    def _same_plan(first: IngestionPlan, second: IngestionPlan) -> bool:
        return (isinstance(second, IngestionPlan) and first.principal == second.principal
                and first.display_graph_id == second.display_graph_id
                and first.scope == second.scope and first.source == second.source
                and first.ontology == second.ontology
                and first.request_fingerprint == second.request_fingerprint
                and first.source_byte_length == second.source_byte_length
                and first.source_codepoint_length == second.source_codepoint_length
                and first.source_provenance == second.source_provenance)

    async def ingest(self, principal: str, display_graph_id: str, source_revision: UUID,
                     operation_id: UUID, ontology: OntologySpec, *, account_id: UUID,
                     ceiling_microusd: int) -> CompletionReceipt:
        """The host supplies a defensible maximum; no default authorizes spending."""
        ceiling = _money(ceiling_microusd)
        plan = await asyncio.to_thread(self._bridge.plan, principal, display_graph_id,
                                       source_revision, operation_id, ontology)
        reservation = await asyncio.to_thread(self._ledger.reserve, principal, account_id,
                                              plan.scope, plan.source.operation_id,
                                              plan.request_fingerprint, ceiling,
                                              plan.source.evidence_ids)
        if reservation.state == ReservationState.settled:
            if reservation.receipt is None:
                raise BudgetUncertain()
            try:
                saved = _receipt(reservation.receipt, plan.scope, plan.source.operation_id,
                                 plan.request_fingerprint)
            except (AttributeError, TypeError, ValueError):
                raise BudgetUncertain() from None
            if saved.evidence_ids != plan.source.evidence_ids:
                raise BudgetUncertain()
            return saved
        if reservation.state != ReservationState.reserved:
            raise BudgetBusy()
        # This transaction ends before any bridge/coordinator/provider work. Only
        # the winner receives a callback bound to the exact freshly owned plan.
        start_task = asyncio.create_task(asyncio.to_thread(self._ledger.start, principal, account_id,
                                         plan.source.operation_id, reservation.attempt_id))
        try:
            await asyncio.shield(start_task)
        except asyncio.CancelledError:
            async def after_start() -> None:
                try:
                    await start_task
                except Exception:
                    return  # Reserved, never dispatched; still unavailable.
                self._quarantine(principal, account_id, plan.source.operation_id,
                                 reservation.attempt_id, "dispatch_cancelled")
            cleanup = asyncio.create_task(after_start())
            self._cleanup_tasks.add(cleanup)
            def cleanup_finished(done: asyncio.Task) -> None:
                self._cleanup_tasks.discard(done)
                if not done.cancelled():
                    done.exception()
            cleanup.add_done_callback(cleanup_finished)
            raise
        try:
            receipt = await self._bridge.dispatch(principal, display_graph_id,
                                                  source_revision, operation_id, ontology,
                                                  self._coordinator,
                                                  authorize_model_call=lambda fresh: self._same_plan(plan, fresh))
            await asyncio.to_thread(self._ledger.settle, principal, account_id, plan.scope,
                                    plan.source.operation_id, reservation.attempt_id,
                                    plan.request_fingerprint, plan.source.evidence_ids, receipt)
            return receipt
        except asyncio.CancelledError:
            self._quarantine(principal, account_id, plan.source.operation_id,
                             reservation.attempt_id, "dispatch_cancelled")
            raise
        except Exception:
            await self._mark_uncertain(principal, account_id, plan.source.operation_id,
                                       reservation.attempt_id, "dispatch_uncertain")
            raise BudgetUncertain() from None

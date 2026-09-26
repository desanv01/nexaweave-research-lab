"""Internal async coordinator for ledger admitted Graphiti ingestion."""

from __future__ import annotations

import asyncio
import math
from typing import Protocol

from .contracts import IngestResult, KnowledgeScope, OntologySpec, SourceEnvelope
from .operations import (Busy, Claim, CompletionReceipt, InvalidTransition, Ledger,
                         OperationState, _receipt, request_fingerprint)


class IngestionUncertain(RuntimeError):
    """The write outcome requires explicit reconciliation; details stay private."""


class IngestionProvider(Protocol):
    async def ingest(self, scope: KnowledgeScope, source: SourceEnvelope,
                     ontology: OntologySpec) -> IngestResult: ...

    async def completion_proof(self, scope: KnowledgeScope, source: SourceEnvelope,
                               ontology: OntologySpec) -> CompletionReceipt: ...


class KnowledgeIngestionCoordinator:
    def __init__(self, ledger: Ledger, provider: IngestionProvider, *,
                 timeout_seconds: float, max_offloads: int = 4):
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 300):
            raise ValueError("finite operation timeout must be within 300 seconds")
        if isinstance(max_offloads, bool) or not isinstance(max_offloads, int) or not 1 <= max_offloads <= 32:
            raise ValueError("max_offloads must be between 1 and 32")
        self._ledger = ledger
        self._provider = provider
        self._timeout = float(timeout_seconds)
        self._offloads = asyncio.Semaphore(max_offloads)
        self._running_offloads: set[asyncio.Future] = set()
        self._cleanup_tasks: set[asyncio.Task] = set()

    @staticmethod
    def _consume(finished: asyncio.Future) -> None:
        if not finished.cancelled():
            finished.exception()

    def _keep_cleanup(self, task: asyncio.Task) -> None:
        self._cleanup_tasks.add(task)

        def finished(done: asyncio.Task) -> None:
            self._cleanup_tasks.discard(done)
            self._consume(done)

        task.add_done_callback(finished)

    async def _submit(self, method, *args) -> asyncio.Future:
        await self._offloads.acquire()
        try:
            future = asyncio.get_running_loop().run_in_executor(None, method, *args)
        except BaseException:
            self._offloads.release()
            raise
        self._running_offloads.add(future)

        def finished(done: asyncio.Future) -> None:
            self._running_offloads.discard(done)
            self._offloads.release()
            self._consume(done)

        future.add_done_callback(finished)
        return future

    async def _db(self, method, *args):
        future = await self._submit(method, *args)
        return await asyncio.shield(future)

    async def _settle_cancelled_claim(self, future: asyncio.Future,
                                      scope: KnowledgeScope, operation_id) -> None:
        try:
            claimed = await asyncio.shield(future)
        except Exception:
            return
        try:
            await self._record_uncertain(scope, operation_id, claimed.attempt_id, "claim_cancelled")
        except IngestionUncertain:
            pass

    async def _claim(self, scope: KnowledgeScope, operation_id) -> Claim:
        future = await self._submit(self._ledger.claim, scope, operation_id)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            self._keep_cleanup(asyncio.create_task(self._settle_cancelled_claim(future, scope, operation_id)))
            raise

    async def _record_uncertain(self, scope: KnowledgeScope, operation_id,
                                attempt_id, code: str) -> None:
        task = asyncio.create_task(self._db(self._ledger.mark_uncertain, scope, operation_id, attempt_id, code))
        self._keep_cleanup(task)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=15)
        except BaseException:
            # A running admission is quarantined too. Never claim that an
            # acknowledgment or release occurred when storage is unavailable.
            raise IngestionUncertain("knowledge ingestion outcome uncertain") from None

    @staticmethod
    def _validated_request(scope: KnowledgeScope, source: SourceEnvelope,
                           ontology: OntologySpec):
        try:
            scope = KnowledgeScope.model_validate(scope.model_dump())
            source = SourceEnvelope.model_validate(source.model_dump())
            ontology = OntologySpec.model_validate(ontology.model_dump())
            if len(set(source.evidence_ids)) != len(source.evidence_ids):
                raise ValueError
            fingerprint = request_fingerprint(scope, source, ontology)
            return scope, source, ontology, fingerprint
        except (AttributeError, TypeError, ValueError):
            raise ValueError("invalid knowledge ingestion request") from None

    @staticmethod
    def _validated_result(result: IngestResult, scope: KnowledgeScope,
                          source: SourceEnvelope) -> None:
        try:
            result = IngestResult.model_validate(result.model_dump())
            expected = str(scope.episode_uuid(source.operation_id))
            if result.episode_id != expected:
                raise ValueError
            evidence = set(source.evidence_ids)
            for fact in result.facts:
                if (fact.scope != scope or expected not in fact.episode_ids
                        or not evidence.issubset(fact.evidence_ids)):
                    raise ValueError
        except (AttributeError, TypeError, ValueError):
            raise ValueError("invalid knowledge provider result") from None

    async def ingest(self, scope: KnowledgeScope, source: SourceEnvelope,
                     ontology: OntologySpec) -> CompletionReceipt:
        scope, source, ontology, fingerprint = self._validated_request(scope, source, ontology)
        operation_id = source.operation_id
        admitted = await self._db(self._ledger.admit, scope, operation_id, fingerprint)
        if admitted.state == OperationState.completed:
            if admitted.receipt is None:
                raise IngestionUncertain("knowledge completion receipt missing")
            return _receipt(admitted.receipt, scope, operation_id, fingerprint)
        if admitted.state in (OperationState.running, OperationState.uncertain):
            raise Busy("knowledge scope write admission busy")
        if admitted.state == OperationState.cancelled:
            raise InvalidTransition("knowledge operation cancelled")
        claim = await self._claim(scope, operation_id)
        deadline = asyncio.get_running_loop().time() + self._timeout

        async def within_deadline(start):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            result = await asyncio.wait_for(start(), remaining)
            # wait_for may return a late result when the provider suppresses
            # cancellation. Such a result cannot authorize acknowledgment.
            if asyncio.get_running_loop().time() >= deadline:
                raise asyncio.TimeoutError
            return result

        try:
            result = await within_deadline(lambda: self._provider.ingest(scope, source, ontology))
            self._validated_result(result, scope, source)
            proof = await within_deadline(lambda: self._provider.completion_proof(scope, source, ontology))
            valid = _receipt(proof, scope, operation_id, fingerprint)
            if valid.evidence_ids != source.evidence_ids:
                raise ValueError("knowledge proof evidence mismatch")
            completed = await self._db(self._ledger.complete, scope, operation_id, claim.attempt_id, valid)
            if completed.receipt != valid or completed.state != OperationState.completed:
                raise IngestionUncertain("knowledge completion acknowledgment missing")
            return valid
        except asyncio.CancelledError:
            try:
                await self._record_uncertain(scope, operation_id, claim.attempt_id, "provider_cancelled")
            except IngestionUncertain:
                pass
            raise
        except asyncio.TimeoutError:
            await self._record_uncertain(scope, operation_id, claim.attempt_id, "provider_timeout")
            raise IngestionUncertain("knowledge ingestion outcome uncertain") from None
        except Exception:
            await self._record_uncertain(scope, operation_id, claim.attempt_id, "dispatch_uncertain")
            raise IngestionUncertain("knowledge ingestion outcome uncertain") from None

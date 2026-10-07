"""Coordinator behavior with deterministic in-memory collaborators."""

import asyncio
import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from threading import Event, Lock
from time import monotonic
from uuid import uuid4

import pytest

from nexaweave_knowledge.contracts import FactResult, IngestResult, KnowledgeScope, Layer, OntologySpec, SourceEnvelope
from nexaweave_knowledge.ingestion import IngestionUncertain, KnowledgeIngestionCoordinator
from nexaweave_knowledge.operations import Busy, Claim, CompletionReceipt, Conflict, OperationRecord, OperationState, request_fingerprint


def request():
    scope = KnowledgeScope(workspace_id=uuid4(), project_id=uuid4(), graph_id=uuid4(), layer=Layer.source)
    ontology = OntologySpec(revision=uuid4(), entity_types=({"name": "Person", "description": "A person"},), edge_types=())
    content = "Synthetic source"
    source = SourceEnvelope(source_revision=uuid4(), source_sha256=hashlib.sha256(content.encode()).hexdigest(),
                            ontology_revision=ontology.revision, operation_id=uuid4(), source_kind="document",
                            content=content, source_name="fixture", recorded_at=datetime.now(timezone.utc), evidence_ids=(uuid4(),))
    return scope, source, ontology


class FakeLedger:
    def __init__(self):
        self.lock = Lock()
        self.rows = {}
        self.claims = 0
        self.marked = []
        self.fail_complete = False
        self.fail_mark = False
        self.mark_done = Event()

    def admit(self, scope, op, fingerprint):
        with self.lock:
            key = (scope.group_id, op)
            row = self.rows.get(key)
            if row and row.fingerprint != fingerprint:
                raise Conflict("knowledge operation fingerprint conflict")
            if row is None:
                now = datetime.now(timezone.utc)
                row = OperationRecord(scope.group_id, op, fingerprint, OperationState.pending,
                                      now, now, None, None, None)
                self.rows[key] = row
            return row

    def claim(self, scope, op):
        with self.lock:
            key = (scope.group_id, op)
            row = self.rows[key]
            if any(item.group_id == scope.group_id and item.state in (OperationState.running, OperationState.uncertain)
                   for item in self.rows.values()):
                raise Busy("knowledge scope write admission busy")
            token = uuid4()
            row = replace(row, state=OperationState.running, current_attempt=token)
            self.rows[key] = row
            self.claims += 1
            return Claim(row, token)

    def complete(self, scope, op, token, receipt):
        if self.fail_complete:
            raise RuntimeError("private database detail")
        with self.lock:
            key = (scope.group_id, op)
            row = self.rows[key]
            assert row.current_attempt == token
            row = replace(row, state=OperationState.completed, receipt=receipt)
            self.rows[key] = row
            return row

    def mark_uncertain(self, scope, op, token, code):
        if self.fail_mark:
            raise RuntimeError("private DSN detail")
        with self.lock:
            key = (scope.group_id, op)
            row = self.rows[key]
            assert row.current_attempt == token
            self.rows[key] = replace(row, state=OperationState.uncertain, error_code=code)
            self.marked.append(code)
            self.mark_done.set()
            return self.rows[key]


class FakeProvider:
    def __init__(self):
        self.dispatches = 0
        self.started = asyncio.Event()
        self.failure = None
        self.wait = None
        self.result_change = None
        self.proof_change = None

    async def ingest(self, scope, source, ontology):
        self.dispatches += 1
        self.started.set()
        if self.wait:
            await self.wait.wait()
        if self.failure:
            raise RuntimeError(self.failure)
        episode = str(scope.episode_uuid(source.operation_id))
        fact = FactResult(provider_id=episode, scope=scope, kind="episode",
                          episode_ids=(episode,), evidence_ids=source.evidence_ids)
        result = IngestResult(episode_id=episode, already_exists=False, facts=(fact,))
        return self.result_change(result) if self.result_change else result

    async def completion_proof(self, scope, source, ontology):
        proof = CompletionReceipt(scope.group_id, scope.episode_uuid(source.operation_id),
                                  request_fingerprint(scope, source, ontology), source.evidence_ids)
        return self.proof_change(proof) if self.proof_change else proof


@pytest.mark.asyncio
async def test_normal_completion_and_duplicate_from_persisted_receipt():
    ledger, provider = FakeLedger(), FakeProvider()
    scope, source, ontology = request()
    first = KnowledgeIngestionCoordinator(ledger, provider, timeout_seconds=1)
    receipt = await first.ingest(scope, source, ontology)
    assert receipt.episode_id == scope.episode_uuid(source.operation_id)
    assert await KnowledgeIngestionCoordinator(ledger, provider, timeout_seconds=1).ingest(scope, source, ontology) == receipt
    assert provider.dispatches == ledger.claims == 1
    with pytest.raises(Conflict):
        await first.ingest(scope, source.model_copy(update={"source_revision": uuid4()}), ontology)


@pytest.mark.asyncio
async def test_invalid_copies_and_duplicate_evidence_before_collaborators():
    ledger, provider = FakeLedger(), FakeProvider()
    coordinator = KnowledgeIngestionCoordinator(ledger, provider, timeout_seconds=1)
    scope, source, ontology = request()
    for changed in (
        source.model_copy(update={"recorded_at": datetime.now()}),
        source.model_copy(update={"content": "bad hash"}),
        source.model_copy(update={"evidence_ids": source.evidence_ids * 2}),
    ):
        with pytest.raises(ValueError, match="invalid knowledge ingestion request"):
            await coordinator.ingest(scope, changed, ontology)
    with pytest.raises(ValueError):
        await coordinator.ingest(scope.model_copy(update={"layer": "invalid"}), source, ontology)
    assert ledger.rows == {} and provider.dispatches == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    lambda result: result.model_copy(update={"episode_id": str(uuid4())}),
    lambda result: result.model_copy(update={"facts": (result.facts[0].model_copy(update={"scope": request()[0]}),)}),
    lambda result: result.model_copy(update={"facts": (result.facts[0].model_copy(update={"evidence_ids": ()}),)}),
])
async def test_invalid_provider_result_quarantines(change):
    ledger, provider = FakeLedger(), FakeProvider()
    scope, source, ontology = request()
    provider.result_change = change
    with pytest.raises(IngestionUncertain):
        await KnowledgeIngestionCoordinator(ledger, provider, timeout_seconds=1).ingest(scope, source, ontology)
    assert ledger.rows[(scope.group_id, source.operation_id)].state == OperationState.uncertain


@pytest.mark.asyncio
async def test_missing_or_partial_proof_and_provider_text_do_not_leak():
    for proof_change, failure in ((lambda proof: None, None),
                                  (lambda proof: replace(proof, fingerprint="0" * 64), None),
                                  (lambda proof: replace(proof, group_id="wrong"), None),
                                  (lambda proof: replace(proof, episode_id=uuid4()), None),
                                  (lambda proof: replace(proof, evidence_ids=()), None),
                                  (None, "private prompt and model key")):
        ledger, provider = FakeLedger(), FakeProvider()
        scope, source, ontology = request()
        provider.proof_change, provider.failure = proof_change, failure
        with pytest.raises(IngestionUncertain) as caught:
            await KnowledgeIngestionCoordinator(ledger, provider, timeout_seconds=1).ingest(scope, source, ontology)
        assert "private" not in str(caught.value)
        assert ledger.rows[(scope.group_id, source.operation_id)].state == OperationState.uncertain


@pytest.mark.asyncio
async def test_timeout_and_cancellation_quarantine():
    for cancel in (False, True):
        ledger, provider = FakeLedger(), FakeProvider()
        scope, source, ontology = request()
        provider.wait = asyncio.Event()
        task = asyncio.create_task(KnowledgeIngestionCoordinator(ledger, provider, timeout_seconds=0.03).ingest(scope, source, ontology))
        await asyncio.wait_for(provider.started.wait(), timeout=2)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(IngestionUncertain):
                await task
        assert ledger.rows[(scope.group_id, source.operation_id)].state == OperationState.uncertain


@pytest.mark.asyncio
async def test_lost_acknowledgment_and_uncertainty_storage_failure():
    for fail_mark in (False, True):
        ledger, provider = FakeLedger(), FakeProvider()
        scope, source, ontology = request()
        ledger.fail_complete, ledger.fail_mark = True, fail_mark
        with pytest.raises(IngestionUncertain) as caught:
            await KnowledgeIngestionCoordinator(ledger, provider, timeout_seconds=1).ingest(scope, source, ontology)
        assert "private" not in str(caught.value)
        state = ledger.rows[(scope.group_id, source.operation_id)].state
        assert state == (OperationState.running if fail_mark else OperationState.uncertain)


@pytest.mark.asyncio
async def test_cancelled_offloaded_claim_with_one_slot_quarantines_after_thread_finishes():
    class SlowClaimLedger(FakeLedger):
        claim_started = Event()
        claim_release = Event()

        def claim(self, scope, op):
            self.claim_started.set()
            assert self.claim_release.wait(timeout=3)
            return super().claim(scope, op)

    ledger, provider = SlowClaimLedger(), FakeProvider()
    scope, source, ontology = request()
    coordinator = KnowledgeIngestionCoordinator(ledger, provider, timeout_seconds=2, max_offloads=1)
    task = asyncio.create_task(coordinator.ingest(scope, source, ontology))
    assert await asyncio.to_thread(ledger.claim_started.wait, 2)
    task.cancel()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    ledger.claim_release.set()
    assert await asyncio.to_thread(ledger.mark_done.wait, 3)
    assert ledger.rows[(scope.group_id, source.operation_id)].state == OperationState.uncertain
    assert provider.dispatches == 0


@pytest.mark.asyncio
async def test_repeated_cancel_during_admit_keeps_offload_slot_until_thread_finishes():
    class SlowAdmitLedger(FakeLedger):
        def __init__(self):
            super().__init__()
            self.admit_started = Event()
            self.admit_release = Event()
            self.active = 0
            self.peak = 0
            self.count_lock = Lock()

        def admit(self, scope, op, fingerprint):
            with self.count_lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                self.admit_started.set()
                assert self.admit_release.wait(timeout=3)
                return super().admit(scope, op, fingerprint)
            finally:
                with self.count_lock:
                    self.active -= 1

    ledger, provider = SlowAdmitLedger(), FakeProvider()
    scope, source, ontology = request()
    coordinator = KnowledgeIngestionCoordinator(ledger, provider, timeout_seconds=2, max_offloads=1)
    first = asyncio.create_task(coordinator.ingest(scope, source, ontology))
    assert await asyncio.to_thread(ledger.admit_started.wait, 2)
    first.cancel()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(coordinator.ingest(scope, source, ontology))
    await asyncio.sleep(0)
    assert ledger.peak == 1
    ledger.admit_release.set()
    await asyncio.wait_for(second, timeout=3)
    assert ledger.peak == 1
    assert provider.dispatches == 1


@pytest.mark.asyncio
async def test_shared_dispatch_and_proof_deadline():
    class DelayedProofProvider(FakeProvider):
        async def ingest(self, scope, source, ontology):
            await asyncio.sleep(0.12)
            return await super().ingest(scope, source, ontology)

        async def completion_proof(self, scope, source, ontology):
            await asyncio.sleep(0.12)
            return await super().completion_proof(scope, source, ontology)

    ledger, provider = FakeLedger(), DelayedProofProvider()
    scope, source, ontology = request()
    started = monotonic()
    with pytest.raises(IngestionUncertain):
        await KnowledgeIngestionCoordinator(ledger, provider, timeout_seconds=0.2).ingest(scope, source, ontology)
    # The required uncertainty result distinguishes a shared deadline from two
    # full budgets; leave scheduler headroom for loaded Windows/CI machines.
    assert monotonic() - started < 1.0
    assert ledger.rows[(scope.group_id, source.operation_id)].state == OperationState.uncertain


@pytest.mark.asyncio
async def test_late_proof_after_suppressed_cancellation_is_uncertain():
    class NonCooperativeProof(FakeProvider):
        async def completion_proof(self, scope, source, ontology):
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                return await super().completion_proof(scope, source, ontology)

    ledger, provider = FakeLedger(), NonCooperativeProof()
    scope, source, ontology = request()
    with pytest.raises(IngestionUncertain):
        await KnowledgeIngestionCoordinator(ledger, provider, timeout_seconds=0.03).ingest(scope, source, ontology)
    assert ledger.rows[(scope.group_id, source.operation_id)].state == OperationState.uncertain
    assert ledger.rows[(scope.group_id, source.operation_id)].receipt is None

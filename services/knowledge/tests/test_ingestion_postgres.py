"""Opt-in coordinator races on the accepted disposable PostgreSQL fixture."""

import asyncio
import hashlib
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from nexaweave_knowledge.contracts import FactResult, IngestResult, KnowledgeScope, Layer, OntologySpec, SourceEnvelope
from nexaweave_knowledge.ingestion import IngestionUncertain, KnowledgeIngestionCoordinator
from nexaweave_knowledge.operations import Busy, CompletionReceipt, Ledger, OperationState, request_fingerprint
from test_operations_postgres import factory  # accepted fixture guard and migration

pytestmark = pytest.mark.postgres


def request(scope=None):
    scope = scope or KnowledgeScope(workspace_id=uuid4(), project_id=uuid4(), graph_id=uuid4(), layer=Layer.source)
    ontology = OntologySpec(revision=uuid4(), entity_types=({"name": "Person", "description": "A person"},), edge_types=())
    content = "Synthetic fixture source"
    source = SourceEnvelope(source_revision=uuid4(), source_sha256=hashlib.sha256(content.encode()).hexdigest(),
                            ontology_revision=ontology.revision, operation_id=uuid4(), source_kind="document",
                            content=content, source_name="fixture", recorded_at=datetime.now(timezone.utc), evidence_ids=(uuid4(),))
    return scope, source, ontology


class BarrierProvider:
    def __init__(self, blocked_group=None, *, fail=False):
        self.blocked_group = blocked_group
        self.fail = fail
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.dispatches = []

    async def ingest(self, scope, source, ontology):
        self.dispatches.append((scope.group_id, source.operation_id))
        if scope.group_id == self.blocked_group:
            self.started.set()
            await self.release.wait()
        if self.fail:
            raise RuntimeError("private provider response")
        episode = str(scope.episode_uuid(source.operation_id))
        fact = FactResult(provider_id=episode, scope=scope, kind="episode", episode_ids=(episode,),
                          evidence_ids=source.evidence_ids)
        return IngestResult(episode_id=episode, already_exists=False, facts=(fact,))

    async def completion_proof(self, scope, source, ontology):
        return CompletionReceipt(scope.group_id, scope.episode_uuid(source.operation_id),
                                 request_fingerprint(scope, source, ontology), source.evidence_ids)


@pytest.mark.asyncio
async def test_real_admission_blocks_same_scope_without_holding_sql_locks(factory):
    one, source, ontology = request()
    _, other_source, other_ontology = request(one)
    independent, independent_source, independent_ontology = request()
    provider = BarrierProvider(one.group_id)
    coordinator = KnowledgeIngestionCoordinator(Ledger(factory), provider, timeout_seconds=5)
    first = asyncio.create_task(coordinator.ingest(one, source, ontology))
    await asyncio.wait_for(provider.started.wait(), timeout=5)

    def inspect_unlocked_rows():
        with factory() as conn:
            with conn.transaction():
                conn.execute("SET LOCAL lock_timeout = '1s'")
                conn.execute("SELECT group_id FROM mf_knowledge.scopes WHERE group_id = %s FOR UPDATE NOWAIT", (one.group_id,)).fetchone()
                conn.execute("SELECT operation_id FROM mf_knowledge.operations WHERE group_id = %s AND operation_id = %s FOR UPDATE NOWAIT",
                             (one.group_id, source.operation_id)).fetchone()
                return conn.execute("SELECT count(*) FROM mf_knowledge.scope_admissions WHERE group_id = %s", (one.group_id,)).fetchone()[0]

    assert await asyncio.wait_for(asyncio.to_thread(inspect_unlocked_rows), timeout=5) == 1
    with pytest.raises(Busy):
        await KnowledgeIngestionCoordinator(Ledger(factory), provider, timeout_seconds=5).ingest(one, source, ontology)
    with pytest.raises(Busy):
        await KnowledgeIngestionCoordinator(Ledger(factory), provider, timeout_seconds=5).ingest(one, other_source, other_ontology)
    independent_receipt = await KnowledgeIngestionCoordinator(Ledger(factory), provider, timeout_seconds=5).ingest(
        independent, independent_source, independent_ontology)
    assert independent_receipt.group_id == independent.group_id
    provider.release.set()
    receipt = await asyncio.wait_for(first, timeout=5)
    assert receipt.group_id == one.group_id
    fresh = KnowledgeIngestionCoordinator(Ledger(factory), provider, timeout_seconds=5)
    assert await fresh.ingest(one, source, ontology) == receipt
    assert provider.dispatches.count((one.group_id, source.operation_id)) == 1
    assert len(provider.dispatches) == 2


@pytest.mark.asyncio
async def test_fresh_coordinator_never_replays_uncertain_work(factory):
    one, source, ontology = request()
    provider = BarrierProvider(fail=True)
    first = KnowledgeIngestionCoordinator(Ledger(factory), provider, timeout_seconds=5)
    with pytest.raises(IngestionUncertain):
        await first.ingest(one, source, ontology)
    assert Ledger(factory).get(one, source.operation_id).state == OperationState.uncertain
    with pytest.raises(Busy):
        await KnowledgeIngestionCoordinator(Ledger(factory), provider, timeout_seconds=5).ingest(one, source, ontology)
    assert provider.dispatches == [(one.group_id, source.operation_id)]


@pytest.mark.asyncio
async def test_commit_then_lost_ack_returns_stored_receipt_without_replay(factory):
    class LostAckLedger:
        def __init__(self, actual):
            self.actual = actual
            self.lost = False

        def __getattr__(self, name):
            return getattr(self.actual, name)

        def complete(self, scope, operation_id, attempt_id, receipt):
            result = self.actual.complete(scope, operation_id, attempt_id, receipt)
            if not self.lost:
                self.lost = True
                raise RuntimeError("private lost acknowledgment")
            return result

    one, source, ontology = request()
    provider = BarrierProvider()
    ledger = LostAckLedger(Ledger(factory))
    with pytest.raises(IngestionUncertain):
        await KnowledgeIngestionCoordinator(ledger, provider, timeout_seconds=5).ingest(one, source, ontology)
    stored = Ledger(factory).get(one, source.operation_id)
    assert stored.state == OperationState.completed
    assert stored.receipt is not None
    assert await KnowledgeIngestionCoordinator(Ledger(factory), provider, timeout_seconds=5).ingest(
        one, source, ontology) == stored.receipt
    assert provider.dispatches == [(one.group_id, source.operation_id)]

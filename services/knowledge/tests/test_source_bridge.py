"""Offline bridge validation and default-denied dispatch contracts."""

import asyncio
from datetime import datetime, timezone
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from nexaweave_knowledge.bindings import BindingRecord
from nexaweave_knowledge.contracts import KnowledgeScope, Layer, OntologySpec
from nexaweave_knowledge.ingestion import KnowledgeIngestionCoordinator
from nexaweave_knowledge.source_bridge import (BridgeDenied, BridgeInvalid,
                                               BridgeNotFound, SourceIngestionBridge)
from nexaweave_storage.source import PassageRecord, SourceRecord


def ontology():
    return OntologySpec(revision=uuid4(), entity_types=({"name": "Person",
        "description": "A person"},), edge_types=())


def bridge_fixture():
    bridge = SourceIngestionBridge(lambda: None)
    scope = KnowledgeScope(workspace_id=uuid4(), project_id=uuid4(), graph_id=uuid4(),
                           layer=Layer.source)
    revision, evidence = uuid4(), uuid4()
    recorded = datetime(2026, 9, 29, tzinfo=timezone.utc)
    text = "A😀猫"
    import hashlib
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    excerpt = "😀猫"
    passage = PassageRecord(evidence, revision, scope.project_id, 1, 3, None,
                            excerpt, hashlib.sha256(excerpt.encode()).hexdigest())
    source = SourceRecord(scope.project_id, revision, "text", text, digest,
                          len(text.encode()), len(text), recorded, (passage,))
    bridge._bindings = SimpleNamespace(resolve=lambda principal, display: BindingRecord(
        principal, display, scope, recorded))
    bridge._projects = SimpleNamespace(get=lambda principal, project: SimpleNamespace(
        principal=principal, project_id=scope.project_id, workspace_id=scope.workspace_id))
    bridge._sources = SimpleNamespace(get_source=lambda principal, project, revision: source)
    return bridge, scope, source


def test_plan_uses_only_retained_content_hash_time_and_order():
    bridge, scope, retained = bridge_fixture()
    op, spec = uuid4(), ontology()
    plan = bridge.plan("owner", "graph_1", retained.source_revision, op, spec)
    assert plan.scope == scope and plan.source.content == retained.text
    assert plan.source.source_sha256 == retained.text_sha256
    assert plan.source.recorded_at == retained.recorded_at
    assert plan.source.evidence_ids == tuple(p.evidence_id for p in retained.passages)
    assert plan.source.asserted_valid_at is None
    assert plan.source.source_kind == "document"
    assert len(plan.request_fingerprint) == 64


@pytest.mark.parametrize("mutation", ["workspace", "layer", "branch", "empty",
                                      "oversize", "project"], ids=["workspace",
                                      "layer", "branch", "empty-passages",
                                      "long-source", "project"])
def test_plan_fails_closed_on_scope_and_source_mismatch(mutation):
    bridge, scope, retained = bridge_fixture()
    if mutation == "workspace":
        bridge._projects = SimpleNamespace(get=lambda *_: SimpleNamespace(
            principal="owner", project_id=scope.project_id, workspace_id=uuid4()))
    elif mutation == "layer":
        wrong = scope.model_copy(update={"layer": Layer.assumption})
        bridge._bindings = SimpleNamespace(resolve=lambda p, d: BindingRecord(p, d, wrong, retained.recorded_at))
    elif mutation == "branch":
        wrong = scope.model_copy(update={"branch_id": uuid4()})
        bridge._bindings = SimpleNamespace(resolve=lambda p, d: BindingRecord(p, d, wrong, retained.recorded_at))
    elif mutation == "empty":
        bridge._sources = SimpleNamespace(get_source=lambda *_: SourceRecord(
            retained.project_id, retained.source_revision, retained.name, retained.text,
            retained.text_sha256, retained.byte_length, retained.codepoint_length,
            retained.recorded_at, ()))
    elif mutation == "oversize":
        bridge._sources = SimpleNamespace(get_source=lambda *_: SourceRecord(
            retained.project_id, retained.source_revision, retained.name, "x" * 32769,
            retained.text_sha256, 32769, 32769, retained.recorded_at, retained.passages))
    else:
        bridge._projects = SimpleNamespace(get=lambda *_: SimpleNamespace(
            principal="owner", project_id=uuid4(), workspace_id=scope.workspace_id))
    with pytest.raises((BridgeInvalid, BridgeNotFound)):
        bridge.plan("owner", "graph_1", retained.source_revision, uuid4(), ontology())


@pytest.mark.asyncio
async def test_dispatch_default_denied_before_coordinator_call():
    bridge, _, retained = bridge_fixture()
    class DeniedCoordinator(KnowledgeIngestionCoordinator):
        def __init__(self):
            self.called = False
        async def ingest(self, *_):
            self.called = True
    coordinator = DeniedCoordinator()
    with pytest.raises(BridgeDenied):
        await bridge.dispatch("owner", "graph_1", retained.source_revision, uuid4(),
                              ontology(), coordinator)
    assert not coordinator.called


@pytest.mark.asyncio
async def test_dispatch_keeps_loop_responsive_and_cancellation_never_calls_coordinator():
    bridge, _, retained = bridge_fixture()
    started, release = Event(), Event()
    original_plan = bridge.plan
    def blocked_plan(*args):
        started.set()
        release.wait(timeout=5)
        return original_plan(*args)
    bridge.plan = blocked_plan
    class RecordingCoordinator(KnowledgeIngestionCoordinator):
        def __init__(self):
            self.called = False
        async def ingest(self, *_):
            self.called = True
    coordinator = RecordingCoordinator()
    task = asyncio.create_task(bridge.dispatch("owner", "graph_1",
        retained.source_revision, uuid4(), ontology(), coordinator,
        authorize_model_call=lambda _: True))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), timeout=3)
        # The awaited plan has not returned, while the event loop can run work.
        tick = asyncio.Event()
        asyncio.get_running_loop().call_soon(tick.set)
        await asyncio.wait_for(tick.wait(), timeout=1)
        assert not coordinator.called
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    assert not coordinator.called


def test_copy_bypassed_ontology_denied():
    bridge, _, retained = bridge_fixture()
    altered = ontology().model_copy(update={"entity_types": ()})
    with pytest.raises(BridgeInvalid):
        bridge.plan("owner", "graph_1", retained.source_revision, uuid4(), altered)

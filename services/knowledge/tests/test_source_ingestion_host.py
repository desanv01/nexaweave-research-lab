"""Authored pure host tests. Explicit fakes do not establish live extraction."""
import asyncio
import hashlib
import io
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace, ModuleType
from uuid import uuid4

import pytest

from mirofish_knowledge.contracts import KnowledgeScope, Layer, SourceEnvelope, OntologySpec
from mirofish_knowledge.operations import CompletionReceipt, NotFound, request_fingerprint
from mirofish_knowledge.source_bridge import IngestionPlan
from mirofish_knowledge.source_library import SourceSettings, SourceError, encoded
from mirofish_knowledge.source_ingestion_host import (IngestionSettings, SourceIngestionHost,
    LazyGraphitiProvider, validate_payload, plan_result, MAX_BYTES)
from mirofish_knowledge.source_ingestion_bootstrap import serve_once
from mirofish_knowledge.stdio import PipeProtocolError


def configured():
    scope = KnowledgeScope(workspace_id=uuid4(), project_id=uuid4(), graph_id=uuid4(), layer=Layer.source)
    source_settings = SourceSettings("owner", "display", scope, {})
    env = {"KNOWLEDGE_LLM_BASE_URL": "https://api.deepseek.com", "KNOWLEDGE_LLM_MODEL": "deepseek-flash",
        "KNOWLEDGE_LLM_API_KEY": "private-key", "KNOWLEDGE_EMBEDDING_BASE_URL": "https://embedding.example/v1",
        "KNOWLEDGE_EMBEDDING_MODEL": "explicit-embedding", "KNOWLEDGE_EMBEDDING_API_KEY": "private-embedding-key",
        "KNOWLEDGE_EMBEDDING_DIMENSION": "8", "KNOWLEDGE_NEO4J_URI": "bolt://127.0.0.1:7687",
        "KNOWLEDGE_NEO4J_USER": "neo4j", "KNOWLEDGE_NEO4J_PASSWORD": "private-password"}
    settings = IngestionSettings(source_settings, True, True, uuid4(), 4, env)
    spec = OntologySpec(revision=uuid4(), entity_types=({"name": "Person", "description": "Person"},), edge_types=())
    text = "Retained evidence 😀"
    source = SourceEnvelope(source_revision=uuid4(), source_sha256=hashlib.sha256(text.encode()).hexdigest(),
        ontology_revision=spec.revision, operation_id=uuid4(), source_kind="document", content=text,
        source_name="retained", recorded_at=datetime.now(timezone.utc), evidence_ids=(uuid4(),))
    plan = IngestionPlan("owner", "display", scope, source, spec, request_fingerprint(scope, source, spec),
                         len(text.encode()), len(text))
    payload = {"schema_version": 1, "source_revision": str(source.source_revision),
        "operation_id": str(source.operation_id), "ontology": spec.model_dump(mode="json")}
    return settings, plan, payload


def pure_host(settings, plan):
    def forbidden_connection():
        raise AssertionError("unexpected database access")
    host = SourceIngestionHost(settings, connection_factory=forbidden_connection,
        provider_factory=lambda: pytest.fail("unexpected provider construction"))
    host.authority = SimpleNamespace(authorize=lambda: plan.scope)
    host.bridge = SimpleNamespace(plan=lambda *args: plan)
    return host


def frame(host, method, payload, scope):
    raw = encoded({"version": 1, "request_id": str(uuid4()), "method": method,
                   "scope": scope.model_dump(mode="json"), "payload": payload})
    return json.loads(host.dispatch(raw))


def test_plan_cold_and_no_mutation(monkeypatch):
    settings, plan, payload = configured()
    host = pure_host(replace(settings, enabled=False, account_id=None), plan)
    host.budget = SimpleNamespace(status=lambda *args: pytest.fail("planning used budget"))
    result = host.execute("plan", payload)
    assert result == plan_result(plan)
    assert result["model_calls_made"] is False and result["spending_authorized"] is False
    assert plan.source.content not in encoded(result).decode()
    assert "private-key" not in repr(result)


@pytest.mark.parametrize("change", [{"enabled": False}, {"model_calls_authorized": False},
    {"account_id": None}, {"ceiling_microusd": 0}, {"ceiling_microusd": True},
    {"provider_environment": {}}, {"provider_environment": {"KNOWLEDGE_LLM_API_KEY": "secret"}}])
def test_denied_before_budget_or_provider(change):
    settings, plan, payload = configured()
    host = pure_host(replace(settings, **change), plan)
    result = frame(host, "execute", payload, plan.scope)
    assert result["ok"] is False
    assert result["error"]["code"] == "model_calls_disabled"
    assert "secret" not in repr(result)


def test_invalid_endpoint_and_local_policy_deny():
    settings, _, _ = configured()
    for url in ("https://user:secret@example.com", "file:///tmp/model", "https://example.com/?key=secret"):
        env = dict(settings.provider_environment, KNOWLEDGE_LLM_BASE_URL=url)
        with pytest.raises(SourceError):
            replace(settings, provider_environment=env).authorize_execution()
    env = dict(settings.provider_environment, KNOWLEDGE_OPERATING_PROFILE="local_only")
    with pytest.raises(SourceError):
        replace(settings, provider_environment=env).authorize_execution()


def test_scope_and_request_rejections():
    settings, plan, payload = configured()
    host = pure_host(settings, plan)
    wrong = plan.scope.model_copy(update={"graph_id": uuid4()})
    assert frame(host, "plan", payload, wrong)["error"]["code"] == "source_denied"
    for value in ({**payload, "principal": "other"}, {**payload, "schema_version": True},
                  {**payload, "operation_id": str(uuid4()).upper()}, {**payload, "ontology": {}}):
        with pytest.raises((ValueError, TypeError)):
            validate_payload("plan", value)
    raw = b'{"version":1,"version":1}'
    assert json.loads(host.dispatch(raw))["error"]["code"] == "invalid_request"
    assert json.loads(host.dispatch(b'{"value":NaN}'))["error"]["code"] == "invalid_request"


def test_status_cold_receipt_and_uncertain_flags():
    settings, plan, _ = configured()
    host = pure_host(settings, plan)
    receipt = CompletionReceipt(plan.scope.group_id, plan.scope.episode_uuid(plan.source.operation_id),
        plan.request_fingerprint, plan.source.evidence_ids)
    record = SimpleNamespace(fingerprint=plan.request_fingerprint, receipt=receipt,
                             state=SimpleNamespace(value="completed"))
    row = SimpleNamespace(fingerprint=plan.request_fingerprint, receipt=receipt,
        evidence_ids=plan.source.evidence_ids, ceiling_microusd=4, state=SimpleNamespace(value="uncertain"))
    host.ledger = SimpleNamespace(get=lambda *args: record)
    host._reservation = lambda *args: row
    result = host.status(plan.source.operation_id)
    assert result["state"] == "completed" and result["budget_state"] == "uncertain"
    assert result["actual_usage_microusd"] is None and result["model_calls_made"] is None
    # Completion in Neo4j is not proof that the money-settle transaction finished.
    row.fingerprint = "0" * 64
    with pytest.raises(SourceError, match="uncertain"):
        host.status(plan.source.operation_id)


def test_lazy_provider_constructs_once_only_at_ingest():
    settings, plan, _ = configured()
    calls = []
    class Provider:
        async def ingest(self, *args):
            calls.append("ingest")
            return "result"
        async def completion_proof(self, *args):
            return "proof"
    def factory():
        calls.append("construct")
        return Provider()
    lazy = LazyGraphitiProvider(settings, factory=factory)
    assert calls == []
    async def run():
        assert await lazy.ingest(plan.scope, plan.source, plan.ontology) == "result"
        assert await lazy.ingest(plan.scope, plan.source, plan.ontology) == "result"
        assert await lazy.completion_proof(plan.scope, plan.source, plan.ontology) == "proof"
    asyncio.run(run())
    assert calls == ["construct", "ingest", "ingest"]


def test_bootstrap_single_frame_and_limits():
    class Host:
        def dispatch(self, body):
            assert body == b"{}"
            return b"{}"
    output = io.BytesIO()
    serve_once(Host(), io.BytesIO(b"\x00\x00\x00\x02{}"), output)
    assert output.getvalue() == b"\x00\x00\x00\x02{}"
    for raw in (b"\x00\x00\x00\x00", b"\x00\x00\x00\x02{", b"\x00\x00\x00\x02{}extra",
                (MAX_BYTES + 1025).to_bytes(4, "big")):
        with pytest.raises(PipeProtocolError):
            serve_once(Host(), io.BytesIO(raw), io.BytesIO())


def production_adapter_module(monkeypatch, events, *, failure=None, close_failure=False):
    """Pin the production import to a lifecycle adapter, without loading SDKs."""
    module = ModuleType("mirofish_knowledge.provider")
    config = object()
    class Config:
        @classmethod
        def from_env(cls):
            events.append("config")
            return config
    class Adapter:
        def __init__(self, supplied):
            assert supplied is config
            events.append("construct")
            self.initialized, self.closed, self.allocated = False, False, []
        async def initialize(self):
            assert not self.closed and not self.initialized
            events.append("initialize")
            self.allocated.append(object())
            if failure is not None:
                raise failure
            self.initialized = True
        async def ingest(self, *args):
            assert self.initialized and not self.closed
            events.append("ingest")
            return "result"
        async def completion_proof(self, *args):
            assert self.initialized and not self.closed
            events.append("proof")
            return "proof"
        async def close(self):
            assert not self.closed
            events.append("close")
            self.closed = True
            self.allocated.clear()
            if close_failure:
                raise RuntimeError("private cleanup failure")
    module.ProviderConfig, module.GraphitiKnowledgeProvider = Config, Adapter
    monkeypatch.setitem(sys.modules, "mirofish_knowledge.provider", module)
    return module


def test_production_branch_initializes_once_before_ingest_and_proof(monkeypatch):
    settings, plan, _ = configured()
    events = []
    production_adapter_module(monkeypatch, events)
    lazy = LazyGraphitiProvider(settings)  # Select actual production branch.
    assert events == []
    async def run():
        with pytest.raises(SourceError, match="uncertain"):
            await lazy.completion_proof(plan.scope, plan.source, plan.ontology)
        assert events == []  # Proof is not an initialization trigger.
        assert await lazy.ingest(plan.scope, plan.source, plan.ontology) == "result"
        adapter = lazy.provider
        assert adapter.initialized and len(adapter.allocated) == 1
        assert await lazy.ingest(plan.scope, plan.source, plan.ontology) == "result"
        assert await lazy.completion_proof(plan.scope, plan.source, plan.ontology) == "proof"
        await lazy.close()
        await lazy.close()
        assert adapter.closed and not adapter.allocated and lazy.provider is None
        with pytest.raises(SourceError, match="uncertain"):
            await lazy.ingest(plan.scope, plan.source, plan.ontology)
        with pytest.raises(SourceError, match="uncertain"):
            await lazy.completion_proof(plan.scope, plan.source, plan.ontology)
    asyncio.run(run())
    assert events == ["config", "construct", "initialize", "ingest", "ingest", "proof", "close"]


@pytest.mark.parametrize("cancelled,close_failure", [(False, False), (False, True), (True, False)])
def test_production_initialization_failure_closes_and_never_dispatches(monkeypatch, cancelled, close_failure):
    settings, plan, _ = configured()
    events = []
    failure = asyncio.CancelledError() if cancelled else RuntimeError("private initialization failure")
    module = production_adapter_module(monkeypatch, events, failure=failure, close_failure=close_failure)
    original = module.GraphitiKnowledgeProvider
    adapters = []
    def construct(config):
        adapter = original(config)
        adapters.append(adapter)
        return adapter
    module.GraphitiKnowledgeProvider = construct
    lazy = LazyGraphitiProvider(settings)
    async def run():
        with pytest.raises(type(failure)) as raised:
            await lazy.ingest(plan.scope, plan.source, plan.ontology)
        assert raised.value is failure  # Cleanup failure cannot replace cause.
        assert adapters[0].closed and not adapters[0].allocated and lazy.provider is None
        with pytest.raises(SourceError, match="uncertain"):
            await lazy.ingest(plan.scope, plan.source, plan.ontology)
        with pytest.raises(SourceError, match="uncertain"):
            await lazy.completion_proof(plan.scope, plan.source, plan.ontology)
        await lazy.close()
    asyncio.run(run())
    assert events == ["config", "construct", "initialize", "close"]


def test_lifecycle_factory_initializes_once_for_concurrent_first_use():
    settings, plan, _ = configured()
    events = []
    class Adapter:
        async def initialize(self):
            events.append("initialize")
            await asyncio.sleep(0)
            self.ready = True
        async def ingest(self, *args):
            assert self.ready
            events.append("ingest")
            return "result"
        async def close(self):
            events.append("close")
    def factory():
        events.append("construct")
        return Adapter()
    lazy = LazyGraphitiProvider(settings, factory=factory)
    async def run():
        results = await asyncio.gather(*(lazy.ingest(plan.scope, plan.source, plan.ontology) for _ in range(2)))
        assert results == ["result", "result"]
        await lazy.close()
    asyncio.run(run())
    assert events == ["construct", "initialize", "ingest", "ingest", "close"]

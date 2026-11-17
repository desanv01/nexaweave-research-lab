"""Approved disposable PostgreSQL bridge and coordinator qualification."""

import hashlib
from io import BytesIO
import json
import os
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from nexaweave_knowledge.bindings import ScopeBindingStore
from nexaweave_knowledge.contracts import (FactResult, IngestResult, KnowledgeScope,
                                          Layer, OntologySpec)
from nexaweave_knowledge.ingestion import KnowledgeIngestionCoordinator
from nexaweave_knowledge.operations import (CompletionReceipt, Ledger,
                                           OperationState, migrate as migrate_knowledge)
from nexaweave_knowledge.source_bridge import (BridgeConflict, BridgeDenied,
                                               BridgeInvalid, BridgeNotFound, BridgeUnavailable,
                                               BridgeUncertain, SourceIngestionBridge)
from nexaweave_storage import ProjectStore, SourceStore, migrate as migrate_app
from nexaweave_storage.__main__ import main as cli_main
from test_project_store import snapshot

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def factory():
    if os.getenv("PROJECT_STORE_POSTGRES_INTEGRATION") != "1":
        pytest.skip("disposable PostgreSQL integration disabled")
    dsn = os.getenv("PROJECT_STORE_POSTGRES_TEST_DSN")
    if not dsn or any(os.getenv(key) for key in ("PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE")):
        pytest.fail("approved fixture DSN required")
    settings = conninfo_to_dict(dsn)
    if (settings.get("host") != "127.0.0.1" or settings.get("port") != "15432"
            or settings.get("dbname") != "mirofish_operations_test"
            or settings.get("user") != "mirofish_fixture" or not settings.get("password")
            or set(settings) - {"host", "port", "dbname", "user", "password", "connect_timeout"}):
        pytest.fail("not the approved disposable fixture")
    def connect():
        return psycopg.connect(dsn, connect_timeout=3)
    with connect() as conn:
        migrate_app(conn)
        migrate_knowledge(conn)
    return connect


def ontology():
    return OntologySpec(revision=uuid4(), entity_types=({"name": "Person",
        "description": "A person"},), edge_types=())


def owned_fixture(factory, *, text="A😀猫", passages=True, layer=Layer.source):
    project, workspace, revision, graph = uuid4(), uuid4(), uuid4(), uuid4()
    ProjectStore(factory).create("owner", workspace, project, "proj_1", snapshot())
    declaration = passages if type(passages) is list else (
        [{"evidence_id": str(uuid4()), "start": 1, "end": 3}] if passages else []
    )
    source = SourceStore(factory).ingest_text("owner", project, revision, "retained",
                                               text, declaration)
    scope = KnowledgeScope(workspace_id=workspace, project_id=project,
                           graph_id=graph, layer=layer)
    display = "graph_" + graph.hex[:12]
    ScopeBindingStore(factory).bind("owner", display, scope)
    return display, scope, source


def test_real_owned_plan_cli_and_denials(factory, tmp_path, monkeypatch, capsys):
    display, scope, retained = owned_fixture(factory)
    bridge, spec, operation = SourceIngestionBridge(factory), ontology(), uuid4()
    plan = bridge.plan("owner", display, retained.source_revision, operation, spec)
    assert plan.source.content == retained.text
    assert plan.source.source_sha256 == hashlib.sha256(retained.text.encode()).hexdigest()
    assert plan.source.recorded_at == retained.recorded_at
    assert plan.source.evidence_ids == tuple(item.evidence_id for item in retained.passages)
    monkeypatch.setenv("NEXAWEAVE_APPSTORE_DSN", os.environ["PROJECT_STORE_POSTGRES_TEST_DSN"])
    ontology_file = tmp_path / "ontology.json"
    ontology_file.write_text(spec.model_dump_json(), encoding="utf-8")
    args = ["export-ingestion", "--principal", "owner", "--display-graph-id", display,
            "--source-revision", str(retained.source_revision), "--operation-id", str(operation),
            "--ontology", str(ontology_file)]
    assert cli_main(args) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["source"]["content"] == retained.text
    assert exported["request_fingerprint"] == plan.request_fingerprint
    assert exported["ingestion_executed"] is False
    output = tmp_path / "plan.json"
    assert cli_main([*args, "--output", str(output)]) == 0
    original = output.read_bytes()
    assert cli_main([*args, "--output", str(output)]) == 2
    assert output.read_bytes() == original
    assert capsys.readouterr().err == "storage_error\n"
    with pytest.raises(BridgeNotFound):
        bridge.plan("other", display, retained.source_revision, operation, spec)
    with pytest.raises(BridgeNotFound):
        bridge.plan("owner", display, uuid4(), operation, spec)
    empty_display, _, empty = owned_fixture(factory, passages=False)
    with pytest.raises(BridgeInvalid):
        bridge.plan("owner", empty_display, empty.source_revision, uuid4(), spec)
    long_display, _, long = owned_fixture(factory, text="x" * 32769)
    with pytest.raises(BridgeInvalid):
        bridge.plan("owner", long_display, long.source_revision, uuid4(), spec)
    wrong_display, _, wrong = owned_fixture(factory, layer=Layer.assumption)
    with pytest.raises(BridgeInvalid):
        bridge.plan("owner", wrong_display, wrong.source_revision, uuid4(), spec)
    corrupt_display, _, corrupt = owned_fixture(factory)
    with factory() as conn:
        conn.execute("UPDATE mf_app.source_revisions SET retained_text='tampered' WHERE source_revision=%s",
                     (corrupt.source_revision,))
    with pytest.raises(BridgeUnavailable):
        bridge.plan("owner", corrupt_display, corrupt.source_revision, uuid4(), spec)
    Ledger(factory).tombstone_scope(scope)
    with pytest.raises(BridgeDenied):
        bridge.plan("owner", display, retained.source_revision, operation, spec)


def test_retained_window_plan_and_budgeted_execution(factory):
    from unittest.mock import AsyncMock

    from nexaweave_execution import BudgetLedger, migrate as migrate_budget
    from nexaweave_knowledge.operations import NotFound
    from nexaweave_knowledge.source_ingestion_bootstrap import serve_once
    from nexaweave_knowledge.source_ingestion_host import IngestionSettings, SourceIngestionHost
    from nexaweave_knowledge.source_library import SourceSettings, encoded

    first, second = "a" * 16400 + "😀", "猫" + "b" * 16400
    evidence = (uuid4(), uuid4())
    display, scope, retained = owned_fixture(factory, text=first + second, passages=[
        {"evidence_id": str(evidence[0]), "start": 0, "end": len(first), "page": 1},
        {"evidence_id": str(evidence[1]), "start": len(first), "end": len(first + second), "page": 2},
    ])
    with factory() as conn:
        migrate_budget(conn)
    account, ceiling = uuid4(), 4
    budget = BudgetLedger(factory)
    budget.create_account("owner", scope.project_id, account, 10)
    settings = IngestionSettings(
        SourceSettings("owner", display, scope, {}),
        enabled=True, model_calls_authorized=True, account_id=account, ceiling_microusd=ceiling,
        provider_environment={
            "KNOWLEDGE_LLM_BASE_URL": "http://127.0.0.1:18000/v1",
            "KNOWLEDGE_LLM_MODEL": "fixture", "KNOWLEDGE_LLM_API_KEY": "fixture",
            "KNOWLEDGE_EMBEDDING_BASE_URL": "http://127.0.0.1:18001/v1",
            "KNOWLEDGE_EMBEDDING_MODEL": "fixture", "KNOWLEDGE_EMBEDDING_API_KEY": "fixture",
            "KNOWLEDGE_EMBEDDING_DIMENSION": "8", "KNOWLEDGE_NEO4J_URI": "bolt://127.0.0.1:17687",
            "KNOWLEDGE_NEO4J_USER": "neo4j", "KNOWLEDGE_NEO4J_PASSWORD": "fixture",
        },
    )
    provider = FakeProvider()
    provider.ingest = AsyncMock(wraps=provider.ingest)
    provider.close = AsyncMock()
    host = SourceIngestionHost(settings, connection_factory=factory, provider_factory=lambda: provider)
    spec = ontology().model_dump(mode="json")
    source = {"source_revision": str(retained.source_revision), "ontology": spec}

    def reply(payload, *, method="plan"):
        raw = encoded({"version": 1, "request_id": str(uuid4()), "method": method,
                       "scope": scope.model_dump(mode="json"), "payload": payload})
        output = BytesIO()
        serve_once(host, BytesIO(len(raw).to_bytes(4, "big") + raw), output)
        framed = output.getvalue()
        assert int.from_bytes(framed[:4], "big") == len(framed) - 4
        return json.loads(framed[4:])

    assert reply(dict(source, schema_version=1, operation_id=str(uuid4())))["error"] == {
        "code": "invalid_request",
    }
    assert reply(dict(source, schema_version=2, evidence_id=str(uuid4())))["error"] == {
        "code": "not_found",
    }
    operations = []
    plans = []
    for index, (item, excerpt) in enumerate(zip(evidence, (first, second))):
        payload = dict(source, schema_version=2, evidence_id=str(item))
        result = reply(payload)["result"]
        assert reply(payload)["result"] == result
        plans.append(result)
        start = 0 if index == 0 else len(first)
        digest = hashlib.sha256(excerpt.encode()).hexdigest()
        assert result["window"] == {
            "evidence_id": str(item), "start": start, "end": start + len(excerpt),
            "page": index + 1, "ordinal": index + 1, "count": 2,
            "sha256": digest, "retained_sha256": retained.text_sha256,
            "retained_byte_length": retained.byte_length,
            "retained_codepoint_length": retained.codepoint_length,
        }
        assert result["source_sha256"] == digest != retained.text_sha256
        assert result["evidence_ids"] == [str(item)]
        operation = UUID(result["operation_id"])
        operations.append(operation)
        with pytest.raises(NotFound):
            Ledger(factory).get(scope, operation)
    assert operations[0] != operations[1]
    assert provider.calls == 0
    assert budget.status("owner", account).remaining_microusd == 10

    selected = dict(source, schema_version=2, evidence_id=str(evidence[0]))
    result = reply(selected, method="execute")["result"]
    expected = CompletionReceipt(
        scope.group_id, scope.episode_uuid(operations[0]), plans[0]["fingerprint"], (evidence[0],),
    )
    record = Ledger(factory).get(scope, operations[0])
    assert record.state == OperationState.completed and record.receipt == expected
    assert result["state"] == "completed" and result["budget_state"] == "settled"
    assert result["receipt"] == expected.json_value() and result["window"] == plans[0]["window"]
    provider_scope, envelope, provider_ontology = provider.ingest.await_args.args
    assert provider_scope == scope and provider_ontology.model_dump(mode="json") == spec
    assert envelope.source_revision == retained.source_revision and envelope.operation_id == operations[0]
    assert envelope.content == first and envelope.source_sha256 == plans[0]["source_sha256"]
    assert envelope.evidence_ids == (evidence[0],)

    assert reply(selected, method="execute")["result"] == result
    changed_spec = dict(spec, entity_types=[dict(spec["entity_types"][0], description="Changed")])
    assert reply(dict(selected, ontology=changed_spec), method="execute")["error"] == {"code": "conflict"}
    assert reply({"operation_id": str(operations[1])}, method="status")["error"] == {"code": "not_found"}
    with pytest.raises(NotFound):
        Ledger(factory).get(scope, operations[1])
    assert provider.calls == 1
    assert provider.ingest.await_count == 1 and provider.close.await_count == 1
    totals = budget.status("owner", account)
    assert (totals.accounted_ceiling_microusd, totals.remaining_microusd) == (ceiling, 10 - ceiling)


class FakeProvider:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0
    async def ingest(self, scope, source, ontology):
        self.calls += 1
        if self.fail:
            raise RuntimeError("private provider failure")
        episode = str(scope.episode_uuid(source.operation_id))
        fact = FactResult(provider_id=episode, scope=scope, kind="episode",
                          episode_ids=(episode,), evidence_ids=source.evidence_ids)
        return IngestResult(episode_id=episode, already_exists=False, facts=(fact,))
    async def completion_proof(self, scope, source, ontology):
        from nexaweave_knowledge.operations import request_fingerprint
        return CompletionReceipt(scope.group_id, scope.episode_uuid(source.operation_id),
                                 request_fingerprint(scope, source, ontology), source.evidence_ids)


@pytest.mark.asyncio
async def test_dispatch_gate_completion_retry_conflict_uncertain(factory):
    display, scope, retained = owned_fixture(factory)
    bridge, spec, operation = SourceIngestionBridge(factory), ontology(), uuid4()
    provider = FakeProvider()
    coordinator = KnowledgeIngestionCoordinator(Ledger(factory), provider, timeout_seconds=5)
    with pytest.raises(BridgeDenied):
        await bridge.dispatch("owner", display, retained.source_revision, operation, spec,
                              coordinator)
    assert provider.calls == 0
    receipt = await bridge.dispatch("owner", display, retained.source_revision,
        operation, spec, coordinator, authorize_model_call=lambda _: True)
    assert receipt.evidence_ids == tuple(p.evidence_id for p in retained.passages)
    assert await bridge.dispatch("owner", display, retained.source_revision,
        operation, spec, coordinator, authorize_model_call=lambda _: True) == receipt
    assert provider.calls == 1
    with pytest.raises(BridgeConflict):
        await bridge.dispatch("owner", display, retained.source_revision,
            operation, ontology(), coordinator, authorize_model_call=lambda _: True)
    failed = FakeProvider(fail=True)
    failing_coordinator = KnowledgeIngestionCoordinator(Ledger(factory), failed, timeout_seconds=5)
    uncertain_op = uuid4()
    with pytest.raises(BridgeUncertain):
        await bridge.dispatch("owner", display, retained.source_revision,
            uncertain_op, spec, failing_coordinator, authorize_model_call=lambda _: True)
    assert Ledger(factory).get(scope, uncertain_op).state == OperationState.uncertain
    assert failed.calls == 1

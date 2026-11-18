"""Real disposable PG, deterministic fake provider; no model/semantic qualification."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from types import ModuleType
from uuid import uuid4

import pytest
from psycopg.conninfo import conninfo_to_dict

from nexaweave_execution.budget import BudgetLedger, BudgetUnavailable, migrate
from nexaweave_knowledge.operations import Ledger, NotFound as OperationNotFound
from nexaweave_knowledge.reviewed_ontology import ReviewedOntologyHost
from nexaweave_knowledge.source_library import SourceSettings, encoded
from nexaweave_knowledge.source_ingestion_host import (
    ENVELOPE_OVERHEAD, MAX_BYTES, SourceIngestionHost, IngestionSettings,
)
from nexaweave_storage import ProjectStore
from test_source_bridge_postgres import FakeProvider, factory, ontology, owned_fixture
from test_source_ingestion_host import configured

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def migrated(factory):
    with factory() as conn:
        migrate(conn)


def setup(factory, *, provider=None, cap=10):
    display, scope, retained = owned_fixture(factory)
    account = uuid4()
    BudgetLedger(factory).create_account("owner", scope.project_id, account, cap)
    env = configured()[0].provider_environment
    settings = IngestionSettings(SourceSettings("owner", display, scope, {}), True, True, account, 4, env)
    constructed = []
    provider = provider or FakeProvider()
    def build():
        constructed.append(True)
        return provider
    host = SourceIngestionHost(settings, connection_factory=factory, provider_factory=build)
    spec = ontology()
    payload = {"schema_version": 1, "source_revision": str(retained.source_revision),
               "operation_id": str(uuid4()), "ontology": spec.model_dump(mode="json")}
    return host, payload, provider, constructed, retained


def reply(host, method, payload):
    return json.loads(host.dispatch(encoded({"version": 1, "request_id": str(uuid4()), "method": method,
        "scope": host.settings.source.scope.model_dump(mode="json"), "payload": payload})))


def test_owned_plan_execute_status_duplicate_changed_plan(factory):
    host, payload, provider, constructed, retained = setup(factory)
    planned = reply(host, "plan", payload)
    assert planned["ok"] and not constructed
    budget = host.budget.status("owner", host.settings.account_id)
    assert budget.remaining_microusd == 10 and budget.reserved_microusd == 0
    result = reply(host, "execute", payload)
    assert result["ok"] and provider.calls == 1 and len(constructed) == 1
    assert result["result"]["receipt"]["evidence_ids"] == [str(p.evidence_id) for p in retained.passages]
    # New host simulates a restart; settled duplicates must not construct a provider.
    fresh = SourceIngestionHost(host.settings, connection_factory=factory,
        provider_factory=lambda: pytest.fail("settled operation redispatched"))
    duplicate = reply(fresh, "execute", payload)
    assert duplicate["ok"] and duplicate["result"] == result["result"]
    status = reply(fresh, "status", {"operation_id": payload["operation_id"]})
    assert status["result"]["budget_state"] == "settled"
    assert status["result"]["actual_usage_microusd"] is None
    assert fresh.budget.status("owner", fresh.settings.account_id).accounted_ceiling_microusd == 4
    changed = {**payload, "ontology": ontology().model_dump(mode="json")}
    assert reply(fresh, "execute", changed)["error"]["code"] == "conflict"


def test_authority_account_cap_denials_construct_nothing(factory):
    host, payload, _, constructed, _ = setup(factory, cap=3)
    assert reply(host, "execute", payload)["error"]["code"] == "budget_denied"
    assert not constructed
    wrong = replace(host.settings, account_id=uuid4())
    missing = SourceIngestionHost(wrong, connection_factory=factory,
        provider_factory=lambda: pytest.fail("missing account constructed provider"))
    assert reply(missing, "execute", payload)["error"]["code"] == "budget_denied"
    other = replace(host.settings, source=replace(host.settings.source, principal="other"))
    denied = SourceIngestionHost(other, connection_factory=factory,
        provider_factory=lambda: pytest.fail("other owner constructed provider"))
    assert reply(denied, "plan", payload)["error"]["code"] == "not_found"
    Ledger(factory).tombstone_scope(host.settings.source.scope)
    assert reply(host, "status", {"operation_id": payload["operation_id"]})["error"]["code"] == "tombstoned"


def test_provider_uncertainty_holds_ceiling_and_no_retry(factory):
    host, payload, provider, constructed, _ = setup(factory, provider=FakeProvider(fail=True))
    assert reply(host, "execute", payload)["error"]["code"] == "uncertain"
    status = reply(host, "status", {"operation_id": payload["operation_id"]})["result"]
    assert status["state"] == "uncertain" and status["budget_state"] == "uncertain"
    assert status["graph_ingestion_executed"] is False and status["receipt"] is None
    assert host.budget.status("owner", host.settings.account_id).uncertain_microusd == 4
    assert reply(host, "execute", payload)["error"]["code"] == "uncertain"
    assert provider.calls == 1 and len(constructed) == 1


def test_settle_failure_retains_proven_graph_completion_and_held_money(factory, monkeypatch):
    host, payload, provider, constructed, _ = setup(factory)
    monkeypatch.setattr(host.budget, "settle", lambda *args: (_ for _ in ()).throw(BudgetUnavailable()))
    assert reply(host, "execute", payload)["error"]["code"] == "uncertain"
    status = reply(host, "status", {"operation_id": payload["operation_id"]})["result"]
    assert status["state"] == "completed" and status["budget_state"] == "uncertain"
    assert status["receipt"] is not None and status["graph_ingestion_executed"] is True
    assert host.budget.status("owner", host.settings.account_id).reserved_microusd == 4
    assert reply(host, "execute", payload)["error"]["code"] == "uncertain"
    assert provider.calls == 1


def test_concurrent_operation_constructs_only_winner(factory):
    entered, release = Event(), Event()
    class WaitingProvider(FakeProvider):
        async def ingest(self, *args):
            import asyncio
            entered.set()
            await asyncio.to_thread(release.wait, 10)
            return await super().ingest(*args)
    host, payload, provider, constructed, _ = setup(factory, provider=WaitingProvider())
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            running = pool.submit(reply, host, "execute", payload)
            assert entered.wait(10)
            other = SourceIngestionHost(host.settings, connection_factory=factory,
                provider_factory=lambda: pytest.fail("concurrent loser constructed provider"))
            assert reply(other, "execute", payload)["error"]["code"] == "busy"
            release.set()
            assert running.result(20)["ok"]
    finally:
        release.set()
    assert provider.calls == 1 and len(constructed) == 1


def test_started_without_response_is_held_without_redispatch(factory):
    host, payload, provider, constructed, _ = setup(factory)
    from uuid import UUID
    from nexaweave_knowledge.source_ingestion_host import validate_payload
    operation, revision, spec = validate_payload("execute", payload)
    plan = host.bridge.plan("owner", host.settings.source.display_graph_id, revision, operation, spec)
    row = host.budget.reserve("owner", host.settings.account_id, plan.scope, operation,
                             plan.request_fingerprint, 4, plan.source.evidence_ids)
    host.budget.start("owner", host.settings.account_id, operation, row.attempt_id)
    # Simulates a process lost after durable start, before any provable graph ack.
    result = reply(host, "status", {"operation_id": str(operation)})["result"]
    assert result["state"] == "not_admitted" and result["budget_state"] == "started"
    assert result["receipt"] is None and result["actual_usage_microusd"] is None
    assert host.budget.status("owner", host.settings.account_id).reserved_microusd == 4
    assert reply(host, "execute", payload)["error"]["code"] == "busy"
    assert not constructed and provider.calls == 0


def test_start_response_failure_keeps_ceiling_without_provider(factory, monkeypatch):
    host, payload, provider, constructed, _ = setup(factory)
    start = host.budget.start
    def lose_response(*args):
        start(*args)
        raise BudgetUnavailable()
    monkeypatch.setattr(host.budget, "start", lose_response)
    assert reply(host, "execute", payload)["error"]["code"] == "source_unavailable"
    status = reply(host, "status", {"operation_id": payload["operation_id"]})["result"]
    assert status["budget_state"] == "started" and status["receipt"] is None
    assert host.budget.status("owner", host.settings.account_id).reserved_microusd == 4
    assert not constructed and provider.calls == 0


@pytest.mark.parametrize("fail_initialization", [False, True])
def test_production_lifecycle_after_durable_admission(factory, monkeypatch, fail_initialization):
    host, payload, _, constructed, _ = setup(factory)
    host.provider_factory = None  # Exercise the real construction branch.
    events, adapters = [], []
    config = object()
    class Config:
        @classmethod
        def from_env(cls):
            events.append("config")
            return config
    class Adapter(FakeProvider):
        def __init__(self, supplied):
            assert supplied is config
            super().__init__()
            self.initialized, self.closed, self.resources = False, False, []
            events.append("construct")
            adapters.append(self)
        async def initialize(self):
            from uuid import UUID
            assert not self.closed and not self.initialized
            # Actual durable PostgreSQL rows, without model/graph calls.
            state = host.status(UUID(payload["operation_id"]))
            assert state["budget_state"] == "started" and state["state"] == "running"
            assert state["receipt"] is None
            assert host.budget.status("owner", host.settings.account_id).reserved_microusd == 4
            events.append("initialize")
            self.resources.append(object())
            if fail_initialization:
                raise RuntimeError("private SDK initialization failure")
            self.initialized = True
        async def ingest(self, *args):
            assert self.initialized and not self.closed
            events.append("ingest")
            return await super().ingest(*args)
        async def completion_proof(self, *args):
            assert self.initialized and not self.closed
            events.append("proof")
            return await super().completion_proof(*args)
        async def close(self):
            assert not self.closed
            events.append("close")
            self.closed = True
            self.resources.clear()
    module = ModuleType("nexaweave_knowledge.provider")
    module.ProviderConfig, module.GraphitiKnowledgeProvider = Config, Adapter
    monkeypatch.setitem(sys.modules, "nexaweave_knowledge.provider", module)
    assert reply(host, "plan", payload)["ok"] and not events
    outcome = reply(host, "execute", payload)
    assert not constructed  # Explicit factory seam was never selected.
    assert len(adapters) == 1 and adapters[0].closed and not adapters[0].resources
    if fail_initialization:
        assert outcome["error"]["code"] == "uncertain"
        assert events == ["config", "construct", "initialize", "close"]
        assert adapters[0].calls == 0
        status = reply(host, "status", {"operation_id": payload["operation_id"]})["result"]
        assert status["state"] == "uncertain" and status["budget_state"] == "uncertain"
        assert status["receipt"] is None
        assert host.budget.status("owner", host.settings.account_id).uncertain_microusd == 4
        assert reply(host, "execute", payload)["error"]["code"] == "uncertain"
    else:
        assert outcome["ok"] and adapters[0].calls == 1
        assert events == ["config", "construct", "initialize", "ingest", "proof", "close"]
        duplicate = reply(host, "execute", payload)
        assert duplicate["ok"] and duplicate["result"] == outcome["result"]
    assert len(adapters) == 1


_SAVED_PLAN_CHILD = r"""
import importlib.abc
import importlib.util
from pathlib import Path
import runpy
import sys

denied = ("nexaweave_knowledge.provider", "graphiti_core", "neo4j", "openai")
attempts = []

class NoProvider(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in denied):
            attempts.append(fullname)
            raise AssertionError("saved ontology plan attempted provider import")

sys.meta_path.insert(0, NoProvider())
module = "nexaweave_knowledge.source_ingestion_bootstrap"
origin = Path(importlib.util.find_spec(module).origin).resolve(strict=True)
assert origin.is_relative_to(Path(sys.prefix).resolve()) and "site-packages" in origin.parts
try:
    runpy.run_module(module, run_name="__main__", alter_sys=True)
finally:
    assert not attempts
"""


def test_installed_saved_ontology_dry_plan_revision_and_repeat(factory, tmp_path):
    display, scope, retained = owned_fixture(factory)
    projects = ProjectStore(factory)
    original = projects.get("owner", scope.project_id)
    account = uuid4()
    ledger = BudgetLedger(factory)
    ledger.create_account("owner", scope.project_id, account, 10)
    review = ReviewedOntologyHost(principal="owner", display_graph_id=display, scope=scope, connection_factory=factory)
    reviewed = {
        "entity_types": [{
            "name": "Speaker", "description": "Public speaker",
            "attributes": [{"name": "role", "type": "text", "description": "Public role"}],
        }],
        "edge_types": [],
    }
    saved = review.save(expected_revision=original.revision, ontology=reviewed)
    assert saved.revision == original.revision + 1

    pg = conninfo_to_dict(os.environ["PROJECT_STORE_POSTGRES_TEST_DSN"])
    if (pg.get("host") != "127.0.0.1" or pg.get("port") != "15432"
            or pg.get("dbname") != "mirofish_operations_test" or pg.get("user") != "mirofish_fixture"
            or not pg.get("password")):
        pytest.fail("approved disposable PostgreSQL fixture required", pytrace=False)
    runtime = {"SYSTEMROOT", "WINDIR", "PATH", "HOME", "USERPROFILE", "TMPDIR", "TMP", "TEMP", "LANG"}
    env = {key: value for key, value in os.environ.items() if key.upper() in runtime}
    scope_wire = scope.model_dump(mode="json")
    env.update(
        KNOWLEDGE_PRINCIPAL="owner", KNOWLEDGE_DISPLAY_GRAPH_ID=display,
        KNOWLEDGE_BOUND_SCOPE_JSON=encoded(scope_wire).decode("utf-8"),
        KNOWLEDGE_PG_HOST=pg["host"], KNOWLEDGE_PG_PORT=pg["port"],
        KNOWLEDGE_PG_DATABASE=pg["dbname"], KNOWLEDGE_PG_USER=pg["user"],
        KNOWLEDGE_PG_PASSWORD=pg["password"],
        KNOWLEDGE_INGESTION_ENABLED="true", KNOWLEDGE_MODEL_CALLS_AUTHORIZED="true",
        KNOWLEDGE_INGESTION_ACCOUNT_ID=str(account), KNOWLEDGE_INGESTION_CEILING_MICROUSD="4",
    )

    def framed(method, payload):
        request_id = str(uuid4())
        body = encoded({"version": 1, "request_id": request_id, "scope": scope_wire,
                        "method": method, "payload": payload})
        reply = subprocess.run(
            [sys.executable, "-I", "-u", "-c", _SAVED_PLAN_CHILD],
            input=len(body).to_bytes(4, "big") + body, cwd=tmp_path, env=env,
            capture_output=True, timeout=30, check=False,
        )
        assert reply.returncode == 0 and not reply.stderr, "installed framed ingestion child failed"
        raw = reply.stdout
        assert 4 < len(raw) <= 4 + MAX_BYTES + ENVELOPE_OVERHEAD
        assert int.from_bytes(raw[:4], "big") == len(raw) - 4
        value = json.loads(raw[4:])
        assert value.pop("version") == 1 and value.pop("request_id") == request_id
        return value

    operation = uuid4()
    payload = {"schema_version": 3, "source_revision": str(retained.source_revision),
               "operation_id": str(operation), "project_revision": saved.revision}
    planned = framed("plan", payload)
    result = planned["result"]
    assert set(planned) == {"ok", "result"} and planned["ok"] is True
    assert result["schema_version"] == 3 and result["project_revision"] == saved.revision
    assert result["scope"] == scope_wire and result["operation_id"] == str(operation)
    assert result["episode_id"] == str(scope.episode_uuid(operation))
    assert result["source_revision"] == str(retained.source_revision)
    assert result["source_sha256"] == retained.text_sha256
    assert result["source_byte_length"] == retained.byte_length
    assert result["source_codepoint_length"] == retained.codepoint_length
    assert result["evidence_ids"] == [str(p.evidence_id) for p in retained.passages]
    assert "window" not in result and "ontology" not in result
    assert result["spending_authorized"] is result["graph_ingestion_executed"] is result["model_calls_made"] is False
    assert result["actual_usage_microusd"] is None
    assert framed("plan", payload) == planned
    inline = {"schema_version": 1, "source_revision": payload["source_revision"],
              "operation_id": payload["operation_id"],
              "ontology": {"schema_version": 1, "revision": result["ontology_revision"], **reviewed}}
    legacy = framed("plan", inline)
    assert legacy["ok"] is True and legacy["result"]["schema_version"] == 1
    saved_identity = json.dumps(
        {"scope": scope_wire, "project_revision": saved.revision, "ontology": saved.snapshot["ontology"]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    expected_fingerprint = hashlib.sha256(b"\0".join((
        b"mirofish:source-ingestion:saved-ontology-plan:v3",
        saved_identity,
        legacy["result"]["fingerprint"].encode("ascii"),
    ))).hexdigest()
    assert result["fingerprint"] == expected_fingerprint
    assert framed("execute", payload) == {"ok": False, "error": {"code": "invalid_request"}}

    advanced = review.save(expected_revision=saved.revision, ontology=reviewed)
    assert advanced.revision == saved.revision + 1 and advanced.snapshot == saved.snapshot
    conflict = {"ok": False, "error": {"code": "conflict"}}
    assert framed("plan", payload) == conflict
    newer = framed("plan", dict(payload, project_revision=advanced.revision))
    updated = newer["result"]
    assert newer["ok"] is True
    assert updated["fingerprint"] != result["fingerprint"]
    assert updated["ontology_revision"] != result["ontology_revision"]
    assert updated == dict(result, project_revision=advanced.revision,
                           fingerprint=updated["fingerprint"], ontology_revision=updated["ontology_revision"])

    unsupported = dict(reviewed, entity_types=[dict(reviewed["entity_types"][0], examples=["Example"])])
    outside = review.save(expected_revision=advanced.revision, ontology=unsupported)
    assert framed("plan", dict(payload, project_revision=outside.revision)) == conflict
    assert projects.history("owner", scope.project_id) == [original, saved, advanced, outside]
    with pytest.raises(OperationNotFound):
        Ledger(factory).get(scope, operation)
    with factory() as conn:
        assert ledger._row(conn, account, operation) is None
    status = ledger.status("owner", account)
    assert status.remaining_microusd == 10 and status.accounted_ceiling_microusd == 0
    assert status.reserved_microusd == status.uncertain_microusd == 0

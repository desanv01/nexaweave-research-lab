"""Real disposable PG, deterministic fake provider; no model/semantic qualification."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from types import ModuleType
from uuid import uuid4

import pytest

from mirofish_execution.budget import BudgetLedger, BudgetUnavailable, migrate
from mirofish_knowledge.operations import Ledger
from mirofish_knowledge.source_library import SourceSettings, encoded
from mirofish_knowledge.source_ingestion_host import SourceIngestionHost, IngestionSettings
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
    from mirofish_knowledge.source_ingestion_host import validate_payload
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
    module = ModuleType("mirofish_knowledge.provider")
    module.ProviderConfig, module.GraphitiKnowledgeProvider = Config, Adapter
    monkeypatch.setitem(sys.modules, "mirofish_knowledge.provider", module)
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

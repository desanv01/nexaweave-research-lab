"""Protected ingestion API test sources; explicit injection is not live qualification."""
import builtins
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import create_app
from app.services.knowledge_read_facade import ReadHostSettings
from app.services.knowledge_ingestion_client import (validate_payload, validate_result, identities,
    KnowledgeIngestionProcessClient, MAX_BYTES, encoded)
from app.services.knowledge_ingestion_facade import KnowledgeIngestionFacade
from app.services.knowledge_transport import KnowledgeInvalidRequest, KnowledgeTransportFailure


def scope():
    return {"schema_version": 1, "workspace_id": str(uuid4()), "project_id": str(uuid4()),
        "graph_id": str(uuid4()), "run_id": None, "branch_id": None, "layer": "source"}


def payload():
    return {"schema_version": 1, "source_revision": str(uuid4()), "operation_id": str(uuid4()),
        "ontology": {"schema_version": 1, "revision": str(uuid4()), "entity_types": [
            {"name": "Person", "description": "Person", "attributes": []}], "edge_types": []}}


def result(bound, request):
    _, episode = identities(bound, request["operation_id"])
    return {"schema_version": 1, "scope": bound, "operation_id": request["operation_id"],
        "episode_id": episode, "fingerprint": "a" * 64, "evidence_ids": [str(uuid4())],
        "source_revision": request["source_revision"], "source_sha256": "b" * 64,
        "source_byte_length": 12, "source_codepoint_length": 12,
        "ontology_revision": request["ontology"]["revision"], "eligibility_codepoint_limit": 32768,
        "spending_authorized": False, "graph_ingestion_executed": False, "model_calls_made": False,
        "actual_usage_microusd": None}


@pytest.fixture
def host(monkeypatch):
    monkeypatch.setenv("NEXAWEAVE_APP_MODE", "research_local")
    monkeypatch.delenv("FLASK_HOST", raising=False)
    monkeypatch.delenv("NEXAWEAVE_ALLOWED_ORIGINS", raising=False)
    bound = scope()
    token = "0123456789abcdef" * 4
    settings = ReadHostSettings("python", "read_bootstrap.py", token, "owner", "display", bound, {})
    monkeypatch.setattr(ReadHostSettings, "from_config", classmethod(lambda cls, config: settings))
    class Facade:
        def __init__(self):
            self.calls, self.corrupt = [], None
        def execute(self, method, graph_id, request):
            self.calls.append((method, graph_id, request))
            dto = result(bound, request)
            if self.corrupt:
                self.corrupt(dto)
            return dto
    facade = Facade()
    app = create_app(ingestion_facade=facade)
    app.config["TESTING"] = True
    return app.test_client(), facade, {"Authorization": "Bearer " + token}, settings


def test_auth_origin_preflight_and_plan(host):
    http, facade, headers, settings = host
    url = "/api/source/ingestion/plan/display"
    body = payload()
    assert http.post(url, json=body).status_code == 401
    assert http.post(url, json=body, headers={**headers, "Origin": "https://evil.example"}).status_code == 403
    assert http.options(url, headers={"Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "DELETE"}).status_code == 403
    assert not facade.calls
    response = http.post(url, json=body, headers=headers)
    assert response.status_code == 200 and response.json["data"]["model_calls_made"] is False
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert http.post("/api/source/ingestion/plan/other", json=body, headers=headers).status_code == 404
    assert len(facade.calls) == 1


def test_default_off_even_with_injected_facade(host, monkeypatch):
    http, facade, headers, _ = host
    monkeypatch.delenv("KNOWLEDGE_INGESTION_ENABLED", raising=False)
    response = http.post("/api/source/ingestion/execute/display", json=payload(), headers=headers)
    assert response.status_code == 403 and response.json["error"]["code"] == "model_calls_disabled"
    assert not facade.calls


@pytest.mark.parametrize("mutate", [lambda dto: dto.update(scope=scope()),
    lambda dto: dto.update(operation_id=str(uuid4())), lambda dto: dto.update(episode_id=str(uuid4())),
    lambda dto: dto.update(spending_authorized=True), lambda dto: dto.update(model_calls_made=0),
    lambda dto: dto.update(graph_ingestion_executed=0), lambda dto: dto.update(actual_usage_microusd=4),
    lambda dto: dto.update(source_revision=str(uuid4())), lambda dto: dto.update(fingerprint="bad"),
    lambda dto: dto.update(source_content="private source"), lambda dto: dto.update(source_codepoint_length=True)])
def test_injected_dto_revalidated(host, mutate):
    http, facade, headers, _ = host
    facade.corrupt = mutate
    response = http.post("/api/source/ingestion/plan/display", json=payload(), headers=headers)
    assert response.status_code == 503 and response.json["error"]["code"] == "outcome_unknown"


def test_strict_body_and_status_uuid(host):
    http, facade, headers, _ = host
    url = "/api/source/ingestion/plan/display"
    for raw in (b'{"schema_version":1,"schema_version":1}', b'{"value":NaN}', b"x" * (MAX_BYTES + 1)):
        assert http.post(url, data=raw, content_type="application/json", headers=headers).status_code == 400
    assert http.post(url + "?account=other", json=payload(), headers=headers).status_code == 400
    assert http.get("/api/source/ingestion/operation/display/bad", headers=headers).status_code == 400
    assert not facade.calls
    for update in ({"schema_version": True}, {"principal": "other"}, {"ceiling_microusd": 4}):
        with pytest.raises(KnowledgeInvalidRequest):
            validate_payload("plan", {**payload(), **update})


def test_readonly_absence_and_provider_free_factory(host, monkeypatch):
    _, facade, headers, _ = host
    original = builtins.__import__
    def block(name, *args, **kwargs):
        if name.split(".")[0] in {"graphiti_core", "camel", "openai", "neo4j", "nexaweave_knowledge"}:
            pytest.fail("Flask startup initialized provider package")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", block)
    assert create_app(ingestion_facade=facade).test_client().get("/health").status_code == 200
    monkeypatch.setenv("NEXAWEAVE_APP_MODE", "graphiti_readonly")
    assert create_app(ingestion_facade=facade).test_client().post(
        "/api/source/ingestion/plan/display", json=payload(), headers=headers).status_code == 404


def test_complete_receipt_identity_and_strict_booleans():
    bound, body = scope(), payload()
    dto = result(bound, body)
    for key in ("source_revision", "source_sha256", "source_byte_length", "source_codepoint_length",
                "ontology_revision", "eligibility_codepoint_limit", "spending_authorized"):
        dto.pop(key)
    group, episode = identities(bound, body["operation_id"])
    dto.update(state="completed", budget_state="settled", ceiling_microusd=4,
               graph_ingestion_executed=True, model_calls_made=None)
    dto["receipt"] = {"group_id": group, "episode_id": episode,
        "fingerprint": dto["fingerprint"], "evidence_ids": dto["evidence_ids"]}
    assert validate_result("execute", dto, bound, body) == dto
    for key, value in (("episode_id", str(uuid4())), ("fingerprint", "0" * 64), ("evidence_ids", [])):
        wrong = deepcopy(dto)
        wrong["receipt"][key] = value
        with pytest.raises(KnowledgeTransportFailure):
            validate_result("execute", wrong, bound, body)
    wrong = deepcopy(dto)
    wrong["graph_ingestion_executed"] = 1
    with pytest.raises(KnowledgeTransportFailure):
        validate_result("execute", wrong, bound, body)


def test_lost_reply_not_retried():
    bound = scope()
    settings = ReadHostSettings("python", "read_bootstrap.py", "token", "owner", "display", bound, {})
    calls = []
    class Client:
        def call(self, raw):
            calls.append(raw)
            raise KnowledgeTransportFailure(outcome_unknown=True)
    facade = KnowledgeIngestionFacade(settings, client_factory=lambda *args: Client(), environment={})
    with pytest.raises(KnowledgeTransportFailure):
        facade.execute("plan", "display", payload())
    assert len(calls) == 1

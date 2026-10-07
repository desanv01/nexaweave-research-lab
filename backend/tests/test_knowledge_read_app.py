"""Actual Flask read-only routes through the accepted bounded byte reader."""

import json
import builtins
import csv
import io
from uuid import UUID

import pytest

from app import create_app
from app.config import Config
from app.services.knowledge_read_facade import KnowledgeReadFacade, ReadHostSettings


def uid(number):
    return str(UUID(int=number))


def scope():
    return {"schema_version": 1, "workspace_id": uid(1), "project_id": uid(2),
            "graph_id": uid(3), "run_id": None, "branch_id": None, "layer": "source"}


def fact(number, kind="node", **changes):
    result = {"schema_version": 1, "provider_id": uid(number), "scope": scope(), "kind": kind,
              "name": "人物" if kind == "node" else "KNOWS", "fact": None if kind == "node" else "认识",
              "source_node_id": None if kind == "node" else uid(10),
              "target_node_id": None if kind == "node" else uid(11),
              "episode_ids": [uid(800)], "evidence_ids": [uid(900)],
              "labels": ["Entity", "Person"] if kind == "node" else [],
              "summary": "摘要" if kind == "node" else None,
              "valid_at": "2024-01-01T00:00:00Z", "invalid_at": None,
              "expired_at": "2024-03-01T00:00:00Z" if kind == "edge" else None,
              "created_at": "2024-01-02T00:00:00Z", "attributes": {"city": "香港"}, "score": None}
    result.update(changes)
    return result


class MemoryClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def call(self, raw):
        if self.fail:
            raise RuntimeError("private database address")
        request = json.loads(raw)
        self.calls.append(request)
        kind = request["payload"]["kind"]
        facts = ([fact(10), fact(11, labels=["Entity", "Company"], name="公司")]
                 if kind == "node" else [fact(20, "edge")])
        reply = {"version": 1, "request_id": request["request_id"], "ok": True,
                 "result": {"schema_version": 1, "facts": facts, "next_cursor": None}}
        return json.dumps(reply, ensure_ascii=False, separators=(",", ":")).encode()


@pytest.fixture
def read_app(monkeypatch):
    monkeypatch.setenv("NEXAWEAVE_APP_MODE", "graphiti_readonly")
    monkeypatch.delenv("NEXAWEAVE_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("FLASK_HOST", raising=False)
    token = "0123456789abcdef" * 4
    settings = ReadHostSettings("python", "read_bootstrap.py", token, "owner", "display-1",
                                scope(), {})
    monkeypatch.setattr(ReadHostSettings, "from_config", classmethod(lambda cls, config: settings))
    transport = MemoryClient()
    facade = KnowledgeReadFacade(settings, client_factory=lambda: transport)
    app = create_app(read_facade=facade)
    app.config["TESTING"] = True
    return app, transport, token


def test_readonly_graph_and_entity_context_shapes(read_app):
    app, transport, token = read_app
    client = app.test_client()
    headers = {"Authorization": "Bearer " + token}
    health = client.get("/health")
    assert health.json["mode"] == "graphiti_readonly"
    graph = client.get("/api/graph/data/display-1", headers=headers)
    assert graph.status_code == 200 and graph.json["success"] is True
    data = graph.json["data"]
    assert data["node_count"] == 2 and data["edge_count"] == 1
    assert data["nodes"][0]["labels"] == ["Entity", "Person"]
    assert data["nodes"][0]["summary"] == "摘要"
    assert data["edges"][0]["expired_at"] == "2024-03-01T00:00:00Z"
    assert data["edges"][0]["evidence_ids"] == [uid(900)]
    entities = client.get("/api/graph/entities/display-1?types=Person&enrich=false", headers=headers)
    assert entities.json["data"]["filtered_count"] == 1
    assert entities.json["data"]["entities"][0]["related_edges"] == []
    context = client.get("/api/graph/entity/display-1/" + uid(10), headers=headers)
    assert context.json["data"]["related_edges"][0]["direction"] == "outgoing"
    assert context.json["data"]["related_nodes"][0]["uuid"] == uid(11)
    assert all(call["method"] == "page" and call["scope"] == scope() for call in transport.calls)


def test_token_origin_wrong_binding_and_unsupported_routes(read_app):
    app, transport, token = read_app
    client = app.test_client()
    endpoint = "/api/graph/data/display-1"
    missing = client.get(endpoint)
    wrong = client.get(endpoint, headers={"Authorization": "Bearer wrong"})
    non_ascii = client.get(endpoint, headers={"Authorization": "Bearer é"})
    assert missing.status_code == wrong.status_code == non_ascii.status_code == 401
    assert missing.json == wrong.json == non_ascii.json
    good = {"Authorization": "Bearer " + token, "Origin": "http://localhost:3000"}
    assert client.get(endpoint, headers=good).headers["Access-Control-Allow-Origin"] == good["Origin"]
    assert client.get(endpoint, headers={**good, "Origin": "http://evil.example"}).status_code == 403
    preflight = client.options(endpoint, headers={"Origin": good["Origin"],
                                                  "Access-Control-Request-Method": "GET",
                                                  "Access-Control-Request-Headers": "Authorization"})
    assert preflight.status_code == 204
    assert client.get("/api/graph/data/other", headers=good).status_code == 404
    assert client.post(endpoint, headers=good).status_code == 405
    assert client.get("/api/graph/build", headers=good).status_code == 404
    assert [call["payload"]["kind"] for call in transport.calls] == ["node", "edge"]


def test_transport_errors_are_fixed_and_no_empty_success(read_app):
    app, transport, token = read_app
    transport.fail = True
    response = app.test_client().get("/api/graph/data/display-1",
                                     headers={"Authorization": "Bearer " + token})
    assert response.status_code == 503
    assert response.json == {"success": False, "error": {"code": "transport_failure"}}
    assert "private" not in response.get_data(as_text=True)


def test_readonly_debug_and_public_bind_refused(monkeypatch):
    monkeypatch.setenv("NEXAWEAVE_APP_MODE", "graphiti_readonly")
    monkeypatch.setenv("FLASK_HOST", "0.0.0.0")
    with pytest.raises(ValueError):
        create_app()
    monkeypatch.setenv("FLASK_HOST", "127.0.0.1")
    class DebugConfig(Config):
        DEBUG = True
    with pytest.raises(ValueError):
        create_app(DebugConfig)


def test_legacy_default_and_readonly_validation_are_separate(monkeypatch):
    monkeypatch.delenv("NEXAWEAVE_APP_MODE", raising=False)
    monkeypatch.delenv("NEXAWEAVE_ALLOWED_ORIGINS", raising=False)
    from app.services.simulation_runner import SimulationRunner
    monkeypatch.setattr(SimulationRunner, "register_cleanup", classmethod(lambda cls: None))
    legacy = create_app()
    assert any(rule.rule.startswith("/api/simulation/") for rule in legacy.url_map.iter_rules())
    monkeypatch.setenv("NEXAWEAVE_APP_MODE", "graphiti_readonly")
    monkeypatch.delenv("KNOWLEDGE_READ_TOKEN", raising=False)
    assert Config.validate_readonly() == ["Invalid graph-read configuration"]


def test_readonly_startup_does_not_import_model_or_zep_modules(read_app, monkeypatch):
    _, transport, token = read_app
    settings = ReadHostSettings("python", "read_bootstrap.py", token, "owner", "display-1",
                                scope(), {})
    facade = KnowledgeReadFacade(settings, client_factory=lambda: transport)
    imported = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith(("zep_cloud", "graphiti_core", "openai")):
            raise AssertionError("read-only startup imported a model or legacy graph SDK")
        return imported(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert create_app(read_facade=facade).test_client().get("/health").status_code == 200


def test_population_preview_and_both_downloads_use_bounded_reader(read_app):
    app, transport, token = read_app
    client = app.test_client()
    headers = {"Authorization": "Bearer " + token}
    base = "/api/graph/population/display-1"
    assert "population_export" in client.get("/health").json["capabilities"]
    preview = client.post(base + "/preview", json={"types": ["Person"], "seed": 17},
                          headers=headers)
    assert preview.status_code == 200
    data = preview.json["data"]
    assert data["eligible_count"] == data["selected_count"] == 1
    assert data["profiles"][0]["source_entity_uuid"] == uid(10)
    assert data["grounding"][uid(10)]["evidence_ids"] == [uid(900)]
    assert data["grounding"][uid(10)]["facts"][0]["fact"] == "认识"
    assert [call["payload"]["kind"] for call in transport.calls] == ["node", "edge"]
    assert len(preview.data) <= 2 * 1024 * 1024

    twitter = client.post(base + "/export", json={"platform": "twitter", "seed": 17},
                          headers=headers)
    assert twitter.status_code == 200 and twitter.mimetype == "text/csv"
    assert twitter.headers["Content-Disposition"] == 'attachment; filename="oasis-twitter-profiles.csv"'
    assert twitter.headers["X-Content-Type-Options"] == "nosniff"
    rows = list(csv.DictReader(io.StringIO(twitter.data.decode("utf-8"), newline="")))
    assert len(rows) == 2 and rows[0]["name"] == "人物" and rows[1]["name"] == "公司"
    assert rows[0]["username"] == data["profiles"][0]["user_name"]

    reddit = client.post(base + "/export", json={"platform": "reddit", "seed": 17},
                         headers=headers)
    assert reddit.status_code == 200 and reddit.mimetype == "application/json"
    profiles = json.loads(reddit.data)
    assert len(profiles) == 2 and profiles[0]["username"] == rows[0]["username"]
    assert profiles[0]["name"] == "人物"
    assert [call["payload"]["kind"] for call in transport.calls] == [
        "node", "edge", "node", "edge", "node", "edge"]


def test_population_auth_origin_binding_preflight_and_validation(read_app):
    app, transport, token = read_app
    client = app.test_client()
    base = "/api/graph/population/display-1"
    headers = {"Authorization": "Bearer " + token}
    assert client.post(base + "/preview", json={}).status_code == 401
    assert client.post(base + "/preview", json={}, headers={
        **headers, "Origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/graph/population/other/preview", json={}, headers=headers).status_code == 404
    preflight = client.options(base + "/preview", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Authorization, Content-Type"})
    assert preflight.status_code == 204
    assert "POST" in preflight.headers["Access-Control-Allow-Methods"]
    assert client.options(base + "/preview", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "PUT"}).status_code == 403
    assert client.options("/api/graph/data/display-1", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"}).status_code == 403
    invalid = [
        (base + "/preview", b'{', "application/json"),
        (base + "/preview", b'{}', "text/plain"),
        (base + "/preview", b'{"seed":true}', "application/json"),
        (base + "/preview", b'{"seed":1,"seed":2}', "application/json"),
        (base + "/preview", b'{"types":["Person","Person"]}', "application/json"),
        (base + "/preview", b'{"types":null}', "application/json"),
        (base + "/preview", b'{"unknown":1}', "application/json"),
        (base + "/preview", b'{"max_agents":101}', "application/json"),
        (base + "/preview", b'{"seed":4294967296}', "application/json"),
        (base + "/preview", b' ' * (16 * 1024 + 1), "application/json"),
        (base + "/export", b'{}', "application/json"),
        (base + "/export", b'{"platform":"mastodon"}', "application/json"),
    ]
    for path, body, content_type in invalid:
        response = client.post(path, data=body, content_type=content_type, headers=headers)
        assert response.status_code == 400
        assert response.json == {"success": False, "error": {"code": "invalid_request"}}
    assert transport.calls == []
    empty = client.post(base + "/preview", json={"types": ["Faculty"]}, headers=headers)
    assert empty.status_code == 422
    assert empty.json == {"success": False, "error": {"code": "empty_selection"}}

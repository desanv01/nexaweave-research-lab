"""Authenticated route and cold-factory qualification source (pure)."""
import builtins
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import create_app
from app.services.knowledge_evidence_facade import KnowledgeEvidenceFacade
from app.services.knowledge_read_facade import ReadHostSettings
from app.services.knowledge_reader import KnowledgeReadError
from app.services.knowledge_transport import KnowledgeBusy
from test_knowledge_read_app import read_app, scope


@pytest.fixture
def evidence_app(read_app, monkeypatch):
    existing, transport, token = read_app
    calls = []
    class Evidence:
        failure = None
        def execute(self, method, graph_id, payload):
            calls.append((method, graph_id, payload))
            if self.failure:
                raise self.failure
            return {"fixture": "猫", "method": method}
    evidence = Evidence()
    app = create_app(evidence_facade=evidence)
    return app.test_client(), calls, token, evidence


def req(method="research"):
    if method == "research":
        return {"display_graph_ids": ["display-1", "simulation"], "text": "Alice 猫"}
    return {"title": "Evidence", "display_graph_ids": ["display-1", "simulation"],
            "sections": [{"heading": "First", "query": "Alice"}, {"heading": "Second", "query": "猫"}]}


@pytest.mark.parametrize("method", ["research", "dossier"])
def test_auth_origin_preflight_method_and_anchor_before_facade(evidence_app, method):
    client, calls, token, _ = evidence_app
    url = "/api/graph/" + method + "/display-1"
    auth = {"Authorization": "Bearer " + token}
    assert client.post(url, json=req(method)).status_code == 401
    assert client.post(url, json=req(method), headers={**auth, "Origin": "http://evil.example"}).status_code == 403
    assert client.post(url.replace("display-1", "foreign"), json=req(method), headers=auth).status_code == 404
    assert client.get(url, headers=auth).status_code == 405
    assert client.options(url, headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"}).status_code == 403
    result = client.options(url, headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
                                         "Access-Control-Request-Headers": "Authorization, Content-Type"})
    assert result.status_code == 204 and result.headers["Access-Control-Allow-Methods"] == "POST, OPTIONS"
    assert calls == []
    success = client.post(url, json=req(method), headers={**auth, "Origin": "http://localhost:3000"})
    assert success.status_code == 200 and success.json["data"]["method"] == method
    assert success.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
    assert calls == [(method, "display-1", req(method))]


@pytest.mark.parametrize("body", [b'{"text":"x","text":"y","display_graph_ids":["display-1"]}',
    b'{"text":"x","top_k":NaN,"display_graph_ids":["display-1"]}',
    json.dumps({**req(), "principal": "foreign"}).encode(), json.dumps({**req(), "top_k": True}).encode(),
    json.dumps({**req(), "display_graph_ids": ["simulation"]}).encode(),
    json.dumps({**req(), "display_graph_ids": ["display-1", "display-1"]}).encode(),
    json.dumps({**req(), "text": " "}).encode(), json.dumps({**req(), "recorded_before": "2026-01-01"}).encode(),
    b'{}' + b' ' * 16384], ids=["duplicate", "nonfinite", "principal", "bool", "missing-anchor", "duplicate-selection", "blank", "naive", "large"])
def test_invalid_payload_never_reaches_facade(evidence_app, body):
    client, calls, token, _ = evidence_app
    reply = client.post("/api/graph/research/display-1", data=body, content_type="application/json",
                        headers={"Authorization": "Bearer " + token})
    assert reply.status_code == 400 and reply.json == {"success": False, "error": {"code": "invalid_request"}}
    assert calls == []


def test_media_length_selectors_and_safe_errors(evidence_app):
    client, calls, token, facade = evidence_app
    headers = {"Authorization": "Bearer " + token}
    url = "/api/graph/research/display-1"
    assert client.post(url, data=json.dumps(req()), content_type="text/plain", headers=headers).status_code == 400
    assert client.post(url, headers=headers, environ_overrides={"CONTENT_LENGTH": ""}).status_code == 400
    assert client.post(url + "?principal=other", json=req(), headers=headers).status_code == 400
    assert calls == []
    for failure, status, code in [(KnowledgeBusy(), 409, "busy"),
            (KnowledgeReadError("timeout"), 503, "timeout"), (RuntimeError("private password"), 500, "internal_error")]:
        facade.failure = failure
        result = client.post(url, json=req(), headers=headers)
        assert result.status_code == status and result.json == {"success": False, "error": {"code": code}}


    _assert_two_hop_boundary(token)


def _assert_two_hop_boundary(token):
    headers = {"Authorization": "Bearer " + token}
    route = "/api/graph/two-hop/display-1"
    payload = {"schema_version": 2, "operation": "two_hop", "display_graph_id": "display-1",
               "source_node_id": str(uuid4()), "target_node_id": str(uuid4())}
    settings = SimpleNamespace(display_graph_id="display-1", scope=scope())
    wire_calls = []
    source, middle, target = payload["source_node_id"], str(uuid4()), payload["target_node_id"]
    source_text = "A猫B"
    source_bytes = source_text.encode("utf-8")
    source_hash = sha256(source_bytes).hexdigest()
    source_recorded_at = "2026-01-01T00:00:00Z"
    project_id, revision = settings.scope["project_id"], str(uuid4())

    def cited_edge(start_node, end_node, start, end):
        excerpt = source_text[start:end]
        evidence_id = str(uuid4())
        citation = {
            "evidence_id": evidence_id, "project_id": project_id, "source_revision": revision,
            "source_name": "retained fixture", "source_sha256": source_hash,
            "source_byte_length": len(source_bytes), "source_codepoint_length": len(source_text),
            "source_recorded_at": source_recorded_at, "start": start, "end": end,
            "offset_unit": "unicode_codepoint", "excerpt": excerpt,
            "excerpt_sha256": sha256(excerpt.encode("utf-8")).hexdigest(), "declared_page": None,
        }
        return {
            "provider_id": str(uuid4()), "name": "RELATES_TO", "fact": excerpt,
            "source_node_id": start_node, "target_node_id": end_node, "episode_ids": [str(uuid4())],
            "evidence_ids": [evidence_id], "valid_at": source_recorded_at, "invalid_at": None,
            "expired_at": None, "created_at": source_recorded_at, "citations": [citation],
        }

    data = {
        "schema_version": 2, "operation": "two_hop", "source_node_id": source,
        "target_node_id": target, "path_status": "found",
        "path": {"intermediate_node_id": middle,
                 "hops": [cited_edge(source, middle, 0, 2), cited_edge(middle, target, 2, 3)]},
        "scope_coverage": {"scope": settings.scope, "display_graph_id": "display-1", "pages": 1,
                           "scanned": 2, "eligible": 2, "excluded": 0, "unknown": 0,
                           "returned": 2, "truncated": False},
        "passage_coverage": [{"project_id": project_id, "source_revision": revision,
                              "source_sha256": source_hash, "retained_codepoints": 3,
                              "retrieved_codepoints": 3, "retrieved_passage_fraction": 1.0}],
        "historical": False, "historical_semantics": "retained_edges_not_bitemporal_reconstruction",
        "rank_basis": "directed_path_edge_id_order",
    }

    class ReplyClient:
        def call(self, raw):
            frame = json.loads(raw)
            wire_calls.append(frame)
            return json.dumps({"version": 1, "request_id": frame["request_id"], "ok": True,
                               "result": data}).encode()

    facade = KnowledgeEvidenceFacade(settings, client_factory=ReplyClient)
    caller = create_app(evidence_facade=facade).test_client()
    assert caller.post(route, json=payload).status_code == 401
    assert caller.post(route, json=payload, headers={**headers, "Origin": "http://evil.example"}).status_code == 403
    assert caller.post(route.replace("display-1", "foreign"), json=payload, headers=headers).status_code == 404
    assert caller.get(route, headers=headers).status_code == 405
    preflight = caller.options(route, headers={"Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Authorization, Content-Type"})
    assert preflight.status_code == 204 and preflight.headers["Access-Control-Allow-Methods"] == "POST, OPTIONS"
    for invalid in (dict(payload, schema_version=1), dict(payload, display_graph_id="foreign"),
                    dict(payload, scope=settings.scope), dict(payload, target_node_id=payload["source_node_id"])):
        assert caller.post(route, json=invalid, headers=headers).status_code == 400
    assert caller.post("/api/graph/research/display-1", json=payload, headers=headers).status_code == 400
    assert wire_calls == []

    allowed = caller.post(route, json=payload, headers={**headers, "Origin": "http://localhost:3000"})
    assert allowed.status_code == 200 and allowed.json == {"success": True, "data": data}
    assert allowed.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
    frame, = wire_calls
    assert frame == {"version": 1, "request_id": frame["request_id"], "method": "two_hop",
                     "scope": settings.scope, "payload": payload}
    correct = deepcopy(data)

    def assert_bad_reply():
        before = len(wire_calls)
        rejected = caller.post(route, json=payload, headers=headers)
        assert rejected.status_code == 502
        assert rejected.json == {"success": False, "error": {"code": "invalid_reply"}}
        assert len(wire_calls) == before + 1
        data.update(deepcopy(correct))

    for field in ("source_node_id", "target_node_id"):
        data[field] = str(uuid4())
        assert_bad_reply()
    for field, wrong in (("display_graph_id", "foreign"), ("scope", {}), ("returned", True)):
        data["scope_coverage"][field] = wrong
        assert_bad_reply()
    del data["scope_coverage"]["pages"]
    assert_bad_reply()
    # Keep the direction and all identifiers correct while corrupting citations.
    del data["path"]["hops"][0]["citations"]
    assert_bad_reply()
    data["path"]["hops"][1]["citations"][0]["excerpt_sha256"] = "0" * 64
    assert_bad_reply()
    data["path"]["hops"][1]["evidence_ids"] = [str(uuid4())]
    assert_bad_reply()
    data["passage_coverage"][0]["retrieved_passage_fraction"] = 1.1
    assert_bad_reply()


def test_graph_health_keeps_evidence_construction_lazy(read_app, monkeypatch):
    app, _, _ = read_app
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.endswith(("knowledge_evidence_client", "knowledge_evidence_facade")):
            raise AssertionError("eager evidence import")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    response = app.test_client().get("/health")
    assert response.status_code == 200
    assert {"evidence_research", "evidence_dossier"} <= set(response.json["capabilities"])


def test_fresh_cold_factory_evidence_health_and_invalid_routes_without_sdks(tmp_path):
    # PURE cold-factory fixture settings; actual test below uses trusted installed settings.
    script = r'''
import builtins,os,sys
sys.path.insert(0,os.environ['TEST_BACKEND'])
original=builtins.__import__
blocked=('nexaweave_knowledge','graphiti_core','openai','camel','oasis','temporalio','torch','transformers')
def imports(name,*args,**kwargs):
    if any(name==p or name.startswith(p+'.') for p in blocked):
        raise AssertionError('cold SDK import')
    return original(name,*args,**kwargs)
builtins.__import__=imports
from app.services.knowledge_read_facade import ReadHostSettings
settings=ReadHostSettings('unused','unused','0123456789abcdef'*4,'owner','anchor',{}, {})
ReadHostSettings.from_config=classmethod(lambda cls,config:settings)
from app import create_app
app=create_app()
assert 'app.services.knowledge_evidence_facade' not in sys.modules
assert 'app.services.knowledge_evidence_client' not in sys.modules
client=app.test_client()
assert 'evidence_dossier' in client.get('/health').json['capabilities']
assert client.post('/api/graph/dossier/anchor',json={}).status_code==401
assert client.post('/api/graph/research/anchor',json={},headers={'Authorization':'Bearer '+settings.token}).status_code==400
assert 'app.services.knowledge_evidence_facade' not in sys.modules
print('cold evidence healthy')
'''
    env = {key: value for key, value in os.environ.items() if not key.startswith(
        ("KNOWLEDGE_", "LLM_", "OPENAI_", "DEEPSEEK_", "ZEP_"))}
    env.update(TEST_BACKEND=str(Path(__file__).resolve().parents[1]), NEXAWEAVE_APP_MODE="graphiti_readonly",
        PYTHON_DOTENV_DISABLED="1", FLASK_HOST="127.0.0.1", FLASK_DEBUG="0", NEXAWEAVE_ALLOWED_ORIGINS="")
    result = subprocess.run([sys.executable, "-I", "-c", script], cwd=tmp_path, env=env,
                            capture_output=True, timeout=30)
    assert result.returncode == 0 and result.stdout.strip() == b"cold evidence healthy"

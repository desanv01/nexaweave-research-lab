"""Authenticated route and cold-factory qualification source (pure)."""
import builtins
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app import create_app
from app.services.knowledge_read_facade import ReadHostSettings
from app.services.knowledge_reader import KnowledgeReadError
from app.services.knowledge_transport import KnowledgeBusy
from test_knowledge_read_app import read_app


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
blocked=('mirofish_knowledge','graphiti_core','openai','camel','oasis','temporalio','torch','transformers')
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
    env.update(TEST_BACKEND=str(Path(__file__).resolve().parents[1]), MIROFISH_APP_MODE="graphiti_readonly",
        PYTHON_DOTENV_DISABLED="1", FLASK_HOST="127.0.0.1", FLASK_DEBUG="0", MIROFISH_ALLOWED_ORIGINS="")
    result = subprocess.run([sys.executable, "-I", "-c", script], cwd=tmp_path, env=env,
                            capture_output=True, timeout=30)
    assert result.returncode == 0 and result.stdout.strip() == b"cold evidence healthy"

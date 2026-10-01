"""Pure adversarial profile and owned subprocess test source; Main executes."""
import io
import json
import queue
import sys
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import knowledge_transport as transport
from app.services.knowledge_evidence_client import (KnowledgeEvidenceProcessClient,
    validate_payload, RESULT_LIMITS, ENVELOPE_OVERHEAD)
from app.services.knowledge_evidence_facade import KnowledgeEvidenceFacade
from app.services.knowledge_read_facade import _CHILD_KEYS
from test_knowledge_read_app import scope


def payload():
    return {"display_graph_ids": ["anchor", "simulation"], "text": "Alice 猫"}


def envelope(**updates):
    value = {"version": 1, "request_id": str(uuid4()), "method": "research",
             "scope": scope(), "payload": payload()}
    value.update(updates)
    return json.dumps(value, separators=(",", ":")).encode()


@pytest.fixture
def client(tmp_path):
    package = tmp_path / "site-packages" / "mirofish_knowledge"
    package.mkdir(parents=True)
    (package / "read_bootstrap.py").write_text("", encoding="utf-8")
    (package / "evidence_bootstrap.py").write_text("", encoding="utf-8")
    settings = SimpleNamespace(python=sys.executable, bootstrap=str(package / "read_bootstrap.py"),
        scope=scope(), display_graph_id="anchor", child_environment={key: "unused" for key in _CHILD_KEYS})
    return KnowledgeEvidenceProcessClient(settings)


@pytest.mark.parametrize("updates", [{"version": True}, {"method": "page"}, {"method": []},
    {"request_id": "private"}, {"scope": {**scope(), "schema_version": True}},
    {"scope": {**scope(), "layer": "analysis"}}, {"payload": {**payload(), "principal": "other"}},
    {"payload": {**payload(), "top_k": True}}, {"payload": {**payload(), "schema_version": True}},
    {"payload": {**payload(), "display_graph_ids": ["simulation"]}},
    {"payload": {**payload(), "recorded_before": "2026-01-01"}}], ids=lambda v: next(iter(v)))
def test_request_denials_before_spawn(client, monkeypatch, updates):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid request spawned child")
    monkeypatch.setattr(transport.subprocess, "Popen", forbidden)
    with pytest.raises(transport.KnowledgeInvalidRequest):
        client.call(envelope(**updates))


@pytest.mark.parametrize("raw", [b'{"version":1,"version":1}', b'{"x":NaN}',
    b'{"x":1e999}', b'{}' + b' ' * 40000, b'{"x":' + b'[' * 33 + b'0' + b']' * 33 + b'}'],
    ids=["duplicate-key", "nonfinite-constant", "overflow-number", "oversized-body", "excessive-depth"])
def test_strict_json_and_size(client, raw):
    with pytest.raises(transport.KnowledgeInvalidRequest):
        client._validate_request(raw)


def test_defaults_and_response_limit_hooks(client):
    raw = envelope()
    assert transport._METHODS == {"page", "entity", "search", "ingest"}
    assert "dossier_unavailable" not in transport._ERROR_CODES
    assert transport.KnowledgeProcessClient._response_limit(None, raw) == 2 * 1024 * 1024
    assert client._response_limit(raw) == RESULT_LIMITS["research"] + ENVELOPE_OVERHEAD
    events = queue.Queue()
    transport._read_response(io.BytesIO((transport._RESPONSE_MAX + 1).to_bytes(4, "big")), events)
    assert events.get()[0] == "reader_error"


@pytest.mark.parametrize("changes", [{"request_id": str(uuid4())}, {"version": True},
    {"ok": 1}, {"extra": "private"}, {"ok": False, "error": {"code": "private_database"}}])
def test_malformed_reply(client, changes):
    identity = str(uuid4())
    reply = {"version": 1, "request_id": identity, "ok": True, "result": {}}
    reply.update(changes)
    with pytest.raises(transport.KnowledgeTransportFailure):
        client._validate_reply(json.dumps(reply).encode(), identity)


@pytest.mark.parametrize("behavior", ["eof", "trailing", "wrong_id", "exit", "timeout", "oversize"])
def test_actual_owned_pipe_failure_cleanup(client, monkeypatch, behavior):
    # A synthetic child is appropriate for this PURE transport test only.
    script = '''import json,sys,time
n=int.from_bytes(sys.stdin.buffer.read(4),'big'); request=json.loads(sys.stdin.buffer.read(n))
BEHAVIOR
reply=json.dumps({'version':1,'request_id':request['request_id'],'ok':True,'result':{}}).encode()
sys.stdout.buffer.write(len(reply).to_bytes(4,'big')+reply);sys.stdout.buffer.flush()
TAIL
'''
    action = {"eof": "raise SystemExit(0)", "trailing": "pass", "wrong_id": "request['request_id']='wrong'",
        "exit": "pass", "timeout": "time.sleep(20)", "oversize": "sys.stdout.buffer.write((5000000).to_bytes(4,'big'));raise SystemExit(0)"}[behavior]
    tail = "sys.stdout.buffer.write(b'x')" if behavior == "trailing" else "raise SystemExit(7)" if behavior == "exit" else "pass"
    from pathlib import Path
    Path(client._script).write_text(script.replace("BEHAVIOR", action).replace("TAIL", tail), encoding="utf-8")
    client._timeout = 0.3 if behavior == "timeout" else 10
    owned = []
    original = transport.subprocess.Popen
    def observe(*args, **kwargs):
        child = original(*args, **kwargs)
        owned.append(child)
        assert "OPENAI_API_KEY" not in kwargs["env"] and "PYTHONPATH" not in kwargs["env"]
        assert kwargs["shell"] is False and args[0][1:3] == ["-I", "-u"]
        return child
    monkeypatch.setattr(transport.subprocess, "Popen", observe)
    with pytest.raises(transport.KnowledgeTransportFailure):
        client.call(envelope())
    assert len(owned) == 1 and owned[0].poll() is not None
    assert owned[0].stdin.closed and owned[0].stdout.closed
    assert client._lock.acquire(blocking=False)
    client._lock.release()


def test_shared_admission_no_unbounded_wait_and_release():
    entered = threading.Barrier(3)
    release = threading.Event()
    calls = []
    class Blocking:
        def call(self, raw):
            value = json.loads(raw)
            calls.append(value)
            entered.wait(timeout=5)
            assert release.wait(5)
            return json.dumps({"version": 1, "request_id": value["request_id"], "ok": False,
                               "error": {"code": "research_unavailable"}}).encode()
    settings = SimpleNamespace(display_graph_id="anchor", scope=scope())
    facade = KnowledgeEvidenceFacade(settings, client_factory=Blocking)
    failures = []
    def run():
        try:
            facade.execute("research", "anchor", payload())
        except Exception as exc:
            failures.append(exc)
    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    try:
        entered.wait(timeout=5)
        with pytest.raises(transport.KnowledgeBusy):
            facade.execute("research", "anchor", payload())
        assert len(calls) == 2
    finally:
        release.set()
        for thread in threads:
            thread.join(5)
    assert all(not t.is_alive() for t in threads)
    assert len(failures) == 2 and all(e.code == "evidence_unavailable" for e in failures)
    assert facade._admission.acquire(False) and facade._admission.acquire(False)
    facade._admission.release(); facade._admission.release()

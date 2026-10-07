"""Pure frames and real synthetic child pipes; no provider/model/network calls."""

import asyncio
import importlib.util
import io
import json
import os
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from nexaweave_knowledge.stdio import serve_once
from nexaweave_knowledge import commands


BACKEND_CLIENT = Path(__file__).resolve().parents[3] / "backend" / "app" / "services" / "knowledge_transport.py"
KNOWLEDGE_SOURCE = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture
def transport():
    spec = importlib.util.spec_from_file_location("standalone_knowledge_transport", BACKEND_CLIENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_standalone_transport_loads_shared_stdlib_helper(transport):
    assert Path(transport._owned_process.__file__).resolve() == (
        BACKEND_CLIENT.parent.parent / "utils" / "owned_process.py").resolve()
    assert transport.OwnedProcess is transport._owned_process.OwnedProcess


def request(method="page", *, request_id=None, payload=None):
    return json.dumps({
        "version": 1, "request_id": request_id or str(uuid4()), "method": method,
        "scope": {"schema_version": 1, "workspace_id": str(uuid4()), "project_id": str(uuid4()),
                  "graph_id": str(uuid4()), "run_id": None, "branch_id": None, "layer": "source"},
        "payload": payload if payload is not None else {"kind": "node", "limit": 1},
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def frame(body):
    return len(body).to_bytes(4, "big") + body


def deep_frame():
    nested = 1
    for _ in range(34):
        nested = [nested]
    return frame(json.dumps({"deep": nested}).encode())


def nested_request(levels):
    root = json.loads(request())
    value = 0
    for _ in range(levels - 2):
        value = [value]
    root["payload"] = {"deep": value}
    return json.dumps(root, separators=(",", ":")).encode()


def nested_reply(request_id, levels):
    value = 0
    for _ in range(levels - 2):
        value = [value]
    return json.dumps({"version": 1, "request_id": request_id, "ok": True,
                       "result": {"deep": value}}, separators=(",", ":")).encode()


def unframe(raw):
    assert len(raw) >= 4
    length = int.from_bytes(raw[:4], "big")
    assert len(raw) == 4 + length
    return json.loads(raw[4:].decode("utf-8"))


def child_script(tmp_path, body, name="fixture.py"):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path.resolve()


def echo_script(tmp_path):
    return child_script(tmp_path, '''
import json, sys
incoming = sys.stdin.buffer.read()
request = json.loads(incoming[4:])
body = json.dumps({"version": 1, "request_id": request["request_id"],
                   "ok": True, "result": {}}).encode()
sys.stdout.buffer.write(len(body).to_bytes(4, "big") + body)
sys.stdout.buffer.flush()
''')


class Dispatcher:
    def __init__(self):
        self.calls = []

    async def dispatch(self, raw, *, principal):
        self.calls.append((raw, principal))
        request_id = json.loads(raw)["request_id"]
        return json.dumps({"version": 1, "request_id": request_id, "ok": True,
                           "result": {"value": "研究"}}, ensure_ascii=False).encode()


@pytest.mark.asyncio
async def test_stdio_one_frame_and_host_principal():
    subject = Dispatcher()
    body = request()
    output = io.BytesIO()
    status = await serve_once(subject, principal="trusted-host-user",
                              input_stream=io.BytesIO(frame(body)), output_stream=output)
    assert status == "replied"
    assert subject.calls == [(body, "trusted-host-user")]
    assert unframe(output.getvalue())["result"] == {"value": "研究"}


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [
    b"", b"\x00\x00", (0).to_bytes(4, "big"), (512 * 1024 + 1).to_bytes(4, "big"),
    (10).to_bytes(4, "big") + b"{}", frame(b"{}") + b"x", frame(b"\xff"),
    frame(b"[]"), frame(b'{"x":1,"x":2}'), frame(b'{"x":NaN}'), frame(b'{"x":1e999}'),
    deep_frame(),
])
async def test_stdio_malformed_frame_safe_reply(invalid):
    subject = Dispatcher()
    output = io.BytesIO()
    status = await serve_once(subject, principal="trusted", input_stream=io.BytesIO(invalid),
                              output_stream=output)
    assert status == "invalid_request"
    assert unframe(output.getvalue()) == {"version": 1, "request_id": None, "ok": False,
                                           "error": {"code": "invalid_request"}}
    assert not subject.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_stdio_dispatch_failure_never_invents_reply(failure):
    class Failing:
        async def dispatch(self, raw, *, principal):
            raise failure("private provider detail")

    output = io.BytesIO()
    with pytest.raises(failure):
        await serve_once(Failing(), principal="trusted", input_stream=io.BytesIO(frame(request())),
                         output_stream=output)
    assert output.getvalue() == b""


@pytest.mark.asyncio
async def test_stdio_exact_request_limit_and_depth_edge():
    subject = Dispatcher()
    ordinary = request()
    exact = ordinary + b" " * (512 * 1024 - len(ordinary))
    assert len(exact) == 512 * 1024
    for body in (exact, nested_request(32)):
        output = io.BytesIO()
        assert await serve_once(subject, principal="trusted", input_stream=io.BytesIO(frame(body)),
                                output_stream=output) == "replied"
        assert unframe(output.getvalue())["ok"] is True
    output = io.BytesIO()
    assert await serve_once(subject, principal="trusted", input_stream=io.BytesIO(frame(nested_request(33))),
                            output_stream=output) == "invalid_request"
    assert unframe(output.getvalue())["error"] == {"code": "invalid_request"}


def test_client_json_depth_and_dispatcher_error_code_contract(transport):
    valid = nested_request(32)
    identifier = transport._request_id(valid)
    with pytest.raises(transport.KnowledgeInvalidRequest):
        transport._request_id(nested_request(33))
    transport._reply(nested_reply(identifier, 32), identifier)
    with pytest.raises(transport.KnowledgeTransportFailure):
        transport._reply(nested_reply(identifier, 33), identifier)
    assert transport._ERROR_CODES == commands._ERROR_CODES
    for code in commands._ERROR_CODES:
        body = json.dumps({"version": 1, "request_id": identifier, "ok": False,
                           "error": {"code": code}}).encode()
        transport._reply(body, identifier)
    with pytest.raises(transport.KnowledgeTransportFailure):
        transport._reply(json.dumps({"version": 1, "request_id": identifier, "ok": False,
                                     "error": {"code": "not_in_contract"}}).encode(), identifier)


def bootstrap_script(tmp_path, *, principal="reader", allow_model_calls=False):
    return child_script(tmp_path, f'''
import asyncio
import os
import sys
from uuid import uuid4
sys.path.insert(0, {str(KNOWLEDGE_SOURCE)!r})
from nexaweave_knowledge.commands import KnowledgeCommandDispatcher
from nexaweave_knowledge.contracts import FactResult, GraphPage, SearchResult
from nexaweave_knowledge.stdio import serve_once

class Provider:
    async def page(self, scope, request):
        fact = FactResult(provider_id=str(uuid4()), scope=scope, kind="node", name="研究 市场",
                          episode_ids=(str(uuid4()),), attributes={{
                              "ambient": os.getenv("KNOWLEDGE_PARENT_SENTINEL"),
                              "explicit": os.getenv("KNOWLEDGE_ALLOWED_SENTINEL"),
                              "cwd": os.getcwd(), "home": os.getenv("HOME")}})
        return GraphPage(facts=(fact,))
    async def entity(self, scope, provider_id):
        return SearchResult(facts=())
    async def search(self, scope, query):
        return SearchResult(facts=())
    async def ingest(self, scope, source, ontology):
        raise AssertionError("direct provider ingest forbidden")

class Coordinator:
    async def ingest(self, scope, source, ontology):
        raise AssertionError("synthetic fixture has no model calls")

async def authorize(principal, method, scope):
    return principal == "reader"

dispatcher = KnowledgeCommandDispatcher(Provider(), Coordinator(), authorize,
                                        allow_model_calls={allow_model_calls!r})
asyncio.run(serve_once(dispatcher, principal={principal!r},
                       input_stream=sys.stdin.buffer, output_stream=sys.stdout.buffer))
''')


def test_real_child_unicode_environment_and_temp_cleanup(tmp_path, monkeypatch, transport):
    monkeypatch.setenv("KNOWLEDGE_PARENT_SENTINEL", "ambient-private-sentinel")
    script = bootstrap_script(tmp_path)
    client = transport.KnowledgeProcessClient(sys.executable, script,
        timeout_seconds=10, child_environment={"KNOWLEDGE_ALLOWED_SENTINEL": "explicit-fixture"})
    sent = request()
    result = json.loads(client.call(sent).decode("utf-8"))
    assert result["ok"] is True and result["request_id"] == json.loads(sent)["request_id"]
    fact = result["result"]["facts"][0]
    assert fact["name"] == "研究 市场"
    assert fact["attributes"]["ambient"] is None
    assert fact["attributes"]["explicit"] == "explicit-fixture"
    assert fact["attributes"]["home"] == fact["attributes"]["cwd"]
    assert not Path(fact["attributes"]["cwd"]).exists()


def test_real_child_denied_and_default_model_gate(tmp_path, transport):
    denied = transport.KnowledgeProcessClient(sys.executable,
        bootstrap_script(tmp_path, principal="denied"), timeout_seconds=10)
    assert json.loads(denied.call(request()))["error"] == {"code": "unauthorized"}
    enabled_policy = transport.KnowledgeProcessClient(sys.executable,
        bootstrap_script(tmp_path, principal="reader", allow_model_calls=False), timeout_seconds=10)
    assert json.loads(enabled_policy.call(request("search", payload={"text": "研究"})))["error"] == {
        "code": "model_calls_disabled"}


def test_exact_response_length_success(tmp_path, transport):
    script = child_script(tmp_path, '''
import json, sys
incoming = sys.stdin.buffer.read()
identifier = json.loads(incoming[4:])["request_id"]
reply = {"version": 1, "request_id": identifier, "ok": True, "result": {"blob": ""}}
base = json.dumps(reply, separators=(",", ":")).encode()
reply["result"]["blob"] = "x" * (2 * 1024 * 1024 - len(base))
body = json.dumps(reply, separators=(",", ":")).encode()
assert len(body) == 2 * 1024 * 1024
sys.stdout.buffer.write(len(body).to_bytes(4, "big") + body)
sys.stdout.buffer.flush()
''')
    client = transport.KnowledgeProcessClient(sys.executable, script, timeout_seconds=10)
    ordinary = request()
    exact_request = ordinary + b" " * (512 * 1024 - len(ordinary))
    assert len(exact_request) == 512 * 1024
    result = client.call(exact_request)
    assert len(result) == 2 * 1024 * 1024


def test_popen_failure_before_spawn_is_known_no_effect(tmp_path, transport, monkeypatch):
    client = transport.KnowledgeProcessClient(sys.executable, echo_script(tmp_path))

    def failed_spawn(*args, **kwargs):
        raise OSError("private executable path")

    monkeypatch.setattr(transport.subprocess, "Popen", failed_spawn)
    with pytest.raises(transport.KnowledgeTransportFailure) as caught:
        client.call(request())
    assert caught.value.code == "transport_failure" and not caught.value.outcome_unknown
    assert "private" not in str(caught.value) and not client._lock.locked()


def test_partial_thread_startup_cleans_child_threads_temp_and_reuses_lock(tmp_path, transport, monkeypatch):
    client = transport.KnowledgeProcessClient(sys.executable, echo_script(tmp_path), timeout_seconds=5)
    original_popen = transport.subprocess.Popen
    original_start = transport.threading.Thread.start
    owned = []

    def capture_spawn(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        owned.append((process, Path(kwargs["cwd"])))
        return process

    def fail_reader_start(thread):
        if thread.name == "nexaweave-knowledge-reader":
            raise RuntimeError("private startup detail")
        return original_start(thread)

    monkeypatch.setattr(transport.subprocess, "Popen", capture_spawn)
    monkeypatch.setattr(transport.threading.Thread, "start", fail_reader_start)
    with pytest.raises(transport.KnowledgeTransportFailure) as caught:
        client.call(request())
    assert caught.value.outcome_unknown and "private" not in str(caught.value)
    assert len(owned) == 1 and owned[0][0].poll() is not None and not owned[0][1].exists()
    assert not any(thread.name.startswith("nexaweave-knowledge-") for thread in threading.enumerate())
    assert not client._lock.locked()
    monkeypatch.setattr(transport.threading.Thread, "start", original_start)
    assert json.loads(client.call(request()))["ok"] is True


def test_temp_cleanup_failure_maps_fixed_error_without_path(tmp_path, transport, monkeypatch):
    client = transport.KnowledgeProcessClient(sys.executable, echo_script(tmp_path), timeout_seconds=5)
    original_temp = transport.tempfile.TemporaryDirectory
    created = []

    class CleanupFailure:
        def __init__(self, *args, **kwargs):
            self.actual = original_temp(*args, **kwargs)
            self.name = self.actual.name
            created.append(Path(self.name))

        def cleanup(self):
            self.actual.cleanup()
            raise OSError("private temp path " + self.name)

    monkeypatch.setattr(transport.tempfile, "TemporaryDirectory", CleanupFailure)
    with pytest.raises(transport.KnowledgeTransportFailure) as caught:
        client.call(request())
    assert caught.value.outcome_unknown and "private" not in str(caught.value)
    assert len(created) == 1 and not created[0].exists() and not client._lock.locked()


def test_request_and_host_configuration_rejection_pre_spawn(tmp_path, transport, monkeypatch):
    script = child_script(tmp_path, "pass")
    for kwargs in ({"timeout_seconds": 0}, {"timeout_seconds": True},
                   {"timeout_seconds": float("nan")}, {"timeout_seconds": 301},
                   {"child_environment": {"OPENAI_KEY": "private"}},
                   {"child_environment": {"KNOWLEDGE_BAD": "x\x00y"}},
                   {"child_environment": {"KNOWLEDGE_BIG": "x" * 65536}}):
        with pytest.raises(ValueError):
            transport.KnowledgeProcessClient(sys.executable, script, **kwargs)
    with pytest.raises(ValueError):
        transport.KnowledgeProcessClient(tmp_path / "missing-python", script)
    with pytest.raises(ValueError):
        transport.KnowledgeProcessClient(sys.executable, tmp_path / "missing.py")
    with pytest.raises(ValueError):
        transport.KnowledgeProcessClient(sys.executable, child_script(tmp_path, "pass", "bad.txt"))
    linked = tmp_path / "linked.py"
    try:
        linked.symlink_to(script)
    except (OSError, NotImplementedError):
        pass
    else:
        with pytest.raises(ValueError):
            transport.KnowledgeProcessClient(sys.executable, linked)

    client = transport.KnowledgeProcessClient(sys.executable, script)
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("spawned")))
    for raw in (b"\xff", b"[]", b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', request().replace(b'"version":1', b'"version":true'),
                request().replace(b'"method":"page"', b'"method":"unknown"'),
                b"{" + b" " * (512 * 1024)):
        with pytest.raises(transport.KnowledgeInvalidRequest) as caught:
            client.call(raw)
        assert caught.value.code == "invalid_request" and not caught.value.outcome_unknown


@pytest.mark.skipif(os.name == "nt", reason="POSIX virtualenv interpreter links")
def test_posix_interpreter_link_retains_original_path(tmp_path, transport):
    linked = tmp_path / "venv-python"
    try:
        linked.symlink_to(sys.executable)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    client = transport.KnowledgeProcessClient(linked, child_script(tmp_path, "pass"))
    assert client._python == str(linked)


def raw_reply_child(tmp_path, body_expr, *, exit_code=0, extra=b""):
    return child_script(tmp_path, f'''
import json
import sys
incoming = sys.stdin.buffer.read()
request = json.loads(incoming[4:])
body = {body_expr}
sys.stdout.buffer.write(len(body).to_bytes(4, "big") + body + {extra!r})
sys.stdout.buffer.flush()
sys.exit({exit_code})
''')


@pytest.mark.parametrize("body_expr,exit_code,extra", [
    ("b'{}'", 0, b""),
    ("json.dumps({'version': 1, 'request_id': str(__import__('uuid').uuid4()), 'ok': True, 'result': {}}).encode()", 0, b""),
    ("json.dumps({'version': 1, 'request_id': request['request_id'], 'ok': 1, 'result': {}}).encode()", 0, b""),
    ("json.dumps({'version': 1, 'request_id': request['request_id'], 'ok': False, 'error': {'code': 'private'}}).encode()", 0, b""),
    ("json.dumps({'version': 1, 'request_id': request['request_id'], 'ok': True, 'result': {'x': float('nan')}}).encode()", 0, b""),
    ("('{\"version\":1,\"request_id\":\"%s\",\"ok\":true,\"result\":{\"x\":1,\"x\":2}}' % request['request_id']).encode()", 0, b""),
    ("json.dumps({'version': 1, 'request_id': request['request_id'], 'ok': True, 'result': {}}).encode()", 3, b""),
    ("json.dumps({'version': 1, 'request_id': request['request_id'], 'ok': True, 'result': {}}).encode()", 0, b"x"),
])
def test_bad_child_reply_unknown_outcome(tmp_path, transport, body_expr, exit_code, extra):
    script = raw_reply_child(tmp_path, body_expr, exit_code=exit_code, extra=extra)
    client = transport.KnowledgeProcessClient(sys.executable, script, timeout_seconds=5)
    with pytest.raises(transport.KnowledgeTransportFailure) as caught:
        client.call(request())
    assert caught.value.code == "transport_failure" and caught.value.outcome_unknown
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("body", [
    "import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(b'\\x00\\x00')",
    "import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write((2097153).to_bytes(4, 'big'))",
    "import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write((10).to_bytes(4, 'big') + b'{}')",
    "import time; time.sleep(10)",
])
def test_partial_oversize_or_hung_child_unknown_outcome(tmp_path, transport, body):
    script = child_script(tmp_path, body)
    client = transport.KnowledgeProcessClient(sys.executable, script, timeout_seconds=0.2)
    with pytest.raises(transport.KnowledgeTransportFailure) as caught:
        client.call(request())
    assert caught.value.outcome_unknown


def test_blocked_stdin_writer_is_bounded_and_owned_threads_stop(tmp_path, transport):
    script = child_script(tmp_path, "import time; time.sleep(10)")
    client = transport.KnowledgeProcessClient(sys.executable, script, timeout_seconds=0.2)
    large = json.loads(request())
    large["payload"] = {"blob": "x" * 450000}
    raw = json.dumps(large).encode()
    with pytest.raises(transport.KnowledgeTransportFailure) as caught:
        client.call(raw)
    assert caught.value.outcome_unknown
    assert not client._lock.locked()
    deadline = time.monotonic() + 2
    while any(thread.name.startswith("nexaweave-knowledge-") for thread in threading.enumerate()) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not any(thread.name.startswith("nexaweave-knowledge-") for thread in threading.enumerate())


def test_lock_busy_and_restoration(tmp_path, transport):
    script = child_script(tmp_path, '''
import json, sys, time
raw = sys.stdin.buffer.read()
request = json.loads(raw[4:])
time.sleep(0.3)
body = json.dumps({"version": 1, "request_id": request["request_id"], "ok": True, "result": {}}).encode()
sys.stdout.buffer.write(len(body).to_bytes(4, "big") + body)
''')
    client = transport.KnowledgeProcessClient(sys.executable, script, timeout_seconds=3)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(client.call, request())
        deadline = time.monotonic() + 2
        while not client._lock.locked() and time.monotonic() < deadline:
            time.sleep(0.005)
        assert client._lock.locked()
        with pytest.raises(transport.KnowledgeBusy) as caught:
            client.call(request())
        assert not caught.value.outcome_unknown
        assert json.loads(first.result(timeout=3))["ok"] is True
    assert json.loads(client.call(request()))["ok"] is True


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_caller_interrupt_releases_owned_child_and_lock(tmp_path, transport, monkeypatch, interrupt,
                                                       cleanup_failure):
    script = child_script(tmp_path, "import time; time.sleep(10)")
    client = transport.KnowledgeProcessClient(sys.executable, script, timeout_seconds=3)
    original_get = queue.Queue.get
    original_popen = transport.subprocess.Popen
    owned = []

    def capture_spawn(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        owned.append((process, Path(kwargs["cwd"])))
        return process

    monkeypatch.setattr(transport.subprocess, "Popen", capture_spawn)
    if cleanup_failure:
        original_temp = transport.tempfile.TemporaryDirectory

        class CleanupFailure:
            def __init__(self, *args, **kwargs):
                self.actual = original_temp(*args, **kwargs)
                self.name = self.actual.name

            def cleanup(self):
                self.actual.cleanup()
                raise OSError("private temp path " + self.name)

        monkeypatch.setattr(transport.tempfile, "TemporaryDirectory", CleanupFailure)

    def interrupt_get(self, *args, **kwargs):
        raise interrupt

    monkeypatch.setattr(queue.Queue, "get", interrupt_get)
    with pytest.raises(interrupt):
        client.call(request())
    monkeypatch.setattr(queue.Queue, "get", original_get)
    assert not client._lock.locked()
    assert len(owned) == 1 and owned[0][0].poll() is not None and not owned[0][1].exists()
    assert not any(thread.name.startswith("nexaweave-knowledge-") for thread in threading.enumerate())

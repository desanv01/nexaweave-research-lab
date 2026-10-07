"""Actual guarded PostgreSQL and fresh installed-module cases; Main executes."""
import hashlib
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from nexaweave_storage import NotFound, ProjectStore, SourceStore
from nexaweave_storage.research_bundle import canonical, decode, export_bundle, inspect_bundle
from nexaweave_storage.research_bundle_cli import main
from test_project_store import snapshot
from test_source_store_postgres import factory

pytestmark = pytest.mark.postgres


def owned(factory):
    project, workspace, selected, omitted, evidence = (uuid4() for _ in range(5))
    store = ProjectStore(factory)
    text = "A😀猫\r\ne\u0301"
    reference = {"evidence_id": str(evidence), "source_revision": str(selected),
                 "sha256": hashlib.sha256(text.encode()).hexdigest(),
                 "object_key": "inert/owned-synthetic.bin", "byte_length": len(text.encode())}
    first = store.create("bundle-owner", workspace, project, "proj_1", snapshot(), [reference])
    retained = SourceStore(factory).ingest_text("bundle-owner", project, selected, "owned synthetic",
        text, [{"evidence_id": str(evidence), "start": 1, "end": 5, "page": 7}])
    SourceStore(factory).ingest_text("bundle-owner", project, omitted, "unrequested", "OMITTED")
    changed = snapshot()
    changed["name"] = "later revision"
    store.update("bundle-owner", project, 1, changed)
    return project, selected, omitted, first, retained


def request(project, selected, output, principal="bundle-owner"):
    return {"operation": "export", "principal": principal, "project_id": str(project),
            "revision": 1, "source_revisions": [str(selected)], "output": str(output)}


def test_owned_immutable_selection_and_connections_closed(factory):
    project, selected, omitted, first, retained = owned(factory)
    connections = []
    def tracked():
        conn = factory()
        connections.append(conn)
        return conn
    raw = export_bundle(ProjectStore(tracked), SourceStore(tracked), "bundle-owner", str(project), 1, [str(selected)])
    assert len(connections) == 2 and all(conn.closed for conn in connections)
    data = decode(raw)["payload"]
    assert data["project"]["revision"] == first.revision == 1
    assert data["project"]["snapshot"] == first.snapshot
    assert data["project"]["digest"] == first.digest
    assert data["project"]["evidence"] == list(first.evidence)
    assert data["project"]["evidence"][0]["evidence_id"] == str(retained.passages[0].evidence_id)
    assert inspect_bundle(raw)["original_binaries_included"] is False
    assert [s["source_revision"] for s in data["sources"]] == [str(selected)]
    source = data["sources"][0]
    assert source["text"] == retained.text
    assert source["recorded_at"] == retained.recorded_at.isoformat()
    assert source["passages"][0]["excerpt"] == retained.passages[0].excerpt
    assert source["passages"][0]["page"] == 7
    assert str(omitted) not in raw.decode()
    assert raw == export_bundle(ProjectStore(factory), SourceStore(factory), "bundle-owner", str(project), 1, [str(selected)])
    with pytest.raises(NotFound):
        export_bundle(ProjectStore(tracked), SourceStore(tracked), "other-owner", str(project), 1, [str(selected)])
    assert all(conn.closed for conn in connections)


def test_same_owner_foreign_project_source_denial_closes_connections_and_creates_no_output(
        factory, tmp_path, monkeypatch):
    project, _, _, _, _ = owned(factory)
    foreign_project, foreign_source, _, _, _ = owned(factory)
    # Both persisted projects have the same principal. Source membership, rather
    # than just principal matching, must deny the foreign selected revision.
    assert ProjectStore(factory).get("bundle-owner", foreign_project).principal == "bundle-owner"
    connections = []
    def tracked():
        connection = factory()
        connections.append(connection)
        return connection
    import nexaweave_storage.research_bundle_cli as cli_module
    monkeypatch.setattr(cli_module, "_stores", lambda: (ProjectStore(tracked), SourceStore(tracked)))
    output = tmp_path / "foreign-denied.json"
    out = StringIO()
    assert main(stdin=BytesIO(canonical(request(project, foreign_source, output))), stdout=out) == 2
    assert json.loads(out.getvalue()) == {"ok": False, "error": "bundle_denied"}
    assert not output.exists()
    assert len(connections) == 2 and all(connection.closed for connection in connections)


def test_denial_creates_no_output_and_ambient_override_denied(factory, tmp_path, monkeypatch):
    project, selected, _, _, _ = owned(factory)
    monkeypatch.setenv("NEXAWEAVE_APPSTORE_DSN", os.environ["PROJECT_STORE_POSTGRES_TEST_DSN"])
    for key in list(os.environ):
        if key.upper().startswith("PG"):
            monkeypatch.delenv(key)
    output = tmp_path / "must-not-exist.json"
    out = StringIO()
    assert main(stdin=BytesIO(canonical(request(project, selected, output, "other-owner"))), stdout=out) == 2
    assert json.loads(out.getvalue()) == {"ok": False, "error": "bundle_denied"}
    assert not output.exists()
    out = StringIO()
    monkeypatch.setenv("PGHOST", "untrusted.example")
    assert main(stdin=BytesIO(canonical(request(project, selected, output))), stdout=out) == 2
    assert json.loads(out.getvalue()) == {"ok": False, "error": "authority_unavailable"}
    assert not output.exists()


_CHILD = '''
import importlib.abc
import os
import runpy
import socket
import sys
blocked = ('app', 'flask', 'dotenv', 'openai', 'camel', 'oasis', 'graphiti_core', 'nexaweave_knowledge')
class NoProviders(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            raise AssertionError('unexpected provider import')
sys.meta_path.insert(0, NoProviders())
original_connect = socket.socket.connect
def guarded(self, address):
    if INSPECT or not isinstance(address, tuple) or address[0] != '127.0.0.1' or address[1] != 15432:
        raise AssertionError('unexpected socket')
    return original_connect(self, address)
socket.socket.connect = guarded
connection_attempts = []
if INSPECT:
    import psycopg
    import psycopg.pq as pq
    def deny_connection(*args, **kwargs):
        connection_attempts.append(True)
        raise AssertionError('inspect attempted database connection construction')
    psycopg.connect = deny_connection
    psycopg.Connection.connect = classmethod(deny_connection)
    psycopg.AsyncConnection.connect = classmethod(deny_connection)
    psycopg.Connection.__init__ = deny_connection
    psycopg.AsyncConnection.__init__ = deny_connection
    # Cover the native libpq entry points used by psycopg connection generators,
    # which bypass Python socket.connect. Wrapping also denies direct construction.
    class DeniedPGconn:
        def __new__(cls, *args, **kwargs):
            return deny_connection(*args, **kwargs)
        connect = staticmethod(deny_connection)
        connect_start = staticmethod(deny_connection)
    pq.PGconn = DeniedPGconn
closed = []
def profile(frame, event, arg):
    if event == 'call' and frame.f_globals.get('__name__') == 'psycopg.connection' and frame.f_code.co_name == 'close':
        closed.append(True)
sys.setprofile(profile)
status = 97
try:
    try:
        runpy.run_module('nexaweave_storage.research_bundle_cli', run_name='__main__')
    except SystemExit as error:
        status = error.code
finally:
    sys.setprofile(None)
if INSPECT and connection_attempts:
    status = 93
elif INSPECT and closed:
    status = 91
elif not INSPECT and status == 0 and len(closed) < 2:
    status = 92
raise SystemExit(status)
'''


def test_fresh_installed_cli_export_inspect_digest_tamper_and_no_fallback(factory, tmp_path):
    project, selected, _, _, retained = owned(factory)
    # Isolated interpreter resolves the installed storage package, without an
    # implementation source sys.path injection. Main must install this candidate.
    env = dict(os.environ)
    for key in list(env):
        upper = key.upper()
        if (upper.startswith(("PG", "KNOWLEDGE_")) or "PROXY" in upper
                or upper.endswith(("API_KEY", "TOKEN", "SECRET"))
                or upper in {"NEXAWEAVE_APPSTORE_DSN", "PROJECT_STORE_POSTGRES_TEST_DSN", "PYTHONPATH",
                             "LLM_BASE_URL", "OPENAI_BASE_URL", "DEEPSEEK_BASE_URL"}):
            env.pop(key, None)
    export_env = dict(env, NEXAWEAVE_APPSTORE_DSN=os.environ["PROJECT_STORE_POSTGRES_TEST_DSN"])
    def run(data, inspect=False):
        wrapper = tmp_path / ("inspect.py" if inspect else "export.py")
        wrapper.write_text(f"INSPECT = {inspect!r}\n" + _CHILD, encoding="utf-8")
        return subprocess.run([sys.executable, "-I", "-u", str(wrapper)],
            input=canonical(data), capture_output=True, env=env if inspect else export_env,
            timeout=30, check=False)
    output = tmp_path / "portable.json"
    completed = run(request(project, selected, output))
    assert completed.returncode == 0 and completed.stderr == b""
    summary = json.loads(completed.stdout)["result"]
    raw = output.read_bytes()
    assert summary["artifact_sha256"] == hashlib.sha256(raw).hexdigest()
    assert decode(raw)["payload"]["sources"][0]["text"] == retained.text
    inspection = {"operation": "inspect", "input": str(output), "expected_sha256": summary["artifact_sha256"]}
    checked = run(inspection, inspect=True)
    assert checked.returncode == 0 and checked.stderr == b""
    assert json.loads(checked.stdout)["result"] == summary
    denied_path = tmp_path / "denied.json"
    denied = run(request(project, selected, denied_path, "other-owner"))
    assert denied.returncode == 2 and not denied_path.exists()
    assert json.loads(denied.stdout) == {"ok": False, "error": "bundle_denied"}
    repeated = run(request(project, selected, output))
    assert repeated.returncode == 2 and output.read_bytes() == raw
    output.write_bytes(raw + b" ")
    denied = run(inspection, inspect=True)
    assert denied.returncode == 2
    assert json.loads(denied.stdout) == {"ok": False, "error": "invalid_bundle"}
    inspection.pop("expected_sha256")
    corrupted = decode(raw)
    corrupted["payload"]["sources"][0]["text"] = "TAMPERED"
    output.write_bytes(canonical(corrupted))
    assert run(inspection, inspect=True).returncode == 2

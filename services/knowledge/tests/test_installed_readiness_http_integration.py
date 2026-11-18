"""Fresh installed canonical migrations and a protected source list across restart."""

import hashlib
import http.client
import json
import os
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from nexaweave_knowledge.bindings import ScopeBindingStore
from nexaweave_knowledge.contracts import KnowledgeScope, Layer
from nexaweave_storage import ProjectStore, SourceStore
from nexaweave_storage.validation import canonical_payload
from test_project_store import snapshot
from tools.run_installed_readiness_tests import (
    MIGRATIONS, PG_DATABASE, PG_HOST, PG_PORT, PG_USER, ROOT, owned_process,
)
from tools.run_unit_tests import _unit_environment


pytestmark = pytest.mark.postgres

# Startup, process observation and stdin shutdown follow the accepted PR139
# source HTTP fixture. This case needs no dependency on that test module.
_HTTP_CHILD = r'''
import builtins
import json
import os
from pathlib import Path
from contextlib import redirect_stdout
import runpy
import subprocess
import sys
import threading

root = Path(os.environ.pop("SOURCE_HTTP_TEST_ROOT"))
sys.path[:0] = [str(root / "backend"), str(root)]
from tools.run_unit_tests import LoopbackOnlySockets

guard = LoopbackOnlySockets()
guard.install()
original = builtins.__import__
blocked = ("nexaweave_knowledge", "graphiti_core", "openai", "neo4j", "camel",
           "oasis", "torch", "transformers", "temporalio", "pymupdf", "fitz")


def imports(name, *args, **kwargs):
    if any(name == item or name.startswith(item + ".") for item in blocked):
        raise AssertionError("metadata backend imported a provider or runtime")
    return original(name, *args, **kwargs)


builtins.__import__ = imports
lock = threading.Lock()
events = sys.stdout


def emit(event):
    with lock:
        print(json.dumps(event, separators=(",", ":")), file=events, flush=True)


python = Path(os.environ["KNOWLEDGE_PYTHON"])
source_bootstrap = Path(os.environ["KNOWLEDGE_BOOTSTRAP_SCRIPT"]).with_name("source_bootstrap.py")
allowed = {
    "HOME", "TMPDIR", "TMP", "TEMP", "GRAPHITI_TELEMETRY_ENABLED", "PYTHONNOUSERSITE",
    "PATH", "LANG", "SystemRoot", "WINDIR", "USERPROFILE", "KNOWLEDGE_PRINCIPAL",
    "KNOWLEDGE_DISPLAY_GRAPH_ID", "KNOWLEDGE_BOUND_SCOPE_JSON", "KNOWLEDGE_PG_HOST",
    "KNOWLEDGE_PG_PORT", "KNOWLEDGE_PG_DATABASE", "KNOWLEDGE_PG_USER", "KNOWLEDGE_PG_PASSWORD",
}


def observe(frame, event, _result):
    if event != "return":
        return
    module, name = frame.f_globals.get("__name__", ""), frame.f_code.co_name
    if module == "subprocess" and name == "__init__":
        child = frame.f_locals.get("self")
        if isinstance(child, subprocess.Popen):
            args, env = frame.f_locals.get("args", []), frame.f_locals.get("env") or {}
            safe = (
                len(args) == 4 and Path(args[0]) == python and args[1:3] == ["-I", "-u"]
                and Path(args[3]) == source_bootstrap and bool(env) and not set(env) - allowed
            )
            emit({"event": "spawn", "safe": safe})
    if module == "app.services.knowledge_transport" and name == "_stop_owned":
        owner = frame.f_locals.get("owner")
        child = getattr(owner, "process", None)
        threads = frame.f_locals.get("threads", [])
        emit({"event": "cleanup", "closed": (
            owner is not None and owner.closed
            and (os.name != "nt" or (owner.tree_empty and owner.job is None))
            and child is not None and child.poll() is not None
            and child.stdin.closed and child.stdout.closed
            and all(not thread.is_alive() for thread in threads)
        )})


sys.setprofile(observe)
threading.setprofile(observe)
from werkzeug import serving

make_server = serving.make_server
server = None
shutdown_thread = None


def owned_listener(host, port, *args, **kwargs):
    global server, shutdown_thread
    if server is not None or host != "127.0.0.1" or port != 0:
        raise RuntimeError("protected listener admission mismatch")
    server = make_server(host, port, *args, **kwargs)
    if server.server_address != ("127.0.0.1", server.server_port) or server.server_port <= 0:
        raise RuntimeError("protected listener identity mismatch")

    def stop():
        sys.stdin.buffer.read(1)
        server.shutdown()

    shutdown_thread = threading.Thread(target=stop, daemon=True)
    shutdown_thread.start()
    emit({"event": "ready", "port": server.server_port})
    return server


serving.make_server = owned_listener
try:
    with redirect_stdout(sys.stderr):
        runpy.run_path(str(root / "backend/run.py"), run_name="__main__")
    if server is None or shutdown_thread is None:
        raise RuntimeError("launcher returned without an owned listener")
    shutdown_thread.join(timeout=2)
    if shutdown_thread.is_alive():
        raise RuntimeError("launcher shutdown did not close")
finally:
    if server is not None:
        server.server_close()
    sys.setprofile(None)
    threading.setprofile(None)
    emit({"event": "shutdown", "loopback_blocked": guard.blocked_attempts})
    guard.restore()
'''

_CATALOGS = (
    ("mf_app", "nexaweave_storage", "sql_sha256", "catalog_sha256", MIGRATIONS["nexaweave_storage"]),
    ("mf_knowledge", "nexaweave_knowledge", "checksum", "schema_checksum", MIGRATIONS["nexaweave_knowledge"]),
    ("mf_execution", "nexaweave_execution", "checksum", "schema_checksum", ("0001_budget_admission.sql",)),
    ("mf_preparation", "nexaweave_execution", "checksum", "schema_checksum", ("preparations_0001.sql",)),
    ("mf_native_execution", "nexaweave_execution", "sql_sha256", "catalog_sha256",
     ("native_runs_0001.sql", "native_runs_0002.sql")),
    ("mf_native_launch", "nexaweave_execution", "checksum", "schema_checksum",
     ("native_launch_0001.sql", "native_launch_0002.sql")),
    ("mf_report", "nexaweave_execution", "checksum", "schema_checksum", ("report_0001.sql",)),
    ("mf_followup", "nexaweave_execution", "checksum", "schema_checksum", ("followup_0001.sql",)),
)


def _migration_receipts(connect):
    receipts = {}
    with connect() as connection:
        for schema, package, sql_digest, catalog_digest, migrations in _CATALOGS:
            rows = connection.execute(
                f"SELECT version,{sql_digest},{catalog_digest} "
                f"FROM {schema}.schema_migrations ORDER BY version"
            ).fetchall()
            resources = files(package).joinpath("migrations")
            expected = [
                (version, hashlib.sha256(resources.joinpath(name).read_text("utf-8").encode("utf-8")).hexdigest())
                for version, name in enumerate(migrations, 1)
            ]
            assert [(version, sql.strip()) for version, sql, _ in rows] == expected
            assert all(re.fullmatch(r"[0-9a-f]{64}", catalog.strip()) for _, _, catalog in rows)
            receipts[schema] = rows
    return receipts


def _operator(directory, dsn, module, *args, refused=False):
    env = _unit_environment(directory)
    env["NEXAWEAVE_APPSTORE_DSN"] = dsn
    command = [sys.executable, "-I", "-B", "-m", module, *args]
    with owned_process(command, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        output, errors = process.communicate(timeout=45)
        assert process.returncode == (2 if refused else 0)
    if refused:
        assert not output and errors.startswith(b"operator_bootstrap_refused")
        return None
    assert not errors and len(output) <= 4096
    return json.loads(output) if output else None


def _events(path):
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, ValueError):
        return []


def _get(port, route, token=None):
    headers = {"Authorization": "Bearer " + token} if token else {}
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=65)
    try:
        connection.request("GET", route, headers=headers)
        response = connection.getresponse()
        raw = response.read(4 * 1024 * 1024 + 1025)
        assert len(raw) <= 4 * 1024 * 1024 + 1024
        return response.status, response.headers, json.loads(raw)
    finally:
        connection.close()


@contextmanager
def _host(directory, scope, display, pg):
    directory.mkdir()
    env = _unit_environment(directory)
    for key in list(env):
        key_upper = key.upper()
        if key_upper.startswith(("PG", "KNOWLEDGE_", "LLM_", "OPENAI_", "DEEPSEEK_", "ZEP_", "TEMPORAL_")) or key_upper in {
            "PYTHONPATH", "PYTHONHOME", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
            "MIROFISH_APP_MODE", "MIROFISH_ALLOWED_ORIGINS",
        }:
            env.pop(key)
    token = secrets.token_hex(32)
    env.update(
        SOURCE_HTTP_TEST_ROOT=str(ROOT),
        NEXAWEAVE_APP_MODE="research_local",
        PYTHON_DOTENV_DISABLED="1",
        FLASK_SKIP_DOTENV="1",
        FLASK_HOST="127.0.0.1",
        FLASK_PORT="0",
        FLASK_DEBUG="0",
        NEXAWEAVE_ALLOWED_ORIGINS="http://localhost:3000",
        KNOWLEDGE_READ_TOKEN=token,
        KNOWLEDGE_PYTHON=os.environ["KNOWLEDGE_PYTHON"],
        KNOWLEDGE_BOOTSTRAP_SCRIPT=os.environ["KNOWLEDGE_BOOTSTRAP_SCRIPT"],
        KNOWLEDGE_PRINCIPAL="owner",
        KNOWLEDGE_DISPLAY_GRAPH_ID=display,
        KNOWLEDGE_BOUND_SCOPE_JSON=scope.model_dump_json(),
        KNOWLEDGE_PG_HOST=pg["host"],
        KNOWLEDGE_PG_PORT=pg["port"],
        KNOWLEDGE_PG_DATABASE=pg["dbname"],
        KNOWLEDGE_PG_USER=pg["user"],
        KNOWLEDGE_PG_PASSWORD=pg["password"],
        # The read-host validator requires this syntax. The observed metadata
        # child environment allows no graph settings.
        KNOWLEDGE_NEO4J_URI="bolt://127.0.0.1:17687",
        KNOWLEDGE_NEO4J_USER="neo4j",
        KNOWLEDGE_NEO4J_PASSWORD="unused-owned-source-fixture",
    )
    output = directory / "source-http-events.jsonl"
    errors = directory / "source-http-errors.log"
    command = [os.environ["NEXAWEAVE_WORKBENCH_BACKEND_PYTHON"], "-I", "-u", "-c", _HTTP_CHILD]
    with output.open("wb") as out, errors.open("wb") as err:
        with owned_process(command, cwd=directory, env=env, stdin=subprocess.PIPE, stdout=out, stderr=err) as process:
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline and process.poll() is None:
                    ready = [event for event in _events(output) if event["event"] == "ready"]
                    if ready:
                        break
                    time.sleep(0.05)
                else:
                    pytest.fail("metadata HTTP readiness failed; private diagnostics retained", pytrace=False)
                assert len(ready) == 1
                yield ready[0]["port"], token, output
            finally:
                try:
                    if process.poll() is None:
                        process.stdin.write(b"x")
                        process.stdin.flush()
                        process.wait(timeout=10)
                finally:
                    process.stdin.close()
            assert process.returncode == 0
    events = _events(output)
    assert [event for event in events if event["event"] == "spawn"] == [{"event": "spawn", "safe": True}]
    assert [event for event in events if event["event"] == "cleanup"] == [{"event": "cleanup", "closed": True}]
    assert events[-1] == {"event": "shutdown", "loopback_blocked": 0}


def _protected_list(directory, pg, scope, display):
    with _host(directory, scope, display, pg) as (port, token, events):
        status, _, health = _get(port, "/health")
        assert status == 200 and health["status"] == "ok" and health["mode"] == "research_local"
        route = "/api/source/library/" + display
        status, _, denied = _get(port, route)
        assert status == 401 and denied == {"success": False, "error": {"code": "unauthorized"}}
        assert [event["event"] for event in _events(events)] == ["ready"]
        status, headers, response = _get(port, route, token)
        assert status == 200 and response["success"] is True
        assert headers["Cache-Control"] == "no-store"
        return response["data"]


def test_clean_installed_migrations_and_owned_metadata_restart(tmp_path):
    if os.environ.get("NEXAWEAVE_W81_INSTALLED_READINESS") != "1":
        pytest.skip("requires the guarded installed readiness entry")
    if any(key.upper().startswith("PG") for key in os.environ):
        pytest.fail("ambient libpq settings", pytrace=False)
    dsn = os.environ.get("PROJECT_STORE_POSTGRES_TEST_DSN", "")
    try:
        pg = conninfo_to_dict(dsn)
    except (psycopg.ProgrammingError, ValueError):
        pytest.fail("fresh disposable PostgreSQL identity required", pytrace=False)
    if not (
        pg.get("host") == PG_HOST and pg.get("port") == str(PG_PORT)
        and pg.get("dbname") == PG_DATABASE and pg.get("user") == PG_USER
        and pg.get("connect_timeout") == "3" and bool(pg.get("password"))
        and set(pg) == {"host", "port", "dbname", "user", "password", "connect_timeout"}
    ):
        pytest.fail("fresh disposable PostgreSQL identity required", pytrace=False)

    def connect():
        return psycopg.connect(
            dsn, autocommit=True,
            options="-c statement_timeout=10000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=15000",
        )

    with connect() as connection:
        assert connection.execute(
            "SELECT current_database(),current_user,pg_get_userbyid(datdba) "
            "FROM pg_database WHERE datname=current_database()"
        ).fetchone() == (PG_DATABASE, PG_USER, PG_USER)
        assert connection.execute(
            "SELECT NOT EXISTS (SELECT 1 FROM pg_namespace WHERE left(nspname,3)='mf_')"
        ).fetchone() == (True,), "fixture already contains application schemas"
    bootstrap = "nexaweave_execution.operator_bootstrap"
    authority = ("--database", PG_DATABASE, "--owner-role", PG_USER)
    _operator(tmp_path, dsn, bootstrap, "migrate", "--database", "wrong_fixture",
              "--owner-role", PG_USER, refused=True)
    with connect() as connection:
        assert connection.execute(
            "SELECT NOT EXISTS (SELECT 1 FROM pg_namespace WHERE left(nspname,3)='mf_')"
        ).fetchone() == (True,)
    installed = _operator(tmp_path, dsn, bootstrap, "migrate", *authority)
    assert installed == {"schema_version": 1, "status": "ready", "catalogs": [catalog[0] for catalog in _CATALOGS]}
    receipts = _migration_receipts(connect)
    assert _operator(tmp_path, dsn, bootstrap, "migrate", *authority) == installed
    assert _migration_receipts(connect) == receipts

    workspace, project, graph, source_revision = uuid4(), uuid4(), uuid4(), uuid4()
    projects, bindings, sources = ProjectStore(connect), ScopeBindingStore(connect), SourceStore(connect)
    project_input = tmp_path / "project.json"
    initial, evidence, digest = canonical_payload(snapshot(), [], "proj_1")
    project_input.write_text(json.dumps({
        "schema_version": 1, "principal": "owner", "workspace_id": str(workspace),
        "project_id": str(project), "display_id": "proj_1", "revision": 1,
        "snapshot": initial, "evidence": list(evidence), "digest": digest, "binary_migration": False,
    }, ensure_ascii=False), encoding="utf-8")
    project_args = ("--principal", "owner", "--workspace-id", str(workspace), "--project-id", str(project))
    _operator(tmp_path, dsn, "nexaweave_storage", "import-project", *project_args,
              "--display-id", "proj_1", "--input", str(project_input))
    before = projects.get("owner", project)
    assert before.revision == 1 and before.workspace_id == workspace
    scope = KnowledgeScope(workspace_id=workspace, project_id=project, graph_id=graph, layer=Layer.source)
    display = "source_" + graph.hex
    bind_args = (*authority, *project_args, "--graph-id", str(graph), "--display-graph-id", display)
    _operator(tmp_path, dsn, bootstrap, "bind-source", *bind_args, "--workspace-id", str(uuid4()), refused=True)
    state = _operator(tmp_path, dsn, bootstrap, "bind-source", *bind_args)
    assert state == {"schema_version": 1, "status": "bound", "principal": "owner",
                     "display_graph_id": display, "scope": scope.model_dump(mode="json")}
    assert _operator(tmp_path, dsn, bootstrap, "bind-source", *bind_args) == state
    _operator(tmp_path, dsn, bootstrap, "bind-source", *bind_args, "--graph-id", str(uuid4()), refused=True)
    binding = bindings.resolve("owner", display)
    text, name = "Owned readiness 猫", "W8.1 fixture"
    retained = sources.ingest_text("owner", project, source_revision, name, text, [
        {"evidence_id": str(uuid4()), "start": 0, "end": len(text)},
    ])

    first = _protected_list(tmp_path / "first", pg, scope, display)
    assert first["has_more"] is False and first["graph_ingestion_executed"] is False
    assert first["sources"] == [{
        "project_id": str(project),
        "source_revision": str(source_revision),
        "source_name": name,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "byte_length": len(text.encode("utf-8")),
        "codepoint_length": len(text),
        "recorded_at": retained.recorded_at.isoformat(),
    }]
    assert _protected_list(tmp_path / "restart", pg, scope, display) == first
    assert projects.get("owner", project) == before
    assert projects.history("owner", project) == [before]
    assert bindings.resolve("owner", display) == binding
    assert sources.get_source("owner", project, source_revision) == retained
    assert _migration_receipts(connect) == receipts

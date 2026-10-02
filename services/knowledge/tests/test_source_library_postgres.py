"""Authored owned-PG/cold-installed-process regressions. Main alone executes."""
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID, uuid4, uuid5

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from mirofish_knowledge.bindings import ScopeBindingStore
from mirofish_knowledge.contracts import KnowledgeScope, Layer
from mirofish_knowledge.operations import Ledger, migrate as migrate_knowledge
from mirofish_knowledge.source_library import (SourceLibrary, SourceSettings, SourceError,
    declarations, encoded, validate_payload, MAX_BYTES)
from mirofish_storage import ProjectStore, SourceStore, Conflict, NotFound, migrate
from test_project_store import snapshot


@pytest.fixture(scope="module")
def factory():
    if os.getenv("PROJECT_STORE_POSTGRES_INTEGRATION") != "1":
        pytest.skip("disposable PostgreSQL integration disabled")
    dsn = os.getenv("PROJECT_STORE_POSTGRES_TEST_DSN")
    if not dsn or any(key in os.environ for key in ("PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER",
            "PGPASSWORD", "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE", "PGOPTIONS")):
        pytest.fail("approved fixture DSN required")
    settings = conninfo_to_dict(dsn)
    if (settings.get("host") != "127.0.0.1" or settings.get("port") != "15432"
            or settings.get("dbname") != "mirofish_operations_test"
            or settings.get("user") != "mirofish_fixture" or not settings.get("password")
            or set(settings) - {"host", "port", "dbname", "user", "password", "connect_timeout"}):
        pytest.fail("not the approved disposable fixture")
    def connect():
        return psycopg.connect(dsn, connect_timeout=3)
    with connect() as connection:
        migrate(connection)  # current accepted schema3, never an old2-only fixture
        migrate_knowledge(connection)
    return connect


def owned(factory, *, layer=Layer.source, run=None, branch=None, workspace_mismatch=False):
    workspace, project, graph = uuid4(), uuid4(), uuid4()
    ProjectStore(factory).create("owner", workspace, project, "proj_1", snapshot())
    scope = KnowledgeScope(workspace_id=uuid4() if workspace_mismatch else workspace,
        project_id=project, graph_id=graph, layer=layer, run_id=run, branch_id=branch)
    display = "graph_" + graph.hex
    ScopeBindingStore(factory).bind("owner", display, scope)
    return SourceSettings("owner", display, scope, {}), project


def payload(text="A😀猫\r\n雪", *, revision=None, name="retained", blocks=None):
    return {"source_revision": revision or str(uuid4()), "source_name": name, "text": text, "blocks": blocks}


def request(settings, method, value, *, request_id=None):
    return encoded({"version": 1, "request_id": request_id or str(uuid4()), "method": method,
        "scope": settings.scope.model_dump(mode="json"), "payload": value})


@pytest.mark.postgres
def test_owned_unicode_idempotence_snapshot_history_and_connections(factory):
    settings, project = owned(factory)
    connections = []
    def tracked():
        conn = factory()
        connections.append(conn)
        return conn
    library = SourceLibrary(settings, connection_factory=tracked)
    before = ProjectStore(factory).get("owner", project)
    history = ProjectStore(factory).history("owner", project)
    value = payload("😀" * 8193 + "猫\r\n雪")
    context = library.execute("context", {})
    assert context["scope"] == settings.scope.model_dump(mode="json")
    first = library.execute("retain", value)
    assert library.execute("retain", value) == first
    stored = library.execute("get", {"source_revision": value["source_revision"]})
    assert stored["text"] == value["text"]
    assert first["passages"] == stored["passages"] and "text" not in first
    assert [(p["start"], p["end"]) for p in stored["passages"]] == [(0, 8192), (8192, len(value["text"]))]
    assert first["source"]["byte_length"] == len(value["text"].encode())
    assert first["source"]["text_sha256"] == hashlib.sha256(value["text"].encode()).hexdigest()
    assert first["graph_ingestion_executed"] is first["binary_retained"] is False
    assert ProjectStore(factory).get("owner", project) == before
    assert ProjectStore(factory).history("owner", project) == history
    assert connections and all(connection.closed for connection in connections)
    with factory() as connection:
        assert connection.execute("SELECT count(*) FROM mf_knowledge.operations WHERE group_id=%s",
                                  (settings.scope.group_id,)).fetchone()[0] == 0


@pytest.mark.postgres
def test_collisions_never_overwrite_and_failure_connections_close(factory):
    settings, project = owned(factory)
    connections = []
    def tracked():
        conn = factory()
        connections.append(conn)
        return conn
    library = SourceLibrary(settings, connection_factory=tracked)
    value = payload()
    first = library.execute("retain", value)
    for changed in ({**value, "source_name": "changed"}, {**value, "text": "different"}):
        with pytest.raises(Conflict):
            library.execute("retain", changed)
    # Same content/name but a different deterministic declaration set also conflicts.
    block = {"kind": "paragraph", "start": 0, "end": len(value["text"]), "table": None,
        "row": None, "cell": None, "grid_span": 1, "vertical_merge": None, "ordinal": 0, "empty": False}
    with pytest.raises(Conflict):
        library.execute("retain", {**value, "blocks": [block]})
    assert library.execute("retain", value) == first
    assert all(conn.closed for conn in connections)
    foreign, _ = owned(factory)
    reply = json.loads(SourceLibrary(foreign, connection_factory=factory).dispatch(request(foreign, "retain", value)))
    assert reply["ok"] is False and reply["error"]["code"] == "conflict"
    assert SourceStore(factory).get_source("owner", project, value["source_revision"]).text == value["text"]


@pytest.mark.postgres
@pytest.mark.parametrize("case", ["crossowner", "workspace", "simulation", "run", "branch", "tombstone", "scope_mismatch"])
def test_persisted_authority_denies_before_storage(factory, monkeypatch, case):
    settings, project = owned(factory, layer=Layer.simulation if case == "simulation" else Layer.source,
        run=uuid4() if case == "run" else None, branch=uuid4() if case == "branch" else None,
        workspace_mismatch=case == "workspace")
    if case == "crossowner":
        settings = replace(settings, principal="other")
    if case == "scope_mismatch":
        settings = replace(settings, scope=KnowledgeScope(workspace_id=settings.scope.workspace_id,
            project_id=settings.scope.project_id, graph_id=uuid4(), layer=Layer.source))
    if case == "tombstone":
        Ledger(factory).tombstone_scope(settings.scope)
    def forbidden(*args, **kwargs):
        raise AssertionError("storage called before authority")
    monkeypatch.setattr(SourceStore, "ingest_text", forbidden)
    library = SourceLibrary(settings, connection_factory=factory)
    value = payload()
    reply = json.loads(library.dispatch(request(settings, "retain", value)))
    assert reply["ok"] is False
    assert reply["error"]["code"] in {"source_denied", "not_found", "tombstoned"}
    assert SourceStore(factory).list_sources("owner", project) == []


@pytest.mark.postgres
def test_retain_repeats_binding_gate_and_closes_before_mutation(factory, monkeypatch):
    settings, project = owned(factory)
    library = SourceLibrary(settings, connection_factory=factory)
    original = library.authorize
    calls = []
    def revoke():
        result = original()
        calls.append(result)
        if len(calls) == 1:
            Ledger(factory).tombstone_scope(settings.scope)
        return result
    monkeypatch.setattr(library, "authorize", revoke)
    value = payload()
    reply = json.loads(library.dispatch(request(settings, "retain", value)))
    assert reply["error"]["code"] == "tombstoned"
    assert SourceStore(factory).list_sources("owner", project) == []


@pytest.mark.postgres
def test_latest_twenty_bounded_window_and_checksum_tamper(factory):
    settings, project = owned(factory)
    library = SourceLibrary(settings, connection_factory=factory)
    values = [payload("text " + str(number)) for number in range(21)]
    for value in values:
        library.execute("retain", value)
    result = library.execute("list", {})
    assert len(result["sources"]) == 20 and result["has_more"] is True and result["window_limit"] == 20
    stamps = [(r["recorded_at"], r["source_revision"]) for r in result["sources"]]
    assert [r[0] for r in stamps] == sorted([r[0] for r in stamps], reverse=True)
    revision = values[-1]["source_revision"]
    with factory() as connection:
        connection.execute("UPDATE mf_app.source_revisions SET text_sha256=%s WHERE source_revision=%s",
                           ("0" * 64, UUID(revision)))
    for method, body in (("get", {"source_revision": revision}), ("list", {})):
        reply = json.loads(library.dispatch(request(settings, method, body)))
        assert reply["ok"] is False and reply["error"]["code"] == "source_unavailable"


@pytest.mark.postgres
def test_exact_docx_typed_block_ids_and_passage_hashes(factory):
    settings, project = owned(factory)
    text = "A😀猫\n\n雪\t"
    fields = {"table": None, "row": None, "cell": None, "grid_span": 1, "vertical_merge": None}
    blocks = [dict(fields, kind="paragraph", start=0, end=3, ordinal=0, empty=False),
        dict(fields, kind="table_cell", start=5, end=6, ordinal=1, empty=False, table=0, row=0, cell=0),
        dict(fields, kind="table_cell", start=7, end=7, ordinal=2, empty=True, table=0, row=0, cell=1)]
    value = payload(text, blocks=blocks)
    library = SourceLibrary(settings, connection_factory=factory)
    result = library.execute("retain", value)
    for passage, block in zip(result["passages"], blocks[:2]):
        identity = "docx-main-body-v1:" + json.dumps(block, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert passage["evidence_id"] == str(uuid5(UUID(value["source_revision"]), identity))
        assert passage["excerpt_sha256"] == hashlib.sha256(text[block["start"]:block["end"]].encode()).hexdigest()
        assert passage["page"] is None
    assert library.execute("get", {"source_revision": value["source_revision"]})["text"] == text


@pytest.mark.postgres
@pytest.mark.parametrize("text,name,spans", [
    ("A😀猫B雪Z", "legacy", [(1, 4), (3, 6)]),
    ("A😀猫B雪Z", "legacy", [(3, 6), (1, 4)]),
    (" \t\r\n ", " \x01\x85\t ", [(3, 5), (1, 4)]),
])
def test_source_library_reads_accepted_legacy_records_in_stored_order(factory, text, name, spans):
    settings, project = owned(factory)
    revision = uuid4()
    declarations = [{"evidence_id": str(uuid4()), "start": start, "end": end} for start, end in spans]
    original = SourceStore(factory).ingest_text("owner", project, revision, name, text, declarations)
    connections = []
    def tracked():
        connection = factory()
        connections.append(connection)
        return connection
    library = SourceLibrary(settings, connection_factory=tracked)
    result = library.execute("get", {"source_revision": str(revision)})
    assert result["text"] == original.text == text and result["source"]["source_name"] == name
    assert [(p["start"], p["end"]) for p in result["passages"]] == spans
    assert [p["evidence_id"] for p in result["passages"]] == [d["evidence_id"] for d in declarations]
    for passage, (start, end) in zip(result["passages"], spans):
        assert passage["excerpt_sha256"] == hashlib.sha256(text[start:end].encode()).hexdigest()
    listed = library.execute("list", {})
    assert listed["sources"] == [result["source"]] and listed["has_more"] is False
    assert all(connection.closed for connection in connections)
    if not text.strip():
        with pytest.raises(ValueError):
            validate_payload("retain", payload(text, name=name))
    assert SourceStore(factory).get_source("owner", project, revision) == original


def test_child_dispatch_rejects_malformed_identity_scope_and_caps_without_db():
    scope = KnowledgeScope(workspace_id=uuid4(), project_id=uuid4(), graph_id=uuid4(), layer=Layer.source)
    settings = SourceSettings("owner", "display", scope, {})
    def forbidden():
        raise AssertionError("database reached")
    library = SourceLibrary(settings, connection_factory=forbidden)
    values = [b"", b"x" * (MAX_BYTES + 1025), b'{"version":1,"version":1}', b'{"x":NaN}']
    for changes in ({"version": True}, {"method": "ingest"}, {"payload": {"principal": "caller"}},
                    {"request_id": "not-a-uuid"}, {"extra": "private"}):
        value = json.loads(request(settings, "context", {}))
        value.update(changes)
        values.append(encoded(value))
    for raw in values:
        result = json.loads(library.dispatch(raw))
        assert result["ok"] is False and result["error"]["code"] == "invalid_request"


def test_fresh_installed_source_import_denies_provider_modules(tmp_path):
    code = r'''
import importlib.abc, sys
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"mirofish_knowledge.provider", "mirofish_knowledge.read_runtime"} or fullname.split(".")[0] in {"graphiti_core", "neo4j", "openai", "camel"}:
            raise AssertionError("forbidden provider import: " + fullname)
sys.meta_path.insert(0, Deny())
import mirofish_knowledge.source_bootstrap
import mirofish_knowledge.bindings
assert "mirofish_knowledge.provider" not in sys.modules
assert "mirofish_knowledge.read_runtime" not in sys.modules
print("source-only")
'''
    result = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path,
                            capture_output=True, timeout=30, check=False)
    assert result.returncode == 0, "cold source-only installed import failed"
    assert result.stdout.strip() == b"source-only"


def test_fresh_public_provider_export_remains_compatible(tmp_path):
    code = r'''
import mirofish_knowledge
assert "GraphitiKnowledgeProvider" in mirofish_knowledge.__all__
from mirofish_knowledge import GraphitiKnowledgeProvider
from mirofish_knowledge.provider import GraphitiKnowledgeProvider as direct
assert GraphitiKnowledgeProvider is direct
assert mirofish_knowledge.GraphitiKnowledgeProvider is direct
print("public-export")
'''
    result = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path,
                            capture_output=True, timeout=30, check=False)
    assert result.returncode == 0, "cold public provider import failed"
    assert result.stdout.strip() == b"public-export"


@pytest.mark.parametrize("key", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE", "PGOPTIONS"])
def test_source_settings_reject_ambient_libpq_before_connect(monkeypatch, key):
    monkeypatch.setenv(key, "")
    with pytest.raises(ValueError):
        SourceSettings.from_environment()

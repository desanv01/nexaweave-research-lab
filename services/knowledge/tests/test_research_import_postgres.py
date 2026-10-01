"""Actual disposable PG and fresh -I installed CLI sources. Main executes only."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import subprocess
import sys
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from mirofish_storage import Conflict, NotFound, ProjectStore, SourceStore, StorageError
from mirofish_storage.research_bundle import canonical, decode, export_bundle
from mirofish_storage.research_import import ResearchImportStore, remap_id
from test_project_store import snapshot
from test_source_store_postgres import factory

pytestmark = pytest.mark.postgres
OWNER = "import-local-owner"


def fixture_bundle(factory):
    project, source, passage = uuid4(), uuid4(), uuid4()
    store = ProjectStore(factory)
    snap = snapshot()
    snap["files"][0]["path"] = "../../PRIVATE/<script>inert</script>"
    snap["graph_build_task_id"] = "https://invalid.example/do-not-dispatch"
    first = store.create("origin-owner", uuid4(), project, "proj_1", snap,
        [{"evidence_id": str(passage), "source_revision": str(source), "sha256": "0" * 64,
          "object_key": "inert/private.bin", "byte_length": 17}])
    retained = SourceStore(factory).ingest_text("origin-owner", project, source, "Unicode origin",
        "\ufeffA😀\r\n猫e\u0301", [{"evidence_id": str(passage), "start": 2, "end": 6, "page": 9}])
    raw = export_bundle(store, SourceStore(factory), "origin-owner", str(project), 1, [str(source)])
    return raw, hashlib.sha256(raw).hexdigest(), first, retained


def target(factory):
    store = ProjectStore(factory)
    project = uuid4()
    first = store.create(OWNER, uuid4(), project, "proj_1", snapshot())
    snap = snapshot()
    snap["name"] = "live target revision"
    current = store.update(OWNER, project, 1, snap)
    return project, first, current


def tracked(factory):
    connections = []
    def connect():
        conn = factory()
        connections.append(conn)
        return conn
    return connect, connections


def counts(factory, project):
    with factory() as conn:
        return tuple(conn.execute(f"SELECT count(*) FROM mf_app.{table} WHERE {column}=%s", (project,)).fetchone()[0]
            for table, column in (("source_revisions", "project_id"), ("passage_evidence", "project_id"),
                                  ("research_imports", "target_project_id")))


def test_actual_owned_unicode_receipt_repeat_distinct_target_and_history(factory):
    raw, digest, origin, retained = fixture_bundle(factory)
    project, first, current = target(factory)
    store = ProjectStore(factory)
    before = store.history(OWNER, project)
    connect, connections = tracked(factory)
    importer = ResearchImportStore(connect)
    receipt = importer.import_bundle(raw, OWNER, project, 2, digest)
    assert len(connections) == 1 and connections[0].closed
    revision = remap_id(project, "source", digest, retained.source_revision)
    evidence = remap_id(project, "passage", digest, retained.passages[0].evidence_id)
    restored = SourceStore(factory).get_source(OWNER, project, revision)
    assert restored.name == retained.name and restored.text == retained.text
    assert restored.recorded_at == receipt.imported_at
    assert restored.recorded_at != retained.recorded_at
    assert restored.text_sha256 == retained.text_sha256
    assert (restored.byte_length, restored.codepoint_length) == (retained.byte_length, retained.codepoint_length)
    resolved = SourceStore(factory).resolve_evidence(OWNER, project, evidence)
    assert resolved.excerpt == "😀\r\n猫" and resolved.declared_page == 9
    assert (resolved.start, resolved.end, resolved.excerpt_sha256) == (2, 6, retained.passages[0].excerpt_sha256)
    assert receipt.origin_project_id == origin.project_id and receipt.origin_revision == 1
    assert receipt.provenance["origin_project"] == decode(raw)["payload"]["project"]
    assert receipt.provenance["sources"][0]["recorded_at"] == retained.recorded_at.isoformat()
    assert receipt.summary()["graph_restored"] is False
    assert importer.import_bundle(raw, OWNER, project, 2, digest) == receipt
    assert len(connections) == 2 and all(c.closed for c in connections)
    assert counts(factory, project) == (1, 1, 1)
    assert store.get(OWNER, project) == current and store.history(OWNER, project) == before
    assert SourceStore(factory).get_source("origin-owner", origin.project_id, retained.source_revision) == retained
    second, _, _ = target(factory)
    other = importer.import_bundle(raw, OWNER, second, 2, digest)
    assert other.target_project_id == second and counts(factory, second) == (1, 1, 1)
    assert remap_id(second, "source", digest, retained.source_revision) != revision
    # A project may advance independently. Repeat uses current expected revision
    # but keeps the original import timestamp and revision-at-import lineage.
    snap = snapshot()
    snap["name"] = "independently advanced"
    store.update(OWNER, project, 2, snap)
    assert importer.import_bundle(raw, OWNER, project, 3, digest) == receipt


def test_owner_missing_and_stale_before_writes_and_repeat_owner_gate(factory):
    raw, digest, _, _ = fixture_bundle(factory)
    project, _, current = target(factory)
    connect, connections = tracked(factory)
    importer = ResearchImportStore(connect)
    for principal, identity, revision, error in (("foreign", project, 2, NotFound),
            (OWNER, uuid4(), 2, NotFound), (OWNER, project, 1, Conflict)):
        with pytest.raises(error): importer.import_bundle(raw, principal, identity, revision, digest)
    assert counts(factory, project) == (0, 0, 0)
    receipt = importer.import_bundle(raw, OWNER, project, 2, digest)
    with pytest.raises(NotFound): importer.import_bundle(raw, "foreign", project, 2, digest)
    with pytest.raises(Conflict): importer.import_bundle(raw, OWNER, project, 1, digest)
    assert counts(factory, project) == (1, 1, 1)
    assert all(c.closed for c in connections) and len(connections) == 6
    assert ProjectStore(factory).get(OWNER, project) == current


def test_late_failure_rolls_back_sources_passages_receipt_and_closes(factory):
    raw, digest, _, _ = fixture_bundle(factory)
    project, _, current = target(factory)
    connections, writes = [], []
    class LateFailure:
        def __init__(self, conn): self.conn = conn
        def __enter__(self): self.conn.__enter__(); return self
        def __exit__(self, *args): return self.conn.__exit__(*args)
        def transaction(self): return self.conn.transaction()
        def execute(self, sql, params=None):
            result = self.conn.execute(sql, params)
            if sql.startswith("INSERT"):
                writes.append(sql)
            if sql.startswith("INSERT INTO mf_app.research_imports"):
                # Fail after the authoritative receipt and all passages were
                # inserted, using an actual driver exception inside transaction.
                self.conn.execute("SELECT 1/0")
            return result
    def fail_factory():
        conn = factory()
        connections.append(conn)
        return LateFailure(conn)
    with pytest.raises(StorageError):
        ResearchImportStore(fail_factory).import_bundle(raw, OWNER, project, 2, digest)
    assert len(writes) == 3 and len(connections) == 1 and connections[0].closed
    assert counts(factory, project) == (0, 0, 0)
    assert ProjectStore(factory).get(OWNER, project) == current
    ResearchImportStore(factory).import_bundle(raw, OWNER, project, 2, digest)
    assert counts(factory, project) == (1, 1, 1)


@pytest.mark.parametrize("collision", ["source", "passage"])
def test_collision_is_never_adopted_and_prior_data_untouched(factory, collision):
    raw, digest, _, retained = fixture_bundle(factory)
    project, _, _ = target(factory)
    revision = remap_id(project, "source", digest, retained.source_revision)
    if collision == "source":
        prior = SourceStore(factory).ingest_text(OWNER, project, revision, "collision", "different")
    else:
        prior = SourceStore(factory).ingest_text(OWNER, project, uuid4(), "collision", "different",
            [{"evidence_id": str(remap_id(project, "passage", digest, retained.passages[0].evidence_id)),
              "start": 0, "end": 1}])
    connect, connections = tracked(factory)
    with pytest.raises(Conflict): ResearchImportStore(connect).import_bundle(raw, OWNER, project, 2, digest)
    assert all(c.closed for c in connections)
    assert counts(factory, project) == (1, 0 if collision == "source" else 1, 0)
    assert SourceStore(factory).get_source(OWNER, project, prior.source_revision) == prior


@pytest.mark.parametrize("tamper", ["source", "passage", "ordinal", "missing", "missing_source", "extra", "recorded_at",
                                  "provenance", "receipt_digest", "payload_digest", "imported_at", "origin", "origin_revision", "target_revision"])
def test_repeat_tampering_fails_closed(factory, tamper):
    raw, digest, _, retained = fixture_bundle(factory)
    project, _, _ = target(factory)
    importer = ResearchImportStore(factory)
    receipt = importer.import_bundle(raw, OWNER, project, 2, digest)
    revision = remap_id(project, "source", digest, retained.source_revision)
    evidence = remap_id(project, "passage", digest, retained.passages[0].evidence_id)
    with factory() as conn:
        if tamper == "source":
            conn.execute("UPDATE mf_app.source_revisions SET name='changed' WHERE source_revision=%s", (revision,))
        elif tamper == "passage":
            conn.execute("UPDATE mf_app.passage_evidence SET page=17 WHERE evidence_id=%s", (evidence,))
        elif tamper == "ordinal":
            conn.execute("UPDATE mf_app.passage_evidence SET ordinal=1 WHERE evidence_id=%s", (evidence,))
        elif tamper == "missing":
            conn.execute("DELETE FROM mf_app.passage_evidence WHERE evidence_id=%s", (evidence,))
        elif tamper == "missing_source":
            conn.execute("DELETE FROM mf_app.passage_evidence WHERE evidence_id=%s", (evidence,))
            conn.execute("DELETE FROM mf_app.source_revisions WHERE source_revision=%s", (revision,))
        elif tamper == "extra":
            conn.execute("INSERT INTO mf_app.passage_evidence "
                "(evidence_id,project_id,source_revision,ordinal,start_offset,end_offset,page,excerpt,excerpt_sha256) "
                "VALUES (%s,%s,%s,1,0,1,NULL,%s,%s)",
                (uuid4(), project, revision, retained.text[:1], hashlib.sha256(retained.text[:1].encode()).hexdigest()))
        elif tamper == "recorded_at":
            conn.execute("UPDATE mf_app.source_revisions SET recorded_at=recorded_at+interval '1 second' WHERE source_revision=%s", (revision,))
        elif tamper == "provenance":
            value = receipt.provenance
            value["sources"][0]["recorded_at"] = "2020-01-01T00:00:00+00:00"
            conn.execute("UPDATE mf_app.research_imports SET provenance=%s WHERE target_project_id=%s", (Jsonb(value), project))
        elif tamper == "receipt_digest":
            conn.execute("UPDATE mf_app.research_imports SET provenance_sha256=%s WHERE target_project_id=%s", ("0" * 64, project))
        elif tamper == "payload_digest":
            conn.execute("UPDATE mf_app.research_imports SET payload_sha256=%s WHERE target_project_id=%s", ("0" * 64, project))
        elif tamper == "imported_at":
            conn.execute("UPDATE mf_app.research_imports SET imported_at=imported_at+interval '1 second' WHERE target_project_id=%s", (project,))
        elif tamper == "origin":
            conn.execute("UPDATE mf_app.research_imports SET origin_project_id=%s WHERE target_project_id=%s", (uuid4(), project))
        elif tamper == "origin_revision":
            conn.execute("UPDATE mf_app.research_imports SET origin_revision=2 WHERE target_project_id=%s", (project,))
        else:
            conn.execute("UPDATE mf_app.research_imports SET target_revision=1 WHERE target_project_id=%s", (project,))
    connect, connections = tracked(factory)
    with pytest.raises(StorageError): ResearchImportStore(connect).import_bundle(raw, OWNER, project, 2, digest)
    assert len(connections) == 1 and connections[0].closed


def test_concurrent_import_serializes_to_one_receipt(factory):
    raw, digest, _, _ = fixture_bundle(factory)
    project, _, _ = target(factory)
    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(lambda _: ResearchImportStore(factory).import_bundle(raw, OWNER, project, 2, digest), range(2)))
    assert receipts[0] == receipts[1] and counts(factory, project) == (1, 1, 1)


@pytest.mark.parametrize("bad", [
    {}, {"schema_version": 1, "origin_project": {}},
    {"schema_version": 1, "sources": []},
    {"origin_project": {}, "sources": []},
    {"schema_version": None, "origin_project": {}, "sources": []},
    {"schema_version": 1, "origin_project": None, "sources": []},
    {"schema_version": 1, "origin_project": {}, "sources": None},
    {"schema_version": "1", "origin_project": {}, "sources": []},
    {"schema_version": True, "origin_project": {}, "sources": []},
    {"schema_version": 2, "origin_project": {}, "sources": []},
    {"schema_version": 1, "origin_project": [], "sources": []},
    {"schema_version": 1, "origin_project": {}, "sources": {}},
], ids=["all-missing", "sources-missing", "origin-missing", "version-missing", "version-null",
        "origin-null", "sources-null", "version-string", "version-bool", "version-other",
        "origin-array", "sources-object"])
def test_sql3_required_provenance_fields_denied_in_savepoint(factory, bad):
    raw, digest, _, _ = fixture_bundle(factory)
    project, _, _ = target(factory)
    receipt = ResearchImportStore(factory).import_bundle(raw, OWNER, project, 2, digest)
    with factory() as conn:
        with conn.transaction():
            # The outer transaction remains usable after each failed SQL CHECK.
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute("UPDATE mf_app.research_imports SET provenance=%s WHERE target_project_id=%s",
                                 (Jsonb(bad), project))
            assert conn.execute("SELECT provenance FROM mf_app.research_imports WHERE target_project_id=%s", (project,)).fetchone()[0] == receipt.provenance
    assert ResearchImportStore(factory).import_bundle(raw, OWNER, project, 2, digest) == receipt


def test_sql3_target_revision_must_exist_in_retained_history(factory):
    raw, digest, _, _ = fixture_bundle(factory)
    project, _, _ = target(factory)
    receipt = ResearchImportStore(factory).import_bundle(raw, OWNER, project, 2, digest)
    with factory() as conn:
        with conn.transaction():
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                with conn.transaction():
                    conn.execute("UPDATE mf_app.research_imports SET target_revision=3 WHERE target_project_id=%s", (project,))
            assert conn.execute("SELECT target_revision FROM mf_app.research_imports WHERE target_project_id=%s", (project,)).fetchone()[0] == 2
    assert ResearchImportStore(factory).import_bundle(raw, OWNER, project, 2, digest) == receipt


_CHILD = '''
import importlib.abc
import runpy
import socket
import sys
blocked = ('app', 'flask', 'dotenv', 'openai', 'camel', 'oasis', 'graphiti_core', 'mirofish_knowledge')
class NoProviders(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            raise AssertionError('unexpected provider import')
sys.meta_path.insert(0, NoProviders())
original = socket.socket.connect
def guarded(self, address):
    if not isinstance(address, tuple) or address[0] != '127.0.0.1' or address[1] != 15432:
        raise AssertionError('unexpected socket')
    return original(self, address)
socket.socket.connect = guarded
import psycopg
real_connect = psycopg.connect
connections = []
def connect(*a, **k):
    conn = real_connect(*a, **k)
    connections.append(conn)
    return conn
psycopg.connect = connect
status = 97
try:
    runpy.run_module('mirofish_storage.research_import_cli', run_name='__main__')
except SystemExit as error:
    status = error.code
if any(not c.closed for c in connections) or (status == 0 and len(connections) != 1):
    status = 91
raise SystemExit(status)
'''


def test_fresh_noneditable_cli_file_to_owned_pg_digest_repeat_denial_and_tamper(factory, tmp_path):
    raw, digest, _, retained = fixture_bundle(factory)
    project, _, current = target(factory)
    path = tmp_path / "PRIVATE-import.json"
    path.write_bytes(raw)
    wrapper = tmp_path / "import-child.py"
    wrapper.write_text(_CHILD, encoding="utf-8")
    env = dict(os.environ)
    for key in list(env):
        upper = key.upper()
        if (upper.startswith(("PG", "KNOWLEDGE_")) or "PROXY" in upper
                or upper.endswith(("API_KEY", "TOKEN", "SECRET"))
                or upper in {"MIROFISH_APPSTORE_DSN", "PROJECT_STORE_POSTGRES_TEST_DSN", "PYTHONPATH",
                             "LLM_BASE_URL", "OPENAI_BASE_URL", "DEEPSEEK_BASE_URL"}): env.pop(key, None)
    env["MIROFISH_APPSTORE_DSN"] = os.environ["PROJECT_STORE_POSTGRES_TEST_DSN"]
    data = {"operation": "import", "principal": OWNER, "target_project_id": str(project),
            "expected_revision": 2, "input": str(path), "expected_sha256": digest}
    def run(value, environment=None):
        return subprocess.run([sys.executable, "-I", "-u", str(wrapper)], input=canonical(value),
            capture_output=True, env=env if environment is None else environment, timeout=30, check=False)
    first = run(data)
    assert first.returncode == 0 and first.stderr == b""
    summary = json.loads(first.stdout)["result"]
    assert summary["artifact_sha256"] == digest
    assert summary["original_binaries_restored"] is False and summary["publisher_authenticated"] is False
    assert "PRIVATE" not in first.stdout.decode() and OWNER not in first.stdout.decode()
    restored = SourceStore(factory).get_source(OWNER, project, remap_id(project, "source", digest, retained.source_revision))
    assert restored.text == retained.text and restored.text_sha256 == hashlib.sha256(restored.text.encode()).hexdigest()
    repeat = run(data)
    assert repeat.returncode == 0 and repeat.stderr == b"" and repeat.stdout == first.stdout
    for changed, error in ((dict(data, principal="foreign"), "import_denied"),
                           (dict(data, expected_revision=1), "conflict"),
                           (dict(data, expected_sha256="0" * 64), "invalid_import"),
                           (dict(data, dsn="PRIVATE"), "invalid_request")):
        result = run(changed)
        assert result.returncode == 2 and result.stderr == b""
        assert json.loads(result.stdout) == {"ok": False, "error": error}
    result = run(data, dict(env, PGHOST="invalid.example"))
    assert result.returncode == 2 and json.loads(result.stdout)["error"] == "authority_unavailable"
    path.write_bytes(raw + b" ")
    result = run(data)
    assert result.returncode == 2 and json.loads(result.stdout)["error"] == "invalid_import"
    assert counts(factory, project) == (1, 1, 1) and ProjectStore(factory).get(OWNER, project) == current

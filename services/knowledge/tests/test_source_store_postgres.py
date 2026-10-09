"""Disposable PostgreSQL qualification for source revision and evidence flow."""

import hashlib
import json
import os
from importlib.resources import files
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from nexaweave_storage import (Conflict, MigrationMismatch, NotFound, ProjectStore,
                              SourceStore, StorageError, migrate)
from nexaweave_storage.__main__ import main as cli_main
from nexaweave_storage.store import _catalog
from nexaweave_storage.validation import canonical_payload
import nexaweave_storage.store as store_module
from test_project_store import snapshot

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def factory():
    if os.getenv("PROJECT_STORE_POSTGRES_INTEGRATION") != "1":
        pytest.skip("disposable PostgreSQL integration disabled")
    dsn = os.getenv("PROJECT_STORE_POSTGRES_TEST_DSN")
    if not dsn or any(os.getenv(key) for key in ("PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE")):
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
        migrate(connection)
        migrate(connection)
    return connect


def project(factory, principal="owner"):
    project_id = uuid4()
    ProjectStore(factory).create(principal, uuid4(), project_id, "proj_1", snapshot())
    return project_id


def test_source_restart_idempotence_resolution_and_isolation(factory):
    project_id, revision, evidence = project(factory), uuid4(), uuid4()
    text = "\ufeffA😀\r\n猫"
    declaration = [{"evidence_id": str(evidence), "start": 2, "end": 6, "page": 3}]
    store = SourceStore(factory)
    first = store.ingest_text("owner", project_id, revision, "upload", text, declaration)
    assert store.ingest_text("owner", project_id, revision, "upload", text, declaration) == first
    assert SourceStore(factory).get_source("owner", project_id, revision) == first
    assert len(store.list_sources("owner", project_id)) == 1
    resolved = store.resolve_evidence("owner", project_id, evidence)
    assert resolved.excerpt == "😀\r\n猫" and resolved.offset_unit == "unicode_codepoint"
    assert resolved.source_sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert resolved.declared_page == 3 and resolved.source_recorded_at == first.recorded_at
    with pytest.raises(NotFound):
        store.get_source("other", project_id, revision)
    with pytest.raises(NotFound):
        store.resolve_evidence("other", project_id, evidence)
    with pytest.raises(NotFound):
        store.resolve_evidence("owner", project(factory), evidence)
    with pytest.raises(NotFound):
        store.list_sources("other", project_id)
    with pytest.raises(Conflict):
        store.ingest_text("owner", project_id, revision, "changed", text, declaration)
    with pytest.raises(Conflict):
        store.ingest_text("owner", project_id, revision, "upload", text, [])
    with pytest.raises(Conflict):
        store.ingest_text("owner", project(factory), uuid4(), "other", "text",
                          [{"evidence_id": str(evidence), "start": 0, "end": 1}])


def test_atomic_failed_passage_and_concurrent_revision_conflict(factory):
    owner_project = project(factory)
    revision, evidence = uuid4(), uuid4()
    SourceStore(factory).ingest_text("owner", owner_project, uuid4(), "seed", "X",
        [{"evidence_id": str(evidence), "start": 0, "end": 1}])
    with pytest.raises(Conflict):
        SourceStore(factory).ingest_text("owner", owner_project, revision, "new", "Y",
            [{"evidence_id": str(evidence), "start": 0, "end": 1}])
    with pytest.raises(NotFound):
        SourceStore(factory).get_source("owner", owner_project, revision)
    barrier = Barrier(2)
    race_revision = uuid4()
    def ingest(name):
        barrier.wait(timeout=5)
        return SourceStore(factory).ingest_text("owner", owner_project, race_revision, name, "same")
    winners, conflicts = [], 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(ingest, name) for name in ("A", "B")]
        for future in futures:
            try:
                winners.append(future.result(timeout=10))
            except Conflict:
                conflicts += 1
    assert len(winners) == conflicts == 1
    assert SourceStore(factory).get_source("owner", owner_project, race_revision) == winners[0]


def test_stored_corruption_denied(factory):
    owner_project, revision, evidence = project(factory), uuid4(), uuid4()
    SourceStore(factory).ingest_text("owner", owner_project, revision, "name", "猫 A",
        [{"evidence_id": str(evidence), "start": 0, "end": 1}])
    with factory() as conn:
        conn.execute("UPDATE mf_app.passage_evidence SET excerpt='wrong' WHERE evidence_id=%s", (evidence,))
    try:
        with pytest.raises(StorageError):
            SourceStore(factory).resolve_evidence("owner", owner_project, evidence)
    finally:
        with factory() as conn:
            conn.execute("UPDATE mf_app.passage_evidence SET excerpt='猫' WHERE evidence_id=%s", (evidence,))


def test_v1_upgrade_preserves_rows_and_rolls_back_failed_v2(factory, monkeypatch):
    # Only the disposable fixture is touched. The outer transaction always rolls back.
    class RollbackFixture(Exception):
        pass
    with factory() as conn:
        with pytest.raises(RollbackFixture):
            with conn.transaction():
                conn.execute("DROP SCHEMA mf_app CASCADE")
                sql1 = files("nexaweave_storage").joinpath("migrations", "0001_project_revisions.sql").read_text("utf-8")
                conn.execute(sql1)
                conn.execute("INSERT INTO mf_app.schema_migrations VALUES (1,%s,%s)",
                             (hashlib.sha256(sql1.encode("utf-8")).hexdigest(), _catalog(conn)))
                owner_project, workspace = uuid4(), uuid4()
                snap, evidence, digest = canonical_payload(snapshot(), [], "proj_1")
                conn.execute("INSERT INTO mf_app.projects (project_id,principal,workspace_id,display_id,current_revision) VALUES (%s,'owner',%s,'proj_1',1)",
                             (owner_project, workspace))
                conn.execute("INSERT INTO mf_app.project_revisions (project_id,revision,snapshot,evidence,digest) VALUES (%s,1,%s,%s,%s)",
                             (owner_project, Jsonb(snap), Jsonb(evidence), digest))
                original_catalog = store_module._catalog
                def fail_after_ddl(connection):
                    if connection.execute("SELECT to_regclass('mf_app.source_revisions')").fetchone()[0] is not None:
                        raise MigrationMismatch()
                    return original_catalog(connection)
                monkeypatch.setattr(store_module, "_catalog", fail_after_ddl)
                with pytest.raises(MigrationMismatch):
                    migrate(conn)
                monkeypatch.setattr(store_module, "_catalog", original_catalog)
                assert conn.execute("SELECT to_regclass('mf_app.source_revisions')").fetchone()[0] is None
                assert conn.execute("SELECT version FROM mf_app.schema_migrations").fetchall() == [(1,)]
                migrate(conn)
                migrate(conn)
                assert conn.execute("SELECT version FROM mf_app.schema_migrations ORDER BY version").fetchall() == [(1,), (2,), (3,), (4,)]
                assert conn.execute("SELECT count(*) FROM mf_app.projects WHERE project_id=%s", (owner_project,)).fetchone()[0] == 1
                conn.execute("ALTER TABLE mf_app.source_revisions ADD COLUMN rogue integer")
                with pytest.raises(MigrationMismatch):
                    migrate(conn)
                raise RollbackFixture()


def test_fresh_v4_install_and_sql_checksum_denial(factory):
    class RollbackFixture(Exception):
        pass
    with factory() as conn:
        with pytest.raises(RollbackFixture):
            with conn.transaction():
                conn.execute("DROP SCHEMA mf_app CASCADE")
                migrate(conn)
                assert conn.execute("SELECT version FROM mf_app.schema_migrations ORDER BY version").fetchall() == [(1,), (2,), (3,), (4,)]
                assert conn.execute("SELECT to_regclass('mf_app.passage_evidence')").fetchone()[0] is not None
                conn.execute("UPDATE mf_app.schema_migrations SET sql_sha256=%s WHERE version=1", ("0" * 64,))
                with pytest.raises(MigrationMismatch):
                    migrate(conn)
                raise RollbackFixture()


def test_v3_catalog_upgrades_additively_to_v4_without_rewriting_sources(factory, monkeypatch):
    class RollbackFixture(Exception):
        pass
    with factory() as conn:
        with pytest.raises(RollbackFixture):
            with conn.transaction():
                conn.execute("DROP SCHEMA mf_app CASCADE")
                for version, filename in ((1, "0001_project_revisions.sql"),
                                          (2, "0002_source_evidence.sql"),
                                          (3, "0003_research_imports.sql")):
                    sql = files("nexaweave_storage").joinpath("migrations", filename).read_text("utf-8")
                    conn.execute(sql)
                    conn.execute("INSERT INTO mf_app.schema_migrations VALUES (%s,%s,%s)",
                                 (version, hashlib.sha256(sql.encode()).hexdigest(), _catalog(conn)))
                owner_project, revision, workspace = uuid4(), uuid4(), uuid4()
                snap, evidence, digest = canonical_payload(snapshot(), [], "proj_1")
                conn.execute("INSERT INTO mf_app.projects (project_id,principal,workspace_id,display_id,current_revision) VALUES (%s,'owner',%s,'proj_1',1)",
                             (owner_project, workspace))
                conn.execute("INSERT INTO mf_app.project_revisions (project_id,revision,snapshot,evidence,digest) VALUES (%s,1,%s,%s,%s)",
                             (owner_project, Jsonb(snap), Jsonb(evidence), digest))
                text = "legacy 猫"
                conn.execute("INSERT INTO mf_app.source_revisions (source_revision,project_id,name,retained_text,text_sha256,byte_length,codepoint_length) VALUES (%s,%s,'old.pdf',%s,%s,%s,%s)",
                             (revision, owner_project, text, hashlib.sha256(text.encode()).hexdigest(),
                              len(text.encode()), len(text)))
                before = conn.execute("SELECT name,retained_text,text_sha256 FROM mf_app.source_revisions WHERE source_revision=%s", (revision,)).fetchone()
                original_catalog = store_module._catalog
                def fail_after_binary_ddl(connection):
                    if connection.execute("SELECT to_regclass('mf_app.source_binaries')").fetchone()[0] is not None:
                        raise MigrationMismatch()
                    return original_catalog(connection)
                monkeypatch.setattr(store_module, "_catalog", fail_after_binary_ddl)
                with pytest.raises(MigrationMismatch):
                    migrate(conn)
                monkeypatch.setattr(store_module, "_catalog", original_catalog)
                assert conn.execute("SELECT to_regclass('mf_app.source_binaries')").fetchone()[0] is None
                assert conn.execute("SELECT version FROM mf_app.schema_migrations ORDER BY version").fetchall() == [(1,), (2,), (3,)]
                assert conn.execute("SELECT name,retained_text,text_sha256 FROM mf_app.source_revisions WHERE source_revision=%s", (revision,)).fetchone() == before
                migrate(conn)
                migrate(conn)
                assert conn.execute("SELECT version FROM mf_app.schema_migrations ORDER BY version").fetchall() == [(1,), (2,), (3,), (4,)]
                assert conn.execute("SELECT name,retained_text,text_sha256 FROM mf_app.source_revisions WHERE source_revision=%s", (revision,)).fetchone() == before
                assert conn.execute("SELECT count(*) FROM mf_app.source_binaries").fetchone()[0] == 0
                raise RollbackFixture()


def test_cli_unicode_import_resolve_and_exclusive_output(factory, tmp_path, monkeypatch, capsys):
    owner_project, revision, evidence = project(factory), uuid4(), uuid4()
    monkeypatch.setenv("NEXAWEAVE_APPSTORE_DSN", os.environ["PROJECT_STORE_POSTGRES_TEST_DSN"])
    text_path = tmp_path / "source.txt"
    text_path.write_bytes("A😀猫\r\n".encode("utf-8"))
    passages_path = tmp_path / "passages.json"
    passages_path.write_text(json.dumps([{"evidence_id": str(evidence), "start": 1,
                                          "end": 3, "page": 9}]), encoding="utf-8")
    common = ["--principal", "owner", "--project-id", str(owner_project)]
    assert cli_main(["import-source", *common, "--source-revision", str(revision),
                     "--name", "extract", "--input", str(text_path),
                     "--passages", str(passages_path)]) == 0
    imported = json.loads(capsys.readouterr().out)
    assert imported["passages"][0]["excerpt"] == "😀猫"
    assert cli_main(["resolve-evidence", *common, "--evidence-id", str(evidence)]) == 0
    citation = json.loads(capsys.readouterr().out)
    assert citation["excerpt"] == "😀猫" and citation["start"] == 1 and citation["end"] == 3
    assert citation["original_document_verified"] is False
    output = tmp_path / "citation.json"
    assert cli_main(["resolve-evidence", *common, "--evidence-id", str(evidence),
                     "--output", str(output)]) == 0
    original = output.read_bytes()
    assert cli_main(["resolve-evidence", *common, "--evidence-id", str(evidence),
                     "--output", str(output)]) == 2
    assert output.read_bytes() == original
    assert capsys.readouterr().err == "storage_error\n"

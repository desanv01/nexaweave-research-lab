"""Main-authored actual migration admission; owned disposable fixture only."""
import hashlib
import os
from contextlib import contextmanager
from importlib.resources import files
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb
import pytest

from nexaweave_storage import MigrationMismatch, ProjectStore, SourceStore, migrate
from nexaweave_storage.research_bundle import decode, inspect_bundle
from nexaweave_storage.research_import import ResearchImportStore, remap_id
from nexaweave_storage.store import _catalog
import nexaweave_storage.store as store_module
from nexaweave_storage.validation import canonical_payload
from test_project_store import snapshot
from test_research_import_postgres import fixture_bundle
from test_source_store_postgres import factory

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize('start_version', [1, 2])
def test_prior_application_upgrade_and_failed_sql3_are_atomic(factory, monkeypatch, start_version):
    class RollbackFixture(Exception):
        pass
    with factory() as conn:
        with pytest.raises(RollbackFixture):
            # All temporary schema changes roll back, restoring retained fixture data.
            with conn.transaction():
                conn.execute('DROP SCHEMA mf_app CASCADE')
                for version, name in [(1, '0001_project_revisions.sql'), (2, '0002_source_evidence.sql')][:start_version]:
                    sql = files('nexaweave_storage').joinpath('migrations', name).read_text('utf-8')
                    conn.execute(sql)
                    conn.execute('INSERT INTO mf_app.schema_migrations VALUES (%s,%s,%s)',
                        (version, hashlib.sha256(sql.encode('utf-8')).hexdigest(), _catalog(conn)))
                project, workspace, source, passage = (uuid4() for _ in range(4))
                snap, evidence, digest = canonical_payload(snapshot(), [], 'proj_1')
                conn.execute("INSERT INTO mf_app.projects (project_id,principal,workspace_id,display_id,current_revision) VALUES (%s,'migration-owner',%s,'proj_1',1)",
                    (project, workspace))
                conn.execute('INSERT INTO mf_app.project_revisions (project_id,revision,snapshot,evidence,digest) VALUES (%s,1,%s,%s,%s)',
                    (project, Jsonb(snap), Jsonb(evidence), digest))
                original_project = conn.execute('SELECT * FROM mf_app.projects WHERE project_id=%s', (project,)).fetchone()
                original_revision = conn.execute('SELECT * FROM mf_app.project_revisions WHERE project_id=%s', (project,)).fetchone()
                if start_version == 2:
                    text = 'A😀猫\r\n'
                    sha = hashlib.sha256(text.encode('utf-8')).hexdigest()
                    excerpt = text[1:3]
                    excerpt_sha = hashlib.sha256(excerpt.encode('utf-8')).hexdigest()
                    conn.execute('INSERT INTO mf_app.source_revisions (source_revision,project_id,name,retained_text,text_sha256,byte_length,codepoint_length) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                        (source, project, 'synthetic prior retention', text, sha, len(text.encode('utf-8')), len(text)))
                    conn.execute('INSERT INTO mf_app.passage_evidence (evidence_id,project_id,source_revision,ordinal,start_offset,end_offset,page,excerpt,excerpt_sha256) VALUES (%s,%s,%s,0,1,3,7,%s,%s)',
                        (passage, project, source, excerpt, excerpt_sha))
                    original_source = conn.execute('SELECT * FROM mf_app.source_revisions WHERE source_revision=%s', (source,)).fetchone()
                    original_passage = conn.execute('SELECT * FROM mf_app.passage_evidence WHERE evidence_id=%s', (passage,)).fetchone()
                original_catalog = store_module._catalog
                def fail_after_sql3(connection):
                    if connection.execute("SELECT to_regclass('mf_app.research_imports')").fetchone()[0] is not None:
                        raise MigrationMismatch()
                    return original_catalog(connection)
                monkeypatch.setattr(store_module, '_catalog', fail_after_sql3)
                with pytest.raises(MigrationMismatch):
                    migrate(conn)
                monkeypatch.setattr(store_module, '_catalog', original_catalog)
                assert conn.execute("SELECT to_regclass('mf_app.research_imports')").fetchone()[0] is None
                assert conn.execute("SELECT to_regclass('mf_app.source_binaries')").fetchone()[0] is None
                assert conn.execute('SELECT version FROM mf_app.schema_migrations ORDER BY version').fetchall() == [(v,) for v in range(1, start_version + 1)]
                migrate(conn)
                migrate(conn)
                assert conn.execute('SELECT version FROM mf_app.schema_migrations ORDER BY version').fetchall() == [(1,), (2,), (3,), (4,), (5,)]
                assert conn.execute("SELECT to_regclass('mf_app.source_binaries')").fetchone()[0] is not None
                assert conn.execute('SELECT count(*) FROM mf_app.source_binaries').fetchone()[0] == 0
                assert conn.execute('SELECT * FROM mf_app.projects WHERE project_id=%s', (project,)).fetchone() == original_project
                assert conn.execute('SELECT * FROM mf_app.project_revisions WHERE project_id=%s', (project,)).fetchone() == original_revision
                if start_version == 2:
                    assert conn.execute('SELECT * FROM mf_app.source_revisions WHERE source_revision=%s', (source,)).fetchone() == original_source
                    assert conn.execute('SELECT * FROM mf_app.passage_evidence WHERE evidence_id=%s', (passage,)).fetchone() == original_passage
                conn.execute('UPDATE mf_app.schema_migrations SET sql_sha256=%s WHERE version=3', ('0' * 64,))
                with pytest.raises(MigrationMismatch):
                    migrate(conn)
                raise RollbackFixture()


def _database_identity(conn):
    return conn.execute(
        "SELECT current_database(), oid, current_user, pg_get_userbyid(datdba), "
        "inet_server_addr()::text, inet_server_port() "
        "FROM pg_database WHERE datname=current_database()"
    ).fetchone()


def _database_owner(conn, name):
    return conn.execute(
        "SELECT oid, pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s",
        (name,),
    ).fetchone()


@contextmanager
def _fresh_restore_destination(factory):
    # Derive the connection solely from the existing guarded fixture. Only the
    # generated, identity-checked database name differs from that authority.
    fixed = {"host": "127.0.0.1", "port": "15432", "dbname": "mirofish_operations_test",
             "user": "mirofish_fixture"}
    settings = conninfo_to_dict(os.environ["PROJECT_STORE_POSTGRES_TEST_DSN"])
    if (any(key.upper().startswith("PG") for key in os.environ)
            or any(settings.get(key) != value for key, value in fixed.items())
            or set(settings) - (fixed.keys() | {"password", "connect_timeout"})
            or not settings.get("password")):
        pytest.fail("approved isolated fixture settings required", pytrace=False)
    role = fixed["user"]
    name = "nw_w74_dst_" + uuid4().hex
    connections = []

    with factory() as maintenance:
        maintenance.autocommit = True
        maintenance.execute("SET statement_timeout = '30s'")
        maintenance.execute("SET lock_timeout = '5s'")
        identity = _database_identity(maintenance)
        if (identity is None or identity[0] != fixed["dbname"] or identity[2:4] != (role, role)
                or identity[4] is None or identity[5] is None):
            pytest.fail("maintenance database identity does not match the disposable fixture", pytrace=False)
        allowed = maintenance.execute(
            "SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        if allowed != (True,):
            pytest.fail("the admitted disposable fixture role cannot create a restore database", pytrace=False)
        if _database_owner(maintenance, name) is not None:
            pytest.fail("generated restore database already exists", pytrace=False)
        try:
            maintenance.execute(sql.SQL("CREATE DATABASE {} WITH TEMPLATE template0 OWNER {}").format(
                sql.Identifier(name), sql.Identifier(role),
            ))
            owned = _database_owner(maintenance, name)
        except psycopg.Error:
            pytest.fail(f"creation identity unconfirmed for {name}", pytrace=False)
        if owned is None or owned[0] == identity[1] or owned[1] != role:
            pytest.fail(f"creation identity unconfirmed for {name}", pytrace=False)
        oid = owned[0]
        destination_identity = (name, oid, role, role, *identity[4:])
        settings.update(dbname=name, connect_timeout=3)

        def connect():
            conn = psycopg.connect(**settings, autocommit=True, passfile=os.devnull)
            connections.append(conn)
            try:
                conn.execute("SET statement_timeout = '5s'")
                conn.execute("SET lock_timeout = '2s'")
                if _database_identity(conn) != destination_identity:
                    pytest.fail("restore destination database identity changed", pytrace=False)
                conn.autocommit = False
                return conn
            except BaseException:
                conn.close()
                raise

        try:
            yield connect
        finally:
            remaining = [conn for conn in connections if not conn.closed]
            for conn in remaining:
                conn.close()
            maintenance.execute("SET statement_timeout = '5s'")
            maintenance.execute("SET lock_timeout = '2s'")
            if _database_identity(maintenance) != identity or _database_owner(maintenance, name) != (oid, role):
                pytest.fail(f"cleanup refused for unverified restore database {name} (OID {oid})", pytrace=False)
            maintenance.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
            assert _database_owner(maintenance, name) is None
            assert not remaining, "restore operations left a destination connection open"


def test_v1_portable_restore_into_independent_database(factory):
    with _fresh_restore_destination(factory) as destination:
        raw, digest, origin, retained = fixture_bundle(factory)
        assert inspect_bundle(raw, digest)["schema_version"] == 1
        with destination() as conn:
            assert conn.execute("SELECT to_regnamespace('mf_app')").fetchone() == (None,)
            migrate(conn)

        owner, project = "w74-restore-owner", uuid4()
        projects = ProjectStore(destination)
        initial = projects.create(owner, uuid4(), project, "proj_1", snapshot())
        first = ResearchImportStore(destination).import_bundle(raw, owner, project, initial.revision, digest)
        replay = ResearchImportStore(destination).import_bundle(raw, owner, project, initial.revision, digest)
        assert replay == first
        assert first.target_project_id == project and first.target_revision == initial.revision == 1
        assert first.origin_project_id == origin.project_id and first.origin_revision == origin.revision
        assert first.artifact_sha256 == digest
        assert first.provenance["origin_project"] == decode(raw)["payload"]["project"]

        passage, = retained.passages
        revision = remap_id(project, "source", digest, retained.source_revision)
        evidence = remap_id(project, "passage", digest, passage.evidence_id)
        sources = SourceStore(destination)
        restored = sources.get_source(owner, project, revision)
        resolved = sources.resolve_evidence(owner, project, evidence)
        assert (restored.project_id, restored.source_revision, restored.name) == (project, revision, retained.name)
        assert restored.text.encode("utf-8") == retained.text.encode("utf-8")
        assert (restored.text_sha256, restored.byte_length, restored.codepoint_length) == (
            retained.text_sha256, retained.byte_length, retained.codepoint_length,
        )
        assert restored.recorded_at == first.imported_at
        assert (resolved.principal, resolved.project_id, resolved.source_revision, resolved.evidence_id) == (
            owner, project, revision, evidence,
        )
        assert resolved.excerpt.encode("utf-8") == passage.excerpt.encode("utf-8")
        assert (resolved.start, resolved.end, resolved.declared_page, resolved.excerpt_sha256, resolved.offset_unit) == (
            passage.start, passage.end, passage.page, passage.excerpt_sha256, "unicode_codepoint",
        )
        assert resolved.source_sha256 == retained.text_sha256
        assert projects.get(owner, project) == initial and projects.history(owner, project) == [initial]
        with destination() as conn:
            assert conn.execute(
                "SELECT NOT EXISTS (SELECT 1 FROM mf_app.projects WHERE project_id=%s) "
                "AND NOT EXISTS (SELECT 1 FROM mf_app.source_revisions WHERE source_revision=%s) "
                "AND NOT EXISTS (SELECT 1 FROM mf_app.passage_evidence WHERE evidence_id=%s)",
                (origin.project_id, retained.source_revision, passage.evidence_id),
            ).fetchone() == (True,)
            assert conn.execute(
                "SELECT (SELECT count(*) FROM mf_app.source_revisions), "
                "(SELECT count(*) FROM mf_app.passage_evidence), (SELECT count(*) FROM mf_app.research_imports)"
            ).fetchone() == (1, 1, 1)

        assert ProjectStore(factory).history("origin-owner", origin.project_id) == [origin]
        assert SourceStore(factory).get_source("origin-owner", origin.project_id, retained.source_revision) == retained

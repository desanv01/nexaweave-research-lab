"""Main-authored actual migration admission; owned disposable fixture only."""
import hashlib
from importlib.resources import files
from uuid import uuid4

from psycopg.types.json import Jsonb
import pytest

from nexaweave_storage import MigrationMismatch, migrate
from nexaweave_storage.store import _catalog
import nexaweave_storage.store as store_module
from nexaweave_storage.validation import canonical_payload
from test_project_store import snapshot
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
                assert conn.execute('SELECT version FROM mf_app.schema_migrations ORDER BY version').fetchall() == [(1,), (2,), (3,), (4,)]
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

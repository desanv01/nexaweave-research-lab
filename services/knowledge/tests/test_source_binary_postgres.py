"""Owned PostgreSQL original-byte and no-orphan regressions; Main executes."""
import hashlib
from uuid import uuid4

import pytest

from nexaweave_storage import Conflict, NotFound, SourceStore, StorageError
from test_source_store_postgres import factory, project

pytestmark = pytest.mark.postgres


def test_original_replay_restart_owner_and_v1_absence(factory):
    project_id, revision, evidence = project(factory), uuid4(), uuid4()
    text, raw = "A😀猫", b"%PDF-1.7\nowned\n%%EOF"
    passages = [{"evidence_id": str(evidence), "start": 1, "end": 3, "page": 1}]
    store = SourceStore(factory)
    first = store.ingest_pdf("owner", project_id, revision, "original.pdf", text, passages, raw)
    assert first.content == raw and first.sha256 == hashlib.sha256(raw).hexdigest()
    assert store.ingest_pdf("owner", project_id, revision, "original.pdf", text, passages, raw) == first
    assert SourceStore(factory).get_original("owner", project_id, revision) == first
    assert SourceStore(factory).get_source("owner", project_id, revision) == first.source
    with pytest.raises(NotFound):
        store.get_original("other", project_id, revision)
    with pytest.raises(Conflict):
        store.ingest_pdf("owner", project_id, revision, "original.pdf", text, passages,
                         b"%PDF-1.7\nchanged\n%%EOF")
    for changed_name, changed_text, changed_passages in (
            ("other.pdf", text, passages), ("original.pdf", text + "x", passages),
            ("original.pdf", text, [])):
        with pytest.raises(Conflict):
            store.ingest_pdf("owner", project_id, revision, changed_name, changed_text,
                             changed_passages, raw)
    old = uuid4()
    store.ingest_text("owner", project_id, old, "old.pdf", text, passages=[])
    with pytest.raises(NotFound):
        store.get_original("owner", project_id, old)
    with pytest.raises(Conflict):
        store.ingest_pdf("owner", project_id, old, "old.pdf", text, [], raw)


def test_original_passage_collision_leaves_no_source_or_binary(factory):
    project_id, revision, evidence = project(factory), uuid4(), uuid4()
    store = SourceStore(factory)
    store.ingest_text("owner", project_id, uuid4(), "seed", "A",
                      [{"evidence_id": str(evidence), "start": 0, "end": 1}])
    with pytest.raises(Conflict):
        store.ingest_pdf("owner", project_id, revision, "new.pdf", "B",
                         [{"evidence_id": str(evidence), "start": 0, "end": 1}],
                         b"%PDF-1.7\nnew\n%%EOF")
    with pytest.raises(NotFound):
        store.get_source("owner", project_id, revision)
    with factory() as conn:
        assert conn.execute("SELECT count(*) FROM mf_app.source_binaries WHERE project_id=%s AND source_revision=%s",
                            (project_id, revision)).fetchone()[0] == 0


def test_original_stored_digest_corruption_refuses_read(factory):
    project_id, revision = project(factory), uuid4()
    store = SourceStore(factory)
    raw = b"%PDF-1.7\noriginal\n%%EOF"
    store.ingest_pdf("owner", project_id, revision, "pdf", "text", [], raw)
    with factory() as conn:
        conn.execute("UPDATE mf_app.source_binaries SET sha256=%s WHERE project_id=%s AND source_revision=%s",
                     ("0" * 64, project_id, revision))
    try:
        with pytest.raises(StorageError):
            store.get_original("owner", project_id, revision)
    finally:
        with factory() as conn:
            conn.execute("UPDATE mf_app.source_binaries SET sha256=%s WHERE project_id=%s AND source_revision=%s",
                         (hashlib.sha256(raw).hexdigest(), project_id, revision))


@pytest.mark.parametrize("boundary", [
    "INSERT INTO mf_app.source_revisions", "INSERT INTO mf_app.passage_evidence",
    "INSERT INTO mf_app.source_binaries", "SELECT contract_version,media_type,byte_length,sha256,original_bytes",
])
def test_original_four_transaction_failure_points_leave_no_orphan(factory, boundary):
    project_id, revision = project(factory), uuid4()
    class FailingConnection:
        def __init__(self):
            self.connection = factory()
        def __enter__(self):
            self.connection.__enter__()
            return self
        def __exit__(self, *args):
            return self.connection.__exit__(*args)
        def transaction(self):
            return self.connection.transaction()
        def execute(self, sql, params=None):
            if boundary in sql:
                raise RuntimeError("injected fixture failure")
            return self.connection.execute(sql, params)
    store = SourceStore(lambda: FailingConnection())
    with pytest.raises(RuntimeError, match="injected fixture failure"):
        store.ingest_pdf("owner", project_id, revision, "failed.pdf", "A猫",
                         [{"evidence_id": str(uuid4()), "start": 0, "end": 2, "page": 1}],
                         b"%PDF-1.7\nowned\n%%EOF")
    with pytest.raises(NotFound):
        SourceStore(factory).get_source("owner", project_id, revision)
    with factory() as conn:
        assert conn.execute("SELECT count(*) FROM mf_app.source_binaries WHERE project_id=%s AND source_revision=%s",
                            (project_id, revision)).fetchone()[0] == 0

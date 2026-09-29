"""Pure validation checks; the PostgreSQL behavior lives in the opt-in module."""

import hashlib
import warnings
from contextlib import nullcontext
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
import pytest

from mirofish_knowledge.contracts import KnowledgeScope, Layer, OntologySpec, SourceEnvelope
from mirofish_knowledge.bindings import BindingRecord, ScopeBindingStore, _stored
from mirofish_knowledge.operations import CompletionReceipt, Conflict, Ledger, StorageError, Tombstoned, _error_code, _fingerprint, _read_lock_key, _receipt, request_fingerprint
from mirofish_knowledge.provider import _request_fingerprint


def _request():
    scope = KnowledgeScope(workspace_id=uuid4(), project_id=uuid4(), graph_id=uuid4(), layer=Layer.source)
    ontology = OntologySpec(revision=uuid4(), entity_types=({"name": "Person", "description": "A person"},), edge_types=())
    content = "Synthetic source"
    source = SourceEnvelope(source_revision=uuid4(), source_sha256=hashlib.sha256(content.encode()).hexdigest(),
                            ontology_revision=ontology.revision, operation_id=uuid4(), source_kind="document",
                            content=content, source_name="fixture", recorded_at=datetime.now(timezone.utc))
    return scope, source, ontology


def test_fingerprint_matches_existing_provider_and_rejects_unvalidated_copies():
    scope, source, ontology = _request()
    assert request_fingerprint(scope, source, ontology) == _request_fingerprint(scope, source, ontology)
    with pytest.raises(ValueError):
        request_fingerprint(scope, source.model_copy(update={"recorded_at": datetime.now()}), ontology)
    with pytest.raises(ValueError):
        request_fingerprint(scope, source, ontology.model_copy(update={"revision": uuid4()}))


def test_receipt_validation_is_bounded_and_identity_bound():
    scope, source, ontology = _request()
    fingerprint = request_fingerprint(scope, source, ontology)
    receipt = CompletionReceipt(scope.group_id, scope.episode_uuid(source.operation_id), fingerprint, (uuid4(),))
    assert _receipt(receipt, scope, source.operation_id, fingerprint) == receipt
    for changed in (
        CompletionReceipt("wrong", receipt.episode_id, fingerprint),
        CompletionReceipt(scope.group_id, uuid4(), fingerprint),
        CompletionReceipt(scope.group_id, receipt.episode_id, "0" * 64),
        CompletionReceipt(scope.group_id, receipt.episode_id, fingerprint, (uuid4(),) * 101),
    ):
        with pytest.raises(ValueError):
            _receipt(changed, scope, source.operation_id, fingerprint)
    with pytest.raises(ValueError):
        _receipt({**receipt.json_value(), "secret": "payload"}, scope, source.operation_id, fingerprint)


def test_input_validation_precedes_connection():
    scope, source, ontology = _request()
    ledger = Ledger(lambda: (_ for _ in ()).throw(AssertionError("connected")))
    with pytest.raises(ValueError):
        ledger.admit(scope, source.operation_id, "BAD")
    with pytest.raises(ValueError):
        ledger.mark_uncertain(scope, source.operation_id, uuid4(), "private: traceback")
    with pytest.raises(ValueError):
        ledger.claim(scope.model_copy(update={"layer": "invalid"}), source.operation_id)
    with pytest.raises(ValueError):
        ledger.complete(scope, source.operation_id, uuid4(), {"group_id": "wrong", "episode_id": str(scope.episode_uuid(source.operation_id)), "fingerprint": "a" * 64, "evidence_ids": []})
    with pytest.raises(ValueError):
        ledger.complete(scope, source.operation_id, uuid4(),
                        CompletionReceipt(scope.group_id, scope.episode_uuid(source.operation_id), "a" * 64, None))
    with pytest.raises(ValueError):
        ledger.complete(scope, source.operation_id, uuid4(),
                        CompletionReceipt(scope.group_id, scope.episode_uuid(source.operation_id), "a" * 64, []))
    with pytest.raises(ValueError):
        _fingerprint("A" * 64)
    with pytest.raises(ValueError):
        _error_code("Bad Code")


def test_binding_input_domains_and_scope_copies_precede_connection():
    one, _, _ = _request()
    called = []
    store = ScopeBindingStore(lambda: called.append(True))
    for principal in ("", " ", "a\n", "é", "x" * 129, None):
        with pytest.raises(ValueError):
            store.bind(principal, "graph-1", one)
        with pytest.raises(ValueError):
            store.resolve(principal, "graph-1")
    for display in ("", "a/b", "é", "x" * 129, None):
        with pytest.raises(ValueError):
            store.bind("owner!+@", display, one)
        with pytest.raises(ValueError):
            store.resolve("owner!+@", display)
    for invalid in (one.model_copy(update={"layer": "private-invalid"}),
                    one.model_copy(update={"workspace_id": "private-invalid"}),
                    one.model_copy(update={"schema_version": True}),
                    "private-invalid"):
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            with pytest.raises(ValueError) as error:
                store.bind("owner!+@", "graph-1", invalid)
        assert "private-invalid" not in str(error.value)
        assert all("private-invalid" not in str(item.message) for item in captured)
        assert not captured
    assert called == []


def test_binding_record_validates_stored_canonical_scope_and_tombstone():
    one, _, _ = _request()
    now = datetime.now(timezone.utc)
    row = ("owner!+@", "graph-1", one.group_id, now, one.model_dump(mode="json"), False)
    record = _stored(row)
    assert isinstance(record, BindingRecord) and record.scope == one
    assert record.created_at == now and record.principal == "owner!+@"
    assert record.scope is not one
    with pytest.raises(Tombstoned):
        _stored((*row[:-1], True))
    for changed in (
        ("other", "graph-1", "private-group", now, row[4], False),
        ("owner!+@", "graph-1", one.group_id, datetime.now(), row[4], False),
        ("owner!+@", "graph-1", one.group_id, now, {**row[4], "layer": "private-invalid"}, False),
        ("owner!+@", "graph-1", one.group_id, now, {**row[4], "extra": "private"}, False),
        ("owner!+@", "private/bad", one.group_id, now, row[4], False),
    ):
        with pytest.raises(Conflict) as error:
            _stored(changed)
        assert "private" not in str(error.value)


def test_binding_database_failure_uses_fixed_private_error():
    class BrokenConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def transaction(self):
            return nullcontext()

        def execute(self, query, params=None):
            if query.startswith("SELECT b.principal"):
                raise psycopg.errors.UndefinedTable("private database diagnostic")
            return self

    with pytest.raises(StorageError) as error:
        ScopeBindingStore(BrokenConnection).resolve("owner!+@", "graph-1")
    assert "private" not in str(error.value)


def test_read_lock_key_is_stable_signed_and_scope_distinct():
    one, _, _ = _request()
    other, _, _ = _request()
    key = _read_lock_key(one.group_id)
    assert key == _read_lock_key(one.group_id)
    assert -(1 << 63) <= key < (1 << 63)
    assert key != _read_lock_key(other.group_id)


@pytest.mark.parametrize("control", [KeyboardInterrupt, SystemExit], ids=["keyboard", "system_exit"])
def test_read_scope_cleanup_preserves_process_control(control):
    one, _, _ = _request()

    class Connection:
        autocommit = False
        closed = False

        def transaction(self):
            return nullcontext()

        def execute(self, query, params=None):
            if query.startswith("SELECT pg_advisory_unlock_shared"):
                raise psycopg.Error("private failed connection")
            answer = (1,) if query == "SELECT 1" else (True,) if query.startswith("SELECT pg_try_advisory_lock_shared") else None
            return type("Result", (), {"fetchone": lambda self: answer})()

        def close(self):
            self.closed = True

    connection = Connection()
    ledger = Ledger(lambda: connection)
    ledger._scope = lambda conn, scope: None
    ledger._admission = lambda conn, scope: None
    with pytest.raises(control):
        with ledger.read_scope(one):
            raise control()
    assert connection.closed


def test_read_scope_cleanup_failure_is_fixed_storage_error():
    one, _, _ = _request()

    class Connection:
        autocommit = False
        closed = False

        def transaction(self):
            return nullcontext()

        def execute(self, query, params=None):
            if query.startswith("SELECT pg_advisory_unlock_shared"):
                raise psycopg.Error("private failed connection")
            answer = (1,) if query == "SELECT 1" else (True,) if query.startswith("SELECT pg_try_advisory_lock_shared") else None
            return type("Result", (), {"fetchone": lambda self: answer})()

        def close(self):
            self.closed = True

    connection = Connection()
    ledger = Ledger(lambda: connection)
    ledger._scope = lambda conn, scope: None
    ledger._admission = lambda conn, scope: None
    with pytest.raises(StorageError) as error:
        with ledger.read_scope(one):
            pass
    assert "private" not in str(error.value) and connection.closed

"""Synchronous PostgreSQL admission ledger for scoped knowledge writes.

The caller authorizes the scope. This module only enforces operation identity and
one outstanding writer per scope; it never dispatches a provider operation.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from importlib.resources import files
from typing import Callable, Iterator, Mapping
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from .contracts import KnowledgeScope, OntologySpec, SourceEnvelope


class LedgerError(RuntimeError):
    """Public errors have stable text and never include database diagnostics."""


class Conflict(LedgerError):
    pass


class Busy(LedgerError):
    pass


class StaleAttempt(LedgerError):
    pass


class InvalidTransition(LedgerError):
    pass


class Tombstoned(LedgerError):
    pass


class NotFound(LedgerError):
    pass


class MigrationMismatch(LedgerError):
    pass


class StorageError(LedgerError):
    pass


class OperationState(StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    uncertain = "uncertain"
    cancelled = "cancelled"
    failed_no_effect = "failed_no_effect"


@dataclass(frozen=True)
class ScopeRecord:
    group_id: str
    tombstoned: bool
    created_at: datetime


@dataclass(frozen=True)
class CompletionReceipt:
    group_id: str
    episode_id: UUID
    fingerprint: str
    evidence_ids: tuple[UUID, ...] = ()

    def json_value(self) -> dict[str, object]:
        return {
            "group_id": self.group_id,
            "episode_id": str(self.episode_id),
            "fingerprint": self.fingerprint,
            "evidence_ids": [str(value) for value in self.evidence_ids],
        }


@dataclass(frozen=True)
class OperationRecord:
    group_id: str
    operation_id: UUID
    fingerprint: str
    state: OperationState
    created_at: datetime
    updated_at: datetime
    current_attempt: UUID | None
    receipt: CompletionReceipt | None
    error_code: str | None


@dataclass(frozen=True)
class Claim:
    operation: OperationRecord
    attempt_id: UUID


_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_MIGRATION = "migrations/0001_knowledge_operations.sql"


def _validated_scope(scope: KnowledgeScope) -> KnowledgeScope:
    if not isinstance(scope, KnowledgeScope):
        raise ValueError("validated KnowledgeScope required")
    # model_copy(update=...) bypasses Pydantic validation.
    return KnowledgeScope.model_validate(scope.model_dump())


def _uuid(value: UUID, name: str) -> UUID:
    if not isinstance(value, UUID):
        raise ValueError(f"{name} must be a UUID")
    return value


def _fingerprint(value: str) -> str:
    if not isinstance(value, str) or not _FINGERPRINT.fullmatch(value):
        raise ValueError("fingerprint must be lowercase SHA256 hex")
    return value


def _error_code(value: str) -> str:
    if not isinstance(value, str) or not _ERROR_CODE.fullmatch(value):
        raise ValueError("invalid stable error code")
    return value


def request_fingerprint(scope: KnowledgeScope, source: SourceEnvelope, ontology: OntologySpec) -> str:
    """Match provider._request_fingerprint's canonical request fields exactly."""
    scope = _validated_scope(scope)
    if not isinstance(source, SourceEnvelope) or not isinstance(ontology, OntologySpec):
        raise ValueError("validated source and ontology required")
    source = SourceEnvelope.model_validate(source.model_dump())
    ontology = OntologySpec.model_validate(ontology.model_dump())
    if source.ontology_revision != ontology.revision:
        raise ValueError("source ontology revision differs")
    payload = {
        "scope": scope.model_dump(mode="json"),
        "source": source.model_dump(mode="json"),
        "ontology": ontology.model_dump(mode="json"),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _receipt(value: CompletionReceipt | Mapping[str, object], scope: KnowledgeScope,
             operation_id: UUID, fingerprint: str) -> CompletionReceipt:
    if isinstance(value, CompletionReceipt):
        item = value
    elif isinstance(value, Mapping) and set(value) == {"group_id", "episode_id", "fingerprint", "evidence_ids"}:
        try:
            evidence = value["evidence_ids"]
            if not isinstance(evidence, (list, tuple)) or len(evidence) > 100:
                raise ValueError
            item = CompletionReceipt(
                group_id=value["group_id"],
                episode_id=UUID(value["episode_id"]) if isinstance(value["episode_id"], str) else value["episode_id"],
                fingerprint=value["fingerprint"],
                evidence_ids=tuple(UUID(v) if isinstance(v, str) else v for v in evidence),
            )
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            raise ValueError("invalid completion receipt") from exc
    else:
        raise ValueError("invalid completion receipt")
    if (not isinstance(item.group_id, str) or item.group_id != scope.group_id
            or not isinstance(item.episode_id, UUID) or item.episode_id != scope.episode_uuid(operation_id)
            or not isinstance(item.fingerprint, str) or item.fingerprint != fingerprint
            or not isinstance(item.evidence_ids, tuple) or len(item.evidence_ids) > 100
            or any(not isinstance(v, UUID) for v in item.evidence_ids)
            or len(set(item.evidence_ids)) != len(item.evidence_ids)):
        raise ValueError("completion receipt does not match operation")
    if len(json.dumps(item.json_value(), sort_keys=True, separators=(",", ":")).encode("utf-8")) > 65536:
        raise ValueError("completion receipt exceeds 64KiB")
    return item


def _row(row: tuple) -> OperationRecord:
    group_id, operation_id, fingerprint, state, created_at, updated_at, current_attempt, receipt, error_code = row
    item = None
    if receipt is not None:
        item = CompletionReceipt(receipt["group_id"], UUID(receipt["episode_id"]),
                                 receipt["fingerprint"], tuple(UUID(v) for v in receipt["evidence_ids"]))
    return OperationRecord(group_id, operation_id, fingerprint.strip(), OperationState(state),
                           created_at, updated_at, current_attempt, item, error_code)


_COLUMNS = "group_id, operation_id, fingerprint, state, created_at, updated_at, current_attempt, receipt, error_code"


@contextmanager
def _transaction(factory: Callable[[], psycopg.Connection]) -> Iterator[psycopg.Connection]:
    try:
        with factory() as conn:
            with conn.transaction():
                conn.execute("SET LOCAL statement_timeout = '5s'")
                conn.execute("SET LOCAL lock_timeout = '2s'")
                conn.execute("SET LOCAL idle_in_transaction_session_timeout = '10s'")
                yield conn
    except psycopg.Error as exc:
        raise StorageError("knowledge ledger storage failure") from None


def _schema_checksum(connection: psycopg.Connection) -> str:
    """Fingerprint owned tables, columns, constraints and indexes from catalogs."""
    catalog = {
        "tables": connection.execute(
            "SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'mf_knowledge' AND c.relkind = 'r' ORDER BY c.relname"
        ).fetchall(),
        "columns": connection.execute(
            "SELECT c.relname, a.attnum, a.attname, format_type(a.atttypid, a.atttypmod), "
            "a.attnotnull, pg_get_expr(d.adbin, d.adrelid) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_attribute a ON a.attrelid = c.oid "
            "LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum "
            "WHERE n.nspname = 'mf_knowledge' AND c.relkind = 'r' AND a.attnum > 0 "
            "AND NOT a.attisdropped ORDER BY c.relname, a.attnum"
        ).fetchall(),
        "constraints": connection.execute(
            "SELECT c.relname, x.conname, x.contype, pg_get_constraintdef(x.oid, true), "
            "x.condeferrable, x.condeferred FROM pg_constraint x "
            "JOIN pg_class c ON c.oid = x.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'mf_knowledge' AND c.relkind = 'r' "
            "ORDER BY c.relname, x.conname"
        ).fetchall(),
        "indexes": connection.execute(
            "SELECT t.relname, i.relname, pg_get_indexdef(i.oid) "
            "FROM pg_index x JOIN pg_class t ON t.oid = x.indrelid "
            "JOIN pg_class i ON i.oid = x.indexrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = 'mf_knowledge' AND t.relkind = 'r' "
            "ORDER BY t.relname, i.relname"
        ).fetchall(),
    }
    canonical = json.dumps(catalog, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def migrate(connection: psycopg.Connection) -> None:
    """Explicitly install migration 1 on an empty disposable schema.

    The caller supplies a migration-owner connection and retains its ownership.
    """
    sql = files("mirofish_knowledge").joinpath(_MIGRATION).read_text(encoding="utf-8")
    checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    try:
        with connection.transaction():
            connection.execute("SET LOCAL statement_timeout = '10s'")
            connection.execute("SET LOCAL lock_timeout = '5s'")
            connection.execute("SET LOCAL search_path = pg_catalog")
            connection.execute("SELECT pg_advisory_xact_lock(720103, 1)")
            exists = connection.execute("SELECT to_regnamespace('mf_knowledge') IS NOT NULL").fetchone()[0]
            if exists:
                if not connection.execute("SELECT to_regclass('mf_knowledge.schema_migrations') IS NOT NULL").fetchone()[0]:
                    raise MigrationMismatch("knowledge schema exists without migration record")
                rows = connection.execute("SELECT version, checksum, schema_checksum FROM mf_knowledge.schema_migrations").fetchall()
                if len(rows) != 1 or rows[0][0:2] != (1, checksum):
                    raise MigrationMismatch("knowledge migration version or checksum mismatch")
                if rows[0][2] != _schema_checksum(connection):
                    raise MigrationMismatch("knowledge schema shape mismatch")
                return
            connection.execute(sql)
            shape = _schema_checksum(connection)
            connection.execute("INSERT INTO mf_knowledge.schema_migrations(version, checksum, schema_checksum) VALUES (1, %s, %s)", (checksum, shape))
    except psycopg.Error:
        raise StorageError("knowledge migration storage failure") from None


class Ledger:
    def __init__(self, connection_factory: Callable[[], psycopg.Connection]):
        if not callable(connection_factory):
            raise ValueError("connection factory required")
        self._connect = connection_factory

    @staticmethod
    def _scope(conn: psycopg.Connection, scope: KnowledgeScope, *, create: bool = False,
               allow_tombstone: bool = False) -> ScopeRecord:
        canonical = scope.model_dump(mode="json")
        if create:
            conn.execute("INSERT INTO mf_knowledge.scopes(group_id, canonical_scope) VALUES (%s, %s) ON CONFLICT (group_id) DO NOTHING",
                         (scope.group_id, Jsonb(canonical)))
        row = conn.execute("SELECT canonical_scope, tombstoned, created_at FROM mf_knowledge.scopes WHERE group_id = %s FOR UPDATE",
                           (scope.group_id,)).fetchone()
        if row is None:
            raise NotFound("knowledge scope not found")
        if row[0] != canonical:
            raise Conflict("knowledge scope identity conflict")
        if row[1] and not allow_tombstone:
            raise Tombstoned("knowledge scope tombstoned")
        return ScopeRecord(scope.group_id, row[1], row[2])

    @staticmethod
    def _operation(conn: psycopg.Connection, scope: KnowledgeScope, operation_id: UUID) -> OperationRecord:
        row = conn.execute(f"SELECT {_COLUMNS} FROM mf_knowledge.operations WHERE group_id = %s AND operation_id = %s FOR UPDATE",
                           (scope.group_id, operation_id)).fetchone()
        if row is None:
            raise NotFound("knowledge operation not found")
        return _row(row)

    @staticmethod
    def _admission(conn: psycopg.Connection, scope: KnowledgeScope) -> tuple[UUID, UUID] | None:
        return conn.execute("SELECT operation_id, attempt_id FROM mf_knowledge.scope_admissions WHERE group_id = %s",
                            (scope.group_id,)).fetchone()

    @staticmethod
    def _matching_attempt(conn: psycopg.Connection, scope: KnowledgeScope, operation: OperationRecord,
                          attempt_id: UUID) -> None:
        if operation.current_attempt != attempt_id or Ledger._admission(conn, scope) != (operation.operation_id, attempt_id):
            raise StaleAttempt("knowledge attempt is stale")

    @staticmethod
    def _updated(conn: psycopg.Connection, scope: KnowledgeScope, operation_id: UUID) -> OperationRecord:
        return Ledger._operation(conn, scope, operation_id)

    def register_scope(self, scope: KnowledgeScope) -> ScopeRecord:
        scope = _validated_scope(scope)
        with _transaction(self._connect) as conn:
            return self._scope(conn, scope, create=True)

    def admit(self, scope: KnowledgeScope, operation_id: UUID, fingerprint: str) -> OperationRecord:
        scope, operation_id, fingerprint = _validated_scope(scope), _uuid(operation_id, "operation_id"), _fingerprint(fingerprint)
        with _transaction(self._connect) as conn:
            self._scope(conn, scope, create=True)
            conn.execute("INSERT INTO mf_knowledge.operations(group_id, operation_id, fingerprint, state) VALUES (%s, %s, %s, 'pending') ON CONFLICT (group_id, operation_id) DO NOTHING",
                         (scope.group_id, operation_id, fingerprint))
            operation = self._operation(conn, scope, operation_id)
            if operation.fingerprint != fingerprint:
                raise Conflict("knowledge operation fingerprint conflict")
            return operation

    def get(self, scope: KnowledgeScope, operation_id: UUID) -> OperationRecord:
        scope, operation_id = _validated_scope(scope), _uuid(operation_id, "operation_id")
        with _transaction(self._connect) as conn:
            self._scope(conn, scope, allow_tombstone=True)
            return self._operation(conn, scope, operation_id)

    def claim(self, scope: KnowledgeScope, operation_id: UUID) -> Claim:
        scope, operation_id = _validated_scope(scope), _uuid(operation_id, "operation_id")
        with _transaction(self._connect) as conn:
            self._scope(conn, scope)
            operation = self._operation(conn, scope, operation_id)
            if self._admission(conn, scope) is not None or operation.state in (OperationState.running, OperationState.uncertain):
                raise Busy("knowledge scope write admission busy")
            if operation.state not in (OperationState.pending, OperationState.failed_no_effect):
                raise InvalidTransition("knowledge operation cannot be claimed")
            token = uuid4()
            conn.execute("INSERT INTO mf_knowledge.attempts(group_id, operation_id, attempt_id) VALUES (%s, %s, %s)",
                         (scope.group_id, operation_id, token))
            conn.execute("INSERT INTO mf_knowledge.scope_admissions(group_id, operation_id, attempt_id) VALUES (%s, %s, %s)",
                         (scope.group_id, operation_id, token))
            conn.execute("UPDATE mf_knowledge.operations SET state = 'running', current_attempt = %s, receipt = NULL, error_code = NULL, updated_at = now() WHERE group_id = %s AND operation_id = %s",
                         (token, scope.group_id, operation_id))
            return Claim(self._updated(conn, scope, operation_id), token)

    def heartbeat(self, scope: KnowledgeScope, operation_id: UUID, attempt_id: UUID) -> OperationRecord:
        scope, operation_id, attempt_id = _validated_scope(scope), _uuid(operation_id, "operation_id"), _uuid(attempt_id, "attempt_id")
        with _transaction(self._connect) as conn:
            self._scope(conn, scope, allow_tombstone=True)
            operation = self._operation(conn, scope, operation_id)
            self._matching_attempt(conn, scope, operation, attempt_id)
            if operation.state != OperationState.running:
                raise InvalidTransition("knowledge operation is not running")
            conn.execute("UPDATE mf_knowledge.attempts SET heartbeat_at = now() WHERE group_id = %s AND operation_id = %s AND attempt_id = %s",
                         (scope.group_id, operation_id, attempt_id))
            conn.execute("UPDATE mf_knowledge.operations SET updated_at = now() WHERE group_id = %s AND operation_id = %s",
                         (scope.group_id, operation_id))
            return self._updated(conn, scope, operation_id)

    def complete(self, scope: KnowledgeScope, operation_id: UUID, attempt_id: UUID,
                 receipt: CompletionReceipt | Mapping[str, object]) -> OperationRecord:
        scope, operation_id, attempt_id = _validated_scope(scope), _uuid(operation_id, "operation_id"), _uuid(attempt_id, "attempt_id")
        proposed_fingerprint = receipt.fingerprint if isinstance(receipt, CompletionReceipt) else receipt.get("fingerprint") if isinstance(receipt, Mapping) else None
        valid = _receipt(receipt, scope, operation_id, _fingerprint(proposed_fingerprint))
        with _transaction(self._connect) as conn:
            self._scope(conn, scope, allow_tombstone=True)
            operation = self._operation(conn, scope, operation_id)
            if operation.current_attempt != attempt_id:
                raise StaleAttempt("knowledge attempt is stale")
            if operation.state == OperationState.completed:
                if operation.receipt != valid:
                    raise Conflict("knowledge completion receipt conflict")
                return operation
            if valid.fingerprint != operation.fingerprint:
                raise ValueError("completion receipt does not match operation")
            self._matching_attempt(conn, scope, operation, attempt_id)
            if operation.state != OperationState.running:
                raise InvalidTransition("knowledge operation cannot complete")
            conn.execute("UPDATE mf_knowledge.attempts SET heartbeat_at = now(), finished_at = now(), outcome = 'completed', error_code = NULL WHERE group_id = %s AND operation_id = %s AND attempt_id = %s",
                         (scope.group_id, operation_id, attempt_id))
            conn.execute("UPDATE mf_knowledge.operations SET state = 'completed', receipt = %s, error_code = NULL, updated_at = now() WHERE group_id = %s AND operation_id = %s",
                         (Jsonb(valid.json_value()), scope.group_id, operation_id))
            conn.execute("DELETE FROM mf_knowledge.scope_admissions WHERE group_id = %s AND operation_id = %s AND attempt_id = %s",
                         (scope.group_id, operation_id, attempt_id))
            return self._updated(conn, scope, operation_id)

    def mark_uncertain(self, scope: KnowledgeScope, operation_id: UUID, attempt_id: UUID,
                       error_code: str) -> OperationRecord:
        scope, operation_id, attempt_id, error_code = _validated_scope(scope), _uuid(operation_id, "operation_id"), _uuid(attempt_id, "attempt_id"), _error_code(error_code)
        with _transaction(self._connect) as conn:
            self._scope(conn, scope, allow_tombstone=True)
            operation = self._operation(conn, scope, operation_id)
            self._matching_attempt(conn, scope, operation, attempt_id)
            if operation.state not in (OperationState.running, OperationState.uncertain):
                raise InvalidTransition("knowledge operation cannot become uncertain")
            if operation.state == OperationState.uncertain:
                if operation.error_code != error_code:
                    raise Conflict("knowledge uncertainty classification conflict")
                return operation
            conn.execute("UPDATE mf_knowledge.attempts SET heartbeat_at = now(), finished_at = now(), outcome = 'uncertain', error_code = %s WHERE group_id = %s AND operation_id = %s AND attempt_id = %s",
                         (error_code, scope.group_id, operation_id, attempt_id))
            conn.execute("UPDATE mf_knowledge.operations SET state = 'uncertain', error_code = %s, updated_at = now() WHERE group_id = %s AND operation_id = %s",
                         (error_code, scope.group_id, operation_id))
            return self._updated(conn, scope, operation_id)

    def fail_no_effect(self, scope: KnowledgeScope, operation_id: UUID, attempt_id: UUID,
                       error_code: str) -> OperationRecord:
        scope, operation_id, attempt_id, error_code = _validated_scope(scope), _uuid(operation_id, "operation_id"), _uuid(attempt_id, "attempt_id"), _error_code(error_code)
        with _transaction(self._connect) as conn:
            self._scope(conn, scope, allow_tombstone=True)
            operation = self._operation(conn, scope, operation_id)
            self._matching_attempt(conn, scope, operation, attempt_id)
            if operation.state != OperationState.running:
                raise InvalidTransition("no-effect classification requires running attempt")
            conn.execute("UPDATE mf_knowledge.attempts SET heartbeat_at = now(), finished_at = now(), outcome = 'failed_no_effect', error_code = %s WHERE group_id = %s AND operation_id = %s AND attempt_id = %s",
                         (error_code, scope.group_id, operation_id, attempt_id))
            conn.execute("UPDATE mf_knowledge.operations SET state = 'failed_no_effect', error_code = %s, updated_at = now() WHERE group_id = %s AND operation_id = %s",
                         (error_code, scope.group_id, operation_id))
            conn.execute("DELETE FROM mf_knowledge.scope_admissions WHERE group_id = %s AND operation_id = %s AND attempt_id = %s",
                         (scope.group_id, operation_id, attempt_id))
            return self._updated(conn, scope, operation_id)

    def cancel_pending(self, scope: KnowledgeScope, operation_id: UUID) -> OperationRecord:
        scope, operation_id = _validated_scope(scope), _uuid(operation_id, "operation_id")
        with _transaction(self._connect) as conn:
            self._scope(conn, scope, allow_tombstone=True)
            operation = self._operation(conn, scope, operation_id)
            if operation.state != OperationState.pending:
                raise InvalidTransition("only pending knowledge operations can be cancelled")
            conn.execute("UPDATE mf_knowledge.operations SET state = 'cancelled', updated_at = now() WHERE group_id = %s AND operation_id = %s",
                         (scope.group_id, operation_id))
            return self._updated(conn, scope, operation_id)

    def tombstone_scope(self, scope: KnowledgeScope) -> ScopeRecord:
        scope = _validated_scope(scope)
        with _transaction(self._connect) as conn:
            record = self._scope(conn, scope, allow_tombstone=True)
            if not record.tombstoned:
                conn.execute("UPDATE mf_knowledge.scopes SET tombstoned = true WHERE group_id = %s", (scope.group_id,))
            return ScopeRecord(record.group_id, True, record.created_at)

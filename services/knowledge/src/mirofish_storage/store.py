"""Versioned PostgreSQL metadata store; migration is always explicit."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from importlib.resources import files
from typing import Callable, Iterator
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from .validation import (InvalidProject, canonical_payload, display_id,
                         principal_id, uuid_value)


class StoreError(RuntimeError):
    code = "storage_error"
    def __init__(self):
        super().__init__(self.code)


class Conflict(StoreError):
    code = "conflict"


class NotFound(StoreError):
    code = "not_found"


class MigrationMismatch(StoreError):
    code = "migration_mismatch"


class StorageError(StoreError):
    code = "storage_error"


@dataclass(frozen=True)
class ProjectRecord:
    principal: str
    workspace_id: UUID
    project_id: UUID
    display_id: str
    revision: int
    _snapshot_json: str
    _evidence_json: str
    digest: str
    created_at: datetime

    @property
    def snapshot(self) -> dict:
        return json.loads(self._snapshot_json)

    @property
    def evidence(self) -> tuple[dict, ...]:
        return tuple(json.loads(self._evidence_json))


_SELECT = ("p.principal, p.workspace_id, p.project_id, p.display_id, "
           "r.revision, r.snapshot, r.evidence, r.digest, r.created_at ")
_JOIN = "FROM mf_app.projects p JOIN mf_app.project_revisions r ON r.project_id = p.project_id "


def _record(row) -> ProjectRecord:
    principal, workspace, project, display, revision, snapshot, evidence, digest, created = row
    try:
        copied_snapshot, copied_evidence, actual = canonical_payload(snapshot, evidence, display)
    except InvalidProject:
        raise StorageError() from None
    if actual != digest.strip():
        raise StorageError()
    return ProjectRecord(principal, workspace, project, display, revision,
                         json.dumps(copied_snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
                         json.dumps(copied_evidence, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
                         actual, created)


@contextmanager
def _transaction(factory: Callable[[], psycopg.Connection]) -> Iterator[psycopg.Connection]:
    try:
        with factory() as conn:
            with conn.transaction():
                conn.execute("SET LOCAL statement_timeout = '5s'")
                conn.execute("SET LOCAL lock_timeout = '2s'")
                conn.execute("SET LOCAL idle_in_transaction_session_timeout = '10s'")
                conn.execute("SET LOCAL TIME ZONE 'UTC'")
                yield conn
    except psycopg.Error:
        raise StorageError() from None


def _catalog(connection) -> str:
    """Hash the owned namespace's complete table/column/constraint/index shape."""
    queries = (
        "SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_app' AND c.relkind IN ('r','p','v','m','f') ORDER BY c.relname",
        "SELECT c.relname, a.attnum, a.attname, format_type(a.atttypid,a.atttypmod), a.attnotnull, pg_get_expr(d.adbin,d.adrelid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname='mf_app' AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum",
        "SELECT c.relname,x.conname,x.contype,pg_get_constraintdef(x.oid,true),x.condeferrable,x.condeferred FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_app' ORDER BY c.relname,x.conname",
        "SELECT t.relname,i.relname,pg_get_indexdef(i.oid) FROM pg_index x JOIN pg_class t ON t.oid=x.indrelid JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='mf_app' ORDER BY t.relname,i.relname",
        "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_app' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname",
    )
    rows = [connection.execute(query).fetchall() for query in queries]
    return hashlib.sha256(json.dumps(rows, default=str, separators=(",", ":")).encode()).hexdigest()


def migrate(connection: psycopg.Connection) -> None:
    """Apply sequential owned migrations after checking the current catalog head."""
    migrations = []
    for version, filename in ((1, "0001_project_revisions.sql"),
                              (2, "0002_source_evidence.sql"),
                              (3, "0003_research_imports.sql")):
        sql = files("mirofish_storage").joinpath("migrations", filename).read_text("utf-8")
        migrations.append((version, sql, hashlib.sha256(sql.encode("utf-8")).hexdigest()))
    try:
        with connection.transaction():
            connection.execute("SET LOCAL statement_timeout = '10s'")
            connection.execute("SET LOCAL lock_timeout = '3s'")
            connection.execute("SET LOCAL idle_in_transaction_session_timeout = '15s'")
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (72196248041404,))
            exists = connection.execute("SELECT 1 FROM pg_namespace WHERE nspname='mf_app'").fetchone()
            if not exists:
                applied = 0
            else:
                if connection.execute("SELECT to_regclass('mf_app.schema_migrations')").fetchone()[0] is None:
                    raise MigrationMismatch()
                rows = connection.execute("SELECT version,sql_sha256,catalog_sha256 FROM mf_app.schema_migrations ORDER BY version").fetchall()
                if not rows or len(rows) > len(migrations):
                    raise MigrationMismatch()
                for index, row in enumerate(rows):
                    if row[0] != migrations[index][0] or row[1].strip() != migrations[index][2]:
                        raise MigrationMismatch()
                if rows[-1][2].strip() != _catalog(connection):
                    raise MigrationMismatch()
                applied = len(rows)
            for version, sql, checksum in migrations[applied:]:
                connection.execute(sql)
                shape = _catalog(connection)
                connection.execute("INSERT INTO mf_app.schema_migrations VALUES (%s,%s,%s)",
                                   (version, checksum, shape))
    except psycopg.Error:
        raise MigrationMismatch() from None


def _limit(value: object, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise InvalidProject()
    return value


class ProjectStore:
    def __init__(self, connection_factory: Callable[[], psycopg.Connection]):
        self._factory = connection_factory

    def create(self, principal, workspace_id, project_id, display_id, snapshot, evidence=()) -> ProjectRecord:
        principal = principal_id(principal)
        workspace_id, project_id = uuid_value(workspace_id), uuid_value(project_id)
        display_id = display_id_fn(display_id)
        snapshot, evidence, digest = canonical_payload(snapshot, evidence, display_id)
        with _transaction(self._factory) as conn:
            inserted = conn.execute(
                "INSERT INTO mf_app.projects (project_id,principal,workspace_id,display_id,current_revision) "
                "VALUES (%s,%s,%s,%s,1) ON CONFLICT DO NOTHING RETURNING project_id",
                (project_id, principal, workspace_id, display_id)).fetchone()
            if inserted:
                conn.execute("INSERT INTO mf_app.project_revisions (project_id,revision,snapshot,evidence,digest) VALUES (%s,1,%s,%s,%s)",
                             (project_id, Jsonb(snapshot), Jsonb(evidence), digest))
            else:
                row = conn.execute("SELECT principal,workspace_id,display_id,current_revision FROM mf_app.projects WHERE project_id=%s FOR UPDATE", (project_id,)).fetchone()
                if row is None or row[:3] != (principal, workspace_id, display_id):
                    raise Conflict()
                prior = conn.execute("SELECT digest FROM mf_app.project_revisions WHERE project_id=%s AND revision=1", (project_id,)).fetchone()
                if prior is None or prior[0].strip() != digest:
                    raise Conflict()
            row = conn.execute("SELECT " + _SELECT + _JOIN + "WHERE p.project_id=%s AND r.revision=1", (project_id,)).fetchone()
            return _record(row)

    def get(self, principal, project_id, revision=None) -> ProjectRecord:
        principal, project_id = principal_id(principal), uuid_value(project_id)
        if revision is not None:
            _limit(revision, 2_147_483_647)
        with _transaction(self._factory) as conn:
            row = conn.execute("SELECT " + _SELECT + _JOIN +
                "WHERE p.principal=%s AND p.project_id=%s AND r.revision=" +
                ("p.current_revision" if revision is None else "%s"),
                (principal, project_id) if revision is None else (principal, project_id, revision)).fetchone()
            if row is None:
                raise NotFound()
            return _record(row)

    def list_projects(self, principal, workspace_id, limit=50) -> list[ProjectRecord]:
        principal, workspace_id = principal_id(principal), uuid_value(workspace_id)
        limit = _limit(limit, 200)
        with _transaction(self._factory) as conn:
            rows = conn.execute("SELECT " + _SELECT + _JOIN +
                "WHERE p.principal=%s AND p.workspace_id=%s AND r.revision=p.current_revision "
                "ORDER BY p.created_at DESC,p.project_id LIMIT %s", (principal, workspace_id, limit)).fetchall()
            return [_record(row) for row in rows]

    def history(self, principal, project_id, after_revision=0, limit=50) -> list[ProjectRecord]:
        principal, project_id = principal_id(principal), uuid_value(project_id)
        if type(after_revision) is not int or not 0 <= after_revision < 2_147_483_647:
            raise InvalidProject()
        limit = _limit(limit, 200)
        with _transaction(self._factory) as conn:
            if conn.execute("SELECT 1 FROM mf_app.projects WHERE principal=%s AND project_id=%s", (principal, project_id)).fetchone() is None:
                raise NotFound()
            rows = conn.execute("SELECT " + _SELECT + _JOIN +
                "WHERE p.principal=%s AND p.project_id=%s AND r.revision>%s ORDER BY r.revision LIMIT %s",
                (principal, project_id, after_revision, limit)).fetchall()
            return [_record(row) for row in rows]

    def update(self, principal, project_id, expected_revision, snapshot, evidence=()) -> ProjectRecord:
        principal, project_id = principal_id(principal), uuid_value(project_id)
        expected_revision = _limit(expected_revision, 2_147_483_646)
        # Validate and copy caller-owned values before opening any connection.
        if type(snapshot) is not dict or "project_id" not in snapshot:
            raise InvalidProject()
        proposed_display = display_id(snapshot["project_id"])
        snapshot, evidence, digest = canonical_payload(snapshot, evidence, proposed_display)
        # Resolve the persisted display identity with a scoped read.
        with _transaction(self._factory) as conn:
            row = conn.execute("SELECT display_id FROM mf_app.projects WHERE principal=%s AND project_id=%s", (principal, project_id)).fetchone()
        if row is None:
            raise NotFound()
        if proposed_display != row[0]:
            raise InvalidProject()
        with _transaction(self._factory) as conn:
            row = conn.execute("SELECT current_revision FROM mf_app.projects WHERE principal=%s AND project_id=%s FOR UPDATE", (principal, project_id)).fetchone()
            if row is None:
                raise NotFound()
            if row[0] != expected_revision:
                raise Conflict()
            revision = expected_revision + 1
            conn.execute("INSERT INTO mf_app.project_revisions (project_id,revision,snapshot,evidence,digest) VALUES (%s,%s,%s,%s,%s)",
                         (project_id, revision, Jsonb(snapshot), Jsonb(evidence), digest))
            conn.execute("UPDATE mf_app.projects SET current_revision=%s WHERE project_id=%s", (revision, project_id))
            result = conn.execute("SELECT " + _SELECT + _JOIN + "WHERE p.project_id=%s AND r.revision=%s", (project_id, revision)).fetchone()
            return _record(result)


# Avoid shadowing the validator with the create method argument.
display_id_fn = display_id

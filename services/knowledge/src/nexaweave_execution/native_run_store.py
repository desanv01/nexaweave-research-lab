"""PostgreSQL authority for one-shot native run ownership.

Migration is explicit. No method launches, finds, adopts, or kills a process.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from importlib.resources import files
from typing import Callable, Iterator
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
from nexaweave_storage.transaction_settings import apply_runtime_settings

from nexaweave_storage import ProjectStore
from nexaweave_storage.store import NotFound, StorageError

from .native_run_contracts import (InvalidNativeRun, NativeChildIdentity, NativeRunBusy,
    NativeRunConflict, NativeRunDenied, NativeRunError, NativeRunMigrationMismatch, NativeRunReceipt,
    NativeRunRequest, NativeRunUnavailable, NativeRunUncertain, RunState, canonical_uuid,
    principal_id)

_SELECT = ("run_id,principal,project_id,project_revision,simulation_id,artifact_sha256,"
           "runtime_sha256,platforms,seed,max_rounds,request_fingerprint,state,"
           "cancel_requested,attempt_id,owner_id,lease_until,child_instance_id,"
           "process_id,process_fingerprint,receipt,created_at,updated_at,"
           "source_run_id,checkpoint_sha256,intervention")
_MIGRATION_SELECT = ("SELECT version,sql_sha256,catalog_sha256 "
                     "FROM mf_native_execution.schema_migrations ORDER BY version")
_MIGRATION_INSERT = ("INSERT INTO mf_native_execution.schema_migrations"
                     "(version,sql_sha256,catalog_sha256) VALUES (%s,%s,%s)")


class RedditCheckpointMode(Enum):
    CAPTURE = "capture"
    CONTINUE = "continue"


@dataclass(frozen=True)
class NativeRunRecord:
    request: NativeRunRequest
    fingerprint: str
    state: RunState
    cancel_requested: bool
    attempt_id: UUID | None
    owner_id: UUID | None
    lease_until: datetime | None
    child: NativeChildIdentity | None
    receipt: NativeRunReceipt | None
    created_at: datetime
    updated_at: datetime


@contextmanager
def _transaction(factory: Callable[[], psycopg.Connection]) -> Iterator[psycopg.Connection]:
    try:
        with factory() as conn:
            with conn.transaction():
                apply_runtime_settings(conn, (
                    "SET LOCAL statement_timeout = '5s'",
                    "SET LOCAL lock_timeout = '2s'",
                    "SET LOCAL idle_in_transaction_session_timeout = '10s'",
                    "SET LOCAL search_path = pg_catalog",
                ))
                yield conn
    except NativeRunError:
        raise
    except Exception:
        raise NativeRunUnavailable() from None


def _catalog(conn: psycopg.Connection) -> str:
    queries = (
        "SELECT c.relname,c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_native_execution' AND c.relkind IN ('r','p','v','m','f') ORDER BY c.relname",
        "SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname='mf_native_execution' AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum",
        "SELECT c.relname,x.conname,x.contype,pg_get_constraintdef(x.oid,true),x.condeferrable,x.condeferred FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_native_execution' ORDER BY c.relname,x.conname",
        "SELECT t.relname,i.relname,pg_get_indexdef(i.oid) FROM pg_index x JOIN pg_class t ON t.oid=x.indrelid JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='mf_native_execution' ORDER BY t.relname,i.relname",
        "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_native_execution' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname",
        "SELECT p.proname,pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='mf_native_execution' ORDER BY p.proname",
    )
    return hashlib.sha256(json.dumps([conn.execute(q).fetchall() for q in queries],
                                   default=str, separators=(",", ":")).encode()).hexdigest()


def _v2_catalog_receipt(connection: psycopg.Connection, v1_receipt: tuple) -> str:
    # Bind the verified historical receipt to the current catalog without updating v1.
    evidence = json.dumps([v1_receipt, _catalog(connection)], separators=(",", ":"))
    return hashlib.sha256(evidence.encode("ascii")).hexdigest()


def _catalog_v3_receipt(conn, schema, previous, base_catalog):
    # Retain the original receipt format for the first two migrations. Include
    # enforcement state and new trigger/function definitions in the v3 receipt.
    queries = (
        "SELECT c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s ORDER BY c.relname",
        "SELECT x.conname,x.convalidated FROM pg_constraint x JOIN pg_namespace n "
        "ON n.oid=x.connamespace WHERE n.nspname=%s ORDER BY x.conname,x.conrelid::regclass::text",
        "SELECT i.relname,x.indisvalid,x.indisready,x.indislive FROM pg_index x JOIN pg_class i "
        "ON i.oid=x.indexrelid JOIN pg_namespace n ON n.oid=i.relnamespace WHERE n.nspname=%s ORDER BY i.relname",
        "SELECT c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid) FROM pg_trigger t "
        "JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND NOT t.tgisinternal ORDER BY c.relname,t.tgname",
        "SELECT p.proname,pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n "
        "ON n.oid=p.pronamespace WHERE n.nspname=%s ORDER BY p.proname,p.oid",
    )
    evidence = [previous, base_catalog(conn), [conn.execute(q, (schema,)).fetchall() for q in queries]]
    return hashlib.sha256(json.dumps(evidence, separators=(",", ":")).encode("ascii")).hexdigest()


def migrate_native_runs(connection: psycopg.Connection) -> None:
    """Install or verify only the native execution catalog after mf_app exists."""
    try:
        migrations = files("nexaweave_execution").joinpath("migrations")
        sql_v1 = migrations.joinpath("native_runs_0001.sql").read_text("utf-8")
        sql_v2 = migrations.joinpath("native_runs_0002.sql").read_text("utf-8")
        sql_v3 = migrations.joinpath("native_runs_0003.sql").read_text("utf-8")
        checksum_v1 = hashlib.sha256(sql_v1.encode("utf-8")).hexdigest()
        checksum_v2 = hashlib.sha256(sql_v2.encode("utf-8")).hexdigest()
        checksum_v3 = hashlib.sha256(sql_v3.encode("utf-8")).hexdigest()
        with connection.transaction():
            connection.execute("SET LOCAL statement_timeout = '10s'")
            connection.execute("SET LOCAL lock_timeout = '3s'")
            connection.execute("SET LOCAL idle_in_transaction_session_timeout = '15s'")
            connection.execute("SET LOCAL search_path = pg_catalog")
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (72196248041505,))
            if connection.execute("SELECT to_regclass('mf_app.project_revisions')").fetchone()[0] is None:
                raise NativeRunMigrationMismatch()
            exists = connection.execute("SELECT to_regnamespace('mf_native_execution') IS NOT NULL").fetchone()[0]
            if not exists:
                connection.execute(sql_v1)
                connection.execute(_MIGRATION_INSERT, (1, checksum_v1, _catalog(connection)))
            if connection.execute("SELECT to_regclass('mf_native_execution.schema_migrations')").fetchone()[0] is None:
                raise NativeRunMigrationMismatch()
            rows = connection.execute(_MIGRATION_SELECT).fetchall()
            versions = [row[0] for row in rows]
            if versions not in ([1], [1, 2], [1, 2, 3]) or rows[0][1] != checksum_v1:
                raise NativeRunMigrationMismatch()
            prior_v1 = rows[0]
            if versions == [1]:
                if prior_v1[2] != _catalog(connection):
                    raise NativeRunMigrationMismatch()
                connection.execute(sql_v2)
                connection.execute(_MIGRATION_INSERT, (2, checksum_v2,
                                                        _v2_catalog_receipt(connection, prior_v1)))
                rows = connection.execute(_MIGRATION_SELECT).fetchall()
            if rows[0] != prior_v1 or rows[1][:2] != (2, checksum_v2):
                raise NativeRunMigrationMismatch()
            previous = rows[:2]
            if len(rows) == 2:
                if previous[1][2] != _v2_catalog_receipt(connection, prior_v1):
                    raise NativeRunMigrationMismatch()
                connection.execute(sql_v3)
                chain = _catalog_v3_receipt(connection, 'mf_native_execution', previous, _catalog)
                connection.execute(_MIGRATION_INSERT, (3, checksum_v3, chain))
                rows = connection.execute(_MIGRATION_SELECT).fetchall()
            chain = _catalog_v3_receipt(connection, 'mf_native_execution', previous, _catalog)
            if rows != [*previous, (3, checksum_v3, chain)]:
                raise NativeRunMigrationMismatch()
    except (OSError, psycopg.Error):
        raise NativeRunMigrationMismatch() from None


def _row(value: tuple) -> NativeRunRecord:
    (run, principal, project, revision, simulation, artifact, runtime, platforms, seed,
     rounds, fingerprint, state, cancel, attempt, owner, lease, instance, pid, process,
     receipt, created, updated, source_run_id, checkpoint, intervention) = value
    try:
        wire = {
            "schema_version": 1, "principal": principal, "project_id": project,
            "project_revision": revision, "simulation_id": simulation, "run_id": run,
            "artifact_sha256": artifact.strip(), "runtime_sha256": runtime.strip(),
            "platforms": tuple(platforms), "seed": seed, "max_rounds": rounds}
        if source_run_id is not None or checkpoint is not None:
            wire.update(schema_version=4 if tuple(platforms) == ("reddit",) else 2, source_run_id=source_run_id,
                        checkpoint_sha256=None if checkpoint is None else checkpoint.strip())
        if intervention is not None:
            wire.update(schema_version=3, intervention=intervention)
        request = NativeRunRequest.from_wire(wire)
        if request.fingerprint != fingerprint.strip():
            raise InvalidNativeRun()
        child = None if instance is None else NativeChildIdentity.from_wire({
            "instance_id": instance, "process_id": pid, "process_fingerprint": process.strip()})
        saved = None if receipt is None else NativeRunReceipt.from_wire(receipt)
        if saved is not None and (saved.run_id != run or saved.attempt_id != attempt
                or saved.instance_id != instance or saved.request_fingerprint != request.fingerprint
                or saved.outcome != state):
            raise InvalidNativeRun()
        return NativeRunRecord(request, fingerprint.strip(), RunState(state), cancel,
                               attempt, owner, lease, child, saved, created, updated)
    except (TypeError, ValueError, AttributeError, InvalidNativeRun):
        raise NativeRunUncertain() from None


def _lease(value: object) -> int:
    if type(value) is not int or not 5 <= value <= 300:
        raise InvalidNativeRun()
    return value


def _verify_checkpoint_dispatch(conn, request):
    from .native_launch_store import LaunchAuthorityError, verify_checkpoint_dispatch

    try:
        verify_checkpoint_dispatch(conn, request)
    except LaunchAuthorityError as error:
        if error.code in ('native_launch_unavailable', 'native_launch_uncertain'):
            raise NativeRunUnavailable() from None
        raise NativeRunDenied() from None


def _lock_checkpoint_run_id(conn, run_id):
    # When a producer lock is needed, take it first. Run IDs are global.
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 72196248041506))", (str(run_id),))


def _verify_reddit_mode(conn, request, expected):
    """Keep ordinary V1 runs and typed checkpoint owners disjoint."""
    if expected is None:
        if request.schema_version == 4:
            raise NativeRunDenied()
        if (request.schema_version == 1 and request.platforms == ("reddit",)
                and conn.execute("SELECT to_regclass('mf_native_launch.plans') IS NOT NULL").fetchone()[0]
                and conn.execute(
                    "SELECT 1 FROM mf_native_launch.plans WHERE run_id=%s "
                    "AND frozen #> '{identity,schema_version}'='4'::jsonb", (request.run_id,),
                ).fetchone() is not None):
            raise NativeRunDenied()
        return
    if type(expected) is not RedditCheckpointMode:
        raise InvalidNativeRun()
    version = 1 if expected is RedditCheckpointMode.CAPTURE else 4
    if (request.schema_version != version or request.platforms != ("reddit",) or request.max_rounds != 1
            or not conn.execute("SELECT to_regclass('mf_native_launch.plans') IS NOT NULL").fetchone()[0]):
        raise NativeRunDenied()

    from .native_launch_store import LaunchAuthorityError, NativeLaunchStore

    try:
        launch = NativeLaunchStore._get(conn, request.principal, request.run_id)
    except LaunchAuthorityError as error:
        if error.code == 'not_found':
            raise NativeRunDenied() from None
        raise NativeRunUnavailable() from None
    identity = launch.frozen['identity']
    if (identity['schema_version'] != 4 or identity['checkpoint']['mode'] != expected.value
            or launch.request != request or not launch.dispatch_claimed or launch.budget_attempt_id is None):
        raise NativeRunDenied()


def _reject_protected_launch_id(conn, request):
    has_fork_column = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_attribute WHERE "
        "attrelid=to_regclass('mf_native_launch.plans') AND attname='intervention' AND NOT attisdropped)",
    ).fetchone()[0]
    if not has_fork_column:
        return
    protected = conn.execute(
        "SELECT frozen #> '{identity,schema_version}'='4'::jsonb FROM mf_native_launch.plans WHERE run_id=%s AND "
        "(intervention IS NOT NULL OR frozen #> '{identity,schema_version}'='4'::jsonb)", (request.run_id,),
    ).fetchone()
    if protected is not None:
        if protected[0]:
            raise NativeRunDenied()
        # Preserve replay of an ordinary row recorded before the fork fence.
        existing = conn.execute(
            f"SELECT {_SELECT} FROM mf_native_execution.runs WHERE run_id=%s AND principal=%s",
            (request.run_id, request.principal),
        ).fetchone()
        if existing is None or _row(existing).request != request:
            raise NativeRunConflict()


class NativeRunStore:
    def __init__(self, connection_factory: Callable[[], psycopg.Connection]):
        if not callable(connection_factory):
            raise InvalidNativeRun()
        self._connect = connection_factory
        self._projects = ProjectStore(connection_factory)

    def _owned(self, principal: str, run_id: UUID, *, lock: bool = False, conn) -> NativeRunRecord:
        row = conn.execute(f"SELECT {_SELECT} FROM mf_native_execution.runs WHERE run_id=%s AND principal=%s"
                           + (" FOR UPDATE" if lock else ""), (run_id, principal)).fetchone()
        if row is None:
            raise NativeRunDenied()
        return _row(row)

    def _verify_continuation_source(self, conn, request: NativeRunRequest) -> None:
        source = self._owned(request.principal, request.source_run_id, lock=True, conn=conn)
        original = source.request
        if (original.schema_version != 1 or source.state != RunState.completed
                or source.receipt is None or source.receipt.evidence_sha256 != request.checkpoint_sha256
                or original.project_id != request.project_id
                or original.project_revision != request.project_revision
                or original.simulation_id != request.simulation_id
                or original.artifact_sha256 != request.artifact_sha256
                or original.runtime_sha256 != request.runtime_sha256
                or original.platforms != request.platforms or original.seed != request.seed
                or original.max_rounds != request.max_rounds):
            raise NativeRunDenied()

    def register(self, request: NativeRunRequest, *,
                 reddit_mode: RedditCheckpointMode | None = None) -> NativeRunRecord:
        request = NativeRunRequest.from_wire(request)
        try:
            project = self._projects.get(request.principal, request.project_id,
                                         request.project_revision)
        except NotFound:
            raise NativeRunDenied() from None
        except StorageError:
            raise NativeRunUnavailable() from None
        if (project.principal != request.principal or project.project_id != request.project_id
                or project.revision != request.project_revision):
            raise NativeRunDenied()
        with _transaction(self._connect) as conn:
            _verify_reddit_mode(conn, request, reddit_mode)
            intervention = request.intervention
            if intervention is not None or reddit_mode is not None:
                row = conn.execute(f"SELECT {_SELECT} FROM mf_native_execution.runs WHERE run_id=%s AND principal=%s",
                                   (request.run_id, request.principal)).fetchone()
                if row is not None:
                    existing = _row(row)
                    if existing.request != request:
                        raise NativeRunConflict()
                    return existing
                _verify_checkpoint_dispatch(conn, request)
            else:
                if request.source_run_id is not None:
                    self._verify_continuation_source(conn, request)
                _lock_checkpoint_run_id(conn, request.run_id)
                # A plain read avoids reversing the producer/launch row lock
                # order. Fork planning holds the same run-ID lock on insertion.
                _reject_protected_launch_id(conn, request)
            conn.execute("INSERT INTO mf_native_execution.runs (run_id,principal,project_id,"
                         "project_revision,simulation_id,artifact_sha256,runtime_sha256,"
                         "platforms,seed,max_rounds,request_fingerprint,source_run_id,checkpoint_sha256,intervention) VALUES "
                         "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                         (request.run_id, request.principal, request.project_id,
                          request.project_revision, request.simulation_id,
                          request.artifact_sha256, request.runtime_sha256,
                          list(request.platforms), request.seed, request.max_rounds,
                          request.fingerprint, request.source_run_id, request.checkpoint_sha256,
                          None if intervention is None else Jsonb(intervention.to_wire())))
            row = conn.execute(f"SELECT {_SELECT} FROM mf_native_execution.runs WHERE run_id=%s AND principal=%s",
                               (request.run_id, request.principal)).fetchone()
            if row is None:
                if request.source_run_id is None:
                    other = conn.execute(
                        "SELECT 1 FROM mf_native_execution.runs WHERE project_id=%s "
                        "AND simulation_id=%s AND principal=%s AND source_run_id IS NULL",
                        (request.project_id, request.simulation_id, request.principal),
                    ).fetchone()
                else:
                    other = conn.execute(
                        "SELECT 1 FROM mf_native_execution.runs WHERE source_run_id=%s AND principal=%s "
                        "AND (intervention IS NOT NULL)=%s",
                        (request.source_run_id, request.principal, intervention is not None),
                    ).fetchone()
                if other is not None:
                    raise NativeRunConflict()
                raise NativeRunDenied()
            record = _row(row)
            if record.request != request or record.fingerprint != request.fingerprint:
                raise NativeRunConflict()
            return record

    def get(self, principal: str, run_id: UUID) -> NativeRunRecord:
        principal, run_id = principal_id(principal), canonical_uuid(run_id)
        with _transaction(self._connect) as conn:
            return self._owned(principal, run_id, conn=conn)

    def claim_start(self, principal: str, run_id: UUID, owner_id: UUID,
                    lease_seconds: int = 30, *, reddit_mode: RedditCheckpointMode | None = None) -> NativeRunRecord:
        principal, run_id, owner_id = principal_id(principal), canonical_uuid(run_id), canonical_uuid(owner_id)
        lease_seconds = _lease(lease_seconds)
        with _transaction(self._connect) as conn:
            current = self._owned(principal, run_id, conn=conn)
            _verify_reddit_mode(conn, current.request, reddit_mode)
            if current.request.intervention is not None or reddit_mode is not None:
                if current.state != RunState.declared or current.cancel_requested:
                    raise NativeRunBusy()
                _verify_checkpoint_dispatch(conn, current.request)
            row = conn.execute(f"UPDATE mf_native_execution.runs SET state='starting',"
                "attempt_id=%s,owner_id=%s,lease_until=clock_timestamp()+(%s * interval '1 second'),"
                "updated_at=clock_timestamp() WHERE run_id=%s AND principal=%s "
                f"AND state='declared' AND cancel_requested=false RETURNING {_SELECT}",
                (uuid4(), owner_id, lease_seconds, run_id, principal)).fetchone()
            if row is not None:
                return _row(row)  # The only call that grants launch permission.
            self._owned(principal, run_id, conn=conn)
            raise NativeRunBusy()

    def _token(self, record: NativeRunRecord, attempt_id: UUID, owner_id: UUID) -> None:
        if record.attempt_id != attempt_id or record.owner_id != owner_id:
            raise NativeRunConflict()

    def _fence_expired(self, conn, record: NativeRunRecord) -> bool:
        if record.state not in (RunState.starting, RunState.running):
            return False
        changed = conn.execute("UPDATE mf_native_execution.runs SET state='uncertain',"
            "lease_until=NULL,updated_at=clock_timestamp() WHERE run_id=%s AND "
            "state IN ('starting','running') AND lease_until<=clock_timestamp() RETURNING run_id",
            (record.request.run_id,)).fetchone()
        return changed is not None

    def attach(self, principal: str, run_id: UUID, attempt_id: UUID, owner_id: UUID,
               child: NativeChildIdentity, lease_seconds: int = 30) -> NativeRunRecord:
        principal, run_id = principal_id(principal), canonical_uuid(run_id)
        attempt_id, owner_id = canonical_uuid(attempt_id), canonical_uuid(owner_id)
        child, lease_seconds = NativeChildIdentity.from_wire(child), _lease(lease_seconds)
        expired = False
        with _transaction(self._connect) as conn:
            prior = self._owned(principal, run_id, lock=True, conn=conn)
            self._token(prior, attempt_id, owner_id)
            if prior.state == RunState.uncertain:
                raise NativeRunUncertain()
            expired = self._fence_expired(conn, prior)
            if not expired:
                if prior.state != RunState.starting or prior.child is not None:
                    raise NativeRunBusy()
                row = conn.execute(f"UPDATE mf_native_execution.runs SET state='running',"
                    "child_instance_id=%s,process_id=%s,process_fingerprint=%s,"
                    "lease_until=clock_timestamp()+(%s * interval '1 second'),updated_at=clock_timestamp() "
                    f"WHERE run_id=%s AND principal=%s AND state='starting' AND attempt_id=%s AND owner_id=%s "
                    f"AND lease_until>clock_timestamp() RETURNING {_SELECT}",
                    (child.instance_id, child.process_id, child.process_fingerprint, lease_seconds,
                     run_id, principal, attempt_id, owner_id)).fetchone()
                if row is not None:
                    return _row(row)
                expired = self._fence_expired(conn, prior)
        if expired:
            raise NativeRunUncertain()
        raise NativeRunBusy()

    def heartbeat(self, principal: str, run_id: UUID, attempt_id: UUID,
                  owner_id: UUID, lease_seconds: int = 30) -> NativeRunRecord:
        principal, run_id = principal_id(principal), canonical_uuid(run_id)
        attempt_id, owner_id = canonical_uuid(attempt_id), canonical_uuid(owner_id)
        lease_seconds = _lease(lease_seconds)
        expired = False
        with _transaction(self._connect) as conn:
            prior = self._owned(principal, run_id, lock=True, conn=conn)
            self._token(prior, attempt_id, owner_id)
            if prior.state == RunState.uncertain:
                raise NativeRunUncertain()
            expired = self._fence_expired(conn, prior)
            if not expired:
                if prior.state not in (RunState.starting, RunState.running):
                    raise NativeRunBusy()
                row = conn.execute(f"UPDATE mf_native_execution.runs SET "
                    f"lease_until=clock_timestamp()+(%s * interval '1 second'),updated_at=clock_timestamp() "
                    f"WHERE run_id=%s AND principal=%s AND attempt_id=%s AND owner_id=%s "
                    f"AND state IN ('starting','running') AND lease_until>clock_timestamp() RETURNING {_SELECT}",
                    (lease_seconds, run_id, principal, attempt_id, owner_id)).fetchone()
                if row is not None:
                    return _row(row)
                expired = self._fence_expired(conn, prior)
        if expired:
            raise NativeRunUncertain()
        raise NativeRunBusy()

    def request_cancel(self, principal: str, run_id: UUID) -> NativeRunRecord:
        principal, run_id = principal_id(principal), canonical_uuid(run_id)
        with _transaction(self._connect) as conn:
            self._owned(principal, run_id, lock=True, conn=conn)
            row = conn.execute(f"UPDATE mf_native_execution.runs SET cancel_requested=true,"
                f"updated_at=clock_timestamp() WHERE run_id=%s AND principal=%s RETURNING {_SELECT}",
                (run_id, principal)).fetchone()
            return _row(row)

    def settle(self, principal: str, run_id: UUID, attempt_id: UUID, owner_id: UUID,
               receipt: NativeRunReceipt) -> NativeRunRecord:
        principal, run_id = principal_id(principal), canonical_uuid(run_id)
        attempt_id, owner_id = canonical_uuid(attempt_id), canonical_uuid(owner_id)
        receipt = NativeRunReceipt.from_wire(receipt)
        expired = False
        with _transaction(self._connect) as conn:
            prior = self._owned(principal, run_id, lock=True, conn=conn)
            self._token(prior, attempt_id, owner_id)
            if (prior.child is None or receipt.run_id != run_id or receipt.attempt_id != attempt_id
                    or receipt.instance_id != prior.child.instance_id
                    or receipt.request_fingerprint != prior.fingerprint):
                raise NativeRunConflict()
            if prior.state == RunState.uncertain:
                raise NativeRunUncertain()
            if prior.state in (RunState.completed, RunState.failed, RunState.cancelled):
                if prior.receipt == receipt:
                    return prior
                raise NativeRunConflict()
            expired = self._fence_expired(conn, prior)
            if not expired:
                if prior.state != RunState.running:
                    raise NativeRunBusy()
                row = conn.execute(f"UPDATE mf_native_execution.runs SET state=%s,receipt=%s,"
                    f"lease_until=NULL,updated_at=clock_timestamp() WHERE run_id=%s AND principal=%s "
                    f"AND state='running' AND attempt_id=%s AND owner_id=%s "
                    f"AND lease_until>clock_timestamp() RETURNING {_SELECT}",
                    (receipt.outcome, Jsonb(receipt.to_wire()), run_id, principal,
                     attempt_id, owner_id)).fetchone()
                if row is not None:
                    return _row(row)
                expired = self._fence_expired(conn, prior)
        if expired:
            raise NativeRunUncertain()
        raise NativeRunBusy()

    def mark_uncertain(self, principal: str, run_id: UUID, attempt_id: UUID,
                       owner_id: UUID) -> NativeRunRecord:
        principal, run_id = principal_id(principal), canonical_uuid(run_id)
        attempt_id, owner_id = canonical_uuid(attempt_id), canonical_uuid(owner_id)
        with _transaction(self._connect) as conn:
            prior = self._owned(principal, run_id, lock=True, conn=conn)
            self._token(prior, attempt_id, owner_id)
            if prior.state == RunState.uncertain:
                return prior
            if prior.state not in (RunState.starting, RunState.running):
                raise NativeRunBusy()
            row = conn.execute(f"UPDATE mf_native_execution.runs SET state='uncertain',"
                f"lease_until=NULL,updated_at=clock_timestamp() WHERE run_id=%s AND principal=%s "
                f"AND attempt_id=%s AND owner_id=%s AND state IN ('starting','running') RETURNING {_SELECT}",
                (run_id, principal, attempt_id, owner_id)).fetchone()
            return _row(row)

    def reconcile_expired(self, principal: str, run_id: UUID) -> NativeRunRecord:
        principal, run_id = principal_id(principal), canonical_uuid(run_id)
        with _transaction(self._connect) as conn:
            prior = self._owned(principal, run_id, lock=True, conn=conn)
            if self._fence_expired(conn, prior):
                return self._owned(principal, run_id, conn=conn)
            return prior

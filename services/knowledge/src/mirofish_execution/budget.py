"""Explicit durable USD-microunit admission; amounts are ceilings, not bills."""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from importlib.resources import files
from typing import Callable, Iterator
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from mirofish_knowledge.contracts import KnowledgeScope
from mirofish_knowledge.operations import CompletionReceipt, _receipt
from mirofish_storage import ProjectStore
from mirofish_storage.store import NotFound as ProjectNotFound, StorageError as ProjectStorageError
from mirofish_storage.validation import InvalidProject, principal_id, uuid_value
from .preparation_contracts import PreparedBudgetReceipt, PreparationAuthorityError

MAX_MONEY = 2**63 - 1
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ERROR_CODES = frozenset({"dispatch_cancelled", "dispatch_uncertain"})

class BudgetError(RuntimeError):
    code = "budget_error"
    def __init__(self):
        super().__init__(self.code)

class InvalidBudget(BudgetError):
    code = "invalid_budget"

class BudgetDenied(BudgetError):
    code = "budget_denied"

class BudgetConflict(BudgetError):
    code = "budget_conflict"

class BudgetBusy(BudgetError):
    code = "budget_busy"

class BudgetUncertain(BudgetError):
    code = "budget_uncertain"

class BudgetUnavailable(BudgetError):
    code = "budget_store_unavailable"

class MigrationMismatch(BudgetError):
    code = "budget_migration_mismatch"

class ReservationState(StrEnum):
    reserved = "reserved"
    started = "started"
    settled = "settled"
    uncertain = "uncertain"
    released = "released"

@dataclass(frozen=True)
class Reservation:
    account_id: UUID
    operation_id: UUID
    fingerprint: str
    ceiling_microusd: int
    state: ReservationState
    attempt_id: UUID
    scope_group_id: str
    episode_id: UUID
    evidence_ids: tuple[UUID, ...]
    receipt: CompletionReceipt | PreparedBudgetReceipt | None
    error_code: str | None

@dataclass(frozen=True)
class BudgetStatus:
    account_id: UUID
    principal: str
    project_id: UUID
    cap_microusd: int
    accounted_ceiling_microusd: int
    reserved_microusd: int
    uncertain_microusd: int
    remaining_microusd: int
    actual_usage_microusd: None = None

def _money(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_MONEY:
        raise InvalidBudget()
    return value

def _uuid(value: object) -> UUID:
    try:
        return uuid_value(value)
    except (InvalidProject, TypeError, ValueError):
        raise InvalidBudget() from None

def _principal(value: object) -> str:
    try:
        return principal_id(value)
    except (InvalidProject, TypeError, ValueError):
        raise InvalidBudget() from None

def _fingerprint(value: object) -> str:
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise InvalidBudget()
    return value

def _scope(value: object) -> KnowledgeScope:
    try:
        if not isinstance(value, KnowledgeScope):
            raise ValueError
        return KnowledgeScope.model_validate(value.model_dump())
    except (AttributeError, TypeError, ValueError):
        raise InvalidBudget() from None

@contextmanager
def _transaction(factory: Callable[[], psycopg.Connection]) -> Iterator[psycopg.Connection]:
    try:
        with factory() as conn:
            with conn.transaction():
                conn.execute("SET LOCAL statement_timeout = '5s'")
                conn.execute("SET LOCAL lock_timeout = '2s'")
                conn.execute("SET LOCAL idle_in_transaction_session_timeout = '10s'")
                conn.execute("SET LOCAL search_path = pg_catalog")
                yield conn
    except psycopg.Error:
        raise BudgetUnavailable() from None

def _catalog(conn: psycopg.Connection) -> str:
    queries = (
        "SELECT c.relname,c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_execution' AND c.relkind='r' ORDER BY c.relname",
        "SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname='mf_execution' AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum",
        "SELECT c.relname,x.conname,x.contype,pg_get_constraintdef(x.oid,true),x.condeferrable,x.condeferred FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_execution' AND c.relkind='r' ORDER BY c.relname,x.conname",
        "SELECT t.relname,i.relname,pg_get_indexdef(i.oid) FROM pg_index x JOIN pg_class t ON t.oid=x.indrelid JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='mf_execution' AND t.relkind='r' ORDER BY t.relname,i.relname",
    )
    return hashlib.sha256(json.dumps([conn.execute(q).fetchall() for q in queries], default=str,
                                     separators=(",", ":")).encode()).hexdigest()

def migrate(connection: psycopg.Connection) -> None:
    """Explicit installation with SQL checksum and owned catalog drift checks."""
    try:
        sql = files("mirofish_execution").joinpath("migrations/0001_budget_admission.sql").read_text("utf-8")
        checksum = hashlib.sha256(sql.encode()).hexdigest()
        with connection.transaction():
            connection.execute("SET LOCAL statement_timeout = '10s'")
            connection.execute("SET LOCAL lock_timeout = '5s'")
            connection.execute("SET LOCAL search_path = pg_catalog")
            connection.execute("SELECT pg_advisory_xact_lock(720105, 1)")
            exists = connection.execute("SELECT to_regnamespace('mf_execution') IS NOT NULL").fetchone()[0]
            if exists:
                if not connection.execute("SELECT to_regclass('mf_execution.schema_migrations') IS NOT NULL").fetchone()[0]:
                    raise MigrationMismatch()
                rows = connection.execute("SELECT version,checksum,schema_checksum FROM mf_execution.schema_migrations ORDER BY version").fetchall()
                if len(rows) != 1 or rows[0][0] != 1 or rows[0][1] != checksum or rows[0][2] != _catalog(connection):
                    raise MigrationMismatch()
            else:
                connection.execute(sql)
                connection.execute("INSERT INTO mf_execution.schema_migrations(version,checksum,schema_checksum) VALUES (1,%s,%s)",
                                   (checksum, _catalog(connection)))
    except (OSError, psycopg.Error):
        raise MigrationMismatch() from None

_COLUMNS = ("account_id,operation_id,fingerprint,ceiling_microusd,state,attempt_id,"
            "scope_group_id,episode_id,evidence_ids,receipt,error_code")

def _reservation(row: tuple) -> Reservation:
    account, operation, fingerprint, ceiling, state, attempt, group, episode, evidence, receipt, error = row
    try:
        evidence_ids = tuple(_uuid(item) for item in evidence)
        if len(evidence_ids) > 100 or len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError
        saved = None
        if receipt is not None:
            if type(receipt) is dict and receipt.get('kind') == 'prepared_budget_v1':
                saved = PreparedBudgetReceipt.from_wire(receipt)
                if (saved.operation_id != operation or saved.attempt_id != attempt
                        or saved.fingerprint != fingerprint or evidence_ids):
                    raise ValueError
            elif (set(receipt) != {"group_id", "episode_id", "fingerprint", "evidence_ids"}
                    or receipt["group_id"] != group or receipt["episode_id"] != str(episode)
                    or receipt["fingerprint"] != fingerprint or receipt["evidence_ids"] != [str(v) for v in evidence_ids]):
                raise ValueError
            else:
                saved = CompletionReceipt(group, episode, fingerprint, evidence_ids)
        return Reservation(account, operation, _fingerprint(fingerprint), ceiling,
                           ReservationState(state), attempt, group, episode, evidence_ids, saved, error)
    except (ValueError, TypeError, KeyError, InvalidBudget, PreparationAuthorityError):
        raise BudgetUncertain() from None

class BudgetLedger:
    def __init__(self, connection_factory: Callable[[], psycopg.Connection]):
        if not callable(connection_factory):
            raise InvalidBudget()
        self._connect = connection_factory
        self._projects = ProjectStore(connection_factory)

    def create_account(self, principal: str, project_id: UUID, account_id: UUID,
                       cap_microusd: int) -> BudgetStatus:
        principal, project_id, account_id = _principal(principal), _uuid(project_id), _uuid(account_id)
        cap = _money(cap_microusd)
        try:
            project = self._projects.get(principal, project_id)
        except ProjectNotFound:
            raise BudgetDenied() from None
        except ProjectStorageError:
            raise BudgetUnavailable() from None
        if project.principal != principal or project.project_id != project_id:
            raise BudgetDenied()
        with _transaction(self._connect) as conn:
            conn.execute("INSERT INTO mf_execution.accounts(account_id,principal,project_id,cap_microusd) "
                         "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (account_id, principal, project_id, cap))
            row = conn.execute("SELECT account_id,principal,project_id,cap_microusd FROM mf_execution.accounts "
                               "WHERE project_id=%s", (project_id,)).fetchone()
            if row != (account_id, principal, project_id, cap):
                raise BudgetConflict()
        return self.status(principal, account_id)

    @staticmethod
    def _account(conn, principal, account_id, *, lock=False):
        row = conn.execute("SELECT account_id,principal,project_id,cap_microusd FROM mf_execution.accounts "
                           "WHERE account_id=%s AND principal=%s" + (" FOR UPDATE" if lock else ""),
                           (account_id, principal)).fetchone()
        if row is None:
            raise BudgetDenied()
        return row

    @staticmethod
    def _row(conn, account_id, operation_id):
        row = conn.execute(f"SELECT {_COLUMNS} FROM mf_execution.reservations WHERE account_id=%s AND operation_id=%s",
                           (account_id, operation_id)).fetchone()
        return _reservation(row) if row else None

    @staticmethod
    def _totals(conn, account_id):
        rows = conn.execute("SELECT state,COALESCE(sum(ceiling_microusd),0) FROM mf_execution.reservations "
                            "WHERE account_id=%s GROUP BY state", (account_id,)).fetchall()
        amounts = {state: int(value) for state, value in rows}
        accounted = amounts.get("settled", 0)
        uncertain = amounts.get("uncertain", 0)
        reserved = amounts.get("reserved", 0) + amounts.get("started", 0) + uncertain
        return accounted, reserved, uncertain

    def status(self, principal: str, account_id: UUID) -> BudgetStatus:
        principal, account_id = _principal(principal), _uuid(account_id)
        with _transaction(self._connect) as conn:
            account = self._account(conn, principal, account_id)
            accounted, reserved, uncertain = self._totals(conn, account_id)
            remaining = account[3] - accounted - reserved
            if remaining < 0:
                raise BudgetUncertain()
            return BudgetStatus(*account, accounted, reserved, uncertain, remaining)

    def reserve(self, principal: str, account_id: UUID, scope: KnowledgeScope,
                operation_id: UUID, fingerprint: str, ceiling_microusd: int,
                evidence_ids: tuple[UUID, ...]) -> Reservation:
        principal, account_id, operation_id = _principal(principal), _uuid(account_id), _uuid(operation_id)
        scope, fingerprint, ceiling = _scope(scope), _fingerprint(fingerprint), _money(ceiling_microusd)
        if (not isinstance(evidence_ids, tuple) or len(evidence_ids) > 100
                or any(not isinstance(v, UUID) for v in evidence_ids)
                or len(set(evidence_ids)) != len(evidence_ids)):
            raise InvalidBudget()
        with _transaction(self._connect) as conn:
            account = self._account(conn, principal, account_id, lock=True)
            if account[2] != scope.project_id:
                raise BudgetDenied()
            prior = self._row(conn, account_id, operation_id)
            episode = scope.episode_uuid(operation_id)
            if prior is not None:
                if (prior.fingerprint != fingerprint or prior.ceiling_microusd != ceiling
                        or prior.scope_group_id != scope.group_id or prior.episode_id != episode
                        or prior.evidence_ids != evidence_ids):
                    raise BudgetConflict()
                return prior
            accounted, reserved, _ = self._totals(conn, account_id)
            if ceiling > account[3] - accounted - reserved:
                raise BudgetDenied()
            attempt = uuid4()
            conn.execute("INSERT INTO mf_execution.reservations(account_id,operation_id,fingerprint,ceiling_microusd,"
                         "state,attempt_id,scope_group_id,episode_id,evidence_ids) "
                         "VALUES (%s,%s,%s,%s,'reserved',%s,%s,%s,%s)",
                         (account_id, operation_id, fingerprint, ceiling, attempt, scope.group_id,
                          episode, Jsonb([str(v) for v in evidence_ids])))
            return self._row(conn, account_id, operation_id)

    def _transition(self, principal, account_id, operation_id, attempt_id, expected, target,
                    *, receipt=None, error_code=None) -> Reservation:
        principal, account_id, operation_id, attempt_id = (_principal(principal), _uuid(account_id),
                                                             _uuid(operation_id), _uuid(attempt_id))
        if error_code is not None and error_code not in _ERROR_CODES:
            raise InvalidBudget()
        with _transaction(self._connect) as conn:
            self._account(conn, principal, account_id, lock=True)
            prior = self._row(conn, account_id, operation_id)
            if prior is None:
                raise BudgetDenied()
            if prior.attempt_id != attempt_id:
                raise BudgetConflict()
            if prior.state != expected:
                raise BudgetBusy()
            conn.execute("UPDATE mf_execution.reservations SET state=%s,receipt=%s,error_code=%s,updated_at=now() "
                         "WHERE account_id=%s AND operation_id=%s", (target.value, Jsonb(receipt) if receipt else None,
                                                               error_code, account_id, operation_id))
            return self._row(conn, account_id, operation_id)

    def start(self, principal: str, account_id: UUID, operation_id: UUID, attempt_id: UUID) -> Reservation:
        return self._transition(principal, account_id, operation_id, attempt_id,
                                ReservationState.reserved, ReservationState.started)

    def release_undispatched(self, principal: str, account_id: UUID, operation_id: UUID,
                             attempt_id: UUID) -> Reservation:
        """Only the owner of a provably undispatched reservation may release it."""
        return self._transition(principal, account_id, operation_id, attempt_id,
                                ReservationState.reserved, ReservationState.released)

    def settle(self, principal: str, account_id: UUID, scope: KnowledgeScope,
               operation_id: UUID, attempt_id: UUID, fingerprint: str,
               evidence_ids: tuple[UUID, ...], receipt: CompletionReceipt) -> Reservation:
        scope, operation_id, fingerprint = _scope(scope), _uuid(operation_id), _fingerprint(fingerprint)
        try:
            valid = _receipt(receipt, scope, operation_id, fingerprint)
        except (AttributeError, TypeError, ValueError):
            raise BudgetUncertain() from None
        if valid.evidence_ids != evidence_ids:
            raise BudgetUncertain()
        return self._transition(principal, account_id, operation_id, attempt_id,
                                ReservationState.started, ReservationState.settled,
                                receipt=valid.json_value())

    def mark_uncertain(self, principal: str, account_id: UUID, operation_id: UUID,
                       attempt_id: UUID, error_code: str) -> Reservation:
        return self._transition(principal, account_id, operation_id, attempt_id,
                                ReservationState.started, ReservationState.uncertain,
                                error_code=error_code)

    def reserve_prepared(self, principal, account_id, scope, operation_id,
                         plan_sha256, ceiling_microusd):
        """Use the SAME account lock/cap totals as ingestion, with tagged purpose.

        A domain-separated fingerprint prevents accidental ingestion reuse.
        No knowledge completion receipt is manufactured for preparation.
        """
        plan_sha256 = _fingerprint(plan_sha256)
        fingerprint = hashlib.sha256(('prepared_budget_v1:' + plan_sha256).encode('ascii')).hexdigest()
        return self.reserve(principal, account_id, scope, operation_id, fingerprint,
                            ceiling_microusd, ())

    def settle_prepared(self, principal, account_id, scope, operation_id,
                        attempt_id, plan_sha256, artifact_sha256):
        scope, operation_id = _scope(scope), _uuid(operation_id)
        account_id, attempt_id = _uuid(account_id), _uuid(attempt_id)
        principal = _principal(principal)
        fingerprint = hashlib.sha256(('prepared_budget_v1:' + _fingerprint(plan_sha256)).encode('ascii')).hexdigest()
        receipt = PreparedBudgetReceipt(operation_id, attempt_id, fingerprint,
                                        _fingerprint(artifact_sha256))
        with _transaction(self._connect) as conn:
            account = self._account(conn, principal, account_id, lock=True)
            prior = self._row(conn, account_id, operation_id)
            if (account[2] != scope.project_id or prior is None or prior.attempt_id != attempt_id
                    or prior.fingerprint != fingerprint or prior.scope_group_id != scope.group_id
                    or prior.episode_id != scope.episode_uuid(operation_id) or prior.evidence_ids):
                raise BudgetUncertain()
            if prior.state == ReservationState.settled and prior.receipt == receipt:
                return prior
            if prior.state != ReservationState.started:
                raise BudgetUncertain()
            conn.execute("UPDATE mf_execution.reservations SET state='settled',receipt=%s,updated_at=now() "
                         "WHERE account_id=%s AND operation_id=%s", (Jsonb(receipt.json_value()), account_id, operation_id))
            return self._row(conn, account_id, operation_id)

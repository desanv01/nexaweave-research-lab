"""Immutable trusted-host principal/display graph bindings over the ledger scope table.

Resolving a binding identifies a scope. It does not authorize a caller or admit a
read while knowledge writes are active.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import psycopg

from .contracts import KnowledgeScope
from .operations import Conflict, Ledger, NotFound, Tombstoned, _transaction


_DISPLAY_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_SCOPE_FIELDS = frozenset({"schema_version", "workspace_id", "project_id", "graph_id",
                           "run_id", "branch_id", "layer"})
_SELECT = (
    "SELECT b.principal, b.display_graph_id, b.group_id, b.created_at, "
    "s.canonical_scope, s.tombstoned "
    "FROM mf_knowledge.scope_bindings b "
    "JOIN mf_knowledge.scopes s ON s.group_id = b.group_id "
)


@dataclass(frozen=True)
class BindingRecord:
    principal: str
    display_graph_id: str
    scope: KnowledgeScope
    created_at: datetime


def _identity(principal: object, display_graph_id: object) -> tuple[str, str]:
    if (type(principal) is not str or not 1 <= len(principal) <= 128
            or not principal.strip() or any(not 32 <= ord(char) <= 126 for char in principal)
            or type(display_graph_id) is not str or not _DISPLAY_ID.fullmatch(display_graph_id)):
        raise ValueError("invalid knowledge binding identity")
    return principal, display_graph_id


def _stored(row: tuple) -> BindingRecord:
    try:
        principal, display_id, group_id, created_at, canonical, tombstoned = row
        _identity(principal, display_id)
        if (type(group_id) is not str or type(canonical) is not dict
                or set(canonical) != _SCOPE_FIELDS
                or type(canonical["schema_version"]) is not int
                or canonical["schema_version"] != 1
                or type(tombstoned) is not bool
                or type(created_at) is not datetime
                or created_at.tzinfo is None or created_at.utcoffset() is None):
            raise ValueError
        scope = KnowledgeScope.model_validate_json(
            json.dumps(canonical, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
        if scope.model_dump(mode="json") != canonical or scope.group_id != group_id:
            raise ValueError
    except (ValueError, TypeError, KeyError, OverflowError):
        raise Conflict("knowledge binding record conflict") from None
    if tombstoned:
        raise Tombstoned("knowledge scope tombstoned")
    return BindingRecord(principal, display_id, scope, created_at)


class ScopeBindingStore:
    def __init__(self, connection_factory: Callable[[], psycopg.Connection]):
        if not callable(connection_factory):
            raise ValueError("connection factory required")
        self._connect = connection_factory

    def bind(self, principal: str, display_graph_id: str, scope: KnowledgeScope) -> BindingRecord:
        principal, display_graph_id = _identity(principal, display_graph_id)
        try:
            if (not isinstance(scope, KnowledgeScope) or type(scope.schema_version) is not int
                    or scope.schema_version != 1):
                raise ValueError
            # model_copy(update=...) bypasses construction validation. Avoid the
            # serializer's input-value warning before strict revalidation.
            scope = KnowledgeScope.model_validate(scope.model_dump(warnings=False))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("invalid knowledge scope") from None
        with _transaction(self._connect) as conn:
            Ledger._scope(conn, scope, create=True)
            try:
                conn.execute(
                    "INSERT INTO mf_knowledge.scope_bindings(principal, display_graph_id, group_id) "
                    "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (principal, display_graph_id, scope.group_id),
                )
            except psycopg.errors.UniqueViolation:
                raise Conflict("knowledge binding conflict") from None
            row = conn.execute(_SELECT + "WHERE b.principal = %s AND b.display_graph_id = %s",
                               (principal, display_graph_id)).fetchone()
            if row is None:
                raise Conflict("knowledge binding conflict")
            record = _stored(row)
            if record.principal != principal or record.display_graph_id != display_graph_id or record.scope != scope:
                raise Conflict("knowledge binding conflict")
            return record

    def resolve(self, principal: str, display_graph_id: str) -> BindingRecord:
        principal, display_graph_id = _identity(principal, display_graph_id)
        with _transaction(self._connect) as conn:
            row = conn.execute(_SELECT + "WHERE b.principal = %s AND b.display_graph_id = %s",
                               (principal, display_graph_id)).fetchone()
            if row is None:
                raise NotFound("knowledge binding not found")
            record = _stored(row)
            if record.principal != principal or record.display_graph_id != display_graph_id:
                raise Conflict("knowledge binding record conflict")
            return record

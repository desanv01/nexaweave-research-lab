"""Retained UTF-8 source revisions and exact codepoint passage evidence."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterator
from uuid import UUID

import psycopg

from .store import Conflict, NotFound, StorageError
from .validation import InvalidProject, principal_id, uuid_value


MAX_TEXT_BYTES = 1024 * 1024
MAX_EXCERPT_BYTES = 32768


@dataclass(frozen=True)
class PassageRecord:
    evidence_id: UUID
    source_revision: UUID
    project_id: UUID
    start: int
    end: int
    page: int | None
    excerpt: str
    excerpt_sha256: str


@dataclass(frozen=True)
class SourceRecord:
    project_id: UUID
    source_revision: UUID
    name: str
    text: str
    text_sha256: str
    byte_length: int
    codepoint_length: int
    recorded_at: datetime
    passages: tuple[PassageRecord, ...]


@dataclass(frozen=True)
class SourceMetadata:
    project_id: UUID
    source_revision: UUID
    name: str
    text_sha256: str
    byte_length: int
    codepoint_length: int
    recorded_at: datetime


@dataclass(frozen=True)
class ResolvedEvidence:
    principal: str
    project_id: UUID
    source_revision: UUID
    evidence_id: UUID
    source_name: str
    source_sha256: str
    source_byte_length: int
    source_codepoint_length: int
    source_recorded_at: datetime
    start: int
    end: int
    offset_unit: str
    excerpt: str
    excerpt_sha256: str
    declared_page: int | None


def _source_input(name: object, text: object) -> tuple[str, str, str, int, int]:
    if type(name) is not str or not name or len(name) > 256 or "\x00" in name:
        raise InvalidProject()
    if type(text) is not str or not text or "\x00" in text:
        raise InvalidProject()
    try:
        name.encode("utf-8")
        encoded = text.encode("utf-8")
    except UnicodeError:
        raise InvalidProject() from None
    if len(encoded) > MAX_TEXT_BYTES:
        raise InvalidProject()
    return name, text, hashlib.sha256(encoded).hexdigest(), len(encoded), len(text)


def _passages_input(value: object, project: UUID, revision: UUID, text: str) -> tuple[PassageRecord, ...]:
    if type(value) not in (list, tuple) or len(value) > 100:
        raise InvalidProject()
    result = []
    seen = set()
    for item in value:
        if type(item) is not dict or set(item) not in (
                {"evidence_id", "start", "end"},
                {"evidence_id", "start", "end", "page"}):
            raise InvalidProject()
        evidence_id = uuid_value(item["evidence_id"])
        if evidence_id in seen:
            raise InvalidProject()
        seen.add(evidence_id)
        start, end = item["start"], item["end"]
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
            raise InvalidProject()
        page = item.get("page")
        if page is not None and (type(page) is not int or page <= 0 or page > 2_147_483_647):
            raise InvalidProject()
        excerpt = text[start:end]
        encoded = excerpt.encode("utf-8")
        if not encoded or len(encoded) > MAX_EXCERPT_BYTES:
            raise InvalidProject()
        result.append(PassageRecord(evidence_id, revision, project, start, end,
                                    page, excerpt, hashlib.sha256(encoded).hexdigest()))
    return tuple(result)


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
    except psycopg.errors.UniqueViolation:
        raise Conflict() from None
    except psycopg.Error:
        raise StorageError() from None


_SOURCE = "project_id,source_revision,name,retained_text,text_sha256,byte_length,codepoint_length,recorded_at"
_PASSAGE = "evidence_id,source_revision,project_id,start_offset,end_offset,page,excerpt,excerpt_sha256"


def _validated_source(row, passage_rows) -> SourceRecord:
    project, revision, name, text, digest, byte_length, codepoint_length, recorded = row
    try:
        _, _, actual, size, length = _source_input(name, text)
        if actual != digest.strip() or size != byte_length or length != codepoint_length:
            raise InvalidProject()
        passages = _passages_input([
            {"evidence_id": item[0], "start": item[3], "end": item[4], "page": item[5]}
            for item in passage_rows
        ], project, revision, text)
        if len(passages) != len(passage_rows):
            raise InvalidProject()
        for stored, validated in zip(passage_rows, passages):
            if (stored[1] != revision or stored[2] != project
                    or stored[6] != validated.excerpt
                    or stored[7].strip() != validated.excerpt_sha256):
                raise InvalidProject()
    except (InvalidProject, AttributeError, TypeError):
        raise StorageError() from None
    return SourceRecord(project, revision, name, text, actual, size, length,
                        recorded, passages)


class SourceStore:
    def __init__(self, connection_factory: Callable[[], psycopg.Connection]):
        self._factory = connection_factory

    def ingest_text(self, principal, project_id, source_revision, name, text,
                    passages=()) -> SourceRecord:
        principal, project_id, source_revision = (principal_id(principal),
            uuid_value(project_id), uuid_value(source_revision))
        name, text, digest, byte_length, codepoint_length = _source_input(name, text)
        passages = _passages_input(passages, project_id, source_revision, text)
        with _transaction(self._factory) as conn:
            if conn.execute("SELECT 1 FROM mf_app.projects WHERE principal=%s AND project_id=%s",
                            (principal, project_id)).fetchone() is None:
                raise NotFound()
            inserted = conn.execute(
                "INSERT INTO mf_app.source_revisions (source_revision,project_id,name,retained_text,text_sha256,byte_length,codepoint_length) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING source_revision",
                (source_revision, project_id, name, text, digest, byte_length, codepoint_length)).fetchone()
            if inserted:
                for ordinal, passage in enumerate(passages):
                    conn.execute("INSERT INTO mf_app.passage_evidence "
                        "(evidence_id,project_id,source_revision,ordinal,start_offset,end_offset,page,excerpt,excerpt_sha256) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (passage.evidence_id, project_id, source_revision, ordinal,
                         passage.start, passage.end, passage.page, passage.excerpt,
                         passage.excerpt_sha256))
            row = conn.execute("SELECT " + _SOURCE + " FROM mf_app.source_revisions "
                               "WHERE source_revision=%s FOR UPDATE", (source_revision,)).fetchone()
            if row is None or row[0] != project_id or row[2:7] != (name, text, digest, byte_length, codepoint_length):
                raise Conflict()
            passage_rows = conn.execute("SELECT " + _PASSAGE + " FROM mf_app.passage_evidence "
                "WHERE project_id=%s AND source_revision=%s ORDER BY ordinal",
                (project_id, source_revision)).fetchall()
            source = _validated_source(row, passage_rows)
            if source.passages != passages:
                raise Conflict()
            return source

    def get_source(self, principal, project_id, source_revision) -> SourceRecord:
        principal, project_id, source_revision = (principal_id(principal),
            uuid_value(project_id), uuid_value(source_revision))
        with _transaction(self._factory) as conn:
            row = conn.execute("SELECT s." + _SOURCE.replace(",", ",s.") + " FROM mf_app.source_revisions s "
                "JOIN mf_app.projects p ON p.project_id=s.project_id "
                "WHERE p.principal=%s AND s.project_id=%s AND s.source_revision=%s",
                (principal, project_id, source_revision)).fetchone()
            if row is None:
                raise NotFound()
            passages = conn.execute("SELECT " + _PASSAGE + " FROM mf_app.passage_evidence "
                "WHERE project_id=%s AND source_revision=%s ORDER BY ordinal",
                (project_id, source_revision)).fetchall()
            return _validated_source(row, passages)

    def list_sources(self, principal, project_id, limit=50) -> list[SourceMetadata]:
        principal, project_id = principal_id(principal), uuid_value(project_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidProject()
        with _transaction(self._factory) as conn:
            if conn.execute("SELECT 1 FROM mf_app.projects WHERE principal=%s AND project_id=%s",
                            (principal, project_id)).fetchone() is None:
                raise NotFound()
            rows = conn.execute("SELECT " + _SOURCE + " FROM mf_app.source_revisions s "
                "WHERE s.project_id=%s ORDER BY s.recorded_at DESC,s.source_revision LIMIT %s",
                (project_id, limit)).fetchall()
            result = []
            for row in rows:
                passages = conn.execute("SELECT " + _PASSAGE + " FROM mf_app.passage_evidence "
                    "WHERE project_id=%s AND source_revision=%s ORDER BY ordinal",
                    (project_id, row[1])).fetchall()
                item = _validated_source(row, passages)
                result.append(SourceMetadata(item.project_id, item.source_revision, item.name,
                    item.text_sha256, item.byte_length, item.codepoint_length, item.recorded_at))
            return result

    def resolve_evidence(self, principal, project_id, evidence_id) -> ResolvedEvidence:
        principal, project_id, evidence_id = (principal_id(principal),
            uuid_value(project_id), uuid_value(evidence_id))
        with _transaction(self._factory) as conn:
            row = conn.execute("SELECT s." + _SOURCE.replace(",", ",s.") + " FROM mf_app.passage_evidence e "
                "JOIN mf_app.source_revisions s ON s.project_id=e.project_id AND s.source_revision=e.source_revision "
                "JOIN mf_app.projects p ON p.project_id=s.project_id "
                "WHERE p.principal=%s AND e.project_id=%s AND e.evidence_id=%s",
                (principal, project_id, evidence_id)).fetchone()
            if row is None:
                raise NotFound()
            passages = conn.execute("SELECT " + _PASSAGE + " FROM mf_app.passage_evidence "
                "WHERE project_id=%s AND source_revision=%s ORDER BY ordinal",
                (project_id, row[1])).fetchall()
            source = _validated_source(row, passages)
            passage = next((item for item in source.passages if item.evidence_id == evidence_id), None)
            if passage is None:
                raise StorageError()
            return ResolvedEvidence(principal, project_id, source.source_revision, evidence_id,
                source.name, source.text_sha256, source.byte_length, source.codepoint_length,
                source.recorded_at, passage.start, passage.end, "unicode_codepoint",
                passage.excerpt, passage.excerpt_sha256, passage.page)

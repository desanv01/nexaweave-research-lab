"""Atomic selected-source retention. Artifact metadata is inert provenance only."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID, uuid5

from .research_bundle import BundleError, WARNING, canonical, decode, inspect_bundle
from .validation import HEX, InvalidProject, principal_id, uuid_value

MAX_PROVENANCE_BYTES = 2 * 1024 * 1024


class ImportError(ValueError):
    def __init__(self, code="invalid_import"):
        self.code = code
        super().__init__(code)


def remap_id(target_project_id, kind, artifact_sha256, original_id):
    """Typed UUID5 names under the explicit target namespace, never origin IDs."""
    try:
        target, original = uuid_value(target_project_id), uuid_value(original_id)
        if kind not in ("source", "passage") or type(kind) is not str:
            raise InvalidProject()
        if type(artifact_sha256) is not str or not HEX.fullmatch(artifact_sha256):
            raise InvalidProject()
        return uuid5(target, f"mirofish-retained-import-v1:{kind}:{artifact_sha256}:{original}")
    except (InvalidProject, ValueError, TypeError):
        raise ImportError("invalid_request") from None


def _postgres_tree(value):
    # Bundle validation has already checked types, depth and UTF8. PostgreSQL
    # JSONB additionally refuses NUL even in otherwise inert strings/keys.
    if type(value) is str:
        if "\x00" in value:
            raise ImportError()
        value.encode("utf-8")
    elif type(value) is dict:
        for key, item in value.items():
            _postgres_tree(key)
            _postgres_tree(item)
    elif type(value) is list:
        for item in value:
            _postgres_tree(item)


@dataclass(frozen=True)
class ImportPlan:
    principal: str
    target_project_id: UUID
    expected_revision: int
    artifact_sha256: str
    payload_sha256: str
    # Serialized copied values keep caller mutation out of the transaction.
    _payload_json: str
    _provenance_json: str

    @property
    def payload(self):
        return json.loads(self._payload_json)

    @property
    def provenance(self):
        return json.loads(self._provenance_json)


def prepare_import(raw, principal, target_project_id, expected_revision, expected_sha256):
    """Admission and copying finish before any connection factory is invoked."""
    try:
        principal = principal_id(principal)
        target = uuid_value(target_project_id)
        if type(expected_revision) is not int or not 1 <= expected_revision <= 2_147_483_647:
            raise ImportError("invalid_request")
        if type(expected_sha256) is not str or not HEX.fullmatch(expected_sha256):
            raise ImportError("invalid_request")
        summary = inspect_bundle(raw, expected_sha256)
        payload = decode(raw)["payload"]
        _postgres_tree(payload)
        sources = []
        for source in payload["sources"]:
            revision = remap_id(target, "source", expected_sha256, source["source_revision"])
            metadata = {key: value for key, value in source.items() if key not in ("text", "passages")}
            metadata["imported_source_revision"] = str(revision)
            metadata["passages"] = []
            for ordinal, passage in enumerate(source["passages"]):
                mapped = remap_id(target, "passage", expected_sha256, passage["evidence_id"])
                item = {key: value for key, value in passage.items() if key != "excerpt"}
                item.update(ordinal=ordinal, imported_evidence_id=str(mapped))
                metadata["passages"].append(item)
            sources.append(metadata)
        provenance = {"schema_version": 1, "origin_project": payload["project"],
                      "sources": sources}
        encoded = canonical(provenance)
        # Both canonical UTF8 and the spaced JSON representation are bounded;
        # SQL independently bounds PostgreSQL's own JSONB text representation.
        if (len(encoded) > MAX_PROVENANCE_BYTES or
                len(json.dumps(provenance, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_PROVENANCE_BYTES):
            raise ImportError()
        return ImportPlan(principal, target, expected_revision, expected_sha256,
                          summary["payload_sha256"], canonical(payload).decode("utf-8"),
                          encoded.decode("utf-8"))
    except ImportError:
        raise
    except (BundleError, InvalidProject, ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ImportError() from None


@dataclass(frozen=True)
class ImportReceipt:
    target_project_id: UUID
    artifact_sha256: str
    payload_sha256: str
    origin_project_id: UUID
    origin_revision: int
    target_revision: int
    imported_at: datetime
    provenance_sha256: str
    _provenance_json: str

    @property
    def provenance(self):
        return json.loads(self._provenance_json)

    def summary(self):
        data = self.provenance
        return {"schema_version": 1, "scope": "selected_sources_only",
                "artifact_sha256": self.artifact_sha256,
                "payload_sha256": self.payload_sha256,
                "provenance_sha256": self.provenance_sha256,
                "target_revision_at_import": self.target_revision,
                "imported_at": self.imported_at.isoformat(),
                "source_count": len(data["sources"]),
                "passage_count": sum(len(s["passages"]) for s in data["sources"]),
                "warning": WARNING, "original_binaries_restored": False,
                "graph_restored": False, "publisher_authenticated": False}


def _receipt_digest(plan, target_revision, imported_at, provenance):
    # Bind all lineage fields and new timestamp as well as inert metadata. This
    # is integrity against accidental/direct content edits, not DB-admin trust.
    return hashlib.sha256(canonical({
        "target_project_id": str(plan.target_project_id),
        "artifact_sha256": plan.artifact_sha256, "payload_sha256": plan.payload_sha256,
        "target_revision": target_revision, "imported_at": imported_at.isoformat(),
        "provenance": provenance})).hexdigest()


def _expected_sources(plan, imported_at):
    for source in plan.payload["sources"]:
        revision = remap_id(plan.target_project_id, "source", plan.artifact_sha256, source["source_revision"])
        row = (revision, plan.target_project_id, source["name"], source["text"],
               source["text_sha256"], source["byte_length"], source["codepoint_length"], imported_at)
        passages = []
        for ordinal, passage in enumerate(source["passages"]):
            passages.append((remap_id(plan.target_project_id, "passage", plan.artifact_sha256, passage["evidence_id"]),
                plan.target_project_id, revision, ordinal, passage["start"], passage["end"],
                passage["page"], passage["excerpt"], passage["excerpt_sha256"]))
        yield row, passages


def _verify_sources(conn, plan, imported_at, storage_error):
    for source, passages in _expected_sources(plan, imported_at):
        actual = conn.execute(
            "SELECT source_revision,project_id,name,retained_text,text_sha256,byte_length,codepoint_length,recorded_at "
            "FROM mf_app.source_revisions WHERE source_revision=%s FOR UPDATE", (source[0],)).fetchone()
        if actual != source:
            raise storage_error()
        actual_passages = conn.execute(
            "SELECT evidence_id,project_id,source_revision,ordinal,start_offset,end_offset,page,excerpt,excerpt_sha256 "
            "FROM mf_app.passage_evidence WHERE project_id=%s AND source_revision=%s ORDER BY ordinal FOR UPDATE",
            (plan.target_project_id, source[0])).fetchall()
        if actual_passages != passages:
            raise storage_error()


class ResearchImportStore:
    def __init__(self, connection_factory):
        self._factory = connection_factory

    def import_bundle(self, raw, principal, target_project_id, expected_revision, expected_sha256):
        plan = prepare_import(raw, principal, target_project_id, expected_revision, expected_sha256)
        # Driver/storage dependencies are needed only after pure admission.
        import psycopg
        from psycopg.types.json import Jsonb
        from .store import Conflict, NotFound, StorageError
        try:
            with self._factory() as conn:
                with conn.transaction():
                    conn.execute("SET LOCAL statement_timeout = '5s'")
                    conn.execute("SET LOCAL lock_timeout = '2s'")
                    conn.execute("SET LOCAL idle_in_transaction_session_timeout = '10s'")
                    conn.execute("SET LOCAL TIME ZONE 'UTC'")
                    target = conn.execute(
                        "SELECT current_revision FROM mf_app.projects WHERE principal=%s AND project_id=%s FOR UPDATE",
                        (plan.principal, plan.target_project_id)).fetchone()
                    if target is None:
                        raise NotFound()
                    if target[0] != plan.expected_revision:
                        raise Conflict()
                    prior = conn.execute(
                        "SELECT payload_sha256,origin_project_id,origin_revision,target_revision,imported_at,provenance,provenance_sha256 "
                        "FROM mf_app.research_imports WHERE target_project_id=%s AND artifact_sha256=%s FOR UPDATE",
                        (plan.target_project_id, plan.artifact_sha256)).fetchone()
                    origin = plan.payload["project"]
                    provenance = plan.provenance
                    if prior is not None:
                        payload_hash, origin_id, origin_revision, target_revision, imported_at, stored, digest = prior
                        if (payload_hash != plan.payload_sha256 or origin_id != UUID(origin["project_id"])
                                or origin_revision != origin["revision"]
                                or type(target_revision) is not int or not 1 <= target_revision <= target[0]
                                or type(imported_at) is not datetime or imported_at.tzinfo is None
                                or imported_at.utcoffset() is None
                                or canonical(stored) != canonical(provenance)):
                            raise StorageError()
                        imported_at = imported_at.astimezone(timezone.utc)
                        if digest != _receipt_digest(plan, target_revision, imported_at, provenance):
                            raise StorageError()
                        _verify_sources(conn, plan, imported_at, StorageError)
                    else:
                        imported_at = datetime.now(timezone.utc)
                        target_revision = target[0]
                        digest = _receipt_digest(plan, target_revision, imported_at, provenance)
                        for source, passages in _expected_sources(plan, imported_at):
                            conn.execute(
                                "INSERT INTO mf_app.source_revisions "
                                "(source_revision,project_id,name,retained_text,text_sha256,byte_length,codepoint_length,recorded_at) "
                                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", source)
                            for passage in passages:
                                conn.execute(
                                    "INSERT INTO mf_app.passage_evidence "
                                    "(evidence_id,project_id,source_revision,ordinal,start_offset,end_offset,page,excerpt,excerpt_sha256) "
                                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", passage)
                        conn.execute(
                            "INSERT INTO mf_app.research_imports "
                            "(target_project_id,artifact_sha256,payload_sha256,origin_project_id,origin_revision,target_revision,imported_at,provenance,provenance_sha256) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (plan.target_project_id, plan.artifact_sha256, plan.payload_sha256,
                             UUID(origin["project_id"]), origin["revision"], target_revision,
                             imported_at, Jsonb(provenance), digest))
                        _verify_sources(conn, plan, imported_at, StorageError)
                    return ImportReceipt(plan.target_project_id, plan.artifact_sha256, plan.payload_sha256,
                        UUID(origin["project_id"]), origin["revision"], target_revision, imported_at,
                        digest, plan._provenance_json)
        except psycopg.errors.UniqueViolation:
            raise Conflict() from None
        except psycopg.Error:
            raise StorageError() from None
        except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
            raise StorageError() from None

"""Installed PostgreSQL-only retained source authority. No graph runtime imports."""
import hashlib
import base64
import json
import os
import re
from dataclasses import dataclass
from uuid import UUID, uuid5
from unicodedata import category

import psycopg

from .bindings import ScopeBindingStore
from .contracts import KnowledgeScope
from .operations import (NotFound as BindingNotFound, Tombstoned, Conflict as BindingConflict)
from .stdio import _object, PipeProtocolError
from mirofish_storage import ProjectStore, SourceStore, NotFound, Conflict

MAX_BYTES = 4 * 1024 * 1024
ENVELOPE_OVERHEAD = 1024
TEXT_BYTES = 1024 * 1024
ERROR_CODES = {"invalid_request", "source_unavailable", "not_found", "source_denied",
               "conflict", "tombstoned", "result_too_large"}
SCOPE_FIELDS = {"schema_version", "workspace_id", "project_id", "graph_id", "run_id", "branch_id", "layer"}


class SourceError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def uuid_value(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError
    return UUID(value)


def scope_value(value):
    if (type(value) is not dict or set(value) != SCOPE_FIELDS
            or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        raise ValueError
    for key in ("workspace_id", "project_id", "graph_id"):
        uuid_value(value[key])
    for key in ("run_id", "branch_id"):
        if value[key] is not None:
            uuid_value(value[key])
    scope = KnowledgeScope.model_validate_json(encoded(value))
    if scope.model_dump(mode="json") != value:
        raise ValueError
    return scope


def text_value(value, *, name=False):
    if (type(value) is not str or not value.strip() or (name and len(value) > 256)
            or any((ord(c) < 32 and (name or c not in "\t\n\r")) or ord(c) == 127
                   or (name and category(c) == "Cc") for c in value)
            or len(value.encode()) > (1024 if name else TEXT_BYTES)):
        raise ValueError


def declarations(revision, text, blocks):
    namespace = uuid_value(revision)
    result = []
    if blocks is None:
        start, size = 0, 0
        for end, char in enumerate(text):
            width = len(char.encode())
            if size + width > 32768:
                identity = f"retained-text-v1:{len(result)}:{start}:{end}"
                result.append({"evidence_id": str(uuid5(namespace, identity)), "start": start, "end": end})
                start, size = end, 0
            size += width
        if start < len(text):
            identity = f"retained-text-v1:{len(result)}:{start}:{len(text)}"
            result.append({"evidence_id": str(uuid5(namespace, identity)), "start": start, "end": len(text)})
    else:
        if type(blocks) is not list or len(blocks) > 5000:
            raise ValueError
        previous = 0
        fields = {"kind", "start", "end", "table", "row", "cell", "grid_span", "vertical_merge", "ordinal", "empty"}
        for ordinal, block in enumerate(blocks):
            if (type(block) is not dict or set(block) != fields or type(block["ordinal"]) is not int
                    or block["ordinal"] != ordinal or type(block["start"]) is not int
                    or type(block["end"]) is not int or not previous <= block["start"] <= block["end"] <= len(text)
                    or type(block["empty"]) is not bool or block["empty"] != (block["start"] == block["end"])
                    or block["kind"] not in {"paragraph", "table_cell"}
                    or type(block["grid_span"]) is not int or not 1 <= block["grid_span"] <= 9999
                    or block["vertical_merge"] not in {None, "restart", "continue"}
                    or any(block[key] is not None and (type(block[key]) is not int or block[key] < 0)
                           for key in ("table", "row", "cell"))):
                raise ValueError
            previous = block["end"]
            if not block["empty"]:
                identity = "docx-main-body-v1:" + json.dumps(block, ensure_ascii=False, sort_keys=True,
                    allow_nan=False, separators=(",", ":"))
                result.append({"evidence_id": str(uuid5(namespace, identity)), "start": block["start"], "end": block["end"]})
    if not 1 <= len(result) <= 100 or any(len(text[p["start"]:p["end"]].encode()) > 32768 for p in result):
        raise ValueError
    return result


def validate_payload(method, payload):
    if type(payload) is not dict or len(encoded(payload)) > MAX_BYTES:
        raise ValueError
    if method in {"context", "list"}:
        if payload:
            raise ValueError
    elif method == "get":
        if set(payload) != {"source_revision"}:
            raise ValueError
        uuid_value(payload["source_revision"])
    elif method == "retain":
        if set(payload) != {"source_revision", "source_name", "text", "blocks"}:
            raise ValueError
        uuid_value(payload["source_revision"])
        text_value(payload["source_name"], name=True)
        text_value(payload["text"])
        declarations(payload["source_revision"], payload["text"], payload["blocks"])
    elif method == "retain_pdf":
        pdf_input(payload)
    else:
        raise ValueError


def pdf_input(payload):
    """Validate decoded input and digest before importing any PDF runtime."""
    if (type(payload) is not dict or set(payload) != {"schema_version", "source_revision",
            "source_name", "format", "content", "input_sha256"}
            or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
            or type(payload["format"]) is not str or payload["format"] != "pdf"):
        raise ValueError
    uuid_value(payload["source_revision"])
    text_value(payload["source_name"], name=True)
    content = payload["content"]
    if (type(content) is not str or not content.isascii()
            or not 0 < len(content) <= 4 * ((2 * TEXT_BYTES + 2) // 3)
            or type(payload["input_sha256"]) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", payload["input_sha256"])):
        raise ValueError
    binary = base64.b64decode(content, validate=True)
    if (not 0 < len(binary) <= 2 * TEXT_BYTES
            or base64.b64encode(binary).decode("ascii") != content
            or hashlib.sha256(binary).hexdigest() != payload["input_sha256"]
            or b"%PDF-" not in binary[:1024] or not binary.rstrip().endswith(b"%%EOF")):
        raise ValueError
    return binary


@dataclass(frozen=True, repr=False)
class SourceSettings:
    principal: str
    display_graph_id: str
    scope: KnowledgeScope
    pg: dict

    @classmethod
    def from_environment(cls):
        if any(key in os.environ for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE", "PGOPTIONS")):
            raise ValueError
        principal = os.environ["KNOWLEDGE_PRINCIPAL"]
        display = os.environ["KNOWLEDGE_DISPLAY_GRAPH_ID"]
        if (not 1 <= len(principal) <= 128 or not principal.strip()
                or any(not 32 <= ord(c) <= 126 for c in principal)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", display)):
            raise ValueError
        raw = os.environ["KNOWLEDGE_BOUND_SCOPE_JSON"].encode()
        if len(raw) > 32768:
            raise ValueError
        scope = scope_value(_object(raw))
        port = os.environ["KNOWLEDGE_PG_PORT"]
        if not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535 or str(int(port)) != port:
            raise ValueError
        pg = {"host": os.environ["KNOWLEDGE_PG_HOST"], "dbname": os.environ["KNOWLEDGE_PG_DATABASE"],
              "user": os.environ["KNOWLEDGE_PG_USER"], "password": os.environ["KNOWLEDGE_PG_PASSWORD"],
              "port": int(port), "connect_timeout": 3}
        for key in ("host", "dbname", "user"):
            value = pg[key]
            if not 1 <= len(value) <= (255 if key == "host" else 128) or any(not 33 <= ord(c) <= 126 for c in value):
                raise ValueError
        password = pg["password"]
        if not 1 <= len(password) <= 1024 or any(ord(c) < 32 or ord(c) == 127 for c in password):
            raise ValueError
        return cls(principal, display, scope, pg)

    def connect(self):
        return psycopg.connect(**self.pg)


def metadata(record):
    stamp = record.recorded_at
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise SourceError("source_unavailable")
    # Stored records retain the accepted SourceStore policy. New uploads still
    # use text_value: nonblank and control restricted before mutation.
    if type(record.name) is not str or not record.name or len(record.name) > 256 or "\x00" in record.name:
        raise SourceError("source_unavailable")
    record.name.encode("utf-8")
    return {"project_id": str(record.project_id), "source_revision": str(record.source_revision),
        "source_name": record.name, "text_sha256": record.text_sha256,
        "byte_length": record.byte_length, "codepoint_length": record.codepoint_length,
        "recorded_at": stamp.isoformat()}


def source_result(record, *, include_text=False):
    result = {"schema_version": 1, "binary_retained": False, "graph_ingestion_executed": False,
        "source": metadata(record), "offset_unit": "unicode_codepoint", "passages": [
            {"evidence_id": str(p.evidence_id), "start": p.start, "end": p.end,
             "page": p.page, "excerpt_sha256": p.excerpt_sha256} for p in record.passages]}
    if include_text:
        result["text"] = record.text
    return result


class SourceLibrary:
    def __init__(self, settings, *, connection_factory=None):
        self.settings = settings
        self.connect = connection_factory or settings.connect

    def authorize(self):
        setting = self.settings
        binding = ScopeBindingStore(self.connect).resolve(setting.principal, setting.display_graph_id)
        scope = binding.scope
        if (scope != setting.scope or scope.layer.value != "source"
                or scope.run_id is not None or scope.branch_id is not None):
            raise SourceError("source_denied")
        project = ProjectStore(self.connect).get(setting.principal, scope.project_id)
        if (project.principal != setting.principal or project.project_id != scope.project_id
                or project.workspace_id != scope.workspace_id):
            raise SourceError("source_denied")
        return scope

    def execute(self, method, payload):
        validate_payload(method, payload)
        scope = self.authorize()
        store = SourceStore(self.connect)
        common = {"schema_version": 1, "binary_retained": False, "graph_ingestion_executed": False}
        if method == "context":
            return {**common, "scope": scope.model_dump(mode="json")}
        if method == "list":
            records = store.list_sources(self.settings.principal, scope.project_id, limit=21)
            return {**common, "sources": [metadata(r) for r in records[:20]],
                    "has_more": len(records) > 20, "window_limit": 20}
        if method == "get":
            return source_result(store.get_source(self.settings.principal, scope.project_id,
                                 payload["source_revision"]), include_text=True)
        if method == "retain_pdf":
            # This lazy import is reached only after strict bytes/digest admission
            # and persisted scope/project authorization in the fixed source child.
            from mirofish_storage.pdf import extract_pdf, PdfError
            try:
                extracted = extract_pdf(pdf_input(payload))
            except PdfError as error:
                raise SourceError(error.code) from None
            passages = extracted.declarations(payload["source_revision"])
            extraction = {"format": "pdf", "input_hash_verified": True,
                "input_sha256": payload["input_sha256"], "input_digest_persisted": False,
                "blocks_persisted": False, "original_document_verified": False,
                "binary_persistently_bound": False, "ocr_performed": False,
                "page_layout": "unknown", "semantic_quality": "unknown", "coverage": ["page_text"],
                "page_text": [extracted.text[p["start"]:p["end"]] for p in extracted.pages],
                "pages": list(extracted.pages), "page_count": extracted.page_count,
                "empty_page_count": extracted.empty_page_count, "declared_passage_count": len(passages)}
            # Reserve the exact variable-size receipt and a conservative metadata
            # allowance before the one immutable source transaction.
            if len(encoded(extraction)) + len(encoded(passages)) + 100 * 256 + 4096 > MAX_BYTES:
                raise SourceError("result_too_large")
            text_value(extracted.text)
            self.authorize()
            record = store.ingest_text(self.settings.principal, scope.project_id, payload["source_revision"],
                payload["source_name"], extracted.text, passages)
            return {**source_result(record), "extraction": extraction}
        passages = declarations(payload["source_revision"], payload["text"], payload["blocks"])
        # The mutation result contains at most 100 small metadata records, no full text.
        # Admission reserves worst-case escaped name/time/offset fields before insert.
        if len(encoded(passages)) + len(encoded(payload["source_name"])) + 100 * 256 + 4096 > MAX_BYTES:
            raise SourceError("result_too_large")
        # Re-resolve persisted authority immediately before the independent source transaction.
        self.authorize()
        record = store.ingest_text(self.settings.principal, scope.project_id, payload["source_revision"],
                                   payload["source_name"], payload["text"], passages)
        return source_result(record)

    def dispatch(self, raw):
        request_id = None
        try:
            if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES + ENVELOPE_OVERHEAD:
                raise ValueError
            value = _object(raw)
            if (set(value) != {"version", "request_id", "method", "scope", "payload"}
                    or type(value["version"]) is not int or value["version"] != 1):
                raise ValueError
            request_id = str(uuid_value(value["request_id"]))
            if scope_value(value["scope"]) != self.settings.scope:
                raise SourceError("source_denied")
            validate_payload(value["method"], value["payload"])
            result = self.execute(value["method"], value["payload"])
            if len(encoded(result)) > MAX_BYTES:
                raise SourceError("result_too_large")
            return encoded({"version": 1, "request_id": request_id, "ok": True, "result": result})
        except SourceError as error:
            code = error.code if error.code in ERROR_CODES else "source_unavailable"
        except (BindingNotFound, NotFound):
            code = "not_found"
        except Tombstoned:
            code = "tombstoned"
        except (Conflict, BindingConflict):
            code = "conflict"
        except (ValueError, TypeError, KeyError, UnicodeError, PipeProtocolError, RecursionError, OverflowError):
            code = "invalid_request"
        except Exception:
            code = "source_unavailable"
        return encoded({"version": 1, "request_id": request_id, "ok": False, "error": {"code": code}})

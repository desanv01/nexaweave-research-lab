"""Source-only profile of the accepted owned process transport (stdlib only)."""
import hashlib
import base64
import json
import re
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid5
from unicodedata import category

from .knowledge_reader import _scope
from .knowledge_transport import (KnowledgeProcessClient, KnowledgeInvalidRequest,
    KnowledgeTransportFailure, _json_object, _uuid, _ROOT_FIELDS)

MAX_BYTES = 4 * 1024 * 1024
ENVELOPE_OVERHEAD = 1024
TEXT_BYTES = 1024 * 1024
EXCERPT_BYTES = 32768
ERROR_CODES = frozenset({"invalid_request", "source_unavailable", "not_found",
    "source_denied", "conflict", "tombstoned", "result_too_large"})
CHILD_KEYS = ("KNOWLEDGE_PRINCIPAL", "KNOWLEDGE_DISPLAY_GRAPH_ID", "KNOWLEDGE_BOUND_SCOPE_JSON",
    "KNOWLEDGE_PG_HOST", "KNOWLEDGE_PG_PORT", "KNOWLEDGE_PG_DATABASE", "KNOWLEDGE_PG_USER",
    "KNOWLEDGE_PG_PASSWORD")
HASH = re.compile(r"[0-9a-f]{64}\Z")


def pdf_upload(value, *, version=1):
    """Native-free, strict PDF bytes admission shared by facade and transport."""
    if (type(value) is not dict or set(value) != {"schema_version", "source_revision",
            "source_name", "format", "content", "input_sha256"}
            or type(value["schema_version"]) is not int or value["schema_version"] != version
            or type(value["format"]) is not str or value["format"] != "pdf"):
        raise ValueError
    _uuid(value["source_revision"])
    text_value(value["source_name"], 1024, name=True)
    content = value["content"]
    if (type(content) is not str or not content.isascii()
            or not 0 < len(content) <= 4 * ((2 * TEXT_BYTES + 2) // 3)
            or type(value["input_sha256"]) is not str or not HASH.fullmatch(value["input_sha256"])):
        raise ValueError
    binary = base64.b64decode(content, validate=True)
    if (not 0 < len(binary) <= 2 * TEXT_BYTES
            or base64.b64encode(binary).decode("ascii") != content
            or hashlib.sha256(binary).hexdigest() != value["input_sha256"]
            or b"%PDF-" not in binary[:1024] or not binary.rstrip().endswith(b"%%EOF")
            or len(encoded(value)) > MAX_BYTES):
        raise ValueError
    return binary


def pdf_receipt(value, scope, payload):
    """Reconstruct exact retained Unicode text and verify every page and passage."""
    common = {"schema_version", "binary_retained", "graph_ingestion_executed"}
    if (type(value) is not dict or set(value) != common | {"source", "passages", "offset_unit", "extraction"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["binary_retained"] is not False or value["graph_ingestion_executed"] is not False
            or value["offset_unit"] != "unicode_codepoint"):
        raise ValueError
    extraction = value["extraction"]
    fields = {"format", "input_hash_verified", "input_sha256", "input_digest_persisted",
        "blocks_persisted", "original_document_verified", "binary_persistently_bound",
        "ocr_performed", "page_layout", "semantic_quality", "coverage", "page_text", "pages",
        "page_count", "empty_page_count", "declared_passage_count"}
    if (type(extraction) is not dict or set(extraction) != fields
            or any(type(extraction[k]) is not str for k in ("format", "input_sha256", "page_layout", "semantic_quality"))
            or type(extraction["coverage"]) is not list
            or extraction["format"] != "pdf" or payload["format"] != "pdf"
            or extraction["input_hash_verified"] is not True
            or extraction["input_sha256"] != payload["input_sha256"]
            or any(extraction[k] is not False for k in ("input_digest_persisted", "blocks_persisted",
                "original_document_verified", "binary_persistently_bound", "ocr_performed"))
            or extraction["page_layout"] != "unknown" or extraction["semantic_quality"] != "unknown"
            or extraction["coverage"] != ["page_text"]):
        raise ValueError
    count = extraction["page_count"]
    texts, pages = extraction["page_text"], extraction["pages"]
    if (type(count) is not int or not 1 <= count <= 100 or type(texts) is not list
            or type(pages) is not list or len(texts) != count or len(pages) != count
            or type(extraction["empty_page_count"]) is not int
            or type(extraction["declared_passage_count"]) is not int):
        raise ValueError
    expected, offset, size, empty_count = [], 0, 0, 0
    for ordinal, (text, page) in enumerate(zip(texts, pages)):
        if type(text) is not str or any((ord(c) < 32 and c not in "\t\n\r") or ord(c) == 127 for c in text):
            raise ValueError
        raw = text.encode("utf-8")
        offset += 2 if ordinal else 0
        size += len(raw) + (2 if ordinal else 0)
        if len(raw) > EXCERPT_BYTES or size > TEXT_BYTES:
            raise ValueError
        declared = {"page": ordinal + 1, "start": offset, "end": offset + len(text),
            "empty": not text.strip(), "excerpt_sha256": hashlib.sha256(raw).hexdigest()}
        if (type(page) is not dict or set(page) != set(declared)
                or any(type(page[k]) is not int for k in ("page", "start", "end"))
                or type(page["empty"]) is not bool or type(page["excerpt_sha256"]) is not str or page != declared):
            raise ValueError
        if declared["empty"]:
            empty_count += 1
        else:
            identity = "pdf-page-text-v1:" + json.dumps(declared, sort_keys=True,
                ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            expected.append({"evidence_id": str(uuid5(UUID(payload["source_revision"]), identity)),
                "start": declared["start"], "end": declared["end"], "page": ordinal + 1,
                "excerpt_sha256": declared["excerpt_sha256"]})
        offset += len(text)
    if (not expected or extraction["empty_page_count"] != empty_count
            or extraction["declared_passage_count"] != len(expected)
            or type(value["passages"]) is not list or len(value["passages"]) != len(expected)):
        raise ValueError
    for actual, declared in zip(value["passages"], expected):
        if (type(actual) is not dict or set(actual) != set(declared)
                or any(type(actual[k]) is not str for k in ("evidence_id", "excerpt_sha256"))
                or any(type(actual[k]) is not int for k in ("start", "end", "page")) or actual != declared):
            raise ValueError
    source = value["source"]
    metadata(source, scope)
    text = "\n\n".join(texts)
    if (source["source_revision"] != payload["source_revision"] or source["source_name"] != payload["source_name"]
            or source["text_sha256"] != hashlib.sha256(text.encode()).hexdigest()
            or source["byte_length"] != size or source["codepoint_length"] != len(text)
            or len(encoded(value)) > MAX_BYTES):
        raise ValueError
    return value


def original_binary(value, scope, revision):
    if (type(value) is not dict or set(value) != {"contract_version", "project_id",
            "source_revision", "media_type", "byte_length", "sha256"}
            or type(value["contract_version"]) is not int or value["contract_version"] != 1
            or value["project_id"] != scope["project_id"] or value["source_revision"] != revision
            or value["media_type"] != "application/pdf" or type(value["byte_length"]) is not int
            or not 0 < value["byte_length"] <= 2 * TEXT_BYTES
            or type(value["sha256"]) is not str or not HASH.fullmatch(value["sha256"])):
        raise ValueError
    return value


def original_result(method, value, scope, payload):
    if method not in {"retain_pdf_binary", "binary_metadata", "binary_read"}:
        raise ValueError
    if method == "retain_pdf_binary":
        if (type(value) is not dict or set(value) != {"schema_version", "binary_retained",
                "graph_ingestion_executed", "source", "passages", "offset_unit", "extraction", "binary"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 2
                or value["binary_retained"] is not True or value["graph_ingestion_executed"] is not False):
            raise ValueError
        binary = original_binary(value["binary"], scope, payload["source_revision"])
        if binary["sha256"] != payload["input_sha256"]:
            raise ValueError
        extraction = value["extraction"]
        if (type(extraction) is not dict or extraction.get("input_hash_verified") is not True
                or any(extraction.get(k) is not True for k in ("input_digest_persisted",
                    "original_document_verified", "binary_persistently_bound"))
                or extraction.get("blocks_persisted") is not False
                or extraction.get("ocr_performed") is not False):
            raise ValueError
        old = {k: v for k, v in value.items() if k != "binary"}
        old["schema_version"] = 1
        old["binary_retained"] = False
        old["extraction"] = dict(extraction, input_digest_persisted=False,
            original_document_verified=False, binary_persistently_bound=False)
        pdf_receipt(old, scope, dict(payload, schema_version=1))
        if value["source"]["project_id"] != binary["project_id"]:
            raise ValueError
        if len(encoded(value)) > MAX_BYTES:
            raise ValueError
        return value
    fields = {"schema_version", "binary_retained", "graph_ingestion_executed", "source", "binary"}
    if method == "binary_read":
        fields.add("content_base64")
    if (type(value) is not dict or set(value) != fields
            or type(value["schema_version"]) is not int or value["schema_version"] != 2
            or value["binary_retained"] is not True or value["graph_ingestion_executed"] is not False):
        raise ValueError
    metadata(value["source"], scope)
    binary = original_binary(value["binary"], scope, payload["source_revision"])
    if value["source"]["source_revision"] != payload["source_revision"]:
        raise ValueError
    if method == "binary_read":
        encoded_bytes = value["content_base64"]
        if (type(encoded_bytes) is not str or not encoded_bytes.isascii()
                or len(encoded_bytes) > 4 * ((2 * TEXT_BYTES + 2) // 3)):
            raise ValueError
        raw = base64.b64decode(encoded_bytes, validate=True)
        if (base64.b64encode(raw).decode("ascii") != encoded_bytes
                or len(raw) != binary["byte_length"]
                or hashlib.sha256(raw).hexdigest() != binary["sha256"]):
            raise ValueError
    if len(encoded(value)) > MAX_BYTES:
        raise ValueError
    return value


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def text_value(value, maximum=TEXT_BYTES, *, name=False):
    if (type(value) is not str or not value.strip() or (name and len(value) > 256)
            or any((ord(c) < 32 and (name or c not in "\t\n\r")) or ord(c) == 127
                   or (name and category(c) == "Cc") for c in value)
            or len(value.encode("utf-8")) > maximum):
        raise ValueError
    return value


def retained_text_value(value, *, name=False):
    """Match accepted SourceStore eligibility, not stricter new-upload policy."""
    if (type(value) is not str or not value or "\x00" in value
            or (name and len(value) > 256)
            or (not name and len(value.encode("utf-8")) > TEXT_BYTES)):
        raise ValueError
    value.encode("utf-8")
    return value


def declarations(revision, text, blocks=None):
    """Typed UUID5 identities; offsets count Unicode codepoints, never bytes."""
    namespace = UUID(_uuid(revision))
    result = []
    if blocks is None:
        start, size = 0, 0
        for end, char in enumerate(text):
            width = len(char.encode("utf-8"))
            if size + width > EXCERPT_BYTES:
                ordinal = len(result)
                identity = f"retained-text-v1:{ordinal}:{start}:{end}"
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
                result.append({"evidence_id": str(uuid5(namespace, identity)),
                    "start": block["start"], "end": block["end"]})
    if not 1 <= len(result) <= 100 or any(len(text[p["start"]:p["end"]].encode("utf-8")) > EXCERPT_BYTES for p in result):
        raise ValueError
    return result


def validate_payload(method, value):
    try:
        if type(value) is not dict:
            raise ValueError
        if method in {"context", "list"}:
            if value:
                raise ValueError
        elif method in {"get", "binary_metadata", "binary_read"}:
            if set(value) != {"source_revision"}:
                raise ValueError
            _uuid(value["source_revision"])
        elif method == "retain":
            if set(value) != {"source_revision", "source_name", "text", "blocks"}:
                raise ValueError
            _uuid(value["source_revision"])
            text_value(value["source_name"], 1024, name=True)
            text_value(value["text"])
            declarations(value["source_revision"], value["text"], value["blocks"])
        elif method in {"retain_pdf", "retain_pdf_binary"}:
            pdf_upload(value, version=2 if method == "retain_pdf_binary" else 1)
        else:
            raise ValueError
        if len(encoded(value)) > MAX_BYTES:
            raise ValueError
        return value
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise KnowledgeInvalidRequest() from None


def metadata(value, scope):
    if type(value) is not dict or set(value) != {"project_id", "source_revision", "source_name",
            "text_sha256", "byte_length", "codepoint_length", "recorded_at"}:
        raise ValueError
    if _uuid(value["project_id"]) != scope["project_id"]:
        raise ValueError
    _uuid(value["source_revision"])
    retained_text_value(value["source_name"], name=True)
    if type(value["text_sha256"]) is not str or not HASH.fullmatch(value["text_sha256"]):
        raise ValueError
    for key in ("byte_length", "codepoint_length"):
        if type(value[key]) is not int or not 1 <= value[key] <= TEXT_BYTES:
            raise ValueError
    if not value["codepoint_length"] <= value["byte_length"] <= 4 * value["codepoint_length"]:
        raise ValueError
    stamp = value["recorded_at"]
    if type(stamp) is not str or len(stamp) > 64:
        raise ValueError
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError


def validate_result(method, value, scope, payload):
    try:
        if method in {"retain_pdf_binary", "binary_metadata", "binary_read"}:
            result = original_result(method, value, scope, payload)
            if len(encoded(result)) > MAX_BYTES:
                raise ValueError
            return result
        if method == "retain_pdf":
            return pdf_receipt(value, scope, payload)
        if (type(value) is not dict or type(value.get("schema_version")) is not int
                or value["schema_version"] != 1 or value.get("binary_retained") is not False
                or value.get("graph_ingestion_executed") is not False):
            raise ValueError
        common = {"schema_version", "binary_retained", "graph_ingestion_executed"}
        if method == "context":
            if set(value) != common | {"scope"} or _scope(value["scope"]) != scope:
                raise ValueError
        elif method == "list":
            if (set(value) != common | {"sources", "has_more", "window_limit"}
                    or type(value["sources"]) is not list or len(value["sources"]) > 20
                    or type(value["has_more"]) is not bool or type(value["window_limit"]) is not int
                    or value["window_limit"] != 20 or (value["has_more"] and len(value["sources"]) != 20)):
                raise ValueError
            seen = set()
            previous = None
            for item in value["sources"]:
                metadata(item, scope)
                if item["source_revision"] in seen:
                    raise ValueError
                seen.add(item["source_revision"])
                stamp = datetime.fromisoformat(item["recorded_at"].replace("Z", "+00:00"))
                current = (stamp, item["source_revision"])
                if previous is not None and (current[0] > previous[0]
                        or (current[0] == previous[0] and current[1] <= previous[1])):
                    raise ValueError
                previous = current
        else:
            fields = common | {"source", "passages", "offset_unit"} | ({"text"} if method == "get" else set())
            if set(value) != fields or value["offset_unit"] != "unicode_codepoint":
                raise ValueError
            source = value["source"]
            metadata(source, scope)
            if source["source_revision"] != payload["source_revision"]:
                raise ValueError
            text = value["text"] if method == "get" else payload["text"]
            if method == "get":
                retained_text_value(text)
            else:
                text_value(text)
            if (source["text_sha256"] != hashlib.sha256(text.encode()).hexdigest()
                    or source["byte_length"] != len(text.encode()) or source["codepoint_length"] != len(text)
                    or (method == "retain" and source["source_name"] != payload["source_name"])):
                raise ValueError
            passages = value["passages"]
            if type(passages) is not list or len(passages) > 100:
                raise ValueError
            seen = set()
            for item in passages:
                if type(item) is not dict or set(item) != {"evidence_id", "start", "end", "page", "excerpt_sha256"}:
                    raise ValueError
                identity = _uuid(item["evidence_id"])
                start, end = item["start"], item["end"]
                if (identity in seen or type(start) is not int or type(end) is not int
                        or not 0 <= start < end <= len(text)
                        or (item["page"] is not None and (type(item["page"]) is not int or not 1 <= item["page"] <= 2147483647))
                        or len(text[start:end].encode()) > EXCERPT_BYTES
                        or item["excerpt_sha256"] != hashlib.sha256(text[start:end].encode()).hexdigest()):
                    raise ValueError
                seen.add(identity)
            if method == "retain":
                expected = declarations(payload["source_revision"], text, payload["blocks"])
                if [{k: p[k] for k in ("evidence_id", "start", "end")} for p in passages] != expected or any(p["page"] is not None for p in passages):
                    raise ValueError
        if len(encoded(value)) > MAX_BYTES:
            raise ValueError
        return value
    except (KeyError, ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise KnowledgeTransportFailure(outcome_unknown=True) from None


class KnowledgeSourceProcessClient(KnowledgeProcessClient):
    def __init__(self, settings):
        self._scope = dict(settings.scope)
        script = Path(settings.bootstrap).with_name("source_bootstrap.py")
        if (script.parent.name != "nexaweave_knowledge" or "site-packages" not in script.parts
                or Path(settings.bootstrap).name != "read_bootstrap.py"):
            raise ValueError("invalid source configuration")
        super().__init__(settings.python, str(script), timeout_seconds=60,
                         child_environment={key: settings.child_environment[key] for key in CHILD_KEYS})

    def _validate_request(self, raw):
        try:
            if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES + ENVELOPE_OVERHEAD:
                raise ValueError
            value = _json_object(raw)
            if (set(value) != _ROOT_FIELDS or type(value["version"]) is not int
                    or value["version"] != 1 or _scope(value["scope"]) != self._scope):
                raise ValueError
            if (self._scope["layer"] != "source" or self._scope["run_id"] is not None
                    or self._scope["branch_id"] is not None):
                raise ValueError
            validate_payload(value["method"], value["payload"])
            return _uuid(value["request_id"])
        except (ValueError, TypeError, KeyError):
            raise KnowledgeInvalidRequest() from None

    def _response_limit(self, raw):
        return MAX_BYTES + ENVELOPE_OVERHEAD

    def _validate_reply(self, raw, request_id):
        try:
            if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES + ENVELOPE_OVERHEAD:
                raise ValueError
            value = _json_object(raw)
            if (type(value.get("version")) is not int or value["version"] != 1
                    or value.get("request_id") != request_id or type(value.get("ok")) is not bool):
                raise ValueError
            if value["ok"]:
                if set(value) != {"version", "request_id", "ok", "result"} or type(value["result"]) is not dict:
                    raise ValueError
            elif (set(value) != {"version", "request_id", "ok", "error"}
                    or type(value["error"]) is not dict or set(value["error"]) != {"code"}
                    or type(value["error"]["code"]) is not str or value["error"]["code"] not in ERROR_CODES):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise KnowledgeTransportFailure(outcome_unknown=True) from None

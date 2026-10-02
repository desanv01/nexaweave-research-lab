"""Source-only profile of the accepted owned process transport (stdlib only)."""
import hashlib
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
        elif method == "get":
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
        if (script.parent.name != "mirofish_knowledge" or "site-packages" not in script.parts
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

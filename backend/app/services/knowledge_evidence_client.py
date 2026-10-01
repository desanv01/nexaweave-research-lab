"""Fixed evidence profile on the accepted owned binary transport (stdlib only)."""
from datetime import datetime
import json
from pathlib import Path

from .knowledge_reader import _DISPLAY_ID, _scope
from .knowledge_transport import (KnowledgeProcessClient, KnowledgeInvalidRequest,
    KnowledgeTransportFailure, _json_object, _uuid, _ROOT_FIELDS)

REQUEST_LIMITS = {"research": 16384, "dossier": 32768}
RESULT_LIMITS = {"research": 2 * 1024 * 1024, "dossier": 4 * 1024 * 1024}
ENVELOPE_OVERHEAD = 1024
ERROR_CODES = frozenset({"invalid_request", "invalid_configuration", "research_unavailable",
    "research_deadline", "research_busy", "dossier_unavailable", "dossier_deadline",
    "dossier_busy", "result_too_large"})


def _text(value, maximum):
    if type(value) is not str or not 1 <= len(value) <= maximum or not value.strip():
        raise ValueError
    value.encode("utf-8")


def validate_payload(method, value, anchor):
    """Validate public request shape before any child is admitted or spawned."""
    try:
        if type(method) is not str or method not in REQUEST_LIMITS or type(value) is not dict:
            raise ValueError
        common = {"schema_version", "display_graph_ids", "valid_at", "recorded_before"}
        allowed = common | ({"text", "top_k"} if method == "research" else {"title", "sections"})
        if not set(value) <= allowed:
            raise ValueError
        if type(value.get("schema_version", 1)) is not int or value.get("schema_version", 1) != 1:
            raise ValueError
        ids = value.get("display_graph_ids")
        if (type(ids) is not list or not 1 <= len(ids) <= 5
                or any(type(item) is not str or not _DISPLAY_ID.fullmatch(item) for item in ids)
                or len(set(ids)) != len(ids) or anchor not in ids):
            raise ValueError
        for key in ("valid_at", "recorded_before"):
            if value.get(key) is not None:
                stamp = value[key]
                if type(stamp) is not str or len(stamp) > 64:
                    raise ValueError
                parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    raise ValueError
        entries = [value] if method == "research" else value.get("sections")
        if method == "dossier":
            _text(value.get("title"), 256)
            if type(entries) is not list or not 1 <= len(entries) <= 6:
                raise ValueError
        for entry in entries:
            if type(entry) is not dict:
                raise ValueError
            if method == "dossier":
                if not set(entry) <= {"heading", "query", "top_k"}:
                    raise ValueError
                _text(entry.get("heading"), 256)
            _text(entry.get("text" if method == "research" else "query"), 2000)
            maximum = entry.get("top_k", 10)
            if type(maximum) is not int or not 1 <= maximum <= 100:
                raise ValueError
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) > REQUEST_LIMITS[method]:
            raise ValueError
        return value
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise KnowledgeInvalidRequest() from None


class KnowledgeEvidenceProcessClient(KnowledgeProcessClient):
    def __init__(self, settings):
        self._scope = dict(settings.scope)
        self._anchor = settings.display_graph_id
        script = Path(settings.bootstrap).with_name("evidence_bootstrap.py")
        if (script.parent.name != "mirofish_knowledge" or "site-packages" not in script.parts
                or Path(settings.bootstrap).name != "read_bootstrap.py"):
            raise ValueError("invalid evidence configuration")
        # Only the accepted trusted connection/binding keys cross the boundary.
        from .knowledge_read_facade import _CHILD_KEYS
        environment = {key: settings.child_environment[key] for key in _CHILD_KEYS}
        super().__init__(settings.python, str(script), timeout_seconds=120, child_environment=environment)

    def _validate_request(self, raw):
        try:
            if type(raw) is not bytes or not 0 < len(raw) <= 32768 + ENVELOPE_OVERHEAD:
                raise ValueError
            value = _json_object(raw)
            if (set(value) != _ROOT_FIELDS or type(value["version"]) is not int
                    or value["version"] != 1 or _scope(value["scope"]) != self._scope):
                raise ValueError
            request_id = _uuid(value["request_id"])
            validate_payload(value["method"], value["payload"], self._anchor)
            if len(raw) > REQUEST_LIMITS[value["method"]] + ENVELOPE_OVERHEAD:
                raise ValueError
            return request_id
        except (KeyError, ValueError, TypeError):
            raise KnowledgeInvalidRequest() from None

    def _response_limit(self, raw):
        return RESULT_LIMITS[_json_object(raw)["method"]] + ENVELOPE_OVERHEAD

    def _validate_reply(self, raw, request_id):
        try:
            value = _json_object(raw)
            if (type(value.get("version")) is not int or value["version"] != 1
                    or value.get("request_id") != request_id or type(value.get("ok")) is not bool):
                raise ValueError
            if value["ok"]:
                if set(value) != {"version", "request_id", "ok", "result"} or type(value["result"]) is not dict:
                    raise ValueError
            elif (set(value) != {"version", "request_id", "ok", "error"}
                    or type(value["error"]) is not dict or set(value["error"]) != {"code"}
                    or value["error"]["code"] not in ERROR_CODES):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise KnowledgeTransportFailure(outcome_unknown=True) from None

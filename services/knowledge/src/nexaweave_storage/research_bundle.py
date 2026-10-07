"""Version 1 retained-source data artifacts; inspection never dispatches metadata."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .validation import InvalidProject, HEX, canonical_payload, principal_id, uuid_value

MAX_ARTIFACT_BYTES = 12 * 1024 * 1024
MAX_TEXT_BYTES = 1024 * 1024
MAX_TOTAL_TEXT_BYTES = 8 * 1024 * 1024
WARNING = "private_retained_text_and_snapshot_metadata_not_secret_scanned"


class BundleError(ValueError):
    def __init__(self, code="invalid_bundle"):
        self.code = code
        super().__init__(code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _fields(value, fields):
    if type(value) is not dict or set(value) != set(fields.split()):
        raise BundleError()


def _uuid(value):
    if type(value) is not str:
        raise BundleError()
    return str(uuid_value(value))


def _int(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise BundleError()


def _digest(value, data):
    if type(value) is not str or not HEX.fullmatch(value) or value != hashlib.sha256(data).hexdigest():
        raise BundleError()


def _timestamp(value):
    if type(value) is not str or len(value) > 64:
        raise BundleError()
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BundleError()
    # Check UTC conversion too: offsets must not overflow the calendar.
    parsed.astimezone(timezone.utc)
    if parsed.isoformat() != value:
        raise BundleError()


def _tree(value, depth=0):
    if depth > 32:
        raise BundleError()
    if type(value) is dict:
        if len(value) > 1000:
            raise BundleError()
        for key, item in value.items():
            if type(key) is not str or len(key) > 256:
                raise BundleError()
            key.encode("utf-8")
            _tree(item, depth + 1)
    elif type(value) is list:
        if len(value) > 1000:
            raise BundleError()
        for item in value:
            _tree(item, depth + 1)
    elif type(value) is str:
        if len(value.encode("utf-8")) > MAX_TEXT_BYTES:
            raise BundleError()
    elif value is None or type(value) is bool:
        pass
    elif type(value) is int:
        if abs(value) > 1_000_000_000_000:
            raise BundleError()
    elif type(value) is float:
        import math
        if not math.isfinite(value) or abs(value) > 1_000_000_000_000:
            raise BundleError()
    else:
        raise BundleError()


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise BundleError()
            result[key] = value
        return result
    def constant(_):
        raise BundleError()
    try:
        if type(raw) is not bytes or len(raw) > MAX_ARTIFACT_BYTES:
            raise BundleError()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
        _tree(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise BundleError() from None


def _validate(value):
    _fields(value, "schema_version kind payload payload_sha256")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["kind"] != "mirofish_retained_sources":
        raise BundleError()
    payload = value["payload"]
    _fields(payload, "scope project sources")
    if payload["scope"] != "selected_sources_only":
        raise BundleError()
    project = payload["project"]
    _fields(project, "workspace_id project_id display_id revision snapshot evidence digest")
    _uuid(project["workspace_id"])
    project_id = _uuid(project["project_id"])
    _int(project["revision"], 1, 2_147_483_647)
    _, _, digest = canonical_payload(project["snapshot"], project["evidence"], project["display_id"])
    if project["digest"] != digest:
        raise BundleError()
    sources = payload["sources"]
    if type(sources) is not list or not 1 <= len(sources) <= 16:
        raise BundleError()
    seen_sources, seen_evidence = set(), set()
    # Project evidence is inert reference metadata, validated independently by
    # canonical_payload. References may name an included retained passage.
    # Only actual retained passage records must be globally unique here.
    total, passage_count = 0, 0
    for source in sources:
        _fields(source, "project_id source_revision name text text_sha256 byte_length codepoint_length recorded_at passages")
        revision = _uuid(source["source_revision"])
        if revision in seen_sources or _uuid(source["project_id"]) != project_id:
            raise BundleError()
        seen_sources.add(revision)
        name, text = source["name"], source["text"]
        if type(name) is not str or not name or len(name) > 256 or "\x00" in name:
            raise BundleError()
        name.encode("utf-8")
        if type(text) is not str or not text or "\x00" in text:
            raise BundleError()
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_TEXT_BYTES:
            raise BundleError()
        total += len(encoded)
        if total > MAX_TOTAL_TEXT_BYTES:
            raise BundleError()
        _int(source["byte_length"], len(encoded), len(encoded))
        _int(source["codepoint_length"], len(text), len(text))
        _digest(source["text_sha256"], encoded)
        _timestamp(source["recorded_at"])
        passages = source["passages"]
        if type(passages) is not list or len(passages) > 100:
            raise BundleError()
        for passage in passages:
            _fields(passage, "evidence_id source_revision project_id start end page excerpt excerpt_sha256")
            evidence = _uuid(passage["evidence_id"])
            if (evidence in seen_evidence or _uuid(passage["source_revision"]) != revision
                    or _uuid(passage["project_id"]) != project_id):
                raise BundleError()
            seen_evidence.add(evidence)
            _int(passage["start"], 0, len(text) - 1)
            _int(passage["end"], passage["start"] + 1, len(text))
            if passage["page"] is not None:
                _int(passage["page"], 1, 2_147_483_647)
            excerpt = text[passage["start"]:passage["end"]]
            if type(passage["excerpt"]) is not str or passage["excerpt"] != excerpt:
                raise BundleError()
            excerpt_bytes = excerpt.encode("utf-8")
            if len(excerpt_bytes) > 32768:
                raise BundleError()
            _digest(passage["excerpt_sha256"], excerpt_bytes)
            passage_count += 1
    _digest(value["payload_sha256"], canonical(payload))
    return {"schema_version": 1, "scope": payload["scope"],
            "project_id": project_id, "project_revision": project["revision"],
            "source_count": len(sources), "passage_count": passage_count,
            "text_byte_length": total, "payload_sha256": value["payload_sha256"],
            "warning": WARNING, "original_binaries_included": False,
            "publisher_authenticated": False}


def inspect_bundle(raw, expected_sha256=None):
    try:
        value = decode(raw)
        if expected_sha256 is not None:
            _digest(expected_sha256, raw)
        result = _validate(value)
        result.update(artifact_sha256=hashlib.sha256(raw).hexdigest(), artifact_byte_length=len(raw))
        return result
    except (InvalidProject, ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise BundleError() from None


def export_bundle(project_store, source_store, principal, project_id, revision, source_revisions):
    """Stores resolve persisted ownership for every read before any file creation."""
    try:
        principal = principal_id(principal)
        project_id = _uuid(project_id)
        _int(revision, 1, 2_147_483_647)
        if type(source_revisions) is not list or not 1 <= len(source_revisions) <= 16:
            raise BundleError("invalid_request")
        selected = [_uuid(item) for item in source_revisions]
        if len(set(selected)) != len(selected):
            raise BundleError("invalid_request")
        project = project_store.get(principal, project_id, revision)
        if str(project.project_id) != project_id or project.revision != revision or project.principal != principal:
            raise BundleError()
        sources = []
        total_text_bytes = 0
        # Selection is a set; canonical order makes caller ordering irrelevant.
        for selected_revision in sorted(selected):
            source = source_store.get_source(principal, project_id, selected_revision)
            # Count actual retained UTF-8 bytes, not caller/stored length claims.
            # Stop before another source read or assembling this source's data.
            total_text_bytes += len(source.text.encode("utf-8"))
            if total_text_bytes > MAX_TOTAL_TEXT_BYTES:
                raise BundleError()
            if str(source.source_revision) != selected_revision:
                raise BundleError()
            passages = [{"evidence_id": str(p.evidence_id), "source_revision": str(p.source_revision),
                         "project_id": str(p.project_id), "start": p.start, "end": p.end,
                         "page": p.page, "excerpt": p.excerpt, "excerpt_sha256": p.excerpt_sha256}
                        for p in source.passages]
            sources.append({"project_id": str(source.project_id), "source_revision": str(source.source_revision),
                            "name": source.name, "text": source.text, "text_sha256": source.text_sha256,
                            "byte_length": source.byte_length, "codepoint_length": source.codepoint_length,
                            "recorded_at": source.recorded_at.isoformat(), "passages": passages})
        payload = {"scope": "selected_sources_only", "project": {
            "workspace_id": str(project.workspace_id), "project_id": str(project.project_id),
            "display_id": project.display_id, "revision": project.revision,
            "snapshot": project.snapshot, "evidence": list(project.evidence), "digest": project.digest},
            "sources": sources}
        raw = canonical({"schema_version": 1, "kind": "mirofish_retained_sources", "payload": payload,
                         "payload_sha256": hashlib.sha256(canonical(payload)).hexdigest()})
        inspect_bundle(raw)
        return raw
    except InvalidProject:
        raise BundleError("invalid_request") from None

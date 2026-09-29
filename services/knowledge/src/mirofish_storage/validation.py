"""Pure, strict validation of inherited Project.to_dict v1 metadata."""

from __future__ import annotations

import hashlib
import json
import re
from uuid import UUID


class InvalidProject(ValueError):
    def __init__(self):
        super().__init__("invalid_project")


FIELDS = frozenset({
    "project_id", "name", "status", "created_at", "updated_at", "files",
    "total_text_length", "ontology", "analysis_summary", "graph_id",
    "graph_build_task_id", "zep_batch_id", "zep_batch_operation_id",
    "simulation_requirement", "chunk_size", "chunk_overlap", "error",
})
STATUSES = frozenset({"created", "ontology_generated", "graph_building",
                      "graph_completed", "failed"})
OPTIONAL_STRINGS = FIELDS - {"project_id", "name", "status", "created_at",
                              "updated_at", "files", "total_text_length",
                              "ontology", "chunk_size", "chunk_overlap"}
DISPLAY = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
PRINCIPAL = re.compile(r"[\x20-\x7e]{1,128}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
MAX_BYTES = 1024 * 1024


def principal_id(value: object) -> str:
    if type(value) is not str or not PRINCIPAL.fullmatch(value) or not value.strip(" "):
        raise InvalidProject()
    return value


def display_id(value: object) -> str:
    if type(value) is not str or not DISPLAY.fullmatch(value):
        raise InvalidProject()
    return value


def uuid_value(value: object) -> UUID:
    if type(value) is UUID:
        return value
    if type(value) is str:
        try:
            parsed = UUID(value)
        except ValueError:
            pass
        else:
            if value == str(parsed):
                return parsed
    raise InvalidProject()


def _text(value: object, maximum: int = 65536, *, nonempty: bool = False) -> None:
    if type(value) is not str or len(value) > maximum or (nonempty and not value):
        raise InvalidProject()
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise InvalidProject() from None


def _json_tree(value: object, depth: int = 0) -> None:
    if depth > 32:
        raise InvalidProject()
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if abs(value) > 1_000_000_000_000:
            raise InvalidProject()
        return
    if type(value) is float:
        import math
        if not math.isfinite(value) or abs(value) > 1_000_000_000_000:
            raise InvalidProject()
        return
    if type(value) is str:
        _text(value)
        return
    if type(value) is list:
        if len(value) > 1000:
            raise InvalidProject()
        for item in value:
            _json_tree(item, depth + 1)
        return
    if type(value) is dict:
        if len(value) > 1000:
            raise InvalidProject()
        for key, item in value.items():
            _text(key, 256)
            _json_tree(item, depth + 1)
        return
    raise InvalidProject()


def _copy(value: object):
    _json_tree(value)
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise InvalidProject() from None


def validate_snapshot(value: object, expected_display: str) -> dict:
    display_id(expected_display)
    snapshot = _copy(value)
    if type(snapshot) is not dict or set(snapshot) != FIELDS:
        raise InvalidProject()
    if snapshot["project_id"] != expected_display:
        raise InvalidProject()
    for key in ("name", "created_at", "updated_at"):
        _text(snapshot[key], nonempty=key == "name")
    if type(snapshot["status"]) is not str or snapshot["status"] not in STATUSES:
        raise InvalidProject()
    for key in OPTIONAL_STRINGS:
        if snapshot[key] is not None:
            _text(snapshot[key])
    if type(snapshot["files"]) is not list or len(snapshot["files"]) > 1000:
        raise InvalidProject()
    for item in snapshot["files"]:
        if type(item) is not dict or set(item) not in (
                {"filename", "size"},
                {"filename", "path", "size"},
                {"original_filename", "saved_filename", "path", "size"}):
            raise InvalidProject()
        for key in ("filename", "original_filename", "saved_filename"):
            if key in item:
                _text(item[key], 1024)
        if "path" in item:
            _text(item["path"], 65536)
        if type(item["size"]) is not int or not 0 <= item["size"] <= 50 * 1024 * 1024:
            raise InvalidProject()
    for key in ("total_text_length", "chunk_size", "chunk_overlap"):
        if type(snapshot[key]) is not int or not 0 <= snapshot[key] <= 1_000_000_000:
            raise InvalidProject()
    if snapshot["ontology"] is not None and type(snapshot["ontology"]) is not dict:
        raise InvalidProject()
    return snapshot


def validate_evidence(value: object) -> list[dict]:
    if type(value) is tuple:
        value = list(value)
    evidence = _copy(value)
    if type(evidence) is not list or len(evidence) > 100:
        raise InvalidProject()
    seen = set()
    for item in evidence:
        if type(item) is not dict or set(item) != {"evidence_id", "source_revision", "sha256", "object_key", "byte_length"}:
            raise InvalidProject()
        for key in ("evidence_id", "source_revision"):
            parsed = uuid_value(item[key])
            if type(item[key]) is not str or item[key] != str(parsed):
                raise InvalidProject()
        if item["evidence_id"] in seen:
            raise InvalidProject()
        seen.add(item["evidence_id"])
        if type(item["sha256"]) is not str or not HEX.fullmatch(item["sha256"]):
            raise InvalidProject()
        key = item["object_key"]
        _text(key, 1024, nonempty=True)
        if (key.startswith("/") or "\\" in key or ":" in key or "?" in key or "#" in key or "%" in key
                or any(part in ("", ".", "..") for part in key.split("/"))
                or any(ord(char) < 32 or ord(char) == 127 for char in key)):
            raise InvalidProject()
        if type(item["byte_length"]) is not int or not 0 <= item["byte_length"] <= 50 * 1024 * 1024:
            raise InvalidProject()
    return evidence


def canonical_payload(snapshot: object, evidence: object, display: str) -> tuple[dict, list[dict], str]:
    snapshot = validate_snapshot(snapshot, display)
    evidence = validate_evidence(evidence)
    encoded = json.dumps({"snapshot": snapshot, "evidence": evidence},
                         sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                         allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise InvalidProject()
    return snapshot, evidence, hashlib.sha256(encoded).hexdigest()


def strict_json(raw: bytes):
    if len(raw) > MAX_BYTES + 16384:
        raise InvalidProject()
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InvalidProject()
            result[key] = value
        return result
    def bad_constant(_):
        raise InvalidProject()
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=bad_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise InvalidProject() from None
    _json_tree(value)
    return value

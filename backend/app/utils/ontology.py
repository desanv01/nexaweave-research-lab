"""Helpers for generated and user-reviewed ontology structures."""

from copy import deepcopy
import re
from typing import Any, Dict, List, Optional


MAX_ONTOLOGY_TYPES = 10
MAX_ONTOLOGY_ATTRIBUTES = 10
MAX_ONTOLOGY_SOURCE_TARGETS = 10
RESERVED_ONTOLOGY_ATTRIBUTE_NAMES = frozenset({
    "uuid",
    "name",
    "group_id",
    "graph_id",
    "name_embedding",
    "summary",
    "created_at",
})

_ENTITY_NAME = re.compile(r"[A-Z][A-Za-z0-9]*")
_EDGE_NAME = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*")
_ATTRIBUTE_NAME = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
_TYPE_FIELDS = {"name", "description", "attributes"}
_ATTRIBUTE_FIELDS = {"name", "type", "description"}

_FALLBACK_ATTRIBUTE = {
    "name": "details",
    "type": "text",
    "description": "Additional details about this ontology type.",
}


def _reviewed_text(value: Any) -> bool:
    if type(value) is not str or not value.strip() or "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _validate_reviewed_attributes(attributes: Any, path: str) -> None:
    if type(attributes) is not list or not 1 <= len(attributes) <= MAX_ONTOLOGY_ATTRIBUTES:
        raise ValueError(f"{path} must contain 1 to {MAX_ONTOLOGY_ATTRIBUTES} attributes")

    names: set[str] = set()
    for index, attribute in enumerate(attributes):
        field = f"{path}[{index}]"
        if type(attribute) is not dict or attribute.keys() != _ATTRIBUTE_FIELDS:
            raise ValueError(f"{field} must contain name, type and description")
        name = attribute["name"]
        if (type(name) is not str or not _ATTRIBUTE_NAME.fullmatch(name)
                or name in RESERVED_ONTOLOGY_ATTRIBUTE_NAMES):
            raise ValueError(f"{field}.name must be a non-reserved snake_case name")
        if name in names:
            raise ValueError(f"{field}.name is a duplicate attribute name")
        if type(attribute["type"]) is not str or attribute["type"] != "text":
            raise ValueError(f"{field}.type must be text")
        if not _reviewed_text(attribute["description"]):
            raise ValueError(f"{field}.description must be nonempty text")
        names.add(name)


def _validate_reviewed_types(
    entries: Any, path: str, name_pattern: re.Pattern[str],
    required: set[str], optional: set[str],
) -> set[str]:
    if type(entries) is not list or len(entries) > MAX_ONTOLOGY_TYPES:
        raise ValueError(f"{path} must be a list of at most {MAX_ONTOLOGY_TYPES} types")

    names: set[str] = set()
    for index, entry in enumerate(entries):
        field = f"{path}[{index}]"
        if (type(entry) is not dict or not required <= entry.keys()
                or entry.keys() - required - optional):
            raise ValueError(f"{field} has invalid type fields")
        name = entry["name"]
        if type(name) is not str or not name_pattern.fullmatch(name):
            raise ValueError(f"{field}.name is not a canonical type name")
        if name in names:
            raise ValueError(f"{field}.name is a duplicate type name")
        if not _reviewed_text(entry["description"]) or len(entry["description"]) > 100:
            raise ValueError(f"{field}.description must be nonempty text of at most 100 characters")
        _validate_reviewed_attributes(entry["attributes"], f"{field}.attributes")
        if "examples" in entry:
            examples = entry["examples"]
            if type(examples) is not list or not all(_reviewed_text(example) for example in examples):
                raise ValueError(f"{field}.examples must be a list of nonempty text values")
        names.add(name)
    return names


def validate_reviewed_ontology(ontology: Any) -> Dict[str, Any]:
    """Return a detached copy of a reviewed ontology or raise ``ValueError``."""
    if type(ontology) is not dict or ontology.keys() != {"entity_types", "edge_types"}:
        raise ValueError("ontology must contain entity_types and edge_types")
    entity_names = _validate_reviewed_types(
        ontology["entity_types"], "entity_types", _ENTITY_NAME, _TYPE_FIELDS, {"examples"},
    )
    _validate_reviewed_types(
        ontology["edge_types"], "edge_types", _EDGE_NAME, _TYPE_FIELDS | {"source_targets"}, set(),
    )
    allowed_endpoints = entity_names | {"Entity"}
    for index, edge in enumerate(ontology["edge_types"]):
        path = f"edge_types[{index}].source_targets"
        targets = edge["source_targets"]
        if type(targets) is not list or not 1 <= len(targets) <= MAX_ONTOLOGY_SOURCE_TARGETS:
            raise ValueError(f"{path} must contain 1 to {MAX_ONTOLOGY_SOURCE_TARGETS} endpoint pairs")
        seen: set[tuple[str, str]] = set()
        for target_index, endpoints in enumerate(targets):
            field = f"{path}[{target_index}]"
            if type(endpoints) is not dict or endpoints.keys() != {"source", "target"}:
                raise ValueError(f"{field} must contain source and target")
            source, target = endpoints["source"], endpoints["target"]
            if type(source) is not str or type(target) is not str:
                raise ValueError(f"{field} endpoints must be strings")
            if source not in allowed_endpoints or target not in allowed_endpoints:
                raise ValueError(f"{field} references an unknown entity type")
            pair = (source, target)
            if pair in seen:
                raise ValueError(f"{field} is a duplicate endpoint pair")
            seen.add(pair)
    return deepcopy(ontology)


def normalize_ontology_attribute(attribute: Any) -> Optional[Dict[str, Any]]:
    """Return a safe attribute definition, or ``None`` for unusable values."""

    if isinstance(attribute, str):
        if not attribute.strip():
            return None
        return {
            "name": attribute,
            "type": "text",
            "description": attribute,
        }

    if not isinstance(attribute, dict):
        return None

    name = attribute.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    normalized = dict(attribute)
    description = normalized.get("description")
    if not isinstance(description, str) or not description:
        normalized["description"] = name
    return normalized


def normalize_ontology_attributes(attributes: Any) -> List[Dict[str, Any]]:
    """Return a non-empty Zep-compatible attribute list within service limits."""

    if not isinstance(attributes, list):
        attributes = []

    normalized_attributes: List[Dict[str, Any]] = []
    for attribute in attributes:
        normalized = normalize_ontology_attribute(attribute)
        if normalized is None:
            continue
        normalized_attributes.append(normalized)
        if len(normalized_attributes) == MAX_ONTOLOGY_ATTRIBUTES:
            break

    if not normalized_attributes:
        normalized_attributes.append(dict(_FALLBACK_ATTRIBUTE))

    return normalized_attributes


def normalize_ontology_source_targets(
    source_targets: Any,
    *,
    limit: int | None = MAX_ONTOLOGY_SOURCE_TARGETS,
) -> List[Dict[str, str]]:
    """Return unique, structurally valid source-target pairs within Zep limits."""

    if not isinstance(source_targets, list):
        return []

    normalized_targets: List[Dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source_target in source_targets:
        if not isinstance(source_target, dict):
            continue
        source = source_target.get("source")
        target = source_target.get("target")
        if not isinstance(source, str) or not source.strip():
            continue
        if not isinstance(target, str) or not target.strip():
            continue

        pair = (source.strip(), target.strip())
        if pair in seen:
            continue
        seen.add(pair)
        normalized_targets.append({"source": pair[0], "target": pair[1]})
        if limit is not None and len(normalized_targets) == limit:
            break

    return normalized_targets

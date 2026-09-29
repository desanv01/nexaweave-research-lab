"""Bounded, live, scoped Graphiti graph enumeration; no model or write path."""

from __future__ import annotations

import base64
import binascii
import json
import re
from uuid import UUID

from .contracts import FactResult, GraphPage, GraphPageRequest, KnowledgeScope


class GraphReadViolation(RuntimeError):
    """A returned graph row or cursor violates the page contract."""


class ResultTooLarge(RuntimeError):
    """A complete page exceeds the fixed serialized response budget."""


_CURSOR_TEXT = re.compile(r"[A-Za-z0-9_-]+\Z")
_CURSOR_FIELDS = {"v", "group_id", "kind", "entity_type", "last_uuid"}
_MAX_DECODED_CURSOR = 768
_MAX_PAGE_BYTES = 1024 * 1024
_INTERNAL_FIELDS = frozenset({
    "uuid", "group_id", "labels", "summary", "name", "fact", "content", "source_description",
    "source", "episodes", "created_at", "valid_at", "invalid_at", "expired_at",
    "source_node_uuid", "target_node_uuid", "entity_edges", "community_uuid",
})

_QUERIES = {
    "node": (
        "MATCH (n:Entity) WHERE n.group_id = $group_id "
        "AND ($cursor IS NULL OR n.uuid > $cursor) "
        "AND ($entity_type IS NULL OR $entity_type IN labels(n)) "
        "RETURN properties(n) AS properties, n.group_id AS row_group, "
        "labels(n) AS labels ORDER BY n.uuid ASC LIMIT $limit"
    ),
    "edge": (
        "MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) "
        "WHERE r.group_id = $group_id AND a.group_id = $group_id AND b.group_id = $group_id "
        "AND ($cursor IS NULL OR r.uuid > $cursor) "
        "RETURN properties(r) AS properties, r.group_id AS row_group, "
        "a.uuid AS source_id, b.uuid AS target_id, "
        "a.group_id AS source_group, b.group_id AS target_group "
        "ORDER BY r.uuid ASC LIMIT $limit"
    ),
    "episode": (
        "MATCH (e:Episodic) WHERE e.group_id = $group_id "
        "AND ($cursor IS NULL OR e.uuid > $cursor) "
        "RETURN properties(e) AS properties, e.group_id AS row_group "
        "ORDER BY e.uuid ASC LIMIT $limit"
    ),
}


def _uuid(value: object) -> str:
    if not isinstance(value, str):
        raise GraphReadViolation("invalid graph provider identifier")
    try:
        parsed = UUID(value)
    except ValueError:
        raise GraphReadViolation("invalid graph provider identifier") from None
    if str(parsed) != value:
        raise GraphReadViolation("invalid graph provider identifier")
    return value


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate cursor key")
        result[key] = value
    return result


def _decode_cursor(token: str | None, scope: KnowledgeScope, request: GraphPageRequest) -> str | None:
    if token is None:
        return None
    if not _CURSOR_TEXT.fullmatch(token):
        raise ValueError("invalid graph page cursor")
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        if len(raw) > _MAX_DECODED_CURSOR:
            raise ValueError
        if base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != token:
            raise ValueError
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        if (not isinstance(payload, dict) or set(payload) != _CURSOR_FIELDS
                or type(payload["v"]) is not int or payload["v"] != 1
                or type(payload["group_id"]) is not str or payload["group_id"] != scope.group_id
                or type(payload["kind"]) is not str or payload["kind"] != request.kind
                or payload["entity_type"] != request.entity_type
                or (payload["entity_type"] is not None and type(payload["entity_type"]) is not str)):
            raise ValueError
        return _uuid(payload["last_uuid"])
    except (GraphReadViolation, ValueError, UnicodeError, TypeError, KeyError, OverflowError, binascii.Error):
        raise ValueError("invalid graph page cursor") from None


def _encode_cursor(scope: KnowledgeScope, request: GraphPageRequest, last_uuid: str) -> str:
    payload = {"v": 1, "group_id": scope.group_id, "kind": request.kind,
               "entity_type": request.entity_type, "last_uuid": last_uuid}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _clean_attributes(data: dict) -> dict:
    result = {}
    for key, value in data.items():
        if not isinstance(key, str) or key in _INTERNAL_FIELDS or "embedding" in key.lower():
            continue
        if isinstance(value, dict):
            result[key] = _clean_attributes(value)
        elif isinstance(value, list):
            result[key] = [_clean_attributes(item) if isinstance(item, dict) else item for item in value]
        else:
            result[key] = value
    return result


def _node_labels(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > 64:
        raise GraphReadViolation("invalid graph node labels")
    return tuple(value)


def _row_fact(row, kind: str, scope: KnowledgeScope, entity_type: str | None, native) -> FactResult:
    try:
        data = native(dict(row["properties"]))
        if not isinstance(data, dict) or data.get("group_id") != scope.group_id or row["row_group"] != scope.group_id:
            raise GraphReadViolation("graph row outside requested scope")
        identifier = _uuid(data.get("uuid"))
        common = dict(provider_id=identifier, scope=scope, kind=kind,
                      name=data.get("name"), valid_at=data.get("valid_at"),
                      invalid_at=data.get("invalid_at"), created_at=data.get("created_at"),
                      attributes=_clean_attributes(data))
        if kind == "node":
            common["labels"] = _node_labels(row["labels"])
            common["summary"] = data.get("summary")
            if entity_type is not None and entity_type not in common["labels"]:
                raise GraphReadViolation("graph node type mismatch")
        elif kind == "edge":
            if row["source_group"] != scope.group_id or row["target_group"] != scope.group_id:
                raise GraphReadViolation("graph edge endpoint outside requested scope")
            common["source_node_id"] = _uuid(row["source_id"])
            common["target_node_id"] = _uuid(row["target_id"])
            common["fact"] = data.get("fact")
            common["expired_at"] = data.get("expired_at")
            episodes = data.get("episodes")
            if not isinstance(episodes, (list, tuple)):
                raise GraphReadViolation("graph edge episode references missing")
            common["episode_ids"] = tuple(_uuid(item) for item in episodes)
        return FactResult(**common)
    except GraphReadViolation:
        raise
    except (KeyError, TypeError, ValueError):
        raise GraphReadViolation("invalid graph provider row") from None


async def page_graph(provider, scope: KnowledgeScope, request: GraphPageRequest, *, native) -> GraphPage:
    """Validate everything caller controlled before touching the graph driver."""
    try:
        if not isinstance(scope, KnowledgeScope) or not isinstance(request, GraphPageRequest):
            raise ValueError
        scope = KnowledgeScope.model_validate(scope.model_dump())
        request = GraphPageRequest.model_validate(request.model_dump())
        cursor = _decode_cursor(request.cursor, scope, request)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("invalid graph page request or cursor") from None

    params = {"group_id": scope.group_id, "cursor": cursor,
              "limit": request.limit + 1, "entity_type": request.entity_type}
    rows = await provider._rows(_QUERIES[request.kind], **params)
    if len(rows) > request.limit + 1:
        raise GraphReadViolation("graph provider exceeded page bound")
    facts = []
    previous = cursor
    for index, row in enumerate(rows):
        fact = _row_fact(row, request.kind, scope, request.entity_type, native)
        if previous is not None and fact.provider_id <= previous:
            raise GraphReadViolation("graph provider page order invalid")
        previous = fact.provider_id
        if index < request.limit:
            decorated = await provider._decorate_fact(scope, fact)
            try:
                decorated = FactResult.model_validate(decorated.model_dump(warnings=False))
            except (AttributeError, TypeError, ValueError):
                raise GraphReadViolation("invalid decorated graph fact") from None
            if decorated.scope != scope or decorated.kind != request.kind or decorated.provider_id != fact.provider_id:
                raise GraphReadViolation("decorated graph fact identity mismatch")
            if not decorated.episode_ids:
                raise GraphReadViolation("graph fact lacks episode provenance")
            for episode_id in decorated.episode_ids:
                _uuid(episode_id)
            facts.append(decorated)
    next_cursor = _encode_cursor(scope, request, facts[-1].provider_id) if len(rows) > request.limit else None
    page = GraphPage(facts=tuple(facts), next_cursor=next_cursor)
    try:
        size = len(page.model_dump_json().encode("utf-8"))
    except Exception:
        raise GraphReadViolation("graph result cannot be serialized") from None
    if size > _MAX_PAGE_BYTES:
        raise ResultTooLarge("graph page exceeds 1MiB serialized limit")
    return page

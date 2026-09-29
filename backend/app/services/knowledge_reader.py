"""Bounded, stdlib-only read projection over an injected knowledge byte client.

The host supplies a scope and separately enforces authorization. This module
neither creates a transport nor treats a graph group as an access decision.
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from unicodedata import category
from uuid import UUID, uuid4


_SCOPE_FIELDS = frozenset({"schema_version", "workspace_id", "project_id", "graph_id",
                           "run_id", "branch_id", "layer"})
_FACT_FIELDS = frozenset({"schema_version", "provider_id", "scope", "kind", "name", "fact",
                          "source_node_id", "target_node_id", "episode_ids", "evidence_ids",
                          "labels", "summary", "valid_at", "invalid_at", "expired_at",
                          "created_at", "attributes", "score"})
_ERROR_CODES = frozenset({"invalid_request", "unauthorized", "busy", "not_found", "conflict",
                          "tombstoned", "uncertain", "unsupported", "timeout", "result_too_large",
                          "model_calls_disabled", "internal_error"})
_DISPLAY_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_ENTITY_TYPE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_MAX_REPLY = 2 * 1024 * 1024


class KnowledgeReadError(RuntimeError):
    """Fixed public read failure; private transport and payload text is discarded."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code.replace("_", " "))


@dataclass(frozen=True)
class ReadLimits:
    max_pages: int = 200
    max_facts: int = 10_000
    max_reply_bytes: int = 16 * 1024 * 1024
    timeout_seconds: float = 120

    def __post_init__(self):
        for value, cap in ((self.max_pages, 2000), (self.max_facts, 100_000),
                           (self.max_reply_bytes, 64 * 1024 * 1024)):
            if type(value) is not int or not 0 < value <= cap:
                raise ValueError("invalid read limits")
        if (type(self.timeout_seconds) not in (int, float)
                or not math.isfinite(self.timeout_seconds)
                or not 0 < self.timeout_seconds <= 300):
            raise ValueError("invalid read limits")


@dataclass
class _Budget:
    limits: ReadLimits
    deadline: float
    pages: int = 0
    facts: int = 0
    reply_bytes: int = 0

    def check(self):
        if time.monotonic() >= self.deadline:
            raise KnowledgeReadError("timeout")

    def account(self, raw: bytes, count: int):
        self.pages += 1
        self.facts += count
        self.reply_bytes += len(raw)
        if (self.pages > self.limits.max_pages or self.facts > self.limits.max_facts
                or self.reply_bytes > self.limits.max_reply_bytes):
            raise KnowledgeReadError("limit_exceeded")


def _uuid(value: object) -> str:
    if type(value) is not str:
        raise ValueError
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError from None
    return value


def _scope(value: object) -> dict[str, object]:
    if type(value) is not dict or set(value) != _SCOPE_FIELDS:
        raise ValueError("invalid bound scope")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("invalid bound scope")
    result: dict[str, object] = {"schema_version": 1}
    for key in ("workspace_id", "project_id", "graph_id"):
        result[key] = _uuid(value[key])
    for key in ("run_id", "branch_id"):
        result[key] = None if value[key] is None else _uuid(value[key])
    if type(value["layer"]) is not str or value["layer"] not in {
        "source", "assumption", "inference", "simulation", "analysis"
    }:
        raise ValueError("invalid bound scope")
    result["layer"] = value["layer"]
    return result


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _bad_constant(_value):
    raise ValueError


def _scalar_text(value: str) -> None:
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError


def _json_reply(raw: object) -> dict:
    if type(raw) is not bytes or not 0 < len(raw) <= _MAX_REPLY:
        raise KnowledgeReadError("invalid_reply")
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                            parse_constant=_bad_constant)
        if type(result) is not dict:
            raise ValueError
        stack = [(result, 1)]
        while stack:
            value, depth = stack.pop()
            if depth > 32:
                raise ValueError
            if type(value) is dict:
                for key in value:
                    _scalar_text(key)
            for item in (value.values() if type(value) is dict else value):
                if type(item) is str:
                    _scalar_text(item)
                if type(item) is float and not math.isfinite(item):
                    raise ValueError
                if type(item) in (dict, list):
                    stack.append((item, depth + 1))
        return result
    except (UnicodeError, ValueError, TypeError, RecursionError, OverflowError):
        raise KnowledgeReadError("invalid_reply") from None


def _timestamp(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return value


def _fact(value: object, kind: str, scope: dict) -> dict:
    if type(value) is not dict or set(value) != _FACT_FIELDS:
        raise ValueError
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or _scope(value["scope"]) != scope
            or value["kind"] != kind or type(value["kind"]) is not str):
        raise ValueError
    _uuid(value["provider_id"])
    for key in ("name", "fact", "summary"):
        if value[key] is not None and type(value[key]) is not str:
            raise ValueError
    if value["summary"] is not None and len(value["summary"]) > 32768:
        raise ValueError
    if type(value["labels"]) is not list or len(value["labels"]) > 64:
        raise ValueError
    labels = value["labels"]
    if any(type(label) is not str or not label or len(label) > 128
           or any(category(char) == "Cc" for char in label) for label in labels):
        raise ValueError
    if len(set(labels)) != len(labels):
        raise ValueError
    for key in ("source_node_id", "target_node_id"):
        if value[key] is not None:
            _uuid(value[key])
    if kind == "edge" and (value["source_node_id"] is None or value["target_node_id"] is None):
        raise ValueError
    for key in ("episode_ids", "evidence_ids"):
        ids = value[key]
        if type(ids) is not list or (key == "episode_ids" and not ids):
            raise ValueError
        for identifier in ids:
            _uuid(identifier)
    for key in ("valid_at", "invalid_at", "expired_at", "created_at"):
        _timestamp(value[key])
    if type(value["attributes"]) is not dict:
        raise ValueError
    score = value["score"]
    if score is not None and (type(score) not in (int, float) or not math.isfinite(score)):
        raise ValueError
    return value


@dataclass
class EntityNode:
    uuid: str
    name: str
    labels: list[str]
    summary: str
    attributes: dict[str, Any]
    related_edges: list[dict[str, Any]] = field(default_factory=list)
    related_nodes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"uuid": self.uuid, "name": self.name, "labels": self.labels,
                "summary": self.summary, "attributes": self.attributes,
                "related_edges": self.related_edges, "related_nodes": self.related_nodes}

    def get_entity_type(self) -> str | None:
        return next((label for label in self.labels if label not in {"Entity", "Node"}), None)


@dataclass
class FilteredEntities:
    entities: list[EntityNode]
    entity_types: set[str]
    total_count: int
    filtered_count: int

    def to_dict(self) -> dict[str, Any]:
        return {"entities": [entity.to_dict() for entity in self.entities],
                "entity_types": sorted(self.entity_types), "total_count": self.total_count,
                "filtered_count": self.filtered_count}


class KnowledgeGraphReader:
    def __init__(self, client, *, scope, graph_id, limits=None):
        if not callable(getattr(client, "call", None)):
            raise ValueError("knowledge client required")
        if type(graph_id) is not str or not _DISPLAY_ID.fullmatch(graph_id):
            raise ValueError("invalid graph id")
        self._scope = _scope(scope)
        self._graph_id = graph_id
        if limits is None:
            limits = ReadLimits()
        if type(limits) is not ReadLimits:
            raise ValueError("invalid read limits")
        self._limits = limits
        self._client = client

    def _start(self, graph_id: str) -> _Budget:
        if type(graph_id) is not str or graph_id != self._graph_id:
            raise KnowledgeReadError("invalid_request")
        return _Budget(self._limits, time.monotonic() + self._limits.timeout_seconds)

    def _page(self, kind: str, cursor: str | None, budget: _Budget) -> tuple[list[dict], str | None]:
        budget.check()
        if budget.pages >= budget.limits.max_pages:
            raise KnowledgeReadError("limit_exceeded")
        request_id = str(uuid4())
        request = {"version": 1, "request_id": request_id, "method": "page",
                   "scope": dict(self._scope), "payload": {"schema_version": 1, "kind": kind,
                   "limit": 100, "cursor": cursor, "entity_type": None}}
        raw_request = json.dumps(request, ensure_ascii=False, separators=(",", ":"),
                                 allow_nan=False).encode("utf-8")
        try:
            raw = self._client.call(raw_request)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise KnowledgeReadError("transport_failure") from None
        budget.check()
        reply = _json_reply(raw)
        if (type(reply.get("version")) is not int or reply["version"] != 1
                or reply.get("request_id") != request_id
                or type(reply.get("ok")) is not bool):
            raise KnowledgeReadError("invalid_reply")
        if not reply["ok"]:
            if (set(reply) != {"version", "request_id", "ok", "error"}
                    or type(reply["error"]) is not dict or set(reply["error"]) != {"code"}
                    or type(reply["error"]["code"]) is not str
                    or reply["error"]["code"] not in _ERROR_CODES):
                raise KnowledgeReadError("invalid_reply")
            raise KnowledgeReadError(reply["error"]["code"])
        if set(reply) != {"version", "request_id", "ok", "result"}:
            raise KnowledgeReadError("invalid_reply")
        page = reply["result"]
        try:
            if (type(page) is not dict or set(page) != {"schema_version", "facts", "next_cursor"}
                    or type(page["schema_version"]) is not int or page["schema_version"] != 1
                    or type(page["facts"]) is not list or len(page["facts"]) > 100):
                raise ValueError
            next_cursor = page["next_cursor"]
            if next_cursor is not None and (type(next_cursor) is not str
                                            or not 1 <= len(next_cursor) <= 1024):
                raise ValueError
            facts = [_fact(item, kind, self._scope) for item in page["facts"]]
        except (ValueError, KeyError, TypeError, OverflowError):
            raise KnowledgeReadError("invalid_reply") from None
        budget.account(raw, len(facts))
        budget.check()
        return facts, next_cursor

    def _scan(self, kind: str, budget: _Budget) -> list[dict]:
        facts: list[dict] = []
        cursor = None
        cursors = set()
        previous = None
        while True:
            page, next_cursor = self._page(kind, cursor, budget)
            if next_cursor is not None and not page:
                raise KnowledgeReadError("invalid_reply")
            for fact in page:
                budget.check()
                identifier = fact["provider_id"]
                if previous is not None and identifier <= previous:
                    raise KnowledgeReadError("invalid_reply")
                previous = identifier
                facts.append(fact)
            if next_cursor is None:
                return facts
            if next_cursor == cursor or next_cursor in cursors:
                raise KnowledgeReadError("invalid_reply")
            cursors.add(next_cursor)
            cursor = next_cursor

    @staticmethod
    def _node(fact: dict) -> dict:
        return {"uuid": fact["provider_id"], "name": fact["name"] or "",
                "labels": list(fact["labels"]), "summary": fact["summary"] or "",
                "fact": fact["fact"], "attributes": fact["attributes"],
                "created_at": fact["created_at"],
                "valid_at": fact["valid_at"], "invalid_at": fact["invalid_at"],
                "expired_at": fact["expired_at"], "episodes": list(fact["episode_ids"]),
                "evidence_ids": list(fact["evidence_ids"]), "score": fact["score"]}

    @staticmethod
    def _edge(fact: dict) -> dict:
        name = fact["name"] or ""
        return {"uuid": fact["provider_id"], "name": name, "fact": fact["fact"] or "",
                "fact_type": name, "source_node_uuid": fact["source_node_id"],
                "target_node_uuid": fact["target_node_id"], "labels": list(fact["labels"]),
                "summary": fact["summary"], "attributes": fact["attributes"],
                "created_at": fact["created_at"], "valid_at": fact["valid_at"],
                "invalid_at": fact["invalid_at"], "expired_at": fact["expired_at"],
                "episodes": list(fact["episode_ids"]), "evidence_ids": list(fact["evidence_ids"]),
                "score": fact["score"]}

    @staticmethod
    def _index(nodes: list[dict], edges: list[dict], budget: _Budget):
        node_map = {}
        adjacency: dict[str, list[dict]] = {}
        for node in nodes:
            budget.check()
            node_map[node["uuid"]] = node
            adjacency[node["uuid"]] = []
        for edge in edges:
            budget.check()
            source, target = edge["source_node_uuid"], edge["target_node_uuid"]
            if source not in node_map or target not in node_map:
                raise KnowledgeReadError("inconsistent_graph")
            adjacency[source].append(edge)
            if target != source:
                adjacency[target].append(edge)
        return node_map, adjacency

    @staticmethod
    def _entity(node: dict, node_map: dict, adjacency: dict, budget: _Budget) -> EntityNode:
        entity = EntityNode(node["uuid"], node["name"], node["labels"], node["summary"],
                            node["attributes"])
        neighbors = set()
        for edge in adjacency.get(node["uuid"], ()):
            budget.check()
            if edge["source_node_uuid"] == node["uuid"]:
                other = edge["target_node_uuid"]
                entity.related_edges.append({"direction": "outgoing", "edge_name": edge["name"],
                                             "fact": edge["fact"], "target_node_uuid": other})
            else:
                other = edge["source_node_uuid"]
                entity.related_edges.append({"direction": "incoming", "edge_name": edge["name"],
                                             "fact": edge["fact"], "source_node_uuid": other})
            neighbors.add(other)
        for identifier in sorted(neighbors):
            budget.check()
            other = node_map[identifier]
            entity.related_nodes.append({"uuid": identifier, "name": other["name"],
                                         "labels": other["labels"], "summary": other["summary"]})
        return entity

    def get_all_nodes(self, graph_id: str) -> list[dict]:
        budget = self._start(graph_id)
        result = []
        for fact in self._scan("node", budget):
            budget.check()
            result.append(self._node(fact))
        return result

    def get_all_edges(self, graph_id: str) -> list[dict]:
        budget = self._start(graph_id)
        result = []
        for fact in self._scan("edge", budget):
            budget.check()
            result.append(self._edge(fact))
        return result

    def get_graph_data(self, graph_id: str) -> dict:
        budget = self._start(graph_id)
        nodes = []
        for fact in self._scan("node", budget):
            budget.check()
            nodes.append(self._node(fact))
        edges = []
        for fact in self._scan("edge", budget):
            budget.check()
            edges.append(self._edge(fact))
        node_map, _ = self._index(nodes, edges, budget)
        for edge in edges:
            budget.check()
            edge["source_node_name"] = node_map[edge["source_node_uuid"]]["name"]
            edge["target_node_name"] = node_map[edge["target_node_uuid"]]["name"]
        return {"graph_id": self._graph_id, "nodes": nodes, "edges": edges,
                "node_count": len(nodes), "edge_count": len(edges)}

    def filter_defined_entities(self, graph_id: str, defined_entity_types=None,
                                enrich_with_edges: bool = True) -> FilteredEntities:
        budget = self._start(graph_id)
        if type(enrich_with_edges) is not bool:
            raise KnowledgeReadError("invalid_request")
        if defined_entity_types is not None:
            if (type(defined_entity_types) not in (list, tuple)
                    or any(type(item) is not str or not _ENTITY_TYPE.fullmatch(item)
                           for item in defined_entity_types)):
                raise KnowledgeReadError("invalid_request")
        allowed = set(defined_entity_types or ())
        nodes = []
        for fact in self._scan("node", budget):
            budget.check()
            nodes.append(self._node(fact))
        node_map, adjacency = ({}, {})
        if enrich_with_edges:
            edges = []
            for fact in self._scan("edge", budget):
                budget.check()
                edges.append(self._edge(fact))
            node_map, adjacency = self._index(nodes, edges, budget)
        entities = []
        found = set()
        for node in nodes:
            budget.check()
            custom = [label for label in node["labels"] if label not in {"Entity", "Node"}]
            matches = [label for label in custom if label in allowed] if allowed else custom
            if not matches:
                continue
            found.add(matches[0])
            entities.append(self._entity(node, node_map, adjacency, budget))
        return FilteredEntities(entities, found, len(nodes), len(entities))

    def get_entity_with_context(self, graph_id: str, entity_uuid: str) -> EntityNode | None:
        budget = self._start(graph_id)
        try:
            _uuid(entity_uuid)
        except ValueError:
            raise KnowledgeReadError("invalid_request") from None
        nodes = []
        for fact in self._scan("node", budget):
            budget.check()
            nodes.append(self._node(fact))
        node_map = {}
        for node in nodes:
            budget.check()
            node_map[node["uuid"]] = node
        if entity_uuid not in node_map:
            return None
        edges = []
        for fact in self._scan("edge", budget):
            budget.check()
            edges.append(self._edge(fact))
        node_map, adjacency = self._index(nodes, edges, budget)
        return self._entity(node_map[entity_uuid], node_map, adjacency, budget)

    def get_entities_by_type(self, graph_id: str, entity_type: str,
                             enrich_with_edges: bool = True) -> list[EntityNode]:
        if type(entity_type) is not str or not _ENTITY_TYPE.fullmatch(entity_type):
            raise KnowledgeReadError("invalid_request")
        return self.filter_defined_entities(graph_id, [entity_type], enrich_with_edges).entities

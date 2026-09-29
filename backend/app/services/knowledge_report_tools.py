"""Inherited research algorithms over one bound, bounded graph projection."""

from __future__ import annotations

import json
import hashlib
import math

from .zep_tools import EdgeInfo, InterviewResult, NodeInfo, SearchResult, ZepToolsService


class NeutralCapabilityError(RuntimeError):
    """Fixed outward failure for an unavailable or invalid trusted capability."""

    CODES = frozenset({
        "unsupported", "unsupported_scope", "graph_mismatch", "simulation_mismatch",
        "invalid_projection", "invalid_request", "invalid_selection", "unknown_entity",
        "limit_exceeded", "result_too_large", "search_unavailable",
        "interview_unavailable", "invalid_interview", "binding_mismatch", "chat_failed",
        "insufficient_evidence", "invalid_outline", "planning_failed",
        "report_context_unavailable", "section_failed", "section_incomplete", "tool_failed",
        "invalid_evidence", "evidence_conflict", "internal_error",
    })

    def __init__(self, code="unsupported"):
        self.code = code if type(code) is str and code in self.CODES else "internal_error"
        super().__init__(self.code)

    @classmethod
    def safe_code(cls, code):
        return code if type(code) is str and code in cls.CODES else "internal_error"


class KnowledgeReportTools(ZepToolsService):
    """Reuse InsightForge, Panorama and QuickSearch without a legacy client."""

    MAX_OBJECTS = 500
    MAX_SEARCH_CALLS = 100
    MAX_LEDGER_ITEMS = 1000
    MAX_LEDGER_BYTES = 2 * 1024 * 1024

    def __init__(self, *, graph_id, simulation_id, graph, scope, llm_client,
                 search_selector=None, interview_capability=None):
        if graph.get("graph_id") != graph_id:
            raise NeutralCapabilityError("graph_mismatch")
        if type(scope) is not dict or scope.get("layer") != "source":
            raise NeutralCapabilityError("unsupported_scope")
        self.graph_id = graph_id
        self.simulation_id = simulation_id
        self.graph = graph
        self.scope = dict(scope)
        self.projection_fingerprint = hashlib.sha256(json.dumps(
            {"scope": self.scope, "graph": graph}, ensure_ascii=False,
            sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        self.nodes = {node["uuid"]: node for node in graph["nodes"]}
        self.edges = {edge["uuid"]: edge for edge in graph["edges"]}
        if len(self.nodes) != len(graph["nodes"]) or len(self.edges) != len(graph["edges"]):
            raise NeutralCapabilityError("invalid_projection")
        self._llm_client = llm_client
        self.search_selector = search_selector
        self.interview_capability = interview_capability
        self.strict_neutral = True
        self.retrieval_ledger = []
        self._projection_recorded = set()
        self.search_calls = 0

    def _record(self, entries):
        if len(self.retrieval_ledger) + len(entries) > self.MAX_LEDGER_ITEMS:
            raise NeutralCapabilityError("limit_exceeded")
        next_ledger = self.retrieval_ledger + entries
        if len(json.dumps(next_ledger, ensure_ascii=False).encode("utf-8")) > self.MAX_LEDGER_BYTES:
            raise NeutralCapabilityError("result_too_large")
        self.retrieval_ledger = next_ledger

    def _record_projection(self, kind, values):
        pending = []
        for value in values:
            key = (kind, value["uuid"])
            if key in self._projection_recorded:
                continue
            pending.append({"access": "projection_read", "kind": kind, "id": value["uuid"],
                            "scope": dict(self.scope),
                            "episode_ids": list(value["episodes"]),
                            "evidence_ids": list(value["evidence_ids"])})
        self._record(pending)
        self._projection_recorded.update((kind, value["uuid"]) for value in values)

    def restore_evidence(self, metadata):
        """Validate and load a saved report ledger before follow-up model work."""
        required = {"schema_version", "mode", "graph_id", "simulation_id", "scope",
                    "projection_fingerprint", "semantic_search",
                    "live_semantic_adapter_connected", "interview", "native_interview_verified",
                    "claim_citations_validated", "simulation_observations_in_source_graph",
                    "retrievals"}
        if (type(metadata) is not dict or set(metadata) != required
                or type(metadata.get("schema_version")) is not int
                or metadata["schema_version"] != 1
                or metadata.get("mode") != "source_graph_projection"
                or metadata.get("graph_id") != self.graph_id
                or metadata.get("simulation_id") != self.simulation_id
                or type(metadata.get("scope")) is not dict or metadata["scope"] != self.scope
                or metadata.get("projection_fingerprint") != self.projection_fingerprint
                or type(metadata.get("semantic_search")) is not str
                or metadata["semantic_search"] not in {"trusted_selection_only", "unsupported"}
                or type(metadata.get("interview")) is not str
                or metadata["interview"] not in {"trusted_capability", "unsupported"}
                or metadata.get("live_semantic_adapter_connected") is not False
                or metadata.get("native_interview_verified") is not False
                or metadata.get("claim_citations_validated") is not False
                or metadata.get("simulation_observations_in_source_graph") is not False
                or type(metadata.get("retrievals")) is not list
                or len(metadata["retrievals"]) > self.MAX_LEDGER_ITEMS):
            raise NeutralCapabilityError("invalid_evidence")
        recorded = set()
        for item in metadata["retrievals"]:
            if type(item) is not dict or item.get("scope") != self.scope:
                raise NeutralCapabilityError("invalid_evidence")
            kind, identifier, access = item.get("kind"), item.get("id"), item.get("access")
            if (type(kind) is not str or kind not in {"node", "edge"}
                    or type(identifier) is not str
                    or type(access) is not str
                    or access not in {"projection_read", "semantic_selection"}):
                raise NeutralCapabilityError("invalid_evidence")
            source = self.nodes if kind == "node" else self.edges
            if identifier not in source:
                raise NeutralCapabilityError("invalid_evidence")
            fact = source[identifier]
            if (item.get("episode_ids") != fact["episodes"]
                    or item.get("evidence_ids") != fact["evidence_ids"]):
                raise NeutralCapabilityError("invalid_evidence")
            if access == "projection_read":
                if (set(item) != {"access", "kind", "id", "scope", "episode_ids", "evidence_ids"}
                        or (kind, identifier) in recorded):
                    raise NeutralCapabilityError("invalid_evidence")
                recorded.add((kind, identifier))
            else:
                if (set(item) != {"access", "kind", "id", "score", "query", "scope",
                                  "episode_ids", "evidence_ids"}
                        or type(item["query"]) is not str
                        or not 1 <= len(item["query"].strip()) <= 400
                        or type(item["score"]) not in (int, float)
                        or abs(item["score"]) > 1_000_000_000_000
                        or not math.isfinite(item["score"])):
                    raise NeutralCapabilityError("invalid_evidence")
        self.retrieval_ledger = []
        self._record(metadata["retrievals"])
        self._projection_recorded = recorded
        self._saved_interview = metadata["interview"]

    def _bound(self, graph_id):
        if graph_id != self.graph_id:
            raise NeutralCapabilityError("graph_mismatch")

    def _call_with_retry(self, *_args, **_kwargs):
        raise NeutralCapabilityError("unsupported")

    def _local_search(self, *_args, **_kwargs):
        raise NeutralCapabilityError("unsupported")

    @staticmethod
    def _node(node):
        return NodeInfo(node["uuid"], node["name"], list(node["labels"]),
                        node["summary"], dict(node["attributes"]))

    @staticmethod
    def _edge(edge):
        return EdgeInfo(edge["uuid"], edge["name"], edge["fact"],
                        edge["source_node_uuid"], edge["target_node_uuid"],
                        edge.get("source_node_name"), edge.get("target_node_name"),
                        edge.get("created_at"), edge.get("valid_at"),
                        edge.get("invalid_at"), edge.get("expired_at"))

    def get_all_nodes(self, graph_id):
        self._bound(graph_id)
        if len(self.nodes) > self.MAX_OBJECTS:
            raise NeutralCapabilityError("result_too_large")
        self._record_projection("node", self.graph["nodes"])
        return [self._node(node) for node in self.graph["nodes"]]

    def get_all_edges(self, graph_id, include_temporal=True):
        self._bound(graph_id)
        if len(self.edges) > self.MAX_OBJECTS:
            raise NeutralCapabilityError("result_too_large")
        self._record_projection("edge", self.graph["edges"])
        return [self._edge(edge) for edge in self.graph["edges"]]

    def get_node_detail(self, node_uuid):
        if type(node_uuid) is not str or node_uuid not in self.nodes:
            raise NeutralCapabilityError("unknown_entity")
        self._record_projection("node", [self.nodes[node_uuid]])
        return self._node(self.nodes[node_uuid])

    def get_node_edges(self, graph_id, node_uuid):
        self._bound(graph_id)
        self.get_node_detail(node_uuid)
        return super().get_node_edges(graph_id, node_uuid)

    def search_graph(self, graph_id, query, limit=10, scope="edges"):
        self._bound(graph_id)
        if (type(query) is not str or not 1 <= len(query.strip()) <= 400
                or type(limit) is not int or not 1 <= limit <= 50
                or type(scope) is not str or scope not in {"edges", "nodes", "both"}):
            raise NeutralCapabilityError("invalid_request")
        if self.search_selector is None:
            raise NeutralCapabilityError("unsupported")
        self.search_calls += 1
        if self.search_calls > self.MAX_SEARCH_CALLS:
            raise NeutralCapabilityError("limit_exceeded")
        try:
            selected = self.search_selector(graph_id=graph_id, bound_scope=dict(self.scope),
                                            query=query, scope=scope, limit=limit)
        except Exception:
            raise NeutralCapabilityError("search_unavailable") from None
        if type(selected) is not list or len(selected) > limit:
            raise NeutralCapabilityError("invalid_selection")
        seen = set()
        facts, edges, nodes = [], [], []
        pending = []
        for item in selected:
            if type(item) is not dict or set(item) != {"id", "kind", "score", "bound_scope"}:
                raise NeutralCapabilityError("invalid_selection")
            identifier, kind, score = item["id"], item["kind"], item["score"]
            if (type(identifier) is not str or type(kind) is not str
                    or kind not in {"edge", "node"}
                    or (scope != "both" and kind != scope[:-1])
                    or type(item["bound_scope"]) is not dict
                    or item["bound_scope"] != self.scope
                    or identifier in seen or type(score) not in (int, float)
                    or abs(score) > 1_000_000_000_000
                    or not math.isfinite(score)):
                raise NeutralCapabilityError("invalid_selection")
            seen.add(identifier)
            source = self.edges if kind == "edge" else self.nodes
            if identifier not in source:
                raise NeutralCapabilityError("invalid_selection")
            value = source[identifier]
            if kind == "edge":
                facts.append(value["fact"])
                edges.append({"uuid": identifier, "name": value["name"],
                              "fact": value["fact"],
                              "source_node_uuid": value["source_node_uuid"],
                              "target_node_uuid": value["target_node_uuid"]})
            else:
                nodes.append({"uuid": identifier, "name": value["name"],
                              "labels": list(value["labels"]), "summary": value["summary"]})
                if value["summary"]:
                    facts.append(f'[{value["name"]}]: {value["summary"]}')
            pending.append({"access": "semantic_selection", "kind": kind,
                            "id": identifier, "score": score,
                            "query": query,
                            "scope": dict(self.scope), "episode_ids": list(value["episodes"]),
                            "evidence_ids": list(value["evidence_ids"])})
        self._record(pending)
        return SearchResult(facts, edges, nodes, query, len(facts))

    def interview_agents(self, simulation_id, interview_requirement,
                         simulation_requirement="", max_agents=5, custom_questions=None):
        if simulation_id != self.simulation_id:
            raise NeutralCapabilityError("simulation_mismatch")
        if (type(interview_requirement) is not str
                or not 1 <= len(interview_requirement.strip()) <= 400
                or type(simulation_requirement) is not str
                or len(simulation_requirement) > 4000
                or type(max_agents) is not int or not 1 <= max_agents <= 10
                or (custom_questions is not None and (
                    type(custom_questions) is not list or len(custom_questions) > 10
                    or any(type(item) is not str or not 1 <= len(item.strip()) <= 400
                           for item in custom_questions)))):
            raise NeutralCapabilityError("invalid_request")
        if self.interview_capability is None:
            raise NeutralCapabilityError("unsupported")
        try:
            result = self.interview_capability(
                simulation_id=simulation_id, interview_requirement=interview_requirement,
                simulation_requirement=simulation_requirement, max_agents=max_agents,
                custom_questions=custom_questions)
        except Exception:
            raise NeutralCapabilityError("interview_unavailable") from None
        if not isinstance(result, InterviewResult):
            raise NeutralCapabilityError("invalid_interview")
        try:
            if len(result.interviews) > max_agents or len(result.to_text().encode("utf-8")) > 65536:
                raise ValueError
        except Exception:
            raise NeutralCapabilityError("invalid_interview") from None
        return result

    def evidence_metadata(self):
        return {"schema_version": 1, "mode": "source_graph_projection", "graph_id": self.graph_id,
                "simulation_id": self.simulation_id, "scope": dict(self.scope),
                "projection_fingerprint": self.projection_fingerprint,
                "semantic_search": "trusted_selection_only" if self.search_selector else "unsupported",
                "live_semantic_adapter_connected": False,
                "interview": ("trusted_capability" if self.interview_capability
                              or getattr(self, "_saved_interview", None) == "trusted_capability"
                              else "unsupported"),
                "native_interview_verified": False,
                "claim_citations_validated": False,
                "simulation_observations_in_source_graph": False,
                "retrievals": list(self.retrieval_ledger)}

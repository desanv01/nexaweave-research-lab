"""Lazy app-shared admission for at most two owned evidence children."""
from datetime import datetime
from hashlib import sha256
import json
import re
import threading
from uuid import UUID, uuid4

from .knowledge_evidence_client import (KnowledgeEvidenceProcessClient, RESULT_LIMITS,
    validate_payload)
from .knowledge_reader import KnowledgeReadError
from .knowledge_transport import KnowledgeBusy, _json_object

_ERRORS = {"invalid_request": "invalid_request", "result_too_large": "result_too_large",
    "research_busy": "busy", "dossier_busy": "busy", "research_deadline": "timeout",
    "dossier_deadline": "timeout", "research_unavailable": "evidence_unavailable",
    "dossier_unavailable": "evidence_unavailable", "invalid_configuration": "evidence_unavailable",
    "invalid_reply": "invalid_reply"}


# Mirror the installed wire DTO at the stdlib-only HTTP boundary. The Flask
# interpreter does not load the provider runtime or the knowledge package.
_HEX_DIGEST = re.compile(r"[0-9a-f]{64}")
_COVERAGE_LIMITS = {"pages": 5, "scanned": 500, "eligible": 500, "excluded": 500,
                    "unknown": 500, "returned": 100}
_EDGE_TIMESTAMPS = {"valid_at", "invalid_at", "expired_at", "created_at"}


def _exact_object(value, fields):
    if type(value) is not dict or set(value) != fields:
        raise ValueError


def _integer(value, lower, upper=None):
    return type(value) is int and value >= lower and (upper is None or value <= upper)


def _uuid_text(value):
    return type(value) is str and len(value) == 36 and value == str(UUID(value))


def _digest(value):
    return type(value) is str and _HEX_DIGEST.fullmatch(value) is not None


def _timestamp(value, *, aware=False):
    if type(value) is not str or len(value) > 64 or "T" not in value:
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if aware and parsed.utcoffset() is None:
        raise ValueError


def _check_citation(value):
    _exact_object(value, {"evidence_id", "project_id", "source_revision", "source_name",
        "source_sha256", "source_byte_length", "source_codepoint_length", "source_recorded_at",
        "start", "end", "offset_unit", "excerpt", "excerpt_sha256", "declared_page"})
    excerpt = value["excerpt"]
    if (not all(_uuid_text(value[key]) for key in ("evidence_id", "project_id", "source_revision"))
            or type(value["source_name"]) is not str or len(value["source_name"]) > 256
            or not _digest(value["source_sha256"]) or not _digest(value["excerpt_sha256"])
            or not _integer(value["source_byte_length"], 1, 1048576)
            or not _integer(value["source_codepoint_length"], 1, 1048576)
            or not _integer(value["end"], 1, value["source_codepoint_length"])
            or not _integer(value["start"], 0, value["end"] - 1)
            or value["offset_unit"] != "unicode_codepoint"
            or type(excerpt) is not str or not 1 <= len(excerpt) <= 32768
            or len(excerpt) != value["end"] - value["start"]
            or (value["declared_page"] is not None and not _integer(value["declared_page"], 1))):
        raise ValueError
    _timestamp(value["source_recorded_at"], aware=True)
    raw = excerpt.encode("utf-8")
    if len(raw) > 32768 or sha256(raw).hexdigest() != value["excerpt_sha256"]:
        raise ValueError


def _check_passage_coverage(value):
    _exact_object(value, {"project_id", "source_revision", "source_sha256",
        "retained_codepoints", "retrieved_codepoints", "retrieved_passage_fraction"})
    fraction = value["retrieved_passage_fraction"]
    if (not _uuid_text(value["project_id"]) or not _uuid_text(value["source_revision"])
            or not _digest(value["source_sha256"]) or not _integer(value["retained_codepoints"], 1)
            or not _integer(value["retrieved_codepoints"], 0)
            or type(fraction) not in (float, int) or not 0 <= fraction <= 1):
        raise ValueError


def _check_two_hop_edge(edge, source, target):
    _exact_object(edge, {"provider_id", "name", "fact", "source_node_id", "target_node_id",
        "episode_ids", "evidence_ids", "citations"} | _EDGE_TIMESTAMPS)
    episodes, evidence, citations = (edge[key] for key in ("episode_ids", "evidence_ids", "citations"))
    if ((edge["source_node_id"], edge["target_node_id"]) != (source, target)
            or type(edge["provider_id"]) is not str
            or (edge["name"] is not None and (type(edge["name"]) is not str or len(edge["name"]) > 256))
            or type(edge["fact"]) is not str or not 1 <= len(edge["fact"]) <= 32768
            or type(episodes) is not list or not 1 <= len(episodes) <= 100
            or any(type(episode) is not str for episode in episodes)
            or type(evidence) is not list or not 1 <= len(evidence) <= 100
            or any(not _uuid_text(identity) for identity in evidence)
            or type(citations) is not list or len(citations) != len(evidence)):
        raise ValueError
    for key in _EDGE_TIMESTAMPS:
        if edge[key] is not None:
            _timestamp(edge[key])
    for identity, citation in zip(evidence, citations, strict=True):
        _check_citation(citation)
        if citation["evidence_id"] != identity:
            raise ValueError


def _check_two_hop_result(data, payload, scope):
    _exact_object(data, {"schema_version", "operation", "source_node_id", "target_node_id", "path",
        "path_status", "scope_coverage", "passage_coverage", "historical", "historical_semantics", "rank_basis"})
    source, target = payload["source_node_id"], payload["target_node_id"]
    coverage, passages = data["scope_coverage"], data["passage_coverage"]
    rank_basis = data["rank_basis"]
    rank_valid = (rank_basis == "directed_path_edge_id_order"
                  or (rank_basis == "directed_path_lexical_token_overlap"
                      and payload.get("question") is not None and data["path_status"] == "found"))
    _exact_object(coverage, {"display_graph_id", "scope", "truncated"} | _COVERAGE_LIMITS.keys())
    if (not _integer(data["schema_version"], 2, 2) or data["operation"] != "two_hop"
            or (data["source_node_id"], data["target_node_id"]) != (source, target)
            or type(data["historical"]) is not bool
            or data["historical_semantics"] != "retained_edges_not_bitemporal_reconstruction"
            or not rank_valid
            or coverage["display_graph_id"] != payload["display_graph_id"]
            or json.dumps(coverage["scope"], sort_keys=True) != json.dumps(scope, sort_keys=True)
            or type(coverage["truncated"]) is not bool
            or any(not _integer(coverage[key], 0, limit) for key, limit in _COVERAGE_LIMITS.items())
            or type(passages) is not list or len(passages) > 200):
        raise ValueError
    path, status = data["path"], data["path_status"]
    if status in ("no_path_in_scan", "selected_path_evidence_unavailable"):
        if path is not None or passages or coverage["returned"] != 0:
            raise ValueError
        return
    if status != "found" or coverage["returned"] != 2:
        raise ValueError
    _exact_object(path, {"intermediate_node_id", "hops"})
    middle, hops = path["intermediate_node_id"], path["hops"]
    if type(middle) is not str or middle in (source, target) or type(hops) is not list or len(hops) != 2:
        raise ValueError
    _check_two_hop_edge(hops[0], source, middle)
    _check_two_hop_edge(hops[1], middle, target)
    if hops[0]["provider_id"] == hops[1]["provider_id"]:
        raise ValueError
    for passage in passages:
        _check_passage_coverage(passage)


class KnowledgeEvidenceFacade:
    def __init__(self, settings, *, client_factory=None):
        self._settings = settings
        self._factory = client_factory
        self._admission = threading.BoundedSemaphore(2)

    def execute(self, method, graph_id, payload):
        if graph_id != self._settings.display_graph_id:
            raise KnowledgeReadError("not_found")
        validate_payload(method, payload, graph_id)
        if not self._admission.acquire(blocking=False):
            raise KnowledgeBusy()
        try:
            client = self._factory() if self._factory else KnowledgeEvidenceProcessClient(self._settings)
            request_id = str(uuid4())
            raw = json.dumps({"version": 1, "request_id": request_id, "method": method,
                "scope": self._settings.scope, "payload": payload}, ensure_ascii=False,
                allow_nan=False, separators=(",", ":")).encode()
            response = client.call(raw)
            # Also validate injected clients; never trust an uncorrelated completion.
            KnowledgeEvidenceProcessClient._validate_reply(client, response, request_id)
            result = _json_object(response)
            if not result["ok"]:
                raise KnowledgeReadError(_ERRORS[result["error"]["code"]])
            data = result["result"]
            try:
                encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
            except (ValueError, TypeError, OverflowError):
                if method == "two_hop":
                    raise KnowledgeReadError("invalid_reply") from None
                raise
            if len(encoded) > RESULT_LIMITS[method]:
                raise KnowledgeReadError("result_too_large")
            if method == "two_hop":
                try:
                    _check_two_hop_result(data, payload, self._settings.scope)
                except (ValueError, TypeError, OverflowError):
                    raise KnowledgeReadError("invalid_reply") from None
                return data
            if data.get("schema_version") != 1 or type(data.get("schema_version")) is not int:
                raise KnowledgeReadError("invalid_reply")
            if method == "research":
                if (data.get("historical_semantics") != "retained_edges_not_bitemporal_reconstruction"
                        or data.get("rank_basis") != "lexical_token_overlap"
                        or [s.get("display_graph_id") for s in data.get("scopes", [])] != payload["display_graph_ids"]):
                    raise KnowledgeReadError("invalid_reply")
            elif (data.get("model_generated") is not False or data.get("semantic_judge_used") is not False
                    or data.get("claim_support_status") != "not_reviewed"
                    or data.get("mode") != "model_free_evidence_dossier"
                    or data.get("request", {}).get("display_graph_ids") != payload["display_graph_ids"]):
                raise KnowledgeReadError("invalid_reply")
            return data
        finally:
            self._admission.release()

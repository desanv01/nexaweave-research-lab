"""Stdlib-only fixed installed ingestion transport and public contract checks."""
import hashlib
import re
from pathlib import Path
from uuid import UUID, uuid5, NAMESPACE_URL

from .knowledge_transport import (KnowledgeProcessClient, KnowledgeInvalidRequest,
    KnowledgeTransportFailure, _json_object, _uuid, _ROOT_FIELDS)
from .knowledge_reader import _scope
from .knowledge_source_client import encoded, CHILD_KEYS

MAX_BYTES = 256 * 1024
ENVELOPE_OVERHEAD = 1024
HASH = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
RESERVED_ATTRIBUTES = frozenset({"uuid", "name", "group_id", "graph_id", "labels", "attributes",
    "summary", "created_at", "valid_at", "invalid_at", "expired_at", "episodes", "source_node_uuid",
    "target_node_uuid", "fact", "fact_embedding", "name_embedding", "entity_edges", "source", "content",
    "dict", "json", "copy", "construct", "schema", "schema_json", "parse_obj", "parse_raw", "parse_file",
    "validate", "update_forward_refs", "from_orm"})
ERROR_CODES = frozenset({"invalid_request", "not_found", "source_denied", "tombstoned",
    "conflict", "busy", "cancelled", "uncertain", "model_calls_disabled",
    "budget_denied", "source_unavailable", "result_too_large"})
EXECUTION_KEYS = ("KNOWLEDGE_INGESTION_ENABLED", "KNOWLEDGE_MODEL_CALLS_AUTHORIZED",
    "KNOWLEDGE_INGESTION_ACCOUNT_ID", "KNOWLEDGE_INGESTION_CEILING_MICROUSD",
    "KNOWLEDGE_NEO4J_URI", "KNOWLEDGE_NEO4J_USER", "KNOWLEDGE_NEO4J_PASSWORD",
    "KNOWLEDGE_OPERATING_PROFILE", "KNOWLEDGE_LLM_BASE_URL", "KNOWLEDGE_LLM_MODEL",
    "KNOWLEDGE_LLM_API_KEY", "KNOWLEDGE_EMBEDDING_BASE_URL", "KNOWLEDGE_EMBEDDING_MODEL",
    "KNOWLEDGE_EMBEDDING_API_KEY", "KNOWLEDGE_EMBEDDING_DIMENSION",
    "KNOWLEDGE_SEARCH_RECIPE", "KNOWLEDGE_RERANKER_BASE_URL", "KNOWLEDGE_RERANKER_MODEL",
    "KNOWLEDGE_RERANKER_API_KEY", "KNOWLEDGE_STRUCTURED_OUTPUT_MODE",
    "KNOWLEDGE_CALL_TIMEOUT_SECONDS", "KNOWLEDGE_MAX_TOKENS",
    "KNOWLEDGE_MAX_COROUTINES", "KNOWLEDGE_TOTAL_LLM_CALL_BUDGET")


def ontology_value(value):
    """Bound structural checks; installed child additionally applies OntologySpec."""
    if (type(value) is not dict or set(value) != {"schema_version", "revision", "entity_types", "edge_types"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        raise ValueError
    _uuid(value["revision"])
    entities, edges = value["entity_types"], value["edge_types"]
    if type(entities) is not list or not 1 <= len(entities) <= 50 or type(edges) is not list or len(edges) > 50:
        raise ValueError
    entity_names, edge_names = set(), set()
    for items, names, edge in ((entities, entity_names, False), (edges, edge_names, True)):
        for item in items:
            fields = {"name", "description", "attributes"} | ({"source_targets"} if edge else set())
            if type(item) is not dict or set(item) != fields:
                raise ValueError
            name = item["name"]
            if (type(name) is not str or not NAME.fullmatch(name) or name in names
                    or (not edge and name in {"Entity", "Episodic", "Community", "Saga"})):
                raise ValueError
            names.add(name)
            if type(item["description"]) is not str or not 1 <= len(item["description"]) <= 500:
                raise ValueError
            attrs = item["attributes"]
            if type(attrs) is not list or len(attrs) > 20:
                raise ValueError
            seen = set()
            for attr in attrs:
                if (type(attr) is not dict or set(attr) != {"name", "type", "description"}
                        or type(attr["name"]) is not str or not NAME.fullmatch(attr["name"])
                        or attr["name"].lower() in RESERVED_ATTRIBUTES or attr["name"].startswith("model_")
                        or attr["name"] in seen or type(attr["type"]) is not str
                        or attr["type"] not in {"text", "integer", "number", "boolean"}
                        or type(attr["description"]) is not str or not 1 <= len(attr["description"]) <= 500):
                    raise ValueError
                seen.add(attr["name"])
            if edge:
                pairs = item["source_targets"]
                if type(pairs) is not list or not 1 <= len(pairs) <= 100:
                    raise ValueError
                for pair in pairs:
                    if (type(pair) is not dict or set(pair) != {"source", "target"}
                            or type(pair["source"]) is not str or type(pair["target"]) is not str
                            or pair["source"] not in entity_names or pair["target"] not in entity_names):
                        raise ValueError
    return value


def validate_payload(method, value):
    try:
        if type(value) is not dict or len(encoded(value)) > MAX_BYTES:
            raise ValueError
        if method == "status":
            if set(value) != {"operation_id"}:
                raise ValueError
        elif method in {"plan", "execute"}:
            if (set(value) != {"schema_version", "source_revision", "operation_id", "ontology"}
                    or type(value["schema_version"]) is not int or value["schema_version"] != 1):
                raise ValueError
            _uuid(value["source_revision"])
            ontology_value(value["ontology"])
        else:
            raise ValueError
        _uuid(value["operation_id"])
        return value
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise KnowledgeInvalidRequest() from None


def identities(scope, operation):
    canonical = {k: v for k, v in scope.items() if k != "schema_version"}
    group = "mf1_" + hashlib.sha256(encoded_sorted(canonical)).hexdigest()
    return group, str(uuid5(NAMESPACE_URL, f"mirofish:episode:v1:{group}:{operation}"))


def encoded_sorted(value):
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def validate_result(method, value, scope, payload):
    try:
        scope = _scope(scope)
        if scope["layer"] != "source" or scope["run_id"] is not None or scope["branch_id"] is not None:
            raise ValueError
        group, episode = identities(scope, payload["operation_id"])
        common = {"schema_version", "scope", "operation_id", "episode_id", "fingerprint",
            "evidence_ids", "graph_ingestion_executed", "model_calls_made", "actual_usage_microusd"}
        extra = ({"source_revision", "source_sha256", "source_byte_length", "source_codepoint_length",
                  "ontology_revision", "eligibility_codepoint_limit", "spending_authorized"}
                 if method == "plan" else {"state", "budget_state", "ceiling_microusd", "receipt"})
        if (type(value) is not dict or set(value) != common | extra
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or _scope(value["scope"]) != scope or value["operation_id"] != payload["operation_id"]
                or value["episode_id"] != episode or value["actual_usage_microusd"] is not None
                or type(value["fingerprint"]) is not str or not HASH.fullmatch(value["fingerprint"])):
            raise ValueError
        evidence = value["evidence_ids"]
        if type(evidence) is not list or not (0 if method == "status" else 1) <= len(evidence) <= 100 or len(set(evidence)) != len(evidence):
            raise ValueError
        for identity in evidence:
            _uuid(identity)
        if method == "plan":
            if (value["graph_ingestion_executed"] is not False or value["model_calls_made"] is not False
                    or value["spending_authorized"] is not False or value["source_revision"] != payload["source_revision"]
                    or value["ontology_revision"] != payload["ontology"]["revision"]
                    or type(value["source_sha256"]) is not str or not HASH.fullmatch(value["source_sha256"])
                    or type(value["eligibility_codepoint_limit"]) is not int or value["eligibility_codepoint_limit"] != 32768
                    or type(value["source_codepoint_length"]) is not int or not 1 <= value["source_codepoint_length"] <= 32768
                    or type(value["source_byte_length"]) is not int
                    or not value["source_codepoint_length"] <= value["source_byte_length"] <= 4 * value["source_codepoint_length"]):
                raise ValueError
        else:
            state, budget = value["state"], value["budget_state"]
            if (type(state) is not str or state not in {"pending", "running", "completed", "uncertain", "cancelled", "failed_no_effect", "not_admitted"}
                    or type(budget) is not str or budget not in {"reserved", "started", "settled", "uncertain", "released", "not_reserved"}
                    or type(value["graph_ingestion_executed"]) is not bool or value["model_calls_made"] is not None):
                raise ValueError
            ceiling = value["ceiling_microusd"]
            if budget == "not_reserved":
                if ceiling is not None:
                    raise ValueError
            elif type(ceiling) is not int or not 1 <= ceiling <= 2**63 - 1:
                raise ValueError
            receipt = value["receipt"]
            if budget == "settled" and state != "completed":
                raise ValueError
            if state == "not_admitted" and budget == "not_reserved":
                raise ValueError
            if state == "completed":
                if (not evidence or type(receipt) is not dict or set(receipt) != {"group_id", "episode_id", "fingerprint", "evidence_ids"}
                        or receipt != {"group_id": group, "episode_id": episode,
                            "fingerprint": value["fingerprint"], "evidence_ids": evidence}
                        or value["graph_ingestion_executed"] is not True):
                    raise ValueError
            elif receipt is not None or value["graph_ingestion_executed"] is not False:
                raise ValueError
            if method == "execute" and (state != "completed" or budget != "settled"):
                raise ValueError
        if len(encoded(value)) > MAX_BYTES:
            raise ValueError
        return value
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise KnowledgeTransportFailure(outcome_unknown=True) from None


class KnowledgeIngestionProcessClient(KnowledgeProcessClient):
    def __init__(self, settings, method, execution_environment=None):
        self._scope = dict(settings.scope)
        self._method = method
        script = Path(settings.bootstrap).with_name("source_ingestion_bootstrap.py")
        if (Path(settings.bootstrap).name != "read_bootstrap.py" or script.parent.name != "mirofish_knowledge"
                or "site-packages" not in script.parts):
            raise ValueError("invalid ingestion configuration")
        child = {key: settings.child_environment[key] for key in CHILD_KEYS}
        if method == "execute":
            child.update({key: value for key, value in (execution_environment or {}).items() if key in EXECUTION_KEYS})
        elif method == "status" and execution_environment and "KNOWLEDGE_INGESTION_ACCOUNT_ID" in execution_environment:
            child["KNOWLEDGE_INGESTION_ACCOUNT_ID"] = execution_environment["KNOWLEDGE_INGESTION_ACCOUNT_ID"]
        super().__init__(settings.python, str(script), timeout_seconds=120, child_environment=child)

    def _validate_request(self, raw):
        try:
            if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES + ENVELOPE_OVERHEAD:
                raise ValueError
            value = _json_object(raw)
            if (set(value) != _ROOT_FIELDS or type(value["version"]) is not int or value["version"] != 1
                    or _scope(value["scope"]) != self._scope or value["method"] != self._method
                    or self._scope["layer"] != "source" or self._scope["run_id"] is not None or self._scope["branch_id"] is not None):
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

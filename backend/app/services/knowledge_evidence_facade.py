"""Lazy app-shared admission for at most two owned evidence children."""
import json
import threading
from uuid import uuid4

from .knowledge_evidence_client import (KnowledgeEvidenceProcessClient, RESULT_LIMITS,
    validate_payload)
from .knowledge_reader import KnowledgeReadError
from .knowledge_transport import KnowledgeBusy, _json_object

_ERRORS = {"invalid_request": "invalid_request", "result_too_large": "result_too_large",
    "research_busy": "busy", "dossier_busy": "busy", "research_deadline": "timeout",
    "dossier_deadline": "timeout", "research_unavailable": "evidence_unavailable",
    "dossier_unavailable": "evidence_unavailable", "invalid_configuration": "evidence_unavailable"}


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
            encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
            if len(encoded) > RESULT_LIMITS[method]:
                raise KnowledgeReadError("result_too_large")
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

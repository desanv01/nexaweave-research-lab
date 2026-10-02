"""Lazy trusted ingestion host settings; no provider imports in Flask."""
import os
import threading
from uuid import uuid4

from .knowledge_ingestion_client import (KnowledgeIngestionProcessClient, EXECUTION_KEYS,
    encoded, validate_payload, validate_result)
from .knowledge_transport import KnowledgeBusy, KnowledgeTransportFailure, _json_object, _uuid
from .knowledge_reader import KnowledgeReadError


def execution_settings():
    """Snapshot only operator configuration, never request-selected credentials."""
    return {key: os.environ[key] for key in EXECUTION_KEYS if key in os.environ}


def require_execution(values):
    try:
        if values.get("KNOWLEDGE_INGESTION_ENABLED") != "true" or values.get("KNOWLEDGE_MODEL_CALLS_AUTHORIZED") != "true":
            raise ValueError
        _uuid(values["KNOWLEDGE_INGESTION_ACCOUNT_ID"])
        raw = values["KNOWLEDGE_INGESTION_CEILING_MICROUSD"]
        if type(raw) is not str or not raw.isascii() or not raw.isdecimal() or str(int(raw)) != raw or not 1 <= int(raw) <= 2**63 - 1:
            raise ValueError
        for key in ("KNOWLEDGE_LLM_BASE_URL", "KNOWLEDGE_LLM_MODEL", "KNOWLEDGE_LLM_API_KEY",
                    "KNOWLEDGE_EMBEDDING_BASE_URL", "KNOWLEDGE_EMBEDDING_MODEL", "KNOWLEDGE_EMBEDDING_API_KEY",
                    "KNOWLEDGE_EMBEDDING_DIMENSION", "KNOWLEDGE_NEO4J_URI", "KNOWLEDGE_NEO4J_USER", "KNOWLEDGE_NEO4J_PASSWORD"):
            value = values[key]
            if type(value) is not str or not 1 <= len(value) <= 1024 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError
    except (ValueError, TypeError, KeyError):
        raise KnowledgeReadError("model_calls_disabled") from None


class KnowledgeIngestionFacade:
    def __init__(self, settings, *, client_factory=None, environment=None):
        self._settings = settings
        self._factory = client_factory
        self._environment = environment
        self._slots = threading.BoundedSemaphore(2)

    def execute(self, method, graph_id, payload):
        if graph_id != self._settings.display_graph_id:
            raise KnowledgeReadError("not_found")
        validate_payload(method, payload)
        values = execution_settings() if self._environment is None else dict(self._environment)
        if method == "execute":
            # Also enforced in the installed host after fresh persisted authority.
            require_execution(values)
        if not self._slots.acquire(blocking=False):
            raise KnowledgeBusy()
        try:
            client = (self._factory(method, values) if self._factory else
                      KnowledgeIngestionProcessClient(self._settings, method, values))
            request_id = str(uuid4())
            raw = encoded({"version": 1, "request_id": request_id, "method": method,
                           "scope": self._settings.scope, "payload": payload})
            reply = client.call(raw)  # Exactly one launch. Lost reply is never retried.
            KnowledgeIngestionProcessClient._validate_reply(client, reply, request_id)
            result = _json_object(reply)
            if not result["ok"]:
                raise KnowledgeReadError(result["error"]["code"])
            return validate_result(method, result["result"], self._settings.scope, payload)
        finally:
            self._slots.release()

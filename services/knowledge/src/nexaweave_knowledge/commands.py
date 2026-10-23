"""Versioned in-process knowledge commands; transport and policy are injected."""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID

from .contracts import (FactResult, GraphPage, GraphPageRequest, KnowledgeScope,
                        OntologySpec, SearchQuery, SearchResult, SourceEnvelope)
from .graph_reads import GraphReadViolation, ResultTooLarge
from .ingestion import IngestionUncertain
from .operations import (Busy, CompletionReceipt, Conflict, InvalidTransition,
                         NotFound, StaleAttempt, Tombstoned, _receipt, request_fingerprint)
from .provider_errors import OperationConflict, ReconciliationRequired, UnsupportedCapability


_REQUEST_LIMIT = 512 * 1024
_REPLY_LIMIT = 2 * 1024 * 1024
_MAX_DEPTH = 32
_METHODS = frozenset({"page", "entity", "search", "ingest"})
_ROOT_FIELDS = frozenset({"version", "request_id", "method", "scope", "payload"})
_ERROR_CODES = frozenset({
    "invalid_request", "unauthorized", "busy", "not_found", "conflict",
    "tombstoned", "uncertain", "unsupported", "timeout", "result_too_large",
    "model_calls_disabled", "internal_error",
})


@dataclass(frozen=True)
class _Command:
    request_id: str
    method: str
    scope: KnowledgeScope
    payload: Any


class _BadRequest(ValueError):
    def __init__(self, request_id: str | None = None):
        self.request_id = request_id


class _PolicyDenied(Exception):
    pass


def _canonical_uuid(value: object) -> str:
    if not isinstance(value, str):
        raise _BadRequest
    try:
        parsed = UUID(value)
    except ValueError:
        raise _BadRequest from None
    if str(parsed) != value:
        raise _BadRequest
    return value


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise _BadRequest
        result[key] = value
    return result


def _reject_constant(_value):
    raise _BadRequest


def _bounded_structure(value: object) -> None:
    stack = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if isinstance(current, dict):
            if depth > _MAX_DEPTH:
                raise _BadRequest
            stack.extend((item, depth + 1) for item in current.values() if isinstance(item, (dict, list)))
        elif isinstance(current, list):
            if depth > _MAX_DEPTH:
                raise _BadRequest
            stack.extend((item, depth + 1) for item in current if isinstance(item, (dict, list)))


def _nested_model(model, value):
    if not isinstance(value, dict):
        raise _BadRequest
    if "schema_version" in value and (type(value["schema_version"]) is not int or value["schema_version"] != 1):
        raise _BadRequest
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        return model.model_validate_json(encoded)
    except (TypeError, ValueError, UnicodeError):
        raise _BadRequest from None


def _parse(raw: bytes) -> _Command:
    if not isinstance(raw, bytes) or len(raw) > _REQUEST_LIMIT:
        raise _BadRequest
    request_id = None
    try:
        root = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=_reject_constant)
        _bounded_structure(root)
        if not isinstance(root, dict) or set(root) != _ROOT_FIELDS:
            raise _BadRequest
        if type(root["version"]) is not int or root["version"] != 1:
            raise _BadRequest
        request_id = _canonical_uuid(root["request_id"])
        method = root["method"]
        if type(method) is not str or method not in _METHODS:
            raise _BadRequest
        scope = _nested_model(KnowledgeScope, root["scope"])
        payload = root["payload"]
        if method == "page":
            payload = _nested_model(GraphPageRequest, payload)
        elif method == "entity":
            if not isinstance(payload, dict) or set(payload) != {"provider_id"}:
                raise _BadRequest
            payload = _canonical_uuid(payload["provider_id"])
        elif method == "search":
            payload = _nested_model(SearchQuery, payload)
        else:
            if not isinstance(payload, dict) or set(payload) != {"source", "ontology"}:
                raise _BadRequest
            source = _nested_model(SourceEnvelope, payload["source"])
            ontology = _nested_model(OntologySpec, payload["ontology"])
            if source.ontology_revision != ontology.revision or len(set(source.evidence_ids)) != len(source.evidence_ids):
                raise _BadRequest
            payload = (source, ontology)
        return _Command(request_id, method, scope, payload)
    except (json.JSONDecodeError, UnicodeError, ValueError, TypeError, OverflowError, RecursionError):
        raise _BadRequest(request_id) from None


def _wire(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _error(request_id: str | None, code: str) -> bytes:
    assert code in _ERROR_CODES
    return _wire({"version": 1, "request_id": request_id, "ok": False, "error": {"code": code}})


def _success(request_id: str, result: object) -> bytes:
    reply = _wire({"version": 1, "request_id": request_id, "ok": True, "result": result})
    if len(reply) > _REPLY_LIMIT:
        raise ResultTooLarge
    return reply


def _valid_principal(principal: object) -> bool:
    return (isinstance(principal, str) and 1 <= len(principal) <= 128
            and bool(principal.strip()) and all(32 <= ord(char) <= 126 for char in principal))


def _output_uuid(value: object) -> None:
    _canonical_uuid(value)


def _validated_facts(result, scope: KnowledgeScope, *, expected_type, maximum: int):
    if not isinstance(result, expected_type):
        raise ValueError
    if len(result.facts) > maximum:
        raise ValueError
    for fact in result.facts:
        if not isinstance(fact, FactResult):
            raise ValueError
        if fact.score is not None and not math.isfinite(fact.score):
            raise ValueError
    # JSON mode accepts wire UUID/datetime forms without Pydantic's Python-mode
    # coercions. The finite JSON encoder rejects NaN/Infinity anywhere in output.
    payload = result.model_dump(mode="json", warnings=False)
    encoded = _wire(payload)
    result = type(result).model_validate_json(encoded)
    if len(result.facts) > maximum:
        raise ValueError
    for fact in result.facts:
        if not isinstance(fact, FactResult) or fact.scope != scope:
            raise ValueError
        _output_uuid(fact.provider_id)
        for value in (fact.source_node_id, fact.target_node_id):
            if value is not None:
                _output_uuid(value)
        for value in fact.episode_ids:
            _output_uuid(value)
        if fact.score is not None and not math.isfinite(fact.score):
            raise ValueError
        if fact.kind == "edge" and (fact.source_node_id is None or fact.target_node_id is None):
            raise ValueError
    return payload


class KnowledgeCommandDispatcher:
    def __init__(self, provider, coordinator, authorize: Callable, *,
                 allow_model_calls: bool = False, max_inflight: int = 4,
                 read_timeout_seconds: float = 30):
        if not callable(authorize):
            raise ValueError("authorize callback required")
        if type(allow_model_calls) is not bool:
            raise ValueError("allow_model_calls must be bool")
        if type(max_inflight) is not int or not 1 <= max_inflight <= 16:
            raise ValueError("max_inflight must be between 1 and 16")
        if (isinstance(read_timeout_seconds, bool)
                or not isinstance(read_timeout_seconds, (int, float))
                or not math.isfinite(read_timeout_seconds)
                or not 0 < read_timeout_seconds <= 120):
            raise ValueError("read_timeout_seconds must be finite and within 120")
        self._provider = provider
        self._coordinator = coordinator
        self._authorize = authorize
        self._allow_model_calls = allow_model_calls
        self._limit = max_inflight
        self._active = 0
        self._read_timeout = float(read_timeout_seconds)

    async def dispatch(self, raw: bytes, *, principal: str) -> bytes:
        if not _valid_principal(principal):
            return _error(None, "unauthorized")
        try:
            command = _parse(raw)
        except _BadRequest as exc:
            return _error(exc.request_id, "invalid_request")
        if self._active >= self._limit:
            return _error(command.request_id, "busy")
        self._active += 1
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._read_timeout

            async def bounded(start):
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                result = await asyncio.wait_for(start(), remaining)
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError
                if loop.time() >= deadline:
                    raise asyncio.TimeoutError
                return result

            async def call_policy():
                try:
                    return await self._authorize(principal, command.method, command.scope)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    raise _PolicyDenied from None

            try:
                grant = await bounded(call_policy)
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                return _error(command.request_id, "timeout")
            except _PolicyDenied:
                return _error(command.request_id, "unauthorized")
            if grant is not True:
                return _error(command.request_id, "unauthorized")
            if command.method in {"search", "ingest"} and not self._allow_model_calls:
                return _error(command.request_id, "model_calls_disabled")

            try:
                if command.method == "page":
                    result = await bounded(lambda: self._provider.page(command.scope, command.payload))
                    payload = _validated_facts(result, command.scope, expected_type=GraphPage, maximum=100)
                elif command.method == "entity":
                    result = await bounded(lambda: self._provider.entity(command.scope, command.payload))
                    payload = _validated_facts(result, command.scope, expected_type=SearchResult, maximum=101)
                elif command.method == "search":
                    result = await bounded(lambda: self._provider.search(command.scope, command.payload))
                    payload = _validated_facts(result, command.scope, expected_type=SearchResult, maximum=300)
                else:
                    source, ontology = command.payload
                    receipt = await self._coordinator.ingest(command.scope, source, ontology)
                    if asyncio.current_task().cancelling():
                        raise asyncio.CancelledError
                    if not isinstance(receipt, CompletionReceipt):
                        raise ValueError
                    fingerprint = request_fingerprint(command.scope, source, ontology)
                    valid = _receipt(receipt, command.scope, source.operation_id, fingerprint)
                    if valid.evidence_ids != source.evidence_ids:
                        raise ValueError
                    payload = valid.json_value()
                return _success(command.request_id, payload)
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                return _error(command.request_id,
                              "uncertain" if command.method == "ingest" else "timeout")
            except (Busy,):
                return _error(command.request_id, "busy")
            except NotFound:
                return _error(command.request_id, "not_found")
            except (Conflict, OperationConflict, InvalidTransition):
                return _error(command.request_id, "conflict")
            except Tombstoned:
                return _error(command.request_id, "tombstoned")
            except (IngestionUncertain, ReconciliationRequired, StaleAttempt):
                return _error(command.request_id, "uncertain")
            except UnsupportedCapability:
                return _error(command.request_id, "unsupported")
            except ResultTooLarge:
                return _error(command.request_id, "result_too_large")
            except Exception:
                return _error(command.request_id,
                              "uncertain" if command.method == "ingest" else "internal_error")
        finally:
            self._active -= 1

"""Private bounded page session; each frame retains the original dispatcher."""
from __future__ import annotations

import asyncio
import json
import time
from uuid import UUID

from .contracts import KnowledgeScope
from .stdio import (PipeProtocolError, _REQUEST_MAX, _RESPONSE_MAX, _object,
                    _read_exact, _write_one)

MAX_REQUESTS = 800
MAX_LIFETIME_SECONDS = 120
_FIELDS = frozenset({"version", "request_id", "method", "scope", "payload"})
_ERRORS = frozenset({"invalid_request", "unauthorized", "busy", "not_found",
    "conflict", "tombstoned", "uncertain", "unsupported", "timeout",
    "result_too_large", "model_calls_disabled", "internal_error"})


def _scope(value):
    try:
        value = value.model_dump(mode="json") if isinstance(value, KnowledgeScope) else value
        if (type(value) is not dict or type(value.get("schema_version")) is not int
                or value["schema_version"] != 1):
            raise ValueError
        scope = KnowledgeScope.model_validate_json(json.dumps(value, allow_nan=False)).model_dump(mode="json")
        if scope != value or set(scope) != set(value):
            raise ValueError
        return scope
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise PipeProtocolError from None


def _request(raw, scope, seen):
    value = _object(raw)
    try:
        identifier = value["request_id"]
        if (set(value) != _FIELDS or type(value["version"]) is not int
                or value["version"] != 1 or value["method"] != "page"
                or type(identifier) is not str or str(UUID(identifier)) != identifier
                or identifier in seen or type(value["payload"]) is not dict
                or _scope(value["scope"]) != scope):
            raise ValueError
        return identifier
    except (KeyError, ValueError, TypeError, AttributeError):
        raise PipeProtocolError from None


def _response(raw, identifier):
    if type(raw) is not bytes or not 0 < len(raw) <= _RESPONSE_MAX:
        raise PipeProtocolError
    value = _object(raw)
    if (type(value.get("version")) is not int or value["version"] != 1
            or value.get("request_id") != identifier or type(value.get("ok")) is not bool):
        raise PipeProtocolError
    if value["ok"]:
        if set(value) != {"version", "request_id", "ok", "result"} or type(value["result"]) is not dict:
            raise PipeProtocolError
    elif (set(value) != {"version", "request_id", "ok", "error"}
          or type(value["error"]) is not dict or set(value["error"]) != {"code"}
          or type(value["error"]["code"]) is not str or value["error"]["code"] not in _ERRORS):
        raise PipeProtocolError
    return value["ok"]


async def serve_read_session(dispatcher, *, principal, scope, input_stream, output_stream):
    """Return 'closed' only after the zero frame and EOF; all failures raise.

    Blocking stream operations run in the executor so the lifetime also bounds
    waiting for input/output. The installed bootstrap has a process watchdog:
    a blocked executor thread cannot outlive the child lifetime on shutdown.
    No dispatcher exception/cancellation is converted into safe retry evidence.
    """
    deadline = time.monotonic() + MAX_LIFETIME_SECONDS
    scope = _scope(scope)
    seen = set()

    async def bounded(start):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PipeProtocolError
        result = await asyncio.wait_for(start(), timeout=remaining)
        if time.monotonic() >= deadline:
            raise PipeProtocolError
        return result

    async def read(length):
        return await bounded(lambda: asyncio.to_thread(_read_exact, input_stream, length))

    while True:
        length = int.from_bytes(await read(4), "big")
        if length == 0:
            trailing = await bounded(lambda: asyncio.to_thread(input_stream.read, 1))
            if trailing != b"":
                raise PipeProtocolError
            return "closed"
        if length > _REQUEST_MAX or len(seen) >= MAX_REQUESTS:
            raise PipeProtocolError
        raw = await read(length)
        identifier = _request(raw, scope, seen)
        seen.add(identifier)
        response = await bounded(lambda: dispatcher.dispatch(raw, principal=principal))
        ok = _response(response, identifier)
        await bounded(lambda: asyncio.to_thread(_write_one, output_stream, response))
        if not ok:
            # Deliver the unchanged dispatcher error, then permanently close.
            raise PipeProtocolError

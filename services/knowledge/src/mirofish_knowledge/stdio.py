"""One-shot private binary pipe framing for an injected command dispatcher.

The caller owns the streams and process. Blocking reads are intentional here;
this is not an asyncio multiplexed server.
"""

from __future__ import annotations

import json
import math


_REQUEST_MAX = 512 * 1024
_RESPONSE_MAX = 2 * 1024 * 1024
_MAX_DEPTH = 32
_SAFE_ERROR = (b'{"version":1,"request_id":null,"ok":false,'
               b'"error":{"code":"invalid_request"}}')


class PipeProtocolError(ValueError):
    pass


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise PipeProtocolError
        result[key] = value
    return result


def _constant(_value):
    raise PipeProtocolError


def _object(raw: bytes) -> dict:
    try:
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                            parse_constant=_constant)
        if type(parsed) is not dict:
            raise PipeProtocolError
        stack = [(parsed, 1)]
        while stack:
            current, depth = stack.pop()
            if depth > _MAX_DEPTH:
                raise PipeProtocolError
            members = current.values() if isinstance(current, dict) else current
            for item in members:
                if isinstance(item, float) and not math.isfinite(item):
                    raise PipeProtocolError
                if isinstance(item, (dict, list)):
                    stack.append((item, depth + 1))
        return parsed
    except (UnicodeError, ValueError, TypeError, RecursionError, OverflowError):
        raise PipeProtocolError from None


def _read_exact(stream, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = stream.read(length - len(data))
        if not isinstance(chunk, bytes) or not chunk:
            raise PipeProtocolError
        data.extend(chunk)
    return bytes(data)


def _read_one(stream) -> bytes:
    header = _read_exact(stream, 4)
    length = int.from_bytes(header, "big")
    if not 0 < length <= _REQUEST_MAX:
        raise PipeProtocolError
    body = _read_exact(stream, length)
    if stream.read(1) != b"":
        raise PipeProtocolError
    _object(body)
    return body


def _write_one(stream, body: bytes) -> None:
    if type(body) is not bytes or not 0 < len(body) <= _RESPONSE_MAX:
        raise PipeProtocolError
    frame = len(body).to_bytes(4, "big") + body
    view = memoryview(frame)
    while view:
        written = stream.write(view)
        if not isinstance(written, int) or written <= 0:
            raise PipeProtocolError
        view = view[written:]
    stream.flush()


async def serve_once(dispatcher, *, principal, input_stream, output_stream) -> str:
    """Serve exactly one frame; return 'replied' or 'invalid_request'."""
    try:
        request = _read_one(input_stream)
    except PipeProtocolError:
        _write_one(output_stream, _SAFE_ERROR)
        return "invalid_request"
    # A dispatch exception/cancellation may follow a mutation. Never turn it
    # into a response suggesting completion or safe retry.
    response = await dispatcher.dispatch(request, principal=principal)
    if type(response) is not bytes or not 0 < len(response) <= _RESPONSE_MAX:
        raise PipeProtocolError
    _object(response)
    _write_one(output_stream, response)
    return "replied"

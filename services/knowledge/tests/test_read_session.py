"""Private framing and fresh injected runtime authority; no external services."""
import asyncio
import io
import json
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from nexaweave_knowledge import read_session
from nexaweave_knowledge.commands import KnowledgeCommandDispatcher
from nexaweave_knowledge.contracts import KnowledgeScope, Layer
from nexaweave_knowledge.read_runtime import ReadRuntimeProvider, ReadSettings
from nexaweave_knowledge.stdio import PipeProtocolError


def scope():
    return KnowledgeScope(workspace_id=uuid4(), project_id=uuid4(), graph_id=uuid4(), layer=Layer.source)


def request(bound, **changes):
    value = dict(version=1, request_id=str(uuid4()), method="page", scope=bound.model_dump(mode="json"),
        payload=dict(schema_version=1, kind="node", limit=100, cursor=None, entity_type=None))
    value.update(changes)
    return value


def frame(value):
    raw = json.dumps(value, separators=(",", ":")).encode() if type(value) is dict else value
    return len(raw).to_bytes(4, "big") + raw


def replies(raw):
    result = []
    while raw:
        size = int.from_bytes(raw[:4], "big")
        assert 0 < size <= len(raw) - 4
        result.append(json.loads(raw[4:4 + size]))
        raw = raw[4 + size:]
    return result


class Echo:
    def __init__(self):
        self.calls = []

    async def dispatch(self, raw, *, principal):
        value = json.loads(raw)
        self.calls.append((value, principal))
        return json.dumps(dict(version=1, request_id=value["request_id"], ok=True,
            result=dict(schema_version=1, facts=[], next_cursor=None))).encode()


async def serve(bound, wire, dispatcher=None, output=None):
    return await read_session.serve_read_session(dispatcher or Echo(), principal="fixture-owner", scope=bound,
        input_stream=io.BytesIO(wire), output_stream=output if output is not None else io.BytesIO())


@pytest.mark.asyncio
async def test_multiple_frames_preserve_ids_payloads_and_explicit_close():
    bound = scope()
    commands = [request(bound), request(bound, payload=dict(schema_version=1, kind="edge",
        limit=100, cursor=None, entity_type=None))]
    echo, output = Echo(), io.BytesIO()
    assert await serve(bound, b"".join(map(frame, commands)) + bytes(4), echo, output) == "closed"
    assert echo.calls == [(command, "fixture-owner") for command in commands]
    assert [reply["request_id"] for reply in replies(output.getvalue())] == [c["request_id"] for c in commands]


@pytest.mark.asyncio
@pytest.mark.parametrize("wire", [b"", b"\0", b"\0\0\0\x05abc", bytes(4) + b"x",
    bytes(4) + bytes(4), (512 * 1024 + 1).to_bytes(4, "big")])
async def test_incomplete_extra_oversized_or_missing_termination_fails(wire):
    echo = Echo()
    with pytest.raises(PipeProtocolError):
        await serve(scope(), wire, echo)
    assert echo.calls == []


@pytest.mark.asyncio
async def test_bare_eof_after_valid_page_is_not_success():
    bound, echo, output = scope(), Echo(), io.BytesIO()
    with pytest.raises(PipeProtocolError):
        await serve(bound, frame(request(bound)), echo, output)
    assert len(echo.calls) == len(replies(output.getvalue())) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["duplicate", "wrong_scope", "nonpage", "bool_version", "bool_scope_version", "bad_uuid",
    "extra_field", "duplicate_key", "nonfinite", "depth", "bad_json"])
async def test_invalid_frame_never_dispatches_that_frame(mode):
    bound, echo = scope(), Echo()
    valid = request(bound)
    bad = dict(valid)
    prefix = b""
    if mode == "duplicate":
        prefix = frame(valid)
    elif mode == "wrong_scope": bad["scope"] = scope().model_dump(mode="json")
    elif mode == "nonpage": bad["method"] = "ingest"
    elif mode == "bool_version": bad["version"] = True
    elif mode == "bool_scope_version": bad["scope"] = {**bad["scope"], "schema_version": True}
    elif mode == "bad_uuid": bad["request_id"] = "not-a-uuid"
    elif mode == "extra_field": bad["extra"] = True
    raw = json.dumps(bad).encode()
    if mode == "duplicate_key": raw = b'{"version":1,"version":1}'
    elif mode == "nonfinite": raw = b'{"value":NaN}'
    elif mode == "depth": raw = b'{"value":' + b'[' * 33 + b'0' + b']' * 33 + b'}'
    elif mode == "bad_json": raw = b'{'
    with pytest.raises(PipeProtocolError):
        await serve(bound, prefix + frame(raw) + bytes(4), echo)
    assert len(echo.calls) == (1 if mode == "duplicate" else 0)


@pytest.mark.asyncio
async def test_exact_800_bound_does_not_dispatch_801st_request():
    bound = scope()
    wire = b"".join(frame(request(bound)) for _ in range(800))
    echo = Echo()
    assert await serve(bound, wire + bytes(4), echo) == "closed"
    assert len(echo.calls) == 800
    echo = Echo()
    with pytest.raises(PipeProtocolError):
        await serve(bound, wire + frame(request(bound)) + bytes(4), echo)
    assert len(echo.calls) == 800


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["wrong_id", "bool_version", "invalid_json", "oversized", "error"])
async def test_bad_reply_or_dispatcher_error_closes_session(mode):
    bound, output = scope(), io.BytesIO()
    class Bad(Echo):
        async def dispatch(self, raw, *, principal):
            response = json.loads(await super().dispatch(raw, principal=principal))
            if mode == "wrong_id": response["request_id"] = str(uuid4())
            elif mode == "bool_version": response["version"] = True
            elif mode == "invalid_json": return b'{'
            elif mode == "oversized": return b'x' * (2 * 1024 * 1024 + 1)
            else:
                response.pop("result")
                response.update(ok=False, error=dict(code="unauthorized"))
            return json.dumps(response).encode()
    dispatcher = Bad()
    with pytest.raises(PipeProtocolError):
        await serve(bound, frame(request(bound)) + frame(request(bound)) + bytes(4), dispatcher, output)
    assert len(dispatcher.calls) == 1
    assert len(replies(output.getvalue())) == (1 if mode == "error" else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("queued_next", [False, True])
async def test_timeout_reply_is_terminal_without_reading_next_frame_or_close_marker(queued_next):
    bound, output = scope(), io.BytesIO()
    command = request(bound)
    wire = frame(command)
    if queued_next:
        wire += frame(request(bound)) + bytes(4)
    class Input(io.BytesIO):
        reads = 0
        def read(self, length):
            self.reads += 1
            assert self.reads <= 2, "failed page must not wait for another header or EOF"
            return super().read(length)
    class Timeout(Echo):
        async def dispatch(self, raw, *, principal):
            await super().dispatch(raw, principal=principal)
            return (b'{"version":1,"request_id":"' + command["request_id"].encode()
                    + b'","ok":false,"error":{"code":"timeout"}}')
    dispatcher, source = Timeout(), Input(wire)
    with pytest.raises(PipeProtocolError):
        await read_session.serve_read_session(dispatcher, principal="fixture-owner", scope=bound,
            input_stream=source, output_stream=output)
    assert len(dispatcher.calls) == 1 and source.reads == 2
    expected = (b'{"version":1,"request_id":"' + command["request_id"].encode()
                + b'","ok":false,"error":{"code":"timeout"}}')
    assert output.getvalue() == frame(expected)


@pytest.mark.asyncio
async def test_dispatch_cancellation_propagates_without_response_or_next_page():
    bound, output = scope(), io.BytesIO()
    class Cancel(Echo):
        async def dispatch(self, raw, *, principal):
            await super().dispatch(raw, principal=principal)
            raise asyncio.CancelledError
    dispatcher = Cancel()
    with pytest.raises(asyncio.CancelledError):
        await serve(bound, frame(request(bound)) + bytes(4), dispatcher, output)
    assert len(dispatcher.calls) == 1 and output.getvalue() == b""


@pytest.mark.asyncio
async def test_whole_lifetime_expires_before_read_without_clock_restart(monkeypatch):
    values = iter([0, 121])
    monkeypatch.setattr(read_session, "time", SimpleNamespace(monotonic=lambda: next(values)))
    echo = Echo()
    with pytest.raises(PipeProtocolError):
        await serve(scope(), bytes(4), echo)
    assert echo.calls == []


@pytest.mark.asyncio
async def test_time_spent_on_prior_pages_cannot_restart_whole_lifetime(monkeypatch):
    clock = [0]
    monkeypatch.setattr(read_session, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    bound, output = scope(), io.BytesIO()
    class Slow(Echo):
        async def dispatch(self, raw, *, principal):
            response = await super().dispatch(raw, principal=principal)
            clock[0] += 61
            return response
    dispatcher = Slow()
    with pytest.raises(PipeProtocolError):
        await serve(bound, frame(request(bound)) + frame(request(bound)) + bytes(4), dispatcher, output)
    assert len(dispatcher.calls) == 2 and len(replies(output.getvalue())) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("revocation", ["none", "binding", "ledger"])
async def test_actual_injected_dispatcher_rereads_persisted_checks_between_pages(revocation):
    bound, other, output = scope(), scope(), io.BytesIO()
    settings = ReadSettings("fixture-owner", "fixture-graph", bound, "127.0.0.1", 15432,
        "fixture", "fixture", "private", "bolt://127.0.0.1:7687", "neo4j", "private")
    checks, drivers = [], []
    class Driver:
        async def execute_query(self, query, *, parameters_, routing_):
            assert "ORDER BY" in query and parameters_["group_id"] == bound.group_id and routing_ == "r"
            checks.append("query")
            return [], None, None
        async def close(self):
            checks.append("close")
    def driver():
        created = Driver()
        drivers.append(created)
        return created
    runtime = ReadRuntimeProvider(settings, connection_factory=lambda: None, driver_factory=driver)
    @contextmanager
    def guard(current):
        checks.append("guard")
        assert current == bound
        if len(drivers) == 1 and revocation == "ledger":
            from nexaweave_knowledge.operations import Tombstoned
            raise Tombstoned("fixture revoked")
        try:
            yield
        finally:
            checks.append("unlock")
    def resolve(principal, display):
        checks.append("binding")
        assert (principal, display) == ("fixture-owner", "fixture-graph")
        return SimpleNamespace(scope=other if len(drivers) == 1 and revocation == "binding" else bound)
    runtime._ledger.read_scope = guard
    runtime._binding.resolve = resolve
    dispatcher = KnowledgeCommandDispatcher(runtime, object(), runtime.authorize, allow_model_calls=False)
    commands = [request(bound) for _ in range(3)]
    wire = b"".join(map(frame, commands)) + bytes(4)
    if revocation == "none":
        assert await serve(bound, wire, dispatcher, output) == "closed"
        result = replies(output.getvalue())
        assert len(result) == 3 and all(reply["ok"] is True for reply in result)
        assert len(drivers) == 3 and len({id(driver) for driver in drivers}) == 3
        assert all(checks.count(stage) == 3 for stage in ("guard", "binding", "query", "close", "unlock"))
        return
    with pytest.raises(PipeProtocolError):
        await serve(bound, wire, dispatcher, output)
    result = replies(output.getvalue())
    assert len(result) == 2 and result[0]["ok"] is True
    assert result[1]["error"] == dict(code="conflict" if revocation == "binding" else "tombstoned")
    assert len(drivers) == 1 and checks.count("guard") == 2
    assert checks.count("binding") == (2 if revocation == "binding" else 1)
    assert checks.count("query") == checks.count("close") == 1
    assert checks.count("unlock") == (2 if revocation == "binding" else 1)

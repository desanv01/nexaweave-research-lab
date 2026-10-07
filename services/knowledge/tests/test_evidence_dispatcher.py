"""Strict dispatcher source with actual services and synthetic external read data."""
import io
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from nexaweave_knowledge.evidence_dispatcher import EvidenceCommandDispatcher, _encode
from nexaweave_knowledge.evidence_bootstrap import serve_once
from nexaweave_knowledge.evidence_dossier import EvidenceDossierService
from test_evidence_research import Harness


@pytest.fixture
def dispatcher():
    harness = Harness()
    harness.layer()
    first = harness.passage(1, 3)
    harness.edge(evidence=(first,))
    settings = SimpleNamespace(principal="owner", display_graph_id="source", scope=harness.scope)
    dossier = EvidenceDossierService("owner", lambda: None, harness.driver, trusted_scope=harness.scope)
    dossier._research = harness.service
    return EvidenceCommandDispatcher(settings, research=harness.service, dossier=dossier), harness


def envelope(dispatcher, method="research", **changes):
    request = {"version": 1, "request_id": str(uuid4()), "scope": dispatcher.settings.scope.model_dump(mode="json"),
        "method": method, "payload": {"display_graph_ids": ["source", "simulation"], "text": "Alice"}}
    if method == "dossier":
        request["payload"] = {"title": "Evidence", "display_graph_ids": ["source", "simulation"],
            "sections": [{"heading": "First", "query": "Alice"}, {"heading": "Second", "query": "猫"}]}
    request.update(changes)
    return json.dumps(request, ensure_ascii=False).encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["research", "dossier"])
async def test_actual_services_success_and_provenance(dispatcher, method):
    command, harness = dispatcher
    raw = envelope(command, method)
    reply = json.loads(await command.dispatch(raw, principal="owner"))
    assert reply["ok"] is True and reply["request_id"] == json.loads(raw)["request_id"]
    assert harness.closed and harness.active == set()
    if method == "research":
        assert reply["result"]["resolved_citations"] == 1
        assert reply["result"]["historical_semantics"] == "retained_edges_not_bitemporal_reconstruction"
    else:
        assert reply["result"]["model_generated"] is False
        assert reply["result"]["claim_support_status"] == "not_reviewed"
        assert len(reply["result"]["research_trace"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"version": True}, {"method": "ingest"}, {"request_id": "private"},
    {"payload": {"text": "x", "display_graph_ids": ["simulation"]}},
    {"payload": {"text": "x", "display_graph_ids": ["source"], "principal": "foreign"}}])
async def test_bad_request_no_external_read(dispatcher, change):
    command, harness = dispatcher
    reply = json.loads(await command.dispatch(envelope(command, **change), principal="owner"))
    assert reply["error"] == {"code": "invalid_request"} and harness.events == []


@pytest.mark.asyncio
async def test_wrong_principal_scope_and_safe_service_failure(dispatcher):
    command, harness = dispatcher
    assert json.loads(await command.dispatch(envelope(command), principal="foreign"))["error"]["code"] == "invalid_request"
    wrong = command.settings.scope.model_copy(update={"graph_id": uuid4()}).model_dump(mode="json")
    assert json.loads(await command.dispatch(envelope(command, scope=wrong), principal="owner"))["ok"] is False
    assert harness.events == []
    harness.query_failure = RuntimeError("private password database")
    reply = await command.dispatch(envelope(command), principal="owner")
    assert json.loads(reply)["error"] == {"code": "research_unavailable"}
    assert b"private" not in reply and harness.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("frame", [b"", (40000).to_bytes(4, "big"), (2).to_bytes(4, "big") + b"{}x",
    (2).to_bytes(4, "big") + b"{", (9).to_bytes(4, "big") + b'{"x":NaN}'])
async def test_malformed_pipe_no_read(dispatcher, frame):
    command, harness = dispatcher
    output = io.BytesIO()
    await serve_once(command, principal="owner", input_stream=io.BytesIO(frame), output_stream=output)
    raw = output.getvalue()
    assert int.from_bytes(raw[:4], "big") == len(raw[4:])
    assert json.loads(raw[4:])["error"] == {"code": "invalid_request"}
    assert harness.events == []


def test_bounded_encoder_no_clipping():
    with pytest.raises(ValueError):
        _encode({"text": "猫" * 100}, 128)
    assert json.loads(_encode({"text": "猫"}, 100)) == {"text": "猫"}


@pytest.mark.asyncio
async def test_actual_result_cap_returns_error_not_partial_completion(dispatcher, monkeypatch):
    import nexaweave_knowledge.evidence_dispatcher as module
    command, harness = dispatcher
    monkeypatch.setitem(module.RESULT_LIMITS, "research", 128)
    reply = json.loads(await command.dispatch(envelope(command), principal="owner"))
    assert reply["ok"] is False and reply["error"] == {"code": "result_too_large"}
    assert "result" not in reply and harness.closed and harness.active == set()

"""Synthetic one-shot pipe and direct page runtime without model or database calls."""

import asyncio
import io
import json
import sys
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import pytest

from nexaweave_knowledge.commands import KnowledgeCommandDispatcher
from nexaweave_knowledge.contracts import KnowledgeScope, Layer
from nexaweave_knowledge.read_runtime import ReadRuntimeProvider, ReadSettings
from nexaweave_knowledge.stdio import serve_once


def settings(scope):
    return ReadSettings("fixture-owner", "fixture-graph", scope, "127.0.0.1", 15432,
                        "fixture", "fixture", "private", "bolt://127.0.0.1:7687", "neo4j", "private")


def frame(value):
    body = json.dumps(value, separators=(",", ":")).encode()
    return len(body).to_bytes(4, "big") + body


@pytest.mark.asyncio
async def test_one_shot_read_pipe_uses_persisted_binding_and_real_page_adapter():
    one = KnowledgeScope(workspace_id=uuid4(), project_id=uuid4(), graph_id=uuid4(), layer=Layer.source)
    node_id, episode_id, evidence_id = str(uuid4()), str(uuid4()), str(uuid4())
    calls = []

    class Driver:
        closed = False

        async def execute_query(self, query, *, parameters_, routing_):
            assert routing_ == "r" and parameters_["group_id"] == one.group_id
            assert "params" not in parameters_
            calls.append((query, parameters_))
            if "ORDER BY" in query:
                rows = [{"properties": {"uuid": node_id, "group_id": one.group_id,
                                        "name": "人物", "summary": "研究摘要", "role": "analyst"},
                         "row_group": one.group_id, "labels": ["Entity", "Person"]}]
            elif "MENTIONS" in query:
                rows = [{"uuid": episode_id}]
            elif "MATCH (e:Episodic)" in query:
                rows = [{"uuid": episode_id}]
            elif "MATCH (o:MiroFishIngest)" in query:
                rows = [{"uuid": episode_id, "evidence_ids": [evidence_id], "asserted_valid_at": None}]
            else:
                raise AssertionError("unexpected query")
            return rows, None, None

        async def close(self):
            self.closed = True

    driver = Driver()
    runtime = ReadRuntimeProvider(settings(one), connection_factory=lambda: None,
                                  driver_factory=lambda: driver)
    runtime._ledger.read_scope = lambda scope: nullcontext()
    runtime._binding.resolve = lambda principal, display: SimpleNamespace(scope=one)
    dispatcher = KnowledgeCommandDispatcher(runtime, object(), runtime.authorize,
                                            allow_model_calls=False)
    request_id = str(uuid4())
    command = {"version": 1, "request_id": request_id, "method": "page",
               "scope": one.model_dump(mode="json"),
               "payload": {"schema_version": 1, "kind": "node", "limit": 100,
                           "cursor": None, "entity_type": None}}
    output = io.BytesIO()
    assert await serve_once(dispatcher, principal="fixture-owner", input_stream=io.BytesIO(frame(command)),
                            output_stream=output) == "replied"
    raw = output.getvalue()
    response = json.loads(raw[4:])
    assert int.from_bytes(raw[:4], "big") == len(raw) - 4
    assert response["ok"] is True and response["request_id"] == request_id
    fact = response["result"]["facts"][0]
    assert fact["labels"] == ["Entity", "Person"] and fact["summary"] == "研究摘要"
    assert fact["episode_ids"] == [episode_id] and fact["evidence_ids"] == [evidence_id]
    assert fact["attributes"] == {"role": "analyst"} and driver.closed
    assert calls and all("add_episode" not in query for query, _ in calls)

    denied = {**command, "request_id": str(uuid4()), "method": "entity",
              "payload": {"provider_id": node_id}}
    response = json.loads(await dispatcher.dispatch(json.dumps(denied).encode(), principal="fixture-owner"))
    assert response["error"] == {"code": "unauthorized"}


def test_direct_neo4j_driver_uses_explicit_read_credentials_and_timeouts(monkeypatch):
    one = KnowledgeScope(workspace_id=uuid4(), project_id=uuid4(), graph_id=uuid4(), layer=Layer.source)
    calls = []
    fake = SimpleNamespace(AsyncGraphDatabase=SimpleNamespace(
        driver=lambda *args, **kwargs: calls.append((args, kwargs)) or object()))
    monkeypatch.setitem(sys.modules, "neo4j", fake)
    settings(one).driver()
    assert calls == [(("bolt://127.0.0.1:7687",), {
        "auth": ("neo4j", "private"), "connection_timeout": 3,
        "connection_acquisition_timeout": 3,
    })]

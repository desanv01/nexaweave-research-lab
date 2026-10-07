"""Actual HTTPX/OpenAI protocol fixtures, not local inference qualification."""
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError
from openai import APIConnectionError

from graphiti_core.llm_client.config import ModelSize
from graphiti_core.prompts import Message
from nexaweave_knowledge.provider import Endpoint, GraphitiKnowledgeProvider, ProviderConfig
from nexaweave_knowledge.local_transport import LocalPolicyViolation


class Answer(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    answer: str


@contextmanager
def model_server():
    """Bounded local request bodies, canned replies, deterministic cleanup."""
    records = []
    state = {"mode": "ok"}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                self.send_error(413)
                return
            body = json.loads(self.rfile.read(length))
            records.append({"path": self.path, "body": body, "authorization": self.headers.get("Authorization")})
            if state["mode"] == "redirect":
                self.send_response(307)
                self.send_header("Location", state.get("location", "http://must-never-resolve.invalid:80/stolen"))
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path.endswith("/embeddings"):
                reply = {"object": "list", "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}], "model": body["model"], "usage": {"prompt_tokens": 1, "total_tokens": 1}}
            else:
                content = "True" if body.get("logprobs") else json.dumps({"answer": "synthetic fixture"} if state["mode"] == "ok" else {"wrong": 1})
                choice = {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
                if body.get("logprobs"):
                    choice["logprobs"] = {"content": [{"token": "True", "logprob": -0.1, "bytes": [84, 114, 117, 101], "top_logprobs": [{"token": "True", "logprob": -0.1, "bytes": [84, 114, 117, 101]}, {"token": "False", "logprob": -2.0, "bytes": [70, 97, 108, 115, 101]}]}]}
                reply = {"id": "fixture", "object": "chat.completion", "created": 1, "model": body["model"], "choices": [choice], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
            data = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    # Bound per-connection body reads even if a failing client leaves a socket open.
    original_get = server.get_request
    def get_request():
        connection, address = original_get()
        connection.settimeout(3)
        return connection, address
    server.get_request = get_request
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield server, records, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive(), "synthetic HTTP server did not stop"


def local_config(port, *, recipe="hybrid_cross_encoder", budget=4, neo4j_uri="bolt://127.0.0.1:17687", password="fixture-password"):
    def endpoint(model):
        return Endpoint(base_url=f"http://127.0.0.1:{port}/v1", model=model, api_key=SecretStr("explicit-local-dummy"))
    return ProviderConfig(operating_profile="local_only", neo4j_uri=neo4j_uri, neo4j_user="neo4j",
                          neo4j_password=SecretStr(password), llm=endpoint("fixture-generation"),
                          embedding=endpoint("fixture-embedding"), embedding_dimension=3,
                          reranker=endpoint("fixture-reranker") if recipe == "hybrid_cross_encoder" else None,
                          search_recipe=recipe, call_timeout_seconds=3.0, total_llm_call_budget=budget)


def messages():
    return [Message(role="system", content="Synthetic fixture only."), Message(role="user", content="Return the fixture answer.")]


@pytest.fixture
def captured_graph(monkeypatch):
    import nexaweave_knowledge.provider as module
    class Driver:
        def __init__(self, *args):
            pass
        async def close(self):
            pass
    class Graph:
        def __init__(self, **kwargs):
            self.clients = SimpleNamespace(llm_client=kwargs["llm_client"], embedder=kwargs["embedder"], cross_encoder=kwargs["cross_encoder"])
            self.driver = kwargs["graph_driver"]
        async def build_indices_and_constraints(self):
            pass
        async def close(self):
            await self.driver.close()
    monkeypatch.setattr(module, "Neo4jDriver", Driver)
    monkeypatch.setattr(module, "CommunityGraphiti", Graph)


@pytest.mark.parametrize("recipe", ["hybrid_rrf", "hybrid_cross_encoder"], ids=["rrf-local-fallback", "explicit-reranker"])
async def test_actual_sdk_routes_budget_and_proxy_denial(captured_graph, monkeypatch, recipe):
    with model_server() as (server, records, state), model_server() as (proxy, proxy_records, _):
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.setenv(name, f"http://127.0.0.1:{proxy.server_port}")
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.setenv(name, "")
        monkeypatch.setenv("OPENAI_API_KEY", "cloud-key-trap")
        monkeypatch.setenv("OPENAI_BASE_URL", "http://must-never-resolve.invalid/v1")
        subject = GraphitiKnowledgeProvider(local_config(server.server_port, recipe=recipe, budget=1))
        try:
            await subject.initialize()
            clients = subject.graphiti.clients
            assert clients.llm_client.small_model == "fixture-generation"
            assert await clients.llm_client.generate_response(messages(), Answer, model_size=ModelSize.small) == {"answer": "synthetic fixture"}
            assert await clients.embedder.create("synthetic") == [0.1, 0.2, 0.3]
            ranks = await clients.cross_encoder.rank("synthetic", ["fixture passage"])
            assert ranks[0][0] == "fixture passage"
            with pytest.raises(RuntimeError, match="budget exhausted"):
                await clients.llm_client.generate_response(messages(), Answer)
            assert [r["body"]["model"] for r in records] == ["fixture-generation", "fixture-embedding", "fixture-generation" if recipe == "hybrid_rrf" else "fixture-reranker"]
            assert all(r["authorization"] == "Bearer explicit-local-dummy" for r in records)
            assert proxy_records == []
            owned = list(subject._owned_clients)
            http_owned = list(subject._owned_http_clients)
        finally:
            await subject.close()
        assert all(c.is_closed() for c in owned)
        assert all(c.is_closed for c in http_owned)
        await subject.close()


@pytest.mark.parametrize("mode", ["malformed", "redirect", "local-redirect"], ids=["schema-no-retry", "redirect-no-dns", "local-redirect-denied"])
async def test_actual_sdk_failed_reply_has_one_attempt(captured_graph, monkeypatch, mode):
    import socket
    original = socket.getaddrinfo
    denied = []
    def guard(host, *args, **kwargs):
        if host not in {"127.0.0.1", b"127.0.0.1", "::1", b"::1"}:
            denied.append(host)
            raise AssertionError("external DNS attempt")
        return original(host, *args, **kwargs)
    monkeypatch.setattr(socket, "getaddrinfo", guard)
    with model_server() as (server, records, state):
        state["mode"] = "redirect" if mode == "local-redirect" else mode
        if mode == "local-redirect":
            state["location"] = f"http://127.0.0.1:{server.server_port}/v1/redirected"
        subject = GraphitiKnowledgeProvider(local_config(server.server_port))
        try:
            await subject.initialize()
            with pytest.raises(ValidationError if mode == "malformed" else APIConnectionError) as caught:
                await subject.graphiti.clients.llm_client.generate_response(messages(), Answer)
            if mode != "malformed":
                assert isinstance(caught.value.__cause__, LocalPolicyViolation)
                assert str(caught.value.__cause__) == "local knowledge redirect denied"
            assert len(records) == 1
            assert denied == []
            assert subject.graphiti.clients.llm_client._remaining == 3
        finally:
            await subject.close()

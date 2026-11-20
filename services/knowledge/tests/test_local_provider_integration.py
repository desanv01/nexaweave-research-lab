"""Fresh-process actual SDK/HTTP/Neo4j synthetic-model observations only."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from urllib.parse import urlsplit

import pytest


pytestmark = pytest.mark.neo4j


def test_fresh_local_provider_actual_neo4j():
    if os.getenv("KNOWLEDGE_INTEGRATION") != "1":
        pytest.skip("Set KNOWLEDGE_INTEGRATION=1 for retained disposable Neo4j")
    password = os.environ.get("KNOWLEDGE_TEST_PASSWORD")
    if not password or password.lower() in {"neo4j", "password", "changeme", "test"}:
        pytest.fail("Non-default KNOWLEDGE_TEST_PASSWORD required")
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child"],
                            capture_output=True, text=True, timeout=60, env=os.environ.copy())
    assert result.returncode == 0, "fresh local provider failed: " + result.stdout + result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["synthetic_model"] is True
    assert report["indices_initialized"] is True
    assert report["generation"] == {"answer": "synthetic fixture"}
    assert [(request["model"], request["path"]) for request in report["model_requests"]] == [
        ("fixture-generation", "/v1/chat/completions"),
        ("fixture-embedding", "/v1/embeddings"),
        ("fixture-reranker", "/v1/chat/completions"),
    ]
    assert report["denied"] == []
    assert report["http_closed"] and report["driver_closed"]
    model_ports = {request["port"] for request in report["model_requests"]}
    assert len(model_ports) == 3
    assert report["neo4j_port"] not in model_ports
    expected_ports = model_ports | {report["neo4j_port"]}
    assert expected_ports == set(report["connected_ports"])


async def child():
    uri = os.getenv("KNOWLEDGE_TEST_NEO4J_URI", "bolt://127.0.0.1:17687")
    parsed = urlsplit(uri)
    # Policy validates the complete URI separately; instrumentation adds a deny
    # layer to observe every attempted outbound address, including SDK imports.
    allowed_ports = {parsed.port}
    attempts, denied = [], []
    real_dns = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    proactor_type = None
    real_proactor_connect = None
    def allow(host, port, kind):
        if isinstance(host, bytes):
            host = host.decode("ascii")
        attempts.append((kind, host, port))
        if host not in {"127.0.0.1", "::1"} or port not in allowed_ports:
            denied.append((kind, host, port))
            raise AssertionError("outbound attempt outside local fixture allowlist")
    def dns(host, port, *args, **kwargs):
        allow(host, port, "getaddrinfo")
        return real_dns(host, port, *args, **kwargs)
    def connect(instance, address):
        if not isinstance(address, tuple) or len(address) < 2:
            raise AssertionError("non-INET outbound attempt")
        allow(address[0], address[1], "connect")
        return real_connect(instance, address)
    def connect_ex(instance, address):
        if not isinstance(address, tuple) or len(address) < 2:
            raise AssertionError("non-INET outbound attempt")
        allow(address[0], address[1], "connect_ex")
        return real_connect_ex(instance, address)
    socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex = dns, connect, connect_ex
    try:
        if sys.platform == "win32":
            from asyncio.windows_events import IocpProactor
            proactor_type = IocpProactor
            real_proactor_connect = IocpProactor.connect
            def proactor_connect(instance, connection, address):
                if not isinstance(address, tuple) or len(address) < 2:
                    raise AssertionError("non-INET outbound attempt")
                allow(address[0], address[1], "proactor_connect")
                return real_proactor_connect(instance, connection, address)
            IocpProactor.connect = proactor_connect
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            os.environ[name] = "http://proxy-must-never-resolve.invalid:80"
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = ""
        os.environ["OPENAI_API_KEY"] = "unused-cloud-trap"
        os.environ["OPENAI_BASE_URL"] = "https://cloud-must-never-resolve.invalid/v1"
        from test_local_provider_http import Answer, messages, model_server
        from nexaweave_knowledge.provider import GraphitiKnowledgeProvider
        from nexaweave_knowledge.local_transport import local_endpoint
        local_endpoint(uri, bolt=True)
        with (
            model_server() as (generation_server, generation_records, _),
            model_server() as (embedding_server, embedding_records, _),
            model_server() as (reranker_server, reranker_records, _),
        ):
            routes = (
                ("LLM", generation_server, generation_records, "fixture-generation"),
                ("EMBEDDING", embedding_server, embedding_records, "fixture-embedding"),
                ("RERANKER", reranker_server, reranker_records, "fixture-reranker"),
            )
            os.environ.update({
                "KNOWLEDGE_OPERATING_PROFILE": "local_only",
                "KNOWLEDGE_NEO4J_URI": uri,
                "KNOWLEDGE_NEO4J_USER": "neo4j",
                "KNOWLEDGE_NEO4J_PASSWORD": os.environ["KNOWLEDGE_TEST_PASSWORD"],
                "KNOWLEDGE_SEARCH_RECIPE": "hybrid_cross_encoder",
                "KNOWLEDGE_EMBEDDING_DIMENSION": "3",
                "KNOWLEDGE_STRUCTURED_OUTPUT_MODE": "json_object",
                "KNOWLEDGE_CALL_TIMEOUT_SECONDS": "3",
                "KNOWLEDGE_MAX_TOKENS": "4096",
                "KNOWLEDGE_MAX_COROUTINES": "4",
                "KNOWLEDGE_TOTAL_LLM_CALL_BUDGET": "4",
            })
            for role, server, _, model in routes:
                allowed_ports.add(server.server_port)
                os.environ[f"KNOWLEDGE_{role}_BASE_URL"] = f"http://127.0.0.1:{server.server_port}/v1"
                os.environ[f"KNOWLEDGE_{role}_MODEL"] = model
                os.environ[f"KNOWLEDGE_{role}_API_KEY"] = f"explicit-local-{role.lower()}"
            subject = GraphitiKnowledgeProvider()
            try:
                await subject.initialize()
                graph = subject.graphiti
                driver = graph.driver
                rows, _, _ = await driver.execute_query("RETURN 1 AS local_fixture", routing_="r")
                assert rows[0]["local_fixture"] == 1
                generation = await graph.clients.llm_client.generate_response(messages(), Answer)
                assert await graph.clients.embedder.create("synthetic") == [0.1, 0.2, 0.3]
                assert (await graph.clients.cross_encoder.rank("fixture", ["synthetic passage"]))[0][0] == "synthetic passage"
                owned_http = list(subject._owned_http_clients)
                owned_sdk = list(subject._owned_clients)
            finally:
                await subject.close()
            assert len(owned_http) == len(owned_sdk) == len(routes)
            assert all(c.is_closed for c in owned_http)
            assert all(c.is_closed() for c in owned_sdk)
            assert driver.client._closed  # actual pinned Neo4j driver's closure state
            assert denied == []
            model_requests = []
            for role, server, records, _ in routes:
                assert len(records) == 1, role
                request = records[0]
                assert request["authorization"] == f"Bearer explicit-local-{role.lower()}"
                model_requests.append({
                    "port": server.server_port,
                    "path": request["path"],
                    "model": request["body"]["model"],
                })
            report = dict(synthetic_model=True, indices_initialized=True, generation=generation,
                          model_requests=model_requests, denied=denied,
                          connected_ports=sorted({port for kind, host, port in attempts if kind in {"connect", "connect_ex", "proactor_connect"}}),
                          neo4j_port=parsed.port,
                          http_closed=True, driver_closed=True)
        print(json.dumps(report, sort_keys=True))
    finally:
        socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex = real_dns, real_connect, real_connect_ex
        if proactor_type is not None and real_proactor_connect is not None:
            proactor_type.connect = real_proactor_connect


if __name__ == "__main__":
    if sys.argv[1:] != ["--child"]:
        raise SystemExit("explicit --child required")
    asyncio.run(child())

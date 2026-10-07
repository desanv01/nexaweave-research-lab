"""Authored policy regressions; Main owns execution."""
import os
import httpx
import pytest
from pydantic import SecretStr, ValidationError

from nexaweave_knowledge.local_transport import LocalPolicyViolation, LocalTransport, local_endpoint
from nexaweave_knowledge.provider import Endpoint, GraphitiKnowledgeProvider, ProviderConfig


def config(**updates):
    endpoint = Endpoint(base_url="http://127.0.0.1:18080/v1", model="fixture-generation", api_key=SecretStr("local-dummy"))
    values = dict(operating_profile="local_only", neo4j_uri="bolt://127.0.0.1:17687",
                  neo4j_user="neo4j", neo4j_password=SecretStr("fixture-password"),
                  llm=endpoint, embedding=endpoint.model_copy(update={"model": "fixture-embedding"}),
                  embedding_dimension=3, call_timeout_seconds=2.0)
    values.update(updates)
    return ProviderConfig(**values)


BAD_URLS = [
    "https://api.deepseek.com:443/v1", "http://localhost:80/v1",
    "http://127.1:80/v1", "http://2130706433:80/v1", "http://0177.0.0.1:80/v1",
    "http://127.0.0.2:80/v1", "http://0.0.0.0:80/v1", "http://[::ffff:127.0.0.1]:80/v1",
    "http://127.0.0.1/v1", "http://127.0.0.1:0/v1", "http://127.0.0.1:65536/v1",
    "http://127.0.0.1:abc/v1", "http://local-dummy@127.0.0.1:80/v1",
    "http://127.0.0.1:80/v1?key=local-dummy", "http://127.0.0.1:80/v1#fragment",
    "http://127.0.0.1:80/v1?", "http://127.0.0.1:80/v1#",
    "http://127.0.0.1:80/v1/../other", "http://127.0.0.1:80/v1//other",
    "http://127.0.0.1:80/v1/%2e%2e", "http://127.0.0.1:80/v1\\other",
    "http://127.0.0.1:80 /v1", "\nhttp://127.0.0.1:80/v1",
    "ftp://127.0.0.1:80/v1", "http://127%2e0.0.1:80/v1",
    "http://[0:0:0:0:0:0:0:1]:80/v1", "http://127.0.0.1:080/v1",
]


@pytest.mark.parametrize("url", BAD_URLS, ids=[f"denied-{i}" for i in range(len(BAD_URLS))])
def test_strict_endpoint_admission(url):
    with pytest.raises(LocalPolicyViolation, match="^invalid local knowledge endpoint$"):
        local_endpoint(url)
    with pytest.raises(ValidationError) as caught:
        config(llm=Endpoint(base_url=url, model="fixture", api_key=SecretStr("local-dummy")))
    assert url not in str(caught.value)
    assert "local-dummy" not in str(caught.value)


@pytest.mark.parametrize("url", ["http://127.0.0.1:80", "https://[::1]:443/v1", "http://127.0.0.1:1234/local/api/"], ids=["ipv4", "ipv6", "prefix"])
def test_admitted_endpoints(url):
    assert local_endpoint(url).host in {"127.0.0.1", "::1"}


@pytest.mark.parametrize("uri", ["neo4j://127.0.0.1:17687", "bolt+s://127.0.0.1:17687", "bolt://localhost:17687", "bolt://127.0.0.1:17687/", "bolt://127.0.0.1:17687?x=1"], ids=["routing", "tls", "dns", "path", "query"])
def test_neo4j_admission(uri):
    with pytest.raises(ValidationError):
        config(neo4j_uri=uri)


def env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("KNOWLEDGE_"):
            monkeypatch.delenv(key)
    for key, value in {"NEO4J_URI": "bolt://127.0.0.1:17687", "NEO4J_USER": "neo4j", "NEO4J_PASSWORD": "fixture-password", "LLM_API_KEY": "local-dummy", "EMBEDDING_BASE_URL": "http://127.0.0.1:18080/v1", "EMBEDDING_MODEL": "fixture-embedding", "EMBEDDING_API_KEY": "local-dummy", "EMBEDDING_DIMENSION": "3"}.items():
        monkeypatch.setenv("KNOWLEDGE_" + key, value)
    monkeypatch.setenv("OPENAI_API_KEY", "cloud-trap")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://cloud-trap.invalid/v1")


def test_hybrid_defaults_and_explicit_local_requirements(monkeypatch):
    env(monkeypatch)
    hybrid = ProviderConfig.from_env()
    assert hybrid.operating_profile == "hybrid"
    assert hybrid.llm.base_url == "https://api.deepseek.com"
    assert hybrid.llm.model == "deepseek-flash"
    monkeypatch.setenv("KNOWLEDGE_OPERATING_PROFILE", "local_only")
    with pytest.raises(ValueError, match="KNOWLEDGE_LLM_BASE_URL is required"):
        ProviderConfig.from_env()
    monkeypatch.setenv("KNOWLEDGE_LLM_BASE_URL", "http://127.0.0.1:18080/v1")
    with pytest.raises(ValueError, match="KNOWLEDGE_LLM_MODEL is required"):
        ProviderConfig.from_env()
    monkeypatch.setenv("KNOWLEDGE_LLM_MODEL", "fixture-generation")
    assert ProviderConfig.from_env().operating_profile == "local_only"
    monkeypatch.setenv("KNOWLEDGE_SEARCH_RECIPE", "hybrid_cross_encoder")
    with pytest.raises(ValueError, match="KNOWLEDGE_RERANKER_BASE_URL is required"):
        ProviderConfig.from_env()
    monkeypatch.setenv("KNOWLEDGE_OPERATING_PROFILE", "local-only-typo")
    with pytest.raises(ValueError, match="unsupported knowledge operating profile"):
        ProviderConfig.from_env()


def test_local_config_frozen_and_injection_denied():
    subject = config()
    with pytest.raises(ValidationError):
        subject.operating_profile = "hybrid"
    with pytest.raises(LocalPolicyViolation):
        GraphitiKnowledgeProvider(subject, graphiti=object())
    with pytest.raises(ValidationError):
        config(search_recipe="hybrid_cross_encoder")


@pytest.mark.parametrize("url", ["http://localhost:18080/v1/chat/completions", "http://127.0.0.1:18081/v1/chat/completions", "https://127.0.0.1:18080/v1/chat/completions", "http://127.0.0.1:18080/v10/chat", "http://127.0.0.1:18080/v1/../chat", "http://127.0.0.1:18080/v1/%2fchat", "http://127.0.0.1:18080/v1/chat?x=1"], ids=["dns", "port", "scheme", "segment", "normalized-escape", "encoded", "query"])
async def test_request_denied_before_transport(url, monkeypatch):
    transport = LocalTransport("http://127.0.0.1:18080/v1")
    async def forbidden(request):
        pytest.fail("denied request reached real transport")
    monkeypatch.setattr(transport._transport, "handle_async_request", forbidden)
    try:
        with pytest.raises(LocalPolicyViolation, match="request denied"):
            await transport.handle_async_request(httpx.Request("POST", url, json={"secret": "synthetic"}))
    finally:
        await transport.aclose()


async def test_normalized_default_port_keeps_configured_origin(monkeypatch):
    transport = LocalTransport("http://127.0.0.1:80/v1")
    requests = []
    async def accepted(request):
        requests.append(request)
        return httpx.Response(200, content=b"{}")
    monkeypatch.setattr(transport._transport, "handle_async_request", accepted)
    try:
        await transport.handle_async_request(httpx.Request("POST", "http://127.0.0.1:80/v1/chat/completions"))
        assert len(requests) == 1
    finally:
        await transport.aclose()


async def test_initialization_failure_closes_all_and_preserves_error(monkeypatch):
    import nexaweave_knowledge.provider as module
    closed = []
    class Driver:
        def __init__(self, *args):
            pass
        async def close(self):
            closed.append("driver")
    def fail_graph(**kwargs):
        raise RuntimeError("original-construction-error")
    monkeypatch.setattr(module, "Neo4jDriver", Driver)
    monkeypatch.setattr(module, "CommunityGraphiti", fail_graph)
    subject = GraphitiKnowledgeProvider(config())
    with pytest.raises(RuntimeError, match="original-construction-error"):
        await subject.initialize()
    assert closed == ["driver"]
    assert subject._owned_clients == subject._owned_http_clients == []
    await subject.close()
    assert closed == ["driver"]


async def test_cleanup_continues_after_failure_and_is_idempotent():
    closed = []
    class Resource:
        def __init__(self, name, fail=False):
            self.name, self.fail = name, fail
        async def close(self):
            closed.append(self.name)
            if self.fail:
                raise RuntimeError("close-failure")
        aclose = close
    subject = GraphitiKnowledgeProvider(config())
    subject.graphiti = Resource("graph", True)
    subject._owned_driver = Resource("driver")
    subject._owned_clients = [Resource("one", True), Resource("two")]
    subject._owned_http_clients = [Resource("http")]
    with pytest.raises(RuntimeError, match="close-failure"):
        await subject.close()
    assert closed == ["graph", "driver", "one", "two", "http"]
    await subject.close()
    assert len(closed) == 5


@pytest.mark.parametrize("field,value", [("embedding_dimension", 0), ("total_llm_call_budget", 0), ("call_timeout_seconds", 0.0), ("max_coroutines", 33), ("max_tokens", 10)], ids=["dimension", "budget", "deadline", "concurrency", "tokens"])
def test_local_retains_existing_bounds(field, value):
    with pytest.raises(ValidationError):
        config(**{field: value})


@pytest.mark.parametrize("name", ["KNOWLEDGE_LLM_API_KEY", "KNOWLEDGE_EMBEDDING_API_KEY"], ids=["generation-key", "embedding-key"])
def test_cloud_key_cannot_supply_missing_explicit_key(monkeypatch, name):
    env(monkeypatch)
    monkeypatch.setenv("KNOWLEDGE_OPERATING_PROFILE", "local_only")
    monkeypatch.setenv("KNOWLEDGE_LLM_BASE_URL", "http://127.0.0.1:18080/v1")
    monkeypatch.setenv("KNOWLEDGE_LLM_MODEL", "fixture-generation")
    monkeypatch.delenv(name)
    with pytest.raises(ValueError, match=name + " is required"):
        ProviderConfig.from_env()


async def test_client_construction_failure_closes_created_http(monkeypatch):
    import nexaweave_knowledge.provider as module
    original = module.httpx.AsyncClient
    created = []
    class CaptureHTTP(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)
    def fail_sdk(**kwargs):
        raise RuntimeError("original-sdk-error")
    monkeypatch.setattr(module.httpx, "AsyncClient", CaptureHTTP)
    monkeypatch.setattr(module, "AsyncOpenAI", fail_sdk)
    subject = GraphitiKnowledgeProvider(config())
    with pytest.raises(RuntimeError, match="original-sdk-error"):
        await subject.initialize()
    assert len(created) == 1
    assert created[0].is_closed
    assert not subject._owned_transports


async def test_index_failure_cleanup_preserves_original(monkeypatch):
    import nexaweave_knowledge.provider as module
    closed, http_created = [], []
    original_http = module.httpx.AsyncClient
    class CaptureHTTP(original_http):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            http_created.append(self)
    class Driver:
        def __init__(self, *args):
            pass
        async def close(self):
            closed.append("driver")
    class Graph:
        def __init__(self, **kwargs):
            self.driver = kwargs["graph_driver"]
        async def build_indices_and_constraints(self):
            raise RuntimeError("original-index-error")
        async def close(self):
            closed.append("graph")
            raise RuntimeError("cleanup-error")
    monkeypatch.setattr(module, "Neo4jDriver", Driver)
    monkeypatch.setattr(module, "CommunityGraphiti", Graph)
    monkeypatch.setattr(module.httpx, "AsyncClient", CaptureHTTP)
    subject = GraphitiKnowledgeProvider(config())
    with pytest.raises(RuntimeError, match="original-index-error"):
        await subject.initialize()
    assert closed == ["graph", "driver"]
    assert len(http_created) == 3 and all(c.is_closed for c in http_created)

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mirofish_knowledge.contracts import FactResult, KnowledgeScope, Layer, SourceEnvelope, OntologySpec, SearchQuery
from mirofish_knowledge.provider import ProviderConfig, Endpoint, UnsupportedCapability, GraphitiKnowledgeProvider, BoundedGenericClient, OperationConflict, ReconciliationRequired, _fact, _request_fingerprint
from graphiti_core.llm_client import LLMConfig
from graphiti_core.prompts.models import Message
from pydantic import BaseModel
from types import SimpleNamespace


def scope(**changes):
    data = dict(workspace_id=uuid4(), project_id=uuid4(), graph_id=uuid4(), layer=Layer.source)
    data.update(changes)
    return KnowledgeScope(**data)


def test_scope_partition_includes_every_field():
    one = scope()
    assert one.group_id == KnowledgeScope(**one.model_dump()).group_id
    assert one.group_id != one.model_copy(update={"project_id": uuid4()}).group_id
    assert one.group_id != one.model_copy(update={"branch_id": uuid4()}).group_id
    assert one.group_id != one.model_copy(update={"layer": Layer.analysis}).group_id
    assert one.episode_uuid(uuid4()) != one.episode_uuid(uuid4())
    with pytest.raises(ValidationError):
        KnowledgeScope(**one.model_dump(), group_id="attacker")


def test_fact_metadata_defaults_roundtrip_bounds_and_timestamps():
    one = scope()
    minimal = {"provider_id": str(uuid4()), "scope": one.model_dump(mode="json"), "kind": "node"}
    old_wire = FactResult.model_validate_json(__import__("json").dumps(minimal))
    assert old_wire.labels == () and old_wire.summary is None and old_wire.expired_at is None
    expiry = datetime.now(timezone.utc)
    populated = FactResult(**minimal | {"scope": one, "labels": ("Entity", "人物"),
                                      "summary": "研究摘要", "expired_at": expiry})
    assert FactResult.model_validate_json(populated.model_dump_json()) == populated
    assert len(FactResult(**minimal | {"scope": one, "labels": ("x" * 128,),
                                      "summary": "文" * 32768}).summary) == 32768
    assert len(FactResult(**minimal | {"scope": one, "labels": tuple(f"L{i}" for i in range(64))}).labels) == 64
    for labels in (("",), ("x" * 129,), ("bad\nlabel",), ("bad\x7flabel",),
                   ("Person", "Person"), tuple(f"L{i}" for i in range(65)), (123,), "Person"):
        with pytest.raises(ValidationError):
            FactResult(**minimal | {"scope": one, "labels": labels})
    for summary in ("x" * 32769, 123):
        with pytest.raises(ValidationError):
            FactResult(**minimal | {"scope": one, "summary": summary})
    with pytest.raises(ValidationError):
        FactResult(**minimal | {"scope": one, "expired_at": datetime.now()})


def test_graphiti_fact_metadata_normalization_keeps_expiry_distinct():
    one = scope()
    valid = datetime(2024, 1, 1, tzinfo=timezone.utc)
    invalid = datetime(2024, 2, 1, tzinfo=timezone.utc)
    expired = datetime(2024, 3, 1, tzinfo=timezone.utc)

    class NeoDate:
        def __init__(self, value):
            self.value = value

        def to_native(self):
            return self.value

    node = SimpleNamespace(uuid=str(uuid4()), group_id=one.group_id, name="Mira",
                           labels=["Entity", "Person"], summary="研究摘要",
                           attributes={"role": "analyst", "summary": "shadow", "labels": ["Shadow"],
                                       "name_embedding": [0.1]})
    node_fact = _fact(one, "node", node)
    assert node_fact.labels == ("Entity", "Person") and node_fact.summary == "研究摘要"
    assert node_fact.attributes == {"role": "analyst"}
    edge = SimpleNamespace(uuid=str(uuid4()), group_id=one.group_id, name="WORKS_FOR",
                           source_node_uuid=str(uuid4()), target_node_uuid=str(uuid4()),
                           valid_at=NeoDate(valid), invalid_at=NeoDate(invalid), expired_at=NeoDate(expired),
                           attributes={"confidence": 0.9, "expired_at": "shadow", "fact_embedding": [0.1]})
    edge_fact = _fact(one, "edge", edge)
    assert (edge_fact.valid_at, edge_fact.invalid_at, edge_fact.expired_at) == (valid, invalid, expired)
    assert edge_fact.attributes == {"confidence": 0.9}


@pytest.mark.parametrize("labels", ["Person", {"Person": True}, None, False, ["Person"] * 65])
def test_graphiti_object_rejects_malformed_label_containers(labels):
    one = scope()
    value = SimpleNamespace(uuid=str(uuid4()), group_id=one.group_id, labels=labels)
    with pytest.raises(ValueError):
        _fact(one, "node", value)
    del value.labels
    assert _fact(one, "node", value).labels == ()


def test_source_bounds_hash_and_unknown_time():
    content = "Mira works for Harbor Labs."
    data = dict(source_revision=uuid4(), source_sha256=hashlib.sha256(content.encode()).hexdigest(), ontology_revision=uuid4(), operation_id=uuid4(), source_kind="document", content=content, source_name="Synthetic note", recorded_at=datetime.now(timezone.utc))
    assert SourceEnvelope(**data).asserted_valid_at is None
    with pytest.raises(ValidationError):
        SourceEnvelope(**{**data, "recorded_at": datetime.now()})
    with pytest.raises(ValidationError):
        SourceEnvelope(**{**data, "source_sha256": "0" * 64})
    with pytest.raises(ValidationError):
        SourceEnvelope(**{**data, "content": "x" * 32769})


def test_ontology_pair_and_attribute_guards():
    data = dict(revision=uuid4(), entity_types=({"name": "Person", "description": "A named person", "attributes": ({"name": "role", "type": "text", "description": "Job title"},)}, {"name": "Organization", "description": "A named organization"}), edge_types=({"name": "WORKS_FOR", "description": "Employment", "source_targets": ({"source": "Person", "target": "Organization"},)},))
    spec = OntologySpec(**data)
    entities, edges, pairs = spec.graphiti_types()
    assert pairs == {("Person", "Organization"): ["WORKS_FOR"]}
    assert "role" in entities["Person"].model_fields
    assert "WORKS_FOR" in edges
    with pytest.raises(ValidationError):
        OntologySpec(**{**data, "edge_types": ({"name": "WORKS_FOR", "description": "Employment", "source_targets": ({"source": "Person", "target": "Missing"},)},)})
    with pytest.raises(ValidationError):
        OntologySpec(**{**data, "entity_types": ({"name": "Person", "description": "Person", "attributes": ({"name": "uuid", "type": "text", "description": "Unsafe"},)},)})


def test_config_without_cloud_fallback(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("KNOWLEDGE_"):
            monkeypatch.delenv(key)
    with pytest.raises(ValueError, match="required"):
        ProviderConfig.from_env()


def test_query_bounds():
    with pytest.raises(ValidationError):
        SearchQuery(text="x" * 2001)
    with pytest.raises(ValidationError):
        SearchQuery(text="query", top_k=101)


def test_ontology_and_evidence_size_limits():
    attribute = {"name": "role", "type": "text", "description": "A job title"}
    for reserved in ("model_dump", "model_fields", "model_config"):
        with pytest.raises(ValidationError):
            OntologySpec(revision=uuid4(), entity_types=({"name": "Person", "description": "A person", "attributes": ({**attribute, "name": reserved},)},), edge_types=())
    with pytest.raises(ValidationError):
        OntologySpec(revision=uuid4(), entity_types=({"name": "Person", "description": "A person", "attributes": tuple({**attribute, "name": f"field_{i}"} for i in range(21))},), edge_types=())
    with pytest.raises(ValidationError):
        OntologySpec(revision=uuid4(), entity_types=({"name": "Person", "description": "A person"},), edge_types=({"name": "KNOWS", "description": "Relation", "source_targets": tuple({"source": "Person", "target": "Person"} for _ in range(101))},))
    content = "Synthetic source"
    with pytest.raises(ValidationError):
        SourceEnvelope(source_revision=uuid4(), source_sha256=hashlib.sha256(content.encode()).hexdigest(), ontology_revision=uuid4(), operation_id=uuid4(), source_kind="document", content=content, source_name="note", recorded_at=datetime.now(timezone.utc), evidence_ids=tuple(uuid4() for _ in range(101)))


def test_environment_bounds_secrets_and_fingerprint(monkeypatch):
    env = {
        "KNOWLEDGE_NEO4J_URI": "bolt://127.0.0.1:17687",
        "KNOWLEDGE_NEO4J_USER": "neo4j",
        "KNOWLEDGE_NEO4J_PASSWORD": "private-db-secret",
        "KNOWLEDGE_LLM_API_KEY": "private-llm-secret",
        "KNOWLEDGE_EMBEDDING_BASE_URL": "http://localhost/embeddings",
        "KNOWLEDGE_EMBEDDING_MODEL": "fixture-embedding",
        "KNOWLEDGE_EMBEDDING_API_KEY": "private-embedding-secret",
        "KNOWLEDGE_EMBEDDING_DIMENSION": "1024",
        "KNOWLEDGE_CALL_TIMEOUT_SECONDS": "11",
        "KNOWLEDGE_MAX_TOKENS": "512",
        "KNOWLEDGE_MAX_COROUTINES": "2",
        "KNOWLEDGE_TOTAL_LLM_CALL_BUDGET": "7",
        "KNOWLEDGE_STRUCTURED_OUTPUT_MODE": "json_schema",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config = ProviderConfig.from_env()
    assert (config.call_timeout_seconds, config.max_tokens, config.max_coroutines, config.total_llm_call_budget, config.structured_output_mode) == (11, 512, 2, 7, "json_schema")
    for secret in ("private-db-secret", "private-llm-secret", "private-embedding-secret"):
        assert secret not in repr(config)
    one = scope()
    content = "Mira works for Harbor Labs."
    ontology = OntologySpec(revision=uuid4(), entity_types=({"name": "Person", "description": "A person"},), edge_types=())
    source = SourceEnvelope(source_revision=uuid4(), source_sha256=hashlib.sha256(content.encode()).hexdigest(), ontology_revision=ontology.revision, operation_id=uuid4(), source_kind="document", content=content, source_name="note", recorded_at=datetime.now(timezone.utc))
    fingerprint = _request_fingerprint(one, source, ontology)
    for changed in (
        source.model_copy(update={"source_revision": uuid4()}),
        source.model_copy(update={"evidence_ids": (uuid4(),)}),
        source.model_copy(update={"asserted_valid_at": source.recorded_at + timedelta(seconds=1)}),
    ):
        assert _request_fingerprint(one, changed, ontology) != fingerprint
    changed_ontology = OntologySpec(revision=ontology.revision, entity_types=({"name": "Person", "description": "Different"},), edge_types=())
    assert _request_fingerprint(one, source, changed_ontology) != fingerprint


@pytest.mark.asyncio
async def test_search_uses_configured_cross_encoder_recipe():
    class FakeGraph:
        def __init__(self):
            self.recipe = None

        async def search_(self, text, *, config, group_ids):
            self.recipe = config
            return SimpleNamespace(edges=[], nodes=[], episodes=[], edge_reranker_scores=[], node_reranker_scores=[], episode_reranker_scores=[])

    graph = FakeGraph()
    provider = GraphitiKnowledgeProvider(config=ProviderConfig(neo4j_uri="bolt://localhost", neo4j_user="neo4j", neo4j_password="secret", llm=Endpoint(base_url="http://localhost", model="fake", api_key="secret"), embedding=Endpoint(base_url="http://localhost", model="fake", api_key="secret"), embedding_dimension=1024, reranker=Endpoint(base_url="http://localhost", model="fake", api_key="secret"), search_recipe="hybrid_cross_encoder"), graphiti=graph)
    await provider.search(scope(), SearchQuery(text="Mira", top_k=7))
    assert graph.recipe.limit == 7
    assert graph.recipe.edge_config.reranker.value == "cross_encoder"


@pytest.mark.asyncio
async def test_initialize_persists_environment_config(monkeypatch):
    import mirofish_knowledge.provider as module

    config = ProviderConfig(neo4j_uri="bolt://localhost", neo4j_user="neo4j", neo4j_password="secret", llm=Endpoint(base_url="http://localhost", model="fake", api_key="secret"), embedding=Endpoint(base_url="http://localhost", model="fake", api_key="secret"), embedding_dimension=1024)

    class FakeGraph:
        def __init__(self, **kwargs):
            pass

        async def build_indices_and_constraints(self):
            pass

        async def close(self):
            pass

    monkeypatch.setattr(ProviderConfig, "from_env", classmethod(lambda cls: config))
    monkeypatch.setattr(module, "CommunityGraphiti", FakeGraph)
    provider = GraphitiKnowledgeProvider()
    await provider.initialize()
    assert provider.config is config
    await provider.close()


@pytest.mark.asyncio
async def test_same_operation_changed_metadata_conflicts(monkeypatch):
    one = scope()
    content = "Mira works for Harbor Labs."
    ontology = OntologySpec(revision=uuid4(), entity_types=({"name": "Person", "description": "A person"},), edge_types=())
    original = SourceEnvelope(source_revision=uuid4(), source_sha256=hashlib.sha256(content.encode()).hexdigest(), ontology_revision=ontology.revision, operation_id=uuid4(), source_kind="document", content=content, source_name="note", recorded_at=datetime.now(timezone.utc))
    provider = GraphitiKnowledgeProvider(graphiti=object())

    async def rows(query, **params):
        if "MATCH (e:Episodic" in query:
            return [{"group_id": one.group_id, "content": content, "sha256": original.source_sha256}]
        return [{"group_id": one.group_id, "fingerprint": _request_fingerprint(one, original, ontology), "status": "complete"}]

    monkeypatch.setattr(provider, "_rows", rows)
    for change in ({"source_revision": uuid4()}, {"evidence_ids": (uuid4(),)}, {"recorded_at": original.recorded_at + timedelta(seconds=1)}):
        with pytest.raises(OperationConflict):
            await provider.ingest(one, original.model_copy(update=change), ontology)


@pytest.mark.asyncio
async def test_precreate_failure_is_uncertain(monkeypatch):
    from graphiti_core.nodes import EpisodicNode

    one = scope()
    content = "Mira works for Harbor Labs."
    ontology = OntologySpec(revision=uuid4(), entity_types=({"name": "Person", "description": "A person"},), edge_types=())
    source = SourceEnvelope(source_revision=uuid4(), source_sha256=hashlib.sha256(content.encode()).hexdigest(), ontology_revision=ontology.revision, operation_id=uuid4(), source_kind="document", content=content, source_name="note", recorded_at=datetime.now(timezone.utc))
    provider = GraphitiKnowledgeProvider(graphiti=SimpleNamespace(driver=object()))

    async def no_rows(*args, **kwargs):
        return []

    async def failed_save(self, driver):
        raise TimeoutError("write may have occurred")

    monkeypatch.setattr(provider, "_rows", no_rows)
    monkeypatch.setattr(EpisodicNode, "save", failed_save)
    with pytest.raises(ReconciliationRequired):
        await provider.ingest(one, source, ontology)


@pytest.mark.asyncio
async def test_completion_waits_for_returned_scope_validation(monkeypatch):
    from graphiti_core.nodes import EpisodicNode

    one = scope()
    content = "Mira works for Harbor Labs."
    ontology = OntologySpec(revision=uuid4(), entity_types=({"name": "Person", "description": "A person"},), edge_types=())
    source = SourceEnvelope(source_revision=uuid4(), source_sha256=hashlib.sha256(content.encode()).hexdigest(), ontology_revision=ontology.revision, operation_id=uuid4(), source_kind="document", content=content, source_name="note", recorded_at=datetime.now(timezone.utc))
    writes = []

    class FakeDriver:
        async def execute_query(self, query, **kwargs):
            writes.append(query)

    class FakeGraph:
        driver = FakeDriver()

        async def add_episode(self, **kwargs):
            return SimpleNamespace(episode=SimpleNamespace(uuid=kwargs["uuid"], group_id="wrong-group"), nodes=[], edges=[])

    provider = GraphitiKnowledgeProvider(graphiti=FakeGraph())

    async def no_rows(*args, **kwargs):
        return []

    async def no_save(self, driver):
        return None

    monkeypatch.setattr(provider, "_rows", no_rows)
    monkeypatch.setattr(EpisodicNode, "save", no_save)
    with pytest.raises(ReconciliationRequired):
        await provider.ingest(one, source, ontology)
    assert any("'pending'" in query for query in writes)
    assert not any("'complete'" in query for query in writes)


@pytest.mark.asyncio
async def test_entity_normalizes_neo4j_datetime(monkeypatch):
    now = datetime.now(timezone.utc)

    class NeoDate:
        def to_native(self):
            return now

    one = scope()
    provider = GraphitiKnowledgeProvider(graphiti=SimpleNamespace(driver=object()))

    async def rows(query, **params):
        if "MATCH (n:Entity" in query:
            assert "labels(n) AS labels" in query
            return [{"properties": {"uuid": str(uuid4()), "name": "Mira", "created_at": NeoDate(),
                                    "summary": "研究摘要", "labels": ["Shadow"], "role": "analyst",
                                    "name_embedding": [0.1], "group_id": one.group_id},
                     "labels": ["Entity", "Person"]}]
        return []

    async def passthrough(scope, fact):
        return fact

    monkeypatch.setattr(provider, "_rows", rows)
    monkeypatch.setattr(provider, "_decorate_fact", passthrough)
    result = await provider.entity(one, str(uuid4()))
    assert result.facts[0].created_at == now
    assert result.facts[0].labels == ("Entity", "Person")
    assert result.facts[0].summary == "研究摘要"
    assert result.facts[0].attributes == {"role": "analyst"}


@pytest.mark.asyncio
@pytest.mark.parametrize("labels", ["Person", {"Person": True}, None, False, ["Person"] * 65])
async def test_entity_rejects_malformed_neo4j_label_containers(monkeypatch, labels):
    one = scope()
    provider = GraphitiKnowledgeProvider(graphiti=SimpleNamespace(driver=object()))

    async def rows(query, **params):
        return [{"properties": {"uuid": str(uuid4()), "group_id": one.group_id}, "labels": labels}]

    monkeypatch.setattr(provider, "_rows", rows)
    with pytest.raises(ValueError):
        await provider.entity(one, str(uuid4()))


@pytest.mark.asyncio
async def test_entity_edge_expiry_and_search_object_metadata(monkeypatch):
    one = scope()
    valid = datetime(2024, 1, 1, tzinfo=timezone.utc)
    invalid = datetime(2024, 2, 1, tzinfo=timezone.utc)
    expired = datetime(2024, 3, 1, tzinfo=timezone.utc)

    class NeoDate:
        def __init__(self, value):
            self.value = value

        def to_native(self):
            return self.value

    node_id, edge_id, episode_id = str(uuid4()), str(uuid4()), str(uuid4())
    provider = GraphitiKnowledgeProvider(graphiti=object())

    async def rows(query, **params):
        if "MATCH (n:Entity" in query:
            return [{"properties": {"uuid": node_id, "group_id": one.group_id, "name": "Mira",
                                    "summary": "研究摘要"}, "labels": ["Entity", "Person"]}]
        if "MATCH (a:Entity)" in query:
            return [{"properties": {"uuid": edge_id, "group_id": one.group_id,
                                    "episodes": [episode_id], "valid_at": NeoDate(valid),
                                    "invalid_at": NeoDate(invalid), "expired_at": NeoDate(expired),
                                    "confidence": 0.9, "fact_embedding": [0.1]},
                     "source_id": node_id, "target_id": str(uuid4())}]
        return []

    async def passthrough(scope, fact):
        return fact

    monkeypatch.setattr(provider, "_rows", rows)
    monkeypatch.setattr(provider, "_decorate_fact", passthrough)
    entity = await provider.entity(one, node_id)
    edge = next(fact for fact in entity.facts if fact.kind == "edge")
    assert (edge.valid_at, edge.invalid_at, edge.expired_at) == (valid, invalid, expired)
    assert edge.attributes == {"confidence": 0.9}

    class FakeGraph:
        async def search_(self, text, *, config, group_ids):
            assert group_ids == [one.group_id]
            node = SimpleNamespace(uuid=node_id, group_id=one.group_id, name="Mira",
                                   labels=["Entity", "Person"], summary="研究摘要")
            relation = SimpleNamespace(uuid=edge_id, group_id=one.group_id, name="WORKS_FOR",
                                       source_node_uuid=node_id, target_node_uuid=str(uuid4()),
                                       episodes=[episode_id], valid_at=NeoDate(valid),
                                       invalid_at=NeoDate(invalid), expired_at=NeoDate(expired))
            return SimpleNamespace(edges=[relation], nodes=[node], episodes=[],
                                   edge_reranker_scores=[], node_reranker_scores=[], episode_reranker_scores=[])

    search_provider = GraphitiKnowledgeProvider(graphiti=FakeGraph())
    monkeypatch.setattr(search_provider, "_decorate_fact", passthrough)
    found = await search_provider.search(one, SearchQuery(text="Mira"))
    assert next(fact for fact in found.facts if fact.kind == "node").labels == ("Entity", "Person")
    assert next(fact for fact in found.facts if fact.kind == "node").summary == "研究摘要"
    searched_edge = next(fact for fact in found.facts if fact.kind == "edge")
    assert (searched_edge.valid_at, searched_edge.invalid_at, searched_edge.expired_at) == (valid, invalid, expired)


@pytest.mark.asyncio
async def test_fake_graphiti_ingest_preserves_node_metadata_and_edge_expiry(monkeypatch):
    from graphiti_core.nodes import EpisodicNode

    one = scope()
    content = "Mira works for Harbor Labs."
    ontology = OntologySpec(revision=uuid4(), entity_types=({"name": "Person", "description": "A person"},), edge_types=())
    source = SourceEnvelope(source_revision=uuid4(), source_sha256=hashlib.sha256(content.encode()).hexdigest(),
                            ontology_revision=ontology.revision, operation_id=uuid4(), source_kind="document",
                            content=content, source_name="synthetic", recorded_at=datetime.now(timezone.utc))
    expiry = datetime(2024, 3, 1, tzinfo=timezone.utc)

    class NeoDate:
        def to_native(self):
            return expiry

    class FakeDriver:
        async def execute_query(self, query, **kwargs):
            return None

    class FakeGraph:
        driver = FakeDriver()

        async def add_episode(self, **kwargs):
            episode_id = kwargs["uuid"]
            node_id = str(uuid4())
            node = SimpleNamespace(uuid=node_id, group_id=one.group_id, name="Mira",
                                   labels=["Entity", "Person"], summary="研究摘要")
            edge = SimpleNamespace(uuid=str(uuid4()), group_id=one.group_id, name="WORKS_FOR",
                                   source_node_uuid=node_id, target_node_uuid=str(uuid4()),
                                   episodes=[episode_id], expired_at=NeoDate())
            episode = SimpleNamespace(uuid=episode_id, group_id=one.group_id, name="synthetic")
            return SimpleNamespace(episode=episode, nodes=[node], edges=[edge])

    async def no_save(self, driver):
        return None

    async def no_rows(query, **params):
        return []

    async def passthrough(scope, fact, **kwargs):
        return fact

    subject = GraphitiKnowledgeProvider(graphiti=FakeGraph())
    monkeypatch.setattr(EpisodicNode, "save", no_save)
    monkeypatch.setattr(subject, "_rows", no_rows)
    monkeypatch.setattr(subject, "_decorate_fact", passthrough)
    result = await subject.ingest(one, source, ontology)
    node = next(fact for fact in result.facts if fact.kind == "node")
    edge = next(fact for fact in result.facts if fact.kind == "edge")
    assert node.labels == ("Entity", "Person") and node.summary == "研究摘要"
    assert edge.expired_at == expiry


@pytest.mark.asyncio
async def test_temporal_filter_explicit_rejection():
    provider = GraphitiKnowledgeProvider(graphiti=object())
    with pytest.raises(UnsupportedCapability):
        await provider.search(scope(), SearchQuery(text="Mira", valid_at=datetime.now(timezone.utc)))


@pytest.mark.asyncio
async def test_bad_json_and_bad_schema_do_not_pass_or_retry():
    class Expected(BaseModel):
        extracted_entities: list[str]

    class FakeCompletions:
        def __init__(self, body):
            self.body = body
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            return type("Response", (), {"choices": [type("Choice", (), {"message": type("Body", (), {"content": self.body})()})()]})()

    for body in ("not json", '{"wrong": []}'):
        completions = FakeCompletions(body)
        fake_client = type("Client", (), {"chat": type("Chat", (), {"completions": completions})()})()
        llm = BoundedGenericClient(config=LLMConfig(api_key="fixture", base_url="http://localhost/fixture", model="fixture"), client=fake_client, structured_output_mode="json_object", budget=1)
        with pytest.raises((ValueError, ValidationError)):
            await llm._generate_response_with_retry([Message(role="user", content="synthetic")], Expected, max_tokens=128)
        assert completions.calls == 1

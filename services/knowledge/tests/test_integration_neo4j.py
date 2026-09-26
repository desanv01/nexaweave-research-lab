"""Disposable Neo4j qualification fixtures; no provider network calls."""

import hashlib
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from graphiti_core.llm_client.client import LLMClient
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.cross_encoder.client import CrossEncoderClient

from mirofish_knowledge.contracts import Layer, KnowledgeScope, OntologySpec, SourceEnvelope, SearchQuery
from mirofish_knowledge.provider import CommunityGraphiti, GraphitiKnowledgeProvider, OperationConflict, ReconciliationRequired


pytestmark = pytest.mark.neo4j


class FixtureLLM(LLMClient):
    """Deterministic synthetic responses to the pinned Graphiti client signature."""

    def __init__(self):
        super().__init__(None)

    async def _generate_response(self, messages, response_model=None, max_tokens=16384, model_size=None):
        raise AssertionError("Graphiti should call generate_response")

    async def generate_response(self, messages, response_model=None, max_tokens=None, model_size=None, group_id=None, prompt_name=None, *, attribute_extraction=False):
        if response_model is None:
            raise AssertionError(f"Fixture requires a pinned Graphiti response model ({prompt_name})")

        def checked(payload):
            return response_model.model_validate(payload).model_dump(mode="json")

        name = response_model.__name__
        prompt = "\n".join(message.content for message in messages)
        person, organization = (("Lena Moss", "Summit Works") if "Lena Moss works for Summit Works" in prompt else ("Mira Vale", "Harbor Labs"))
        if name == "ExtractedEntities":
            return checked({"extracted_entities": [{"name": person, "entity_type_id": 1, "episode_indices": [0]}, {"name": organization, "entity_type_id": 2, "episode_indices": [0]}]})
        if name == "ExtractedEdges":
            return checked({"edges": [{"source_entity_name": person, "target_entity_name": organization, "relation_type": "WORKS_FOR", "fact": f"{person} works for {organization}", "valid_at": None, "invalid_at": None, "episode_indices": [0]}]})
        if name == "NodeResolutions":
            return checked({"entity_resolutions": [{"id": 0, "name": person, "duplicate_candidate_id": -1}, {"id": 1, "name": organization, "duplicate_candidate_id": -1}]})
        if name == "EdgeDuplicate":
            return checked({"duplicate_facts": [], "contradicted_facts": []})
        if name == "EntitySummary":
            return checked({"summary": "Synthetic fixture entity."})
        if name == "SummarizedEntities":
            return checked({"summaries": []})
        if name == "BatchEdgeTimestamps":
            return checked({"timestamps": [{"valid_at": None, "invalid_at": None}]})
        if name == "EdgeTimestamps":
            return checked({"valid_at": None, "invalid_at": None})
        # Dynamic attribute models have optional fields; null is intentional.
        if not any(field.is_required() for field in response_model.model_fields.values()):
            return checked({field: None for field in response_model.model_fields})
        raise AssertionError(f"Unexpected Graphiti response model: {name} ({prompt_name})")


class FixtureEmbedder(EmbedderClient):
    """Deterministic 1024-dimensional test embeddings; never a live fallback."""

    async def create(self, input_data):
        text = str(input_data)
        digest = hashlib.sha256(text.encode()).digest()
        return [((digest[i % 32] / 255) - 0.5) for i in range(1024)]

    async def create_batch(self, input_data_list):
        return [await self.create(item) for item in input_data_list]


class FixtureReranker(CrossEncoderClient):
    async def rank(self, query, passages):
        return [(passage, 1.0) for passage in passages]


@pytest.fixture
async def provider():
    if os.getenv("KNOWLEDGE_INTEGRATION") != "1":
        pytest.skip("Set KNOWLEDGE_INTEGRATION=1 for disposable Neo4j tests")
    password = os.environ.get("KNOWLEDGE_TEST_PASSWORD")
    if not password or password.lower() in {"neo4j", "password", "changeme", "test"}:
        pytest.fail("A non-default KNOWLEDGE_TEST_PASSWORD is required for opted-in integration tests")
    graph = CommunityGraphiti(
        uri="bolt://127.0.0.1:17687", user="neo4j", password=password,
        llm_client=FixtureLLM(), embedder=FixtureEmbedder(), cross_encoder=FixtureReranker(),
        max_coroutines=1,
    )
    subject = GraphitiKnowledgeProvider(graphiti=graph)
    try:
        await subject.initialize()  # connection/index failure is a real test failure
        yield subject
    finally:
        await subject.close()


def fixture_data(*, content="Mira Vale works for Harbor Labs.", project_id=None):
    scope = KnowledgeScope(workspace_id=uuid4(), project_id=project_id or uuid4(), graph_id=uuid4(), layer=Layer.source)
    ontology = OntologySpec(
        revision=uuid4(),
        entity_types=({"name": "Person", "description": "Named human"}, {"name": "Organization", "description": "Named organization"}),
        edge_types=({"name": "WORKS_FOR", "description": "Employment relationship", "source_targets": ({"source": "Person", "target": "Organization"},)},),
    )
    source = SourceEnvelope(source_revision=uuid4(), source_sha256=hashlib.sha256(content.encode()).hexdigest(), ontology_revision=ontology.revision, operation_id=uuid4(), source_kind="document", content=content, source_name="Synthetic employment note", recorded_at=datetime.now(timezone.utc), evidence_ids=(uuid4(),))
    return scope, source, ontology


@pytest.mark.asyncio
async def test_real_graphiti_ingest_scope_provenance_and_duplicate(provider):
    first_scope, first_source, ontology = fixture_data()
    _, second_source, second_ontology = fixture_data(content="Lena Moss works for Summit Works.")
    second_scope = first_scope.model_copy(update={"project_id": uuid4()})
    first = await provider.ingest(first_scope, first_source, ontology)
    second = await provider.ingest(second_scope, second_source, second_ontology)
    assert any(fact.kind == "node" for fact in first.facts)
    assert any(fact.kind == "edge" for fact in first.facts)
    assert all(fact.scope == first_scope for fact in first.facts)
    assert all(first_source.evidence_ids[0] in fact.evidence_ids for fact in first.facts if fact.episode_ids)
    assert any(fact.kind == "node" for fact in second.facts)
    assert any(fact.kind == "edge" for fact in second.facts)
    assert all(fact.scope == second_scope for fact in second.facts)
    assert all(second_source.evidence_ids[0] in fact.evidence_ids for fact in second.facts if fact.episode_ids)
    assert (await provider.ingest(first_scope, first_source, ontology)).already_exists
    node_id = next(fact.provider_id for fact in first.facts if fact.kind == "node")
    second_node_id = next(fact.provider_id for fact in second.facts if fact.kind == "node")
    assert (await provider.entity(first_scope, node_id)).facts
    assert (await provider.entity(second_scope, second_node_id)).facts
    assert not (await provider.entity(second_scope, node_id)).facts
    assert not (await provider.entity(first_scope, second_node_id)).facts
    first_search = await provider.search(first_scope, SearchQuery(text="Mira Vale", top_k=5))
    second_search = await provider.search(second_scope, SearchQuery(text="Lena Moss", top_k=5))
    assert first_search.facts
    assert second_search.facts
    assert any("Mira Vale" in (fact.name or "") or "Mira Vale" in (fact.fact or "") for fact in first_search.facts)
    assert any("Lena Moss" in (fact.name or "") or "Lena Moss" in (fact.fact or "") for fact in second_search.facts)
    assert all(fact.scope == first_scope for fact in first_search.facts)
    assert all(fact.scope == second_scope for fact in second_search.facts)
    assert not {fact.provider_id for fact in first_search.facts} & {fact.provider_id for fact in second_search.facts}
    with pytest.raises(OperationConflict):
        await provider.ingest(first_scope, first_source.model_copy(update={"content": "Conflicting content"}), ontology)


@pytest.mark.asyncio
async def test_ambiguous_mutation_is_not_replayed(provider, monkeypatch):
    scope, source, ontology = fixture_data()

    async def uncertain(**kwargs):
        raise TimeoutError("synthetic failure after possible mutation")

    monkeypatch.setattr(provider.graphiti, "add_episode", uncertain)
    with pytest.raises(ReconciliationRequired):
        await provider.ingest(scope, source, ontology)
    with pytest.raises(ReconciliationRequired):
        await provider.ingest(scope, source, ontology)

"""Disposable Neo4j qualification fixtures; no provider network calls."""

import hashlib
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from graphiti_core.llm_client.client import LLMClient
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.cross_encoder.client import CrossEncoderClient

from nexaweave_knowledge.contracts import GraphPageRequest, Layer, KnowledgeScope, OntologySpec, SourceEnvelope, SearchQuery
from nexaweave_knowledge.provider import CommunityGraphiti, GraphitiKnowledgeProvider, OperationConflict, ReconciliationRequired
from nexaweave_knowledge.operations import request_fingerprint


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
    assert all(fact.labels for fact in first.facts if fact.kind == "node")
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
async def test_real_graphiti_entity_metadata_roundtrip(provider):
    scope, source, ontology = fixture_data()
    ingested = await provider.ingest(scope, source, ontology)
    node = next(fact for fact in ingested.facts if fact.kind == "node" and "Person" in fact.labels)
    edge = next(fact for fact in ingested.facts if fact.kind == "edge")
    assert node.summary is None or isinstance(node.summary, str)
    summary = "研究摘要 — synthetic fixture"
    expiry = datetime(2024, 3, 1, tzinfo=timezone.utc)
    invalid = datetime(2024, 2, 1, tzinfo=timezone.utc)
    await provider._driver.execute_query(
        "MATCH (n:Entity {uuid: $uuid, group_id: $group_id}) SET n.summary = $summary",
        params={"uuid": node.provider_id, "group_id": scope.group_id, "summary": summary},
    )
    await provider._driver.execute_query(
        "MATCH ()-[r:RELATES_TO {uuid: $uuid, group_id: $group_id}]->() "
        "SET r.invalid_at = datetime($invalid_at), r.expired_at = datetime($expired_at)",
        params={"uuid": edge.provider_id, "group_id": scope.group_id,
                "invalid_at": invalid.isoformat(), "expired_at": expiry.isoformat()},
    )
    node_page = await provider.page(scope, GraphPageRequest(kind="node", entity_type="Person"))
    paged_node = next(fact for fact in node_page.facts if fact.provider_id == node.provider_id)
    assert "Person" in paged_node.labels and paged_node.summary == summary
    assert "summary" not in paged_node.attributes and "labels" not in paged_node.attributes
    detail = await provider.entity(scope, node.provider_id)
    detailed_node = next(fact for fact in detail.facts if fact.kind == "node")
    detailed_edge = next(fact for fact in detail.facts if fact.provider_id == edge.provider_id)
    assert detailed_node.labels == paged_node.labels and detailed_node.summary == summary
    assert (detailed_edge.invalid_at, detailed_edge.expired_at) == (invalid, expiry)
    edge_page = await provider.page(scope, GraphPageRequest(kind="edge"))
    paged_edge = next(fact for fact in edge_page.facts if fact.provider_id == edge.provider_id)
    assert (paged_edge.invalid_at, paged_edge.expired_at) == (invalid, expiry)
    assert paged_edge.expired_at != paged_edge.invalid_at
    assert "expired_at" not in paged_edge.attributes


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


@pytest.mark.asyncio
async def test_read_only_completion_proof_rejects_partial_marker(provider, monkeypatch):
    scope, source, ontology = fixture_data()
    await provider.ingest(scope, source, ontology)

    async def forbidden_dispatch(**kwargs):
        raise AssertionError("completion proof must not extract")

    monkeypatch.setattr(provider.graphiti, "add_episode", forbidden_dispatch)
    proof = await provider.completion_proof(scope, source, ontology)
    assert proof.group_id == scope.group_id
    assert proof.episode_id == scope.episode_uuid(source.operation_id)
    assert proof.evidence_ids == source.evidence_ids
    episode_id = str(scope.episode_uuid(source.operation_id))
    fingerprint = request_fingerprint(scope, source, ontology)

    async def marker(*, status="complete", evidence=None, request_hash=None):
        await provider._driver.execute_query(
            "MATCH (o:MiroFishIngest {uuid: $uuid}) "
            "SET o.status = $status, o.evidence_ids = $evidence_ids, o.fingerprint = $fingerprint",
            params={"uuid": episode_id, "status": status,
                    "evidence_ids": evidence if evidence is not None else [str(item) for item in source.evidence_ids],
                    "fingerprint": request_hash if request_hash is not None else fingerprint},
        )

    try:
        await marker(status="pending")
        with pytest.raises(ReconciliationRequired):
            await provider.completion_proof(scope, source, ontology)
        await marker(evidence=[str(uuid4())])
        with pytest.raises(ReconciliationRequired):
            await provider.completion_proof(scope, source, ontology)
        await marker(request_hash="0" * 64)
        with pytest.raises(ReconciliationRequired):
            await provider.completion_proof(scope, source, ontology)
    finally:
        await marker()


@pytest.mark.asyncio
async def test_real_scoped_graph_enumeration_and_incomplete_episode(provider):
    first_scope, first_source, first_ontology = fixture_data()
    _, second_source, second_ontology = fixture_data(content="Lena Moss works for Summit Works.")
    other_scope, other_source, other_ontology = fixture_data()
    await provider.ingest(first_scope, first_source, first_ontology)
    await provider.ingest(first_scope, second_source, second_ontology)
    await provider.ingest(other_scope, other_source, other_ontology)

    async def all_facts(scope, kind):
        found, cursor = [], None
        for _ in range(20):
            page = await provider.page(scope, GraphPageRequest(kind=kind, limit=1, cursor=cursor))
            found.extend(page.facts)
            cursor = page.next_cursor
            if cursor is None:
                break
        else:
            pytest.fail("scoped page sequence did not terminate")
        ids = [fact.provider_id for fact in found]
        assert ids == sorted(ids) and len(ids) == len(set(ids))
        assert all(fact.scope == scope and fact.episode_ids for fact in found)
        return found

    first = {kind: await all_facts(first_scope, kind) for kind in ("node", "edge", "episode")}
    second = {kind: await all_facts(other_scope, kind) for kind in ("node", "edge", "episode")}
    direct_queries = {
        "node": "MATCH (n:Entity) WHERE n.group_id = $group_id RETURN n.uuid AS uuid",
        "edge": "MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) WHERE r.group_id = $group_id AND a.group_id = $group_id AND b.group_id = $group_id RETURN r.uuid AS uuid",
        "episode": "MATCH (e:Episodic) WHERE e.group_id = $group_id RETURN e.uuid AS uuid",
    }
    for current_scope, collected in ((first_scope, first), (other_scope, second)):
        for kind, query in direct_queries.items():
            direct = [row["uuid"] for row in await provider._rows(query, group_id=current_scope.group_id)]
            paged = [fact.provider_id for fact in collected[kind]]
            assert len(direct) == len(set(direct)) == len(paged)
            assert set(direct) == set(paged)
    assert len(first["episode"]) == 2 and len(second["episode"]) == 1
    assert len(first["node"]) >= 2 and first["edge"]
    assert second["node"] and second["edge"]
    for kind in first:
        assert not {fact.provider_id for fact in first[kind]} & {fact.provider_id for fact in second[kind]}
    first_nodes = {fact.provider_id for fact in first["node"]}
    second_nodes = {fact.provider_id for fact in second["node"]}
    assert all(fact.source_node_id in first_nodes and fact.target_node_id in first_nodes for fact in first["edge"])
    assert all(fact.source_node_id in second_nodes and fact.target_node_id in second_nodes for fact in second["edge"])

    episode_id = str(first_scope.episode_uuid(first_source.operation_id))
    try:
        await provider._driver.execute_query(
            "MATCH (o:MiroFishIngest {uuid: $uuid}) SET o.status = 'pending'", params={"uuid": episode_id})
        with pytest.raises(ReconciliationRequired):
            await all_facts(first_scope, "episode")
    finally:
        await provider._driver.execute_query(
            "MATCH (o:MiroFishIngest {uuid: $uuid}) SET o.status = 'complete'", params={"uuid": episode_id})


@pytest.mark.asyncio
async def test_scoped_edge_page_excludes_cross_scope_endpoint(provider):
    first_scope, first_source, first_ontology = fixture_data()
    other_scope, other_source, other_ontology = fixture_data(content="Lena Moss works for Summit Works.")
    await provider.ingest(first_scope, first_source, first_ontology)
    await provider.ingest(other_scope, other_source, other_ontology)
    first_node = (await provider.page(first_scope, GraphPageRequest(kind="node", limit=1))).facts[0].provider_id
    other_node = (await provider.page(other_scope, GraphPageRequest(kind="node", limit=1))).facts[0].provider_id
    cross_uuid = str(uuid4())
    try:
        records, _, _ = await provider._driver.execute_query(
            "MATCH (a:Entity {uuid: $source_id, group_id: $source_group}), "
            "(b:Entity {uuid: $target_id, group_id: $target_group}) "
            "CREATE (a)-[r:RELATES_TO {uuid: $uuid, group_id: $source_group, "
            "episodes: $episodes, name: 'CROSS_SCOPE_FIXTURE', fact: 'synthetic fixture edge'}]->(b) "
            "RETURN r.uuid AS uuid",
            params={"source_id": first_node, "source_group": first_scope.group_id,
                    "target_id": other_node, "target_group": other_scope.group_id,
                    "uuid": cross_uuid, "episodes": [str(first_scope.episode_uuid(first_source.operation_id))]},
        )
        assert [row["uuid"] for row in records] == [cross_uuid]
        seen, cursor = [], None
        for _ in range(20):
            page = await provider.page(first_scope, GraphPageRequest(kind="edge", limit=1, cursor=cursor))
            seen.extend(fact.provider_id for fact in page.facts)
            cursor = page.next_cursor
            if cursor is None:
                break
        else:
            pytest.fail("edge page sequence did not terminate")
        assert cross_uuid not in seen
        direct = await provider._rows(
            "MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) WHERE r.group_id = $group_id "
            "AND a.group_id = $group_id AND b.group_id = $group_id RETURN r.uuid AS uuid",
            group_id=first_scope.group_id,
        )
        assert set(seen) == {row["uuid"] for row in direct}
    finally:
        await provider._driver.execute_query(
            "MATCH ()-[r:RELATES_TO {uuid: $uuid}]->() DELETE r", params={"uuid": cross_uuid})

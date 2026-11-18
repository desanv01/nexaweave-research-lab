"""One guarded, real offline Neo4j Community dump and restore."""

import asyncio
from contextlib import asynccontextmanager
import os
import time

import pytest
from neo4j import AsyncGraphDatabase
from neo4j.exceptions import ServiceUnavailable, SessionExpired

from nexaweave_knowledge.contracts import GraphPageRequest
from nexaweave_knowledge.provider import CommunityGraphiti, GraphitiKnowledgeProvider
from test_integration_neo4j import FixtureEmbedder, FixtureLLM, FixtureReranker, fixture_data


def time_left(fixture, maximum):
    remaining = min(maximum, fixture.deadline - time.monotonic())
    assert remaining > 0, "community recovery fixture deadline"
    return remaining


async def ready(fixture, uri, password):
    async with asyncio.timeout(time_left(fixture, 80)):
        while True:
            try:
                async with AsyncGraphDatabase.driver(uri, auth=("neo4j", password),
                        connection_timeout=2, max_transaction_retry_time=0,
                        max_connection_pool_size=1) as driver:
                    await driver.verify_connectivity()
                return
            except (ServiceUnavailable, SessionExpired):
                await asyncio.sleep(.5)


@asynccontextmanager
async def provider(fixture, uri, password, *, seed=False):
    await ready(fixture, uri, password)
    subject = GraphitiKnowledgeProvider(graphiti=CommunityGraphiti(
        uri=uri, user="neo4j", password=password,
        llm_client=FixtureLLM(), embedder=FixtureEmbedder(), cross_encoder=FixtureReranker(),
        max_coroutines=1,
    ))
    try:
        if seed:
            async with asyncio.timeout(time_left(fixture, 45)):
                await subject.initialize()
        yield subject
    finally:
        async with asyncio.timeout(10):
            await subject.close()


async def read_scope(subject, scope, source, ontology, expected_fact):
    proof = await subject.completion_proof(scope, source, ontology)
    assert proof.group_id == scope.group_id
    assert proof.episode_id == scope.episode_uuid(source.operation_id)
    assert proof.evidence_ids == source.evidence_ids
    facts = {}
    for kind, expected_count in (("node", 2), ("edge", 1), ("episode", 1)):
        page = await subject.page(scope, GraphPageRequest(kind=kind, limit=8))
        assert len(page.facts) == expected_count and page.next_cursor is None
        identities = [str(fact.provider_id) for fact in page.facts]
        assert len(set(identities)) == expected_count
        for fact in page.facts:
            assert fact.scope == scope
            assert fact.evidence_ids == source.evidence_ids
            assert str(proof.episode_id) in {str(item) for item in fact.episode_ids}
        facts[kind] = {str(fact.provider_id): fact for fact in page.facts}
    assert set(facts["episode"]) == {str(proof.episode_id)}
    edge, = facts["edge"].values()
    assert edge.fact == expected_fact
    assert str(edge.source_node_id) in facts["node"] and str(edge.target_node_id) in facts["node"]
    assert edge.source_node_id != edge.target_node_id
    return proof, facts


@pytest.mark.asyncio
async def test_actual_community_offline_restore_preserves_graph_evidence_and_isolation(request):
    if os.environ.get("NEXAWEAVE_NEO4J_RECOVERY_INTEGRATION") != "1":
        pytest.skip("guarded Community graph recovery runner required")
    fixture = request.getfixturevalue("neo4j_recovery")
    primary = fixture_data()
    foreign = fixture_data(content="Lena Moss works for Summit Works.")
    assert primary[0].project_id != foreign[0].project_id and primary[0].graph_id != foreign[0].graph_id
    assert primary[1].evidence_ids != foreign[1].evidence_ids

    source_uri, source_password = fixture.start_database("source")
    async with provider(fixture, source_uri, source_password, seed=True) as source:
        async with asyncio.timeout(time_left(fixture, 75)):
            for seed in (primary, foreign):
                applied = await source.ingest(*seed)
                assert not applied.already_exists
            expected = (
                await read_scope(source, *primary, "Mira Vale works for Harbor Labs"),
                await read_scope(source, *foreign, "Lena Moss works for Summit Works"),
            )
    fixture.stop_database("source")
    size, digest = fixture.restore()
    assert size > 0 and len(digest) == 64

    restored_uri, restored_password = fixture.start_database("restored")
    assert restored_password != source_password
    async with provider(fixture, restored_uri, restored_password) as restored:
        async with asyncio.timeout(time_left(fixture, 45)):
            observed = (
                await read_scope(restored, *primary, "Mira Vale works for Harbor Labs"),
                await read_scope(restored, *foreign, "Lena Moss works for Summit Works"),
            )
            assert observed == expected
            for kind in ("node", "edge", "episode"):
                assert observed[0][1][kind].keys().isdisjoint(observed[1][1][kind])
            for owner, other, proofs in ((primary, foreign, observed[0]), (foreign, primary, observed[1])):
                node_id = next(iter(proofs[1]["node"]))
                assert (await restored.entity(owner[0], node_id)).facts
                assert not (await restored.entity(other[0], node_id)).facts
    fixture.stop_database("restored")
    fixture.require_stopped("source")

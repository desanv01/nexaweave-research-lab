"""Graphiti 0.30.2 adapter for a single Neo4j Community database.

This is a compatibility spike. The caller supplies an already authorized scope;
the group identifier is a partition key, not an access-control decision.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .contracts import FactResult, IngestResult, KnowledgeScope, OntologySpec, SearchQuery, SearchResult, SourceEnvelope

os.environ["GRAPHITI_TELEMETRY_ENABLED"] = "false"  # before any graphiti_core import

from graphiti_core import Graphiti
from graphiti_core.llm_client import LLMConfig
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
from graphiti_core.nodes import EpisodicNode, EpisodeType
from graphiti_core.search.search_config_recipes import COMBINED_HYBRID_SEARCH_RRF, COMBINED_HYBRID_SEARCH_CROSS_ENCODER
from openai import AsyncOpenAI


class UnsupportedCapability(RuntimeError):
    pass


class ReconciliationRequired(RuntimeError):
    pass


class OperationConflict(RuntimeError):
    pass


class ScopeViolation(RuntimeError):
    pass


class Endpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    base_url: str
    model: str = Field(min_length=1)
    api_key: SecretStr = Field(repr=False)

    @field_validator("api_key")
    @classmethod
    def nonempty_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("API key is required")
        return value


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: SecretStr = Field(repr=False)
    llm: Endpoint
    embedding: Endpoint
    embedding_dimension: int = Field(ge=1, le=4096)
    reranker: Endpoint | None = None
    search_recipe: str = "hybrid_rrf"
    structured_output_mode: str = "json_object"
    call_timeout_seconds: float = Field(default=30, gt=0, le=300)
    max_tokens: int = Field(default=4096, ge=128, le=32768)
    max_coroutines: int = Field(default=4, ge=1, le=32)
    total_llm_call_budget: int = Field(default=32, ge=1, le=10000)

    @field_validator("neo4j_password")
    @classmethod
    def nonempty_password(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("Neo4j password is required")
        return value

    @classmethod
    def from_env(cls) -> "ProviderConfig":
        def required(name: str) -> str:
            value = os.environ.get(name)
            if not value:
                raise ValueError(f"{name} is required; no implicit provider fallback")
            return value
        def setting(name: str, default: str, cast):
            raw = os.getenv(name, default)
            try:
                return cast(raw)
            except ValueError as exc:
                raise ValueError(f"{name} must be a valid {cast.__name__}") from exc

        return cls(
            neo4j_uri=required("KNOWLEDGE_NEO4J_URI"),
            neo4j_user=required("KNOWLEDGE_NEO4J_USER"),
            neo4j_password=required("KNOWLEDGE_NEO4J_PASSWORD"),
            llm=Endpoint(base_url=os.getenv("KNOWLEDGE_LLM_BASE_URL", "https://api.deepseek.com"), model=os.getenv("KNOWLEDGE_LLM_MODEL", "deepseek-flash"), api_key=required("KNOWLEDGE_LLM_API_KEY")),
            embedding=Endpoint(base_url=required("KNOWLEDGE_EMBEDDING_BASE_URL"), model=required("KNOWLEDGE_EMBEDDING_MODEL"), api_key=required("KNOWLEDGE_EMBEDDING_API_KEY")),
            embedding_dimension=int(required("KNOWLEDGE_EMBEDDING_DIMENSION")),
            reranker=Endpoint(base_url=required("KNOWLEDGE_RERANKER_BASE_URL"), model=required("KNOWLEDGE_RERANKER_MODEL"), api_key=required("KNOWLEDGE_RERANKER_API_KEY")) if os.getenv("KNOWLEDGE_SEARCH_RECIPE") == "hybrid_cross_encoder" else None,
            search_recipe=os.getenv("KNOWLEDGE_SEARCH_RECIPE", "hybrid_rrf"),
            structured_output_mode=os.getenv("KNOWLEDGE_STRUCTURED_OUTPUT_MODE", "json_object"),
            call_timeout_seconds=setting("KNOWLEDGE_CALL_TIMEOUT_SECONDS", "30", float),
            max_tokens=setting("KNOWLEDGE_MAX_TOKENS", "4096", int),
            max_coroutines=setting("KNOWLEDGE_MAX_COROUTINES", "4", int),
            total_llm_call_budget=setting("KNOWLEDGE_TOTAL_LLM_CALL_BUDGET", "32", int),
        )


class BoundedGenericClient(OpenAIGenericClient):
    """One SDK attempt per Graphiti call, with parsed-schema validation and budget."""

    def __init__(self, *, budget: int, **kwargs: Any):
        super().__init__(**kwargs)
        self._remaining = budget
        self._budget_lock = asyncio.Lock()

    async def _generate_response_with_retry(self, messages, response_model=None, max_tokens=None, model_size=None):
        async with self._budget_lock:
            if self._remaining <= 0:
                raise RuntimeError("LLM call budget exhausted")
            self._remaining -= 1
        result = await self._generate_response(messages, response_model, max_tokens=max_tokens, model_size=model_size)
        if response_model is not None:
            if not isinstance(result, Mapping):
                raise ValueError("LLM response must be a JSON object")
            return response_model.model_validate(result).model_dump(mode="json")
        return result


class CommunityGraphiti(Graphiti):
    """Keep every logical group on Neo4j Community's one physical database."""

    def _resolve_request_scope(self, group_id: str | None):
        if not group_id or not group_id.startswith("mf1_"):
            raise ValueError("application-derived group_id required")
        return group_id, self.driver, self.clients


def _native(value: Any) -> Any:
    """Convert Neo4j temporal values before strict DTO validation."""
    if hasattr(value, "to_native"):
        return value.to_native()
    if isinstance(value, dict):
        return {key: _native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_native(item) for item in value)
    return value


def _request_fingerprint(scope: KnowledgeScope, source: SourceEnvelope, ontology: OntologySpec) -> str:
    payload = {
        "scope": scope.model_dump(mode="json"),
        "source": source.model_dump(mode="json"),
        "ontology": ontology.model_dump(mode="json"),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _fact(scope: KnowledgeScope, kind: str, value: Any, *, score: float | None = None, evidence_ids=()) -> FactResult:
    if value.group_id != scope.group_id:
        raise ScopeViolation(f"{kind} outside authorized group")
    attributes = _native(getattr(value, "attributes", None) or {})
    return FactResult(
        provider_id=str(value.uuid), scope=scope, kind=kind,
        name=getattr(value, "name", None), fact=getattr(value, "fact", None),
        source_node_id=getattr(value, "source_node_uuid", None), target_node_id=getattr(value, "target_node_uuid", None),
        episode_ids=tuple(getattr(value, "episodes", None) or ()), evidence_ids=tuple(evidence_ids),
        valid_at=_native(getattr(value, "valid_at", None)), invalid_at=_native(getattr(value, "invalid_at", None)),
        created_at=_native(getattr(value, "created_at", None)), attributes=attributes, score=score,
    )


class GraphitiKnowledgeProvider:
    def __init__(self, config: ProviderConfig | None = None, *, graphiti: Any = None):
        self.config = config
        self.graphiti = graphiti
        self._owned_clients: list[AsyncOpenAI] = []

    async def initialize(self) -> None:
        if self.graphiti is not None:
            await self.graphiti.build_indices_and_constraints()
            return
        config = self.config or ProviderConfig.from_env()
        self.config = config
        if config.search_recipe not in {"hybrid_rrf", "hybrid_cross_encoder"}:
            raise ValueError("unsupported search recipe")
        if config.structured_output_mode not in {"json_object", "json_schema"}:
            raise ValueError("unsupported structured output mode")
        if config.search_recipe == "hybrid_cross_encoder" and config.reranker is None:
            raise ValueError("reranker endpoint required for cross encoder recipe")

        def client(endpoint: Endpoint) -> AsyncOpenAI:
            instance = AsyncOpenAI(api_key=endpoint.api_key.get_secret_value(), base_url=endpoint.base_url, timeout=config.call_timeout_seconds, max_retries=0)
            self._owned_clients.append(instance)
            return instance

        llm = BoundedGenericClient(
            config=LLMConfig(api_key=config.llm.api_key.get_secret_value(), base_url=config.llm.base_url, model=config.llm.model, temperature=0, max_tokens=config.max_tokens),
            client=client(config.llm), max_tokens=config.max_tokens,
            structured_output_mode=config.structured_output_mode, budget=config.total_llm_call_budget,
        )
        embedder = OpenAIEmbedder(
            config=OpenAIEmbedderConfig(api_key=config.embedding.api_key.get_secret_value(), base_url=config.embedding.base_url, embedding_model=config.embedding.model, embedding_dim=config.embedding_dimension),
            client=client(config.embedding),
        )
        # Graphiti constructs an OpenAI reranker if omitted. Give it an explicitly
        # configured route even with RRF, so no hidden default client can escape.
        rerank_endpoint = config.reranker or config.llm
        reranker = OpenAIRerankerClient(
            config=LLMConfig(api_key=rerank_endpoint.api_key.get_secret_value(), base_url=rerank_endpoint.base_url, model=rerank_endpoint.model),
            client=client(rerank_endpoint),
        )
        self.graphiti = CommunityGraphiti(
            uri=config.neo4j_uri, user=config.neo4j_user, password=config.neo4j_password.get_secret_value(),
            llm_client=llm, embedder=embedder, cross_encoder=reranker,
            max_coroutines=config.max_coroutines,
        )
        await self.graphiti.build_indices_and_constraints()

    @property
    def _driver(self):
        if self.graphiti is None:
            raise RuntimeError("initialize() required")
        return self.graphiti.driver

    async def _rows(self, cypher: str, **params):
        # All Cypher is fixed in this module. No caller text enters the query string.
        records, _, _ = await self._driver.execute_query(cypher, params=params, routing_="r")
        return records

    async def _check_episodes(self, scope: KnowledgeScope, ids: tuple[str, ...]) -> None:
        if not ids:
            return
        rows = await self._rows(
            "MATCH (e:Episodic) WHERE e.uuid IN $ids AND e.group_id = $group_id RETURN e.uuid AS uuid",
            ids=list(ids), group_id=scope.group_id,
        )
        if {row["uuid"] for row in rows} != set(ids):
            raise ScopeViolation("result references an episode outside authorized group")

    async def _decorate_fact(
        self, scope: KnowledgeScope, fact: FactResult,
        *, pending_episode: str | None = None,
        pending_evidence: tuple[UUID, ...] = (),
        pending_valid_at: datetime | None = None,
    ) -> FactResult:
        ids = fact.episode_ids
        if fact.kind == "episode":
            ids = (fact.provider_id,)
        elif fact.kind == "node":
            rows = await self._rows(
                "MATCH (e:Episodic {group_id: $group_id})-[:MENTIONS]->(n:Entity {uuid: $uuid, group_id: $group_id}) RETURN DISTINCT e.uuid AS uuid",
                uuid=fact.provider_id, group_id=scope.group_id,
            )
            ids = tuple(row["uuid"] for row in rows)
        await self._check_episodes(scope, ids)
        evidence: set[UUID] = set()
        asserted_valid_at = fact.valid_at
        if ids:
            rows = await self._rows(
                "MATCH (o:MiroFishIngest) WHERE o.uuid IN $ids AND o.group_id = $group_id AND o.status = 'complete' RETURN o.uuid AS uuid, o.evidence_ids AS evidence_ids, o.asserted_valid_at AS asserted_valid_at",
                ids=list(ids), group_id=scope.group_id,
            )
            completed_ids = {row["uuid"] for row in rows}
            allowed_pending = {pending_episode} if pending_episode is not None else set()
            if set(ids) - completed_ids - allowed_pending:
                raise ReconciliationRequired("result includes an incomplete episode")
            for row in rows:
                evidence.update(UUID(item) for item in (row["evidence_ids"] or []))
                if fact.kind == "episode":
                    asserted_valid_at = _native(row["asserted_valid_at"])
            if pending_episode in ids:
                evidence.update(pending_evidence)
                if fact.kind == "episode":
                    asserted_valid_at = pending_valid_at
        return fact.model_copy(update={"episode_ids": ids, "evidence_ids": tuple(sorted(evidence)), "valid_at": asserted_valid_at})

    async def ingest(self, scope: KnowledgeScope, source: SourceEnvelope, ontology: OntologySpec) -> IngestResult:
        if source.ontology_revision != ontology.revision:
            raise ValueError("ontology revision mismatch")
        episode_id = str(scope.episode_uuid(source.operation_id))
        fingerprint = _request_fingerprint(scope, source, ontology)
        rows = await self._rows(
            "MATCH (e:Episodic {uuid: $uuid}) RETURN e.group_id AS group_id, e.content AS content, e.source_description AS sha256",
            uuid=episode_id,
        )
        if rows:
            row = rows[0]
            if row["group_id"] != scope.group_id:
                raise OperationConflict("operation ID exists with a different scope")
            if row["sha256"] != source.source_sha256 or row["content"] != source.content:
                raise OperationConflict("operation ID exists with different request input")
            markers = await self._rows("MATCH (o:MiroFishIngest {uuid: $uuid}) RETURN o.group_id AS group_id, o.fingerprint AS fingerprint, o.status AS status", uuid=episode_id)
            if not markers:
                raise ReconciliationRequired("episode exists without a request marker")
            marker = markers[0]
            if marker["group_id"] != scope.group_id or marker["fingerprint"] != fingerprint:
                raise OperationConflict("operation ID exists with different request metadata")
            if marker["status"] == "complete":
                return IngestResult(episode_id=episode_id, already_exists=True, facts=())
            raise ReconciliationRequired("episode exists but completion is not proven")

        marker = await self._rows("MATCH (o:MiroFishIngest {uuid: $uuid}) RETURN o.uuid AS uuid", uuid=episode_id)
        if marker:
            raise ReconciliationRequired("completion marker exists without its episode")

        # Pinned Graphiti add_episode(uuid=...) looks up an existing episode first.
        # Precreate that exact episode, then mark success separately. A failure
        # after this mutation is deliberately classified as uncertain.
        episode = EpisodicNode(
            uuid=episode_id, name=source.source_name, group_id=scope.group_id,
            labels=[], source=EpisodeType.text if source.source_kind == "document" else EpisodeType.message,
            content=source.content, source_description=source.source_sha256,
            created_at=source.recorded_at, valid_at=source.asserted_valid_at or source.recorded_at,
        )
        entity_types, edge_types, edge_type_map = ontology.graphiti_types()
        try:
            await episode.save(self._driver)
            await self._driver.execute_query(
                "MERGE (o:MiroFishIngest {uuid: $uuid}) SET o.group_id = $group_id, o.sha256 = $sha256, o.fingerprint = $fingerprint, o.status = 'pending'",
                params={"uuid": episode_id, "group_id": scope.group_id, "sha256": source.source_sha256, "fingerprint": fingerprint},
            )
            result = await self.graphiti.add_episode(
                name=source.source_name, episode_body=source.content, source_description=source.source_sha256,
                reference_time=source.asserted_valid_at or source.recorded_at,
                source=episode.source, group_id=scope.group_id, uuid=episode_id,
                entity_types=entity_types, edge_types=edge_types, edge_type_map=edge_type_map,
                update_communities=False,
            )
            if result.episode.uuid != episode_id or result.episode.group_id != scope.group_id:
                raise ScopeViolation("Graphiti returned an unexpected episode identity or scope")
            raw_facts = ([_fact(scope, "episode", result.episode)]
                         + [_fact(scope, "node", node) for node in result.nodes]
                         + [_fact(scope, "edge", edge) for edge in result.edges])
            facts = tuple([
                await self._decorate_fact(scope, fact, pending_episode=episode_id,
                                          pending_evidence=source.evidence_ids,
                                          pending_valid_at=source.asserted_valid_at)
                for fact in raw_facts
            ])
            await self._driver.execute_query(
                "MERGE (o:MiroFishIngest {uuid: $uuid}) SET o.group_id = $group_id, o.sha256 = $sha256, o.fingerprint = $fingerprint, o.status = 'complete', o.evidence_ids = $evidence_ids, o.asserted_valid_at = $asserted_valid_at",
                params={"uuid": episode_id, "group_id": scope.group_id, "sha256": source.source_sha256, "fingerprint": fingerprint, "evidence_ids": [str(item) for item in source.evidence_ids], "asserted_valid_at": source.asserted_valid_at},
            )
            return IngestResult(episode_id=episode_id, already_exists=False, facts=facts)
        except Exception as exc:
            raise ReconciliationRequired(f"ingestion outcome uncertain for {episode_id}") from exc

    async def search(self, scope: KnowledgeScope, query: SearchQuery) -> SearchResult:
        if query.valid_at is not None or query.recorded_before is not None:
            raise UnsupportedCapability("temporal search filters require U03 implementation")
        config = self.config
        recipe = COMBINED_HYBRID_SEARCH_CROSS_ENCODER if config and config.search_recipe == "hybrid_cross_encoder" else COMBINED_HYBRID_SEARCH_RRF
        results = await self.graphiti.search_(query.text, config=recipe.model_copy(update={"limit": query.top_k}), group_ids=[scope.group_id])
        facts: list[FactResult] = []
        for kind, values, scores in (
            ("edge", results.edges, results.edge_reranker_scores),
            ("node", results.nodes, results.node_reranker_scores),
            ("episode", results.episodes, results.episode_reranker_scores),
        ):
            for index, value in enumerate(values):
                fact = _fact(scope, kind, value, score=scores[index] if index < len(scores) else None)
                facts.append(await self._decorate_fact(scope, fact))
        return SearchResult(facts=tuple(facts))

    async def entity(self, scope: KnowledgeScope, provider_id: str) -> SearchResult:
        UUID(provider_id)  # reject arbitrary values before database access
        rows = await self._rows(
            "MATCH (n:Entity {uuid: $uuid, group_id: $group_id}) RETURN properties(n) AS properties",
            uuid=provider_id, group_id=scope.group_id,
        )
        if not rows:
            return SearchResult(facts=())
        data = _native(dict(rows[0]["properties"]))
        facts = [await self._decorate_fact(scope, FactResult(provider_id=data["uuid"], scope=scope, kind="node", name=data.get("name"),
                            created_at=data.get("created_at"), attributes={k: v for k, v in data.items() if k not in {"uuid", "group_id", "name", "created_at", "name_embedding"}}))]
        relations = await self._rows(
            "MATCH (a:Entity)-[r:RELATES_TO]-(b:Entity) WHERE a.uuid = $uuid AND a.group_id = $group_id AND b.group_id = $group_id AND r.group_id = $group_id RETURN properties(r) AS properties, startNode(r).uuid AS source_id, endNode(r).uuid AS target_id LIMIT 100",
            uuid=provider_id, group_id=scope.group_id,
        )
        for row in relations:
            edge = _native(dict(row["properties"]))
            ids = tuple(edge.get("episodes") or ())
            facts.append(await self._decorate_fact(scope, FactResult(provider_id=edge["uuid"], scope=scope, kind="edge", name=edge.get("name"), fact=edge.get("fact"), source_node_id=row["source_id"], target_node_id=row["target_id"], episode_ids=ids, valid_at=edge.get("valid_at"), invalid_at=edge.get("invalid_at"), created_at=edge.get("created_at"), attributes={k: v for k, v in edge.items() if k not in {"uuid", "group_id", "name", "fact", "episodes", "valid_at", "invalid_at", "created_at", "fact_embedding"}})))
        return SearchResult(facts=tuple(facts))

    async def close(self) -> None:
        if self.graphiti is not None:
            await self.graphiti.close()
        for client in self._owned_clients:
            await client.close()

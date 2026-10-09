"""Scoped page provenance shared with Graphiti, without importing model clients."""
from __future__ import annotations
from datetime import datetime
from typing import Any
from uuid import UUID
from .contracts import FactResult, GraphPage, GraphPageRequest, KnowledgeScope
from .provider_errors import ReconciliationRequired, ScopeViolation

def _native(value: Any) -> Any:
    """Convert Neo4j temporal values before strict DTO validation."""
    if hasattr(value, "to_native"):
        return value.to_native()
    if isinstance(value, dict):
        return {key: _native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_native(item) for item in value)
    return value


class GraphPageProvider:
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

    async def page(self, scope: KnowledgeScope, request: GraphPageRequest) -> GraphPage:
        from .graph_reads import page_graph

        return await page_graph(self, scope, request, native=_native)


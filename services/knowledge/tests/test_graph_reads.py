"""Pure scoped page fixtures against the actual enumeration implementation."""

import base64
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from mirofish_knowledge.contracts import FactResult, GraphPage, GraphPageRequest, KnowledgeScope, Layer
from mirofish_knowledge.graph_reads import GraphReadViolation, ResultTooLarge
from mirofish_knowledge.provider import GraphitiKnowledgeProvider, ReconciliationRequired


def scope():
    return KnowledgeScope(workspace_id=uuid4(), project_id=uuid4(), graph_id=uuid4(), layer=Layer.source)


def identifier(number):
    return str(UUID(int=number))


class Rows:
    def __init__(self, one, kind="node", count=3):
        self.scope = one
        self.kind = kind
        self.episode = identifier(1000)
        self.calls = []
        self.incomplete = False
        self.wrong_scope = False
        self.bad_uuid = False
        self.big_name = False
        self.count = count

    async def __call__(self, cypher, **params):
        self.calls.append((cypher, params))
        if "ORDER BY" in cypher:
            rows = []
            for number in range(1, self.count + 1):
                value = identifier(number)
                if params["cursor"] is not None and value <= params["cursor"]:
                    continue
                group = "wrong" if self.wrong_scope and number == 1 else self.scope.group_id
                data = {"uuid": "not-a-uuid" if self.bad_uuid and number == 1 else value,
                        "group_id": group, "name": "x" * 1_048_576 if self.big_name else f"Fact {number}",
                        "created_at": datetime.now(timezone.utc), "name_embedding": [1.0] * 3,
                        "fact_embedding": [1.0] * 3, "custom": "retained"}
                row = {"properties": data, "row_group": group}
                if self.kind == "node":
                    row["labels"] = ["Entity", "Person"]
                elif self.kind == "edge":
                    data.update({"episodes": [self.episode], "fact": "synthetic fact"})
                    row.update(source_id=identifier(201), target_id=identifier(202),
                               source_group=group, target_group=group)
                rows.append(row)
            return rows[:params["limit"]]
        if "MENTIONS" in cypher:
            return [{"uuid": self.episode}]
        if "MATCH (e:Episodic)" in cypher:
            return [{"uuid": value} for value in params["ids"]]
        if "MATCH (o:MiroFishIngest)" in cypher:
            return [] if self.incomplete else [
                {"uuid": value, "evidence_ids": [str(UUID(int=500))], "asserted_valid_at": None}
                for value in params["ids"]
            ]
        raise AssertionError("unexpected query")


def provider(rows):
    subject = GraphitiKnowledgeProvider(graphiti=object())
    subject._rows = rows
    return subject


def token(payload):
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["node", "edge", "episode"])
async def test_keyset_pages_boundaries_changed_limit_and_projection(kind):
    one = scope()
    rows = Rows(one, kind)
    subject = provider(rows)
    first = await subject.page(one, GraphPageRequest(kind=kind, limit=1))
    assert [fact.provider_id for fact in first.facts] == [identifier(1)]
    assert first.next_cursor is not None
    second = await subject.page(one, GraphPageRequest(kind=kind, limit=2, cursor=first.next_cursor))
    assert [fact.provider_id for fact in second.facts] == [identifier(2), identifier(3)]
    assert second.next_cursor is None
    assert all(fact.scope == one and fact.episode_ids and fact.evidence_ids for fact in first.facts + second.facts)
    assert all("name_embedding" not in fact.attributes and "fact_embedding" not in fact.attributes
               and fact.attributes["custom"] == "retained" for fact in first.facts + second.facts)
    main_queries = [(query, params) for query, params in rows.calls if "ORDER BY" in query]
    assert main_queries[0][1]["limit"] == 2 and main_queries[1][1]["limit"] == 3
    assert all(params["group_id"] == one.group_id for _, params in main_queries)
    assert all("$cursor" in query and "$limit" in query for query, _ in main_queries)
    assert len({fact.provider_id for fact in first.facts + second.facts}) == 3


@pytest.mark.asyncio
async def test_node_type_is_bound_and_returned_labels_rechecked():
    one = scope()
    rows = Rows(one)
    subject = provider(rows)
    result = await subject.page(one, GraphPageRequest(kind="node", entity_type="Person", limit=1))
    assert result.facts
    query, params = next((query, params) for query, params in rows.calls if "ORDER BY" in query)
    assert "$entity_type IN labels(n)" in query
    assert "Person" not in query and params["entity_type"] == "Person"
    with pytest.raises(GraphReadViolation):
        await subject.page(one, GraphPageRequest(kind="node", entity_type="Organization", limit=1))


@pytest.mark.asyncio
async def test_cursor_scope_kind_filter_duplicates_and_invalid_request_precede_driver():
    one, other = scope(), scope()
    rows = Rows(one)
    subject = provider(rows)
    first = await subject.page(one, GraphPageRequest(kind="node", limit=1, entity_type="Person"))
    rows.calls.clear()
    for target, request in (
        (other, GraphPageRequest(kind="node", cursor=first.next_cursor, entity_type="Person")),
        (one, GraphPageRequest(kind="edge", cursor=first.next_cursor)),
        (one, GraphPageRequest(kind="node", cursor=first.next_cursor, entity_type="Organization")),
        (one, GraphPageRequest(kind="node", cursor="not+urlsafe")),
        (one, GraphPageRequest(kind="node", cursor=token({"v": 1, "group_id": one.group_id,
                                                        "kind": "node", "entity_type": None,
                                                        "last_uuid": "BAD"}))),
        (one, GraphPageRequest(kind="node", limit=1).model_copy(update={"limit": 0})),
        (one, GraphPageRequest(kind="node").model_copy(update={"kind": "bad"})),
        (one.model_copy(update={"layer": "bad"}), GraphPageRequest(kind="node")),
    ):
        with pytest.raises(ValueError):
            await subject.page(target, request)
    duplicate = base64.urlsafe_b64encode(
        ('{"v":1,"v":1,"group_id":"%s","kind":"node","entity_type":null,"last_uuid":"%s"}'
         % (one.group_id, identifier(1))).encode()).decode().rstrip("=")
    with pytest.raises(ValueError):
        await subject.page(one, GraphPageRequest(kind="node", cursor=duplicate))
    assert rows.calls == []


@pytest.mark.asyncio
async def test_wrong_scope_bad_uuid_and_incomplete_marker_fail_closed():
    one = scope()
    for change, error in (("wrong_scope", GraphReadViolation), ("bad_uuid", GraphReadViolation),
                          ("incomplete", ReconciliationRequired)):
        rows = Rows(one)
        setattr(rows, change, True)
        with pytest.raises(error):
            await provider(rows).page(one, GraphPageRequest(kind="node"))
    edge_rows = Rows(one, kind="edge")
    original = edge_rows.__call__

    async def wrong_endpoint(cypher, **params):
        values = await original(cypher, **params)
        if "ORDER BY" in cypher and values:
            values[0]["target_group"] = "wrong"
        return values

    subject = provider(edge_rows)
    subject._rows = wrong_endpoint
    with pytest.raises(GraphReadViolation):
        await subject.page(one, GraphPageRequest(kind="edge"))


@pytest.mark.asyncio
async def test_out_of_order_rows_and_missing_episode_provenance_fail_closed():
    one = scope()
    ordered = Rows(one)
    original = ordered.__call__

    async def reversed_page(cypher, **params):
        values = await original(cypher, **params)
        return list(reversed(values)) if "ORDER BY" in cypher else values

    subject = provider(ordered)
    subject._rows = reversed_page
    with pytest.raises(GraphReadViolation):
        await subject.page(one, GraphPageRequest(kind="node", limit=2))

    missing = Rows(one)
    original_missing = missing.__call__

    async def no_mentions(cypher, **params):
        return [] if "MENTIONS" in cypher else await original_missing(cypher, **params)

    subject = provider(missing)
    subject._rows = no_mentions
    with pytest.raises(GraphReadViolation):
        await subject.page(one, GraphPageRequest(kind="node", limit=1))


@pytest.mark.asyncio
async def test_empty_scope_budget_and_no_model_dispatch():
    one = scope()
    empty = Rows(one, count=0)
    result = await provider(empty).page(one, GraphPageRequest(kind="episode"))
    assert result.facts == () and result.next_cursor is None
    assert all("add_episode" not in query and "search" not in query.lower() for query, _ in empty.calls)
    huge = Rows(one, count=1)
    huge.big_name = True
    with pytest.raises(ResultTooLarge):
        await provider(huge).page(one, GraphPageRequest(kind="node"))


def test_request_types_filter_and_bounds():
    for kwargs in ({"kind": "edge", "entity_type": "Person"},
                   {"kind": "node", "entity_type": "Person`) MATCH (n)"},
                   {"kind": "node", "limit": 0}, {"kind": "node", "limit": 101},
                   {"kind": "node", "limit": True}, {"kind": "node", "cursor": "x" * 1025}):
        with pytest.raises(ValueError):
            GraphPageRequest(**kwargs)
    with pytest.raises(ValueError):
        GraphPage(facts=(), next_cursor="x" * 1025)
    fact = FactResult(provider_id=identifier(1), scope=scope(), kind="node")
    with pytest.raises(ValueError):
        GraphPage(facts=(fact,) * 101)

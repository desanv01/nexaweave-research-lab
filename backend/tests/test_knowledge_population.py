"""Source-level characterization of the inherited basic OASIS population path."""

import csv
import builtins
import io
import json
import random
from uuid import UUID

import pytest

from app.services.knowledge_population import KnowledgePopulation, PopulationError
from app.services.knowledge_reader import EntityNode
from app.services.oasis_profile_generator import OasisProfileGenerator


def uid(number):
    return str(UUID(int=number))


def graph():
    return {
        "graph_id": "display-1",
        "nodes": [
            {"uuid": uid(11), "name": "机构", "labels": ["Entity", "Organization"],
             "summary": "组织摘要", "attributes": {}, "episodes": [uid(801)],
             "evidence_ids": [uid(901)]},
            {"uuid": uid(10), "name": "艾丽丝", "labels": ["Entity", "Person"],
             "summary": "关于技术的观点\n第二行", "attributes": {"city": "香港"},
             "episodes": [uid(800)], "evidence_ids": [uid(900)]},
            {"uuid": uid(12), "name": "未标注", "labels": ["Entity"],
             "summary": "skip", "attributes": {}, "episodes": [], "evidence_ids": []},
        ],
        "edges": [{"uuid": uid(20), "name": "KNOWS", "source_node_uuid": uid(10),
                   "target_node_uuid": uid(11), "fact": "认识", "episodes": [uid(802)],
                   "evidence_ids": [uid(902)]}],
    }


def test_basic_population_is_grounded_sorted_and_request_local():
    state = random.getstate()
    population = KnowledgePopulation(graph())
    first = population.build(seed=42).preview
    second = population.build(seed=42).preview
    assert first == second
    assert random.getstate() == state
    assert [profile["source_entity_uuid"] for profile in first["profiles"]] == [uid(10), uid(11)]
    assert first["eligible_count"] == 2 and first["selected_count"] == 2
    assert first["generator"] == "inherited_rule_based_v1"
    assert first["enrichment"] == "none" and first["llm_used"] is False
    assert first["simulation_executed"] is False and first["snapshot_consistent"] is False
    assert first["grounding"][uid(10)]["labels"] == ["Entity", "Person"]
    assert first["grounding"][uid(10)]["evidence_ids"] == [uid(900)]
    assert first["grounding"][uid(10)]["facts"][0]["evidence_ids"] == [uid(902)]
    assert first["profiles"][0]["source_entity_type"] == "Person"
    assert first["profiles"][1]["gender"] == "other"
    assert population.build(types=["Organization"], max_agents=1).preview["profiles"][0][
        "source_entity_uuid"] == uid(11)
    with pytest.raises(PopulationError) as empty:
        population.build(types=["Faculty"])
    assert empty.value.code == "empty_selection"


def test_basic_mode_rejects_llm_and_exports_inherited_formats(monkeypatch):
    generator = OasisProfileGenerator(basic_only=True, rng=random.Random(3))
    assert generator.client is None and generator.zep_client is None
    entity = EntityNode(uid(10), "艾丽丝", ["Entity", "Person"], "摘要", {})
    with pytest.raises(ValueError, match="forbids model"):
        generator.generate_profile_from_entity(entity, 0, use_llm=True)
    imported = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.endswith("zep") or name.startswith("zep_cloud"):
            raise AssertionError("basic helper imported Zep")
        return imported(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    assert generator._search_zep_for_entity(entity) == {
        "facts": [], "node_summaries": [], "context": ""}
    monkeypatch.setattr(builtins, "__import__", imported)

    population = KnowledgePopulation(graph())
    preview = population.build(seed=12).preview
    csv_body, csv_type, csv_name = population.export(platform="twitter", seed=12)
    rows = list(csv.DictReader(io.StringIO(csv_body.decode("utf-8"), newline="")))
    assert csv_type == "text/csv; charset=utf-8"
    assert csv_name == "oasis-twitter-profiles.csv"
    assert list(rows[0]) == ["user_id", "name", "username", "user_char", "description"]
    assert rows[0]["name"] == "艾丽丝" and rows[0]["user_id"] == "0"
    assert rows[0]["username"] == preview["profiles"][0]["user_name"]
    assert "\n" not in rows[0]["description"]
    json_body, json_type, json_name = population.export(platform="reddit", seed=12)
    reddit = json.loads(json_body)
    assert json_type == "application/json; charset=utf-8"
    assert json_name == "oasis-reddit-profiles.json"
    assert reddit[0]["username"] == preview["profiles"][0]["user_name"]
    assert reddit[0]["persona"] == preview["profiles"][0]["persona"]
    assert len(csv_body) <= 2 * 1024 * 1024 and len(json_body) <= 2 * 1024 * 1024


def test_population_size_limit_is_fixed():
    oversized = graph()
    oversized["nodes"][0]["summary"] = "中" * 700_000
    with pytest.raises(PopulationError) as error:
        KnowledgePopulation(oversized).build()
    assert error.value.code == "result_too_large"


def test_duplicate_inherited_usernames_are_disambiguated_consistently(monkeypatch):
    monkeypatch.setattr(OasisProfileGenerator, "_generate_username", lambda self, name: "same_123")
    population = KnowledgePopulation(graph())
    preview = population.build(seed=7).preview
    names = [profile["user_name"] for profile in preview["profiles"]]
    assert names == ["same_123", "same_123_2"]
    twitter = population.export(platform="twitter", seed=7)[0]
    reddit = population.export(platform="reddit", seed=7)[0]
    assert [row["username"] for row in csv.DictReader(io.StringIO(twitter.decode()))] == names
    assert [profile["username"] for profile in json.loads(reddit)] == names

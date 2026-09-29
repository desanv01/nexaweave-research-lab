"""In-memory wire fixtures for the bounded backend knowledge reader."""

import ast
import json
from pathlib import Path
from uuid import UUID

import pytest

from app.services import knowledge_reader as module
from app.services.knowledge_reader import KnowledgeGraphReader, KnowledgeReadError, ReadLimits


def uid(number):
    return str(UUID(int=number))


def scope():
    return {"schema_version": 1, "workspace_id": uid(1), "project_id": uid(2),
            "graph_id": uid(3), "run_id": None, "branch_id": None, "layer": "source"}


def fact(number, kind="node", **changes):
    result = {"schema_version": 1, "provider_id": uid(number), "scope": scope(), "kind": kind,
              "name": "人物" if kind == "node" else "KNOWS", "fact": None if kind == "node" else "认识",
              "source_node_id": None if kind == "node" else uid(10),
              "target_node_id": None if kind == "node" else uid(11),
              "episode_ids": [uid(800)], "evidence_ids": [uid(900)],
              "labels": ["Entity", "Person"] if kind == "node" else [],
              "summary": "摘要" if kind == "node" else None,
              "valid_at": "2024-01-01T00:00:00Z", "invalid_at": None,
              "expired_at": "2024-03-01T00:00:00Z" if kind == "edge" else None,
              "created_at": "2024-01-02T00:00:00Z", "attributes": {"city": "香港"}, "score": None}
    result.update(changes)
    return result


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


class MemoryClient:
    def __init__(self, pages=None, mutate=None):
        self.pages = pages or {"node": [([], None)], "edge": [([], None)]}
        self.mutate = mutate
        self.calls = []

    def call(self, raw):
        request = json.loads(raw)
        self.calls.append(request)
        kind = request["payload"]["kind"]
        index = len([call for call in self.calls if call["payload"]["kind"] == kind]) - 1
        facts, cursor = self.pages[kind][index]
        reply = {"version": 1, "request_id": request["request_id"], "ok": True,
                 "result": {"schema_version": 1, "facts": facts, "next_cursor": cursor}}
        return self.mutate(reply) if self.mutate else encoded(reply)


def reader(client=None, **kwargs):
    return KnowledgeGraphReader(client or MemoryClient(), scope=scope(), graph_id="display_1", **kwargs)


def fails(code, action):
    with pytest.raises(KnowledgeReadError) as error:
        action()
    assert error.value.code == code
    assert "private" not in str(error.value)


def test_complete_graph_wiring_metadata_and_projection():
    nodes = [fact(10), fact(11, name=None, summary=None, labels=["Entity", "Company"])]
    edge = fact(20, "edge", invalid_at="2024-02-01T00:00:00Z")
    client = MemoryClient({"node": [([nodes[0]], "cursor-1"), ([nodes[1]], None)],
                           "edge": [([edge], None)]})
    bound = scope()
    subject = KnowledgeGraphReader(client, scope=bound, graph_id="display_1")
    bound["workspace_id"] = uid(999)
    data = subject.get_graph_data("display_1")
    assert data["graph_id"] == "display_1" and (data["node_count"], data["edge_count"]) == (2, 1)
    assert data["nodes"][0]["labels"] == ["Entity", "Person"]
    assert data["nodes"][0]["summary"] == "摘要" and data["nodes"][0]["evidence_ids"] == [uid(900)]
    assert data["nodes"][1]["name"] == "" and data["nodes"][1]["summary"] == ""
    projected = data["edges"][0]
    assert projected["source_node_name"] == "人物" and projected["target_node_name"] == ""
    assert projected["fact_type"] == "KNOWS" and projected["fact"] == "认识"
    assert projected["episodes"] == [uid(800)] and projected["evidence_ids"] == [uid(900)]
    assert projected["expired_at"] == "2024-03-01T00:00:00Z"
    assert projected["invalid_at"] == "2024-02-01T00:00:00Z"
    assert [call["payload"]["cursor"] for call in client.calls] == [None, "cursor-1", None]
    assert all(call["method"] == "page" and call["version"] == 1 and call["scope"] == scope()
               and call["payload"]["limit"] == 100 and call["payload"]["entity_type"] is None
               for call in client.calls)
    assert len({call["request_id"] for call in client.calls}) == 3


def test_filter_direction_self_loop_and_no_edge_mode():
    nodes = [fact(10), fact(11, labels=["Entity", "Company"]),
             fact(12, labels=["Entity"])]
    edges = [fact(20, "edge", source_node_id=uid(10), target_node_id=uid(11)),
             fact(21, "edge", source_node_id=uid(11), target_node_id=uid(10)),
             fact(22, "edge", source_node_id=uid(10), target_node_id=uid(10))]
    pages = {"node": [(nodes, None)], "edge": [(edges, None)]}
    result = reader(MemoryClient(pages)).filter_defined_entities("display_1")
    assert result.total_count == 3 and result.filtered_count == 2
    assert result.entity_types == {"Person", "Company"}
    person = result.entities[0]
    assert person.get_entity_type() == "Person"
    assert [item["direction"] for item in person.related_edges] == ["outgoing", "incoming", "outgoing"]
    assert [item["uuid"] for item in person.related_nodes] == [uid(10), uid(11)]
    assert person.to_dict()["summary"] == "摘要"
    client = MemoryClient(pages)
    bare = reader(client).get_entities_by_type("display_1", "Company", enrich_with_edges=False)
    assert len(bare) == 1 and bare[0].related_edges == []
    assert [call["payload"]["kind"] for call in client.calls] == ["node"]
    assert result.to_dict()["entity_types"] == ["Company", "Person"]


def test_optional_filter_membership_and_entity_type_remain_distinct():
    node = fact(10, labels=["Entity", "Alpha", "Beta"])
    pages = {"node": [([node], None)]}
    selected = reader(MemoryClient(pages)).filter_defined_entities(
        "display_1", ["Beta"], enrich_with_edges=False)
    assert selected.filtered_count == 1 and selected.entity_types == {"Beta"}
    assert selected.entities[0].get_entity_type() == "Alpha"
    unfiltered = reader(MemoryClient(pages)).filter_defined_entities(
        "display_1", [], enrich_with_edges=False)
    assert unfiltered.filtered_count == 1 and unfiltered.entity_types == {"Alpha"}


def test_context_not_found_only_after_complete_nodes_and_empty_graph():
    client = MemoryClient()
    subject = reader(client)
    assert subject.get_entity_with_context("display_1", uid(10)) is None
    assert [call["payload"]["kind"] for call in client.calls] == ["node"]
    assert reader().get_graph_data("display_1")["node_count"] == 0
    assert reader().get_all_edges("display_1") == []


def test_context_scans_more_than_100_relations_and_shares_budget():
    edges = [fact(1000 + number, "edge", source_node_id=uid(10), target_node_id=uid(11))
             for number in range(101)]
    pages = {"node": [([fact(10), fact(11)], None)],
             "edge": [(edges[:100], "edge-cursor"), (edges[100:], None)]}
    client = MemoryClient(pages)
    context = reader(client, limits=ReadLimits(max_pages=3, max_facts=103)).get_entity_with_context(
        "display_1", uid(10))
    assert context is not None and len(context.related_edges) == 101
    assert context.related_nodes[0]["uuid"] == uid(11)
    assert [call["payload"]["kind"] for call in client.calls] == ["node", "edge", "edge"]
    fails("limit_exceeded", lambda: reader(MemoryClient(pages), limits=ReadLimits(max_pages=2))
          .get_entity_with_context("display_1", uid(10)))
    fails("limit_exceeded", lambda: reader(MemoryClient(pages), limits=ReadLimits(max_facts=102))
          .get_entity_with_context("display_1", uid(10)))


def test_bound_ids_and_limits_fail_before_client_call():
    client = MemoryClient()
    subject = reader(client)
    fails("invalid_request", lambda: subject.get_all_nodes("other"))
    fails("invalid_request", lambda: subject.get_entity_with_context("display_1", "bad"))
    fails("invalid_request", lambda: subject.get_entities_by_type("display_1", "bad-type"))
    assert client.calls == []
    for bad in (True, 0, 2001):
        with pytest.raises(ValueError):
            ReadLimits(max_pages=bad)
    for bad in (False, 0, float("nan"), 301):
        with pytest.raises(ValueError):
            ReadLimits(timeout_seconds=bad)
    for bad_scope in ({**scope(), "extra": "x"}, {**scope(), "run_id": "bad"},
                      {**scope(), "schema_version": True}):
        with pytest.raises(ValueError):
            KnowledgeGraphReader(client, scope=bad_scope, graph_id="display_1")
    for bad_id in ("", "a/b", "é", "x" * 129):
        with pytest.raises(ValueError):
            KnowledgeGraphReader(client, scope=scope(), graph_id=bad_id)


@pytest.mark.parametrize("mutation", [
    lambda reply: {**reply, "version": True},
    lambda reply: {**reply, "request_id": uid(999)},
    lambda reply: {**reply, "ok": 1},
    lambda reply: {**reply, "extra": "private"},
    lambda reply: {**reply, "result": {**reply["result"], "schema_version": 2}},
    lambda reply: {**reply, "result": {**reply["result"], "next_cursor": ""}},
    lambda reply: {**reply, "result": {**reply["result"], "facts": [fact(10, scope={**scope(), "layer": "analysis"})]}},
    lambda reply: {**reply, "result": {**reply["result"], "facts": [fact(10, kind="edge")]}},
    lambda reply: {**reply, "result": {**reply["result"], "facts": [fact(10, provider_id="bad")]}},
    lambda reply: {**reply, "result": {**reply["result"], "facts": [fact(10, episode_ids=[])]}},
    lambda reply: {**reply, "result": {**reply["result"], "facts": [fact(10, labels="Person")]}},
    lambda reply: {**reply, "result": {**reply["result"], "facts": [fact(10, summary="x" * 32769)]}},
    lambda reply: {**reply, "result": {**reply["result"], "facts": [fact(10, evidence_ids=["bad"])]}},
    lambda reply: {**reply, "result": {**reply["result"], "facts": [fact(10, attributes=[])]}},
    lambda reply: {**reply, "result": {**reply["result"], "facts": [fact(10, score=True)]}},
    lambda reply: {**reply, "result": {**reply["result"], "facts": [fact(10, created_at="yesterday")]}},
])
def test_malformed_envelopes_and_facts_are_fixed_errors(mutation):
    client = MemoryClient(mutate=lambda reply: encoded(mutation(reply)))
    fails("invalid_reply", lambda: reader(client).get_all_nodes("display_1"))
    assert len(client.calls) == 1


@pytest.mark.parametrize("raw", [b'{"version":1,"version":1}', b'{"x":NaN}', b'{"x":Infinity}',
                                     b'x' * (2 * 1024 * 1024 + 1)],
                         ids=["duplicate", "nan", "infinity", "oversize"])
def test_bad_json_duplicate_nonfinite_depth_or_oversize(raw):
    client = MemoryClient(mutate=lambda reply: raw)
    fails("invalid_reply", lambda: reader(client).get_all_nodes("display_1"))


def test_valid_json_with_excessive_depth_is_rejected():
    nested = {}
    for _ in range(33):
        nested = {"child": nested}
    client = MemoryClient(mutate=lambda reply: encoded(nested))
    fails("invalid_reply", lambda: reader(client).get_all_nodes("display_1"))


@pytest.mark.parametrize("mutate", [
    lambda reply: reply["result"].update(next_cursor="\ud800"),
    lambda reply: reply["result"].update(facts=[fact(10, summary="private\ud800")]),
    lambda reply: reply["result"].update(facts=[fact(10, attributes={"private\ud800": 1})]),
    lambda reply: reply["result"].update(facts=[fact(10, attributes={"nested": ["private\ud800"]})]),
], ids=["cursor", "metadata", "nested_key", "nested_value"])
def test_surrogate_reply_fails_before_cursor_reuse(mutate):
    def bad(reply):
        mutate(reply)
        return json.dumps(reply, ensure_ascii=True).encode("utf-8")

    client = MemoryClient(mutate=bad)
    fails("invalid_reply", lambda: reader(client).get_all_nodes("display_1"))
    assert len(client.calls) == 1


def test_supplementary_unicode_is_preserved():
    client = MemoryClient({"node": [([fact(10, summary="研究📈", attributes={"emoji📈": "值📈"})], None)]})
    node = reader(client).get_all_nodes("display_1")[0]
    assert node["summary"] == "研究📈" and node["attributes"]["emoji📈"] == "值📈"


def test_remote_error_transport_failure_and_no_retry():
    client = MemoryClient(mutate=lambda reply: encoded({"version": 1, "request_id": reply["request_id"],
                                                       "ok": False, "error": {"code": "unauthorized"}}))
    fails("unauthorized", lambda: reader(client).get_all_nodes("display_1"))
    assert len(client.calls) == 1

    class Broken:
        calls = 0

        def call(self, raw):
            self.calls += 1
            raise RuntimeError("private transport secret")

    broken = Broken()
    fails("transport_failure", lambda: reader(broken).get_all_nodes("display_1"))
    assert broken.calls == 1


@pytest.mark.parametrize("pages", [
    [([fact(10)], "same"), ([fact(11)], "same")],
    [([fact(10)], "next"), ([fact(10)], None)],
    [([fact(11), fact(10)], None)],
    [([], "next")],
])
def test_cycles_duplicate_regressed_or_empty_continuation(pages):
    client = MemoryClient({"node": pages})
    fails("invalid_reply", lambda: reader(client).get_all_nodes("display_1"))


def test_server_page_fact_count_cannot_exceed_100():
    client = MemoryClient({"node": [([fact(number) for number in range(10, 111)], None)]})
    fails("invalid_reply", lambda: reader(client).get_all_nodes("display_1"))


def test_orphan_edge_fails_combined_graph_and_context():
    pages = {"node": [([fact(10)], None)],
             "edge": [([fact(20, "edge", target_node_id=uid(99))], None)]}
    fails("inconsistent_graph", lambda: reader(MemoryClient(pages)).get_graph_data("display_1"))
    fails("inconsistent_graph", lambda: reader(MemoryClient(pages)).get_entity_with_context("display_1", uid(10)))


def test_shared_page_fact_and_byte_budgets():
    pages = {"node": [([fact(10)], None)], "edge": [([fact(20, "edge")], None)]}
    fails("limit_exceeded", lambda: reader(MemoryClient(pages), limits=ReadLimits(max_pages=1)).get_graph_data("display_1"))
    fails("limit_exceeded", lambda: reader(MemoryClient(pages), limits=ReadLimits(max_facts=1)).get_graph_data("display_1"))
    probe = MemoryClient({"node": [([fact(10)], None)]})
    probe.get_bytes = 0

    def capture(reply):
        raw = encoded(reply)
        probe.get_bytes = len(raw)
        return raw

    probe.mutate = capture
    reader(probe).get_all_nodes("display_1")
    size = probe.get_bytes
    assert reader(MemoryClient({"node": [([fact(10)], None)]}),
                  limits=ReadLimits(max_reply_bytes=size)).get_all_nodes("display_1")
    fails("limit_exceeded", lambda: reader(MemoryClient({"node": [([fact(10)], None)]}),
                                           limits=ReadLimits(max_reply_bytes=size - 1)).get_all_nodes("display_1"))


def test_exact_page_fact_and_shared_byte_boundaries():
    pages = {"node": [([fact(10)], None)],
             "edge": [([fact(20, "edge", source_node_id=uid(10), target_node_id=uid(10))], None)]}
    totals = []

    def capture(reply):
        raw = encoded(reply)
        totals.append(len(raw))
        return raw

    expected = reader(MemoryClient(pages, capture),
                      limits=ReadLimits(max_pages=2, max_facts=2)).get_graph_data("display_1")
    assert expected["edge_count"] == 1 and len(totals) == 2
    total = sum(totals)
    assert reader(MemoryClient(pages), limits=ReadLimits(max_reply_bytes=total)).get_graph_data("display_1")
    fails("limit_exceeded", lambda: reader(MemoryClient(pages),
                                           limits=ReadLimits(max_reply_bytes=total - 1)).get_graph_data("display_1"))


def test_unknown_remote_error_and_exception_privacy():
    client = MemoryClient(mutate=lambda reply: encoded({"version": 1, "request_id": reply["request_id"],
                                                       "ok": False, "error": {"code": "private"}}))
    fails("invalid_reply", lambda: reader(client).get_all_nodes("display_1"))


def test_deadline_before_and_after_call(monkeypatch):
    ticks = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    client = MemoryClient()
    fails("timeout", lambda: reader(client, limits=ReadLimits(timeout_seconds=1)).get_all_nodes("display_1"))
    assert len(client.calls) == 1


def test_deadline_before_call_and_during_projection(monkeypatch):
    client = MemoryClient()
    ticks = iter([0.0, 1.0])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    fails("timeout", lambda: reader(client, limits=ReadLimits(timeout_seconds=1)).get_all_nodes("display_1"))
    assert client.calls == []

    client = MemoryClient({"node": [([fact(10)], None)]})
    ticks = iter([0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
    fails("timeout", lambda: reader(client, limits=ReadLimits(timeout_seconds=1)).get_all_nodes("display_1"))
    assert len(client.calls) == 1


@pytest.mark.parametrize("failure", [KeyboardInterrupt, SystemExit], ids=["interrupt", "system_exit"])
def test_process_control_exceptions_pass_through(failure):
    class Interrupted:
        def call(self, raw):
            raise failure()

    with pytest.raises(failure):
        reader(Interrupted()).get_all_nodes("display_1")


def test_reader_module_imports_stdlib_only():
    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.names[0].name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)}
    names.update(node.module.split(".")[0] for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module)
    assert names <= {"__future__", "json", "math", "re", "time", "dataclasses", "datetime",
                     "typing", "unicodedata", "uuid"}

"""Offline source fixtures for inherited graph-bound report research."""

import json
import math
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID

import pytest

from app.services.knowledge_read_facade import KnowledgeReadFacade, ReadHostSettings
from app.services.knowledge_report_tools import NeutralCapabilityError
from app.services.report_agent import ReportManager, ReportStatus
from app.services.zep_tools import InterviewResult


def uid(number):
    return str(UUID(int=number))


SCOPE = {"schema_version": 1, "workspace_id": uid(1), "project_id": uid(2),
         "graph_id": uid(3), "run_id": None, "branch_id": None, "layer": "source"}


def fact(number, kind="node", **changes):
    value = {"schema_version": 1, "provider_id": uid(number), "scope": SCOPE,
             "kind": kind, "name": "艾丽丝" if kind == "node" else "KNOWS",
             "fact": None if kind == "node" else "艾丽丝认识机构",
             "source_node_id": None if kind == "node" else uid(10),
             "target_node_id": None if kind == "node" else uid(11),
             "episode_ids": [uid(800)], "evidence_ids": [uid(900)],
             "labels": ["Entity", "Person"] if kind == "node" else [],
             "summary": "公开摘要" if kind == "node" else None,
             "valid_at": "2024-01-01T00:00:00Z", "invalid_at": None,
             "expired_at": "2024-03-01T00:00:00Z" if kind == "edge" else None,
             "created_at": "2024-01-02T00:00:00Z", "attributes": {}, "score": None}
    value.update(changes)
    return value


class ByteClient:
    def __init__(self, scope=SCOPE):
        self.calls = []
        self.changed = False
        self.scope = scope

    def call(self, raw):
        request = json.loads(raw)
        self.calls.append(request)
        facts = ([fact(10, scope=self.scope, summary="changed" if self.changed else "公开摘要"),
                  fact(11, scope=self.scope, name="机构", labels=["Entity", "Organization"], summary="机构摘要")]
                 if request["payload"]["kind"] == "node" else
                 [fact(20, "edge", scope=self.scope,
                       fact="changed" if self.changed else "艾丽丝认识机构")])
        return json.dumps({"version": 1, "request_id": request["request_id"], "ok": True,
                           "result": {"schema_version": 1, "facts": facts,
                                      "next_cursor": None}}, ensure_ascii=False).encode()


class ScriptedModel:
    def __init__(self, *, partial_outline=False, empty_section=False, partial_section=False):
        self.partial_outline = partial_outline
        self.empty_section = empty_section
        self.partial_section = partial_section
        self.json_calls = 0
        self.report_calls = 0
        self.chat_calls = 0
        self.mode = "report"

    def chat_json(self, messages, **_kwargs):
        self.json_calls += 1
        if self.json_calls == 1:
            if self.partial_outline:
                return {"title": "部分大纲", "summary": "", "sections": []}
            return {"title": "调查报告", "summary": "来源图谱研究",
                    "sections": [{"title": "证据"}, {"title": "时序"}]}
        return {"sub_queries": ["关系如何形成"]}

    def chat(self, messages, **_kwargs):
        if self.mode == "chat":
            self.chat_calls += 1
            if self.chat_calls == 1:
                return '<tool_call>{"name":"quick_search","parameters":{"query":"关系"}}</tool_call>'
            return "根据来源事实继续研究。"
        self.report_calls += 1
        if self.empty_section:
            return None
        slot = (self.report_calls - 1) % 4
        if slot == 0:
            return '<tool_call>{"name":"quick_search","parameters":{"query":"关系"}}</tool_call>'
        if slot == 1:
            return '<tool_call>{"name":"panorama_search","parameters":{"query":"时序"}}</tool_call>'
        if slot == 2:
            return '<tool_call>{"name":"insight_forge","parameters":{"query":"关系演变"}}</tool_call>'
        if self.partial_section:
            return "未完成的章节文本"
        return "Final Answer: 来源图谱显示关系及其时序；没有模拟观察。"


def selector(*, graph_id, bound_scope, query, scope, limit):
    assert graph_id == "display-1" and bound_scope == SCOPE
    assert 1 <= limit <= 50 and query
    kind, identifier = ("node", uid(10)) if scope == "nodes" else ("edge", uid(20))
    return [{"id": identifier, "kind": kind, "score": 0.75, "bound_scope": bound_scope}]


def agent_for(model=None, search=selector, interview=None, scope=SCOPE, changed=False):
    transport = ByteClient(scope)
    transport.changed = changed
    settings = ReadHostSettings("python", "read_bootstrap.py", "fixture-token", "owner",
                                "display-1", scope, {})
    facade = KnowledgeReadFacade(settings, client_factory=lambda: transport)
    agent = facade.report_agent("display-1", "sim_fixture", "研究关系", model_client=model or ScriptedModel(),
                               search_selector=search, interview_capability=interview)
    return agent, transport, facade


def test_inherited_multisection_research_chat_and_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(tmp_path))
    model = ScriptedModel()
    agent, transport, _ = agent_for(model)
    transport.changed = True
    progress = []
    report = agent.generate_report(report_id="report_fixture",
                                   progress_callback=lambda *parts: progress.append(parts[0]))
    assert report.status is ReportStatus.COMPLETED
    assert len(report.outline.sections) == 2 and model.report_calls == 8
    assert model.json_calls >= 3
    assert (tmp_path / "report_fixture" / "section_01.md").exists()
    assert (tmp_path / "report_fixture" / "section_02.md").exists()
    assert "来源图谱显示关系" in (tmp_path / "report_fixture" / "full_report.md").read_text(encoding="utf-8")
    ledger_path = tmp_path / "report_fixture" / "retrieval_evidence.json"
    metadata = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert metadata["scope"] == SCOPE and metadata["claim_citations_validated"] is False
    assert metadata["semantic_search"] == "trusted_selection_only"
    assert metadata["live_semantic_adapter_connected"] is False
    assert len(metadata["retrievals"]) >= 6
    assert all(item["evidence_ids"] == [uid(900)] and item["scope"] == SCOPE
               for item in metadata["retrievals"])
    assert any(item["access"] == "semantic_selection" and item["id"] == uid(20)
               for item in metadata["retrievals"])
    assert any(item["access"] == "projection_read" and item["kind"] == "node"
               for item in metadata["retrievals"])
    assert [call["payload"]["kind"] for call in transport.calls] == ["node", "edge"]
    assert "completed" in progress
    model.mode = "chat"
    reply = agent.chat("后续问题")
    assert "继续研究" in reply["response"] and len(reply["tool_calls"]) == 1
    assert len(json.loads(ledger_path.read_text(encoding="utf-8"))["retrievals"]) > len(metadata["retrievals"])


def test_inherited_panorama_insight_and_bounded_search_contract():
    model = ScriptedModel()
    model.json_calls = 1
    agent, _, _ = agent_for(model)
    tools = agent.zep_tools
    panorama = tools.panorama_search("display-1", "关系")
    assert panorama.historical_count == 1 and "非模拟观察" in panorama.to_text()
    insight = tools.insight_forge("display-1", "关系", "研究关系")
    assert insight.sub_queries == ["关系如何形成"]
    assert insight.total_entities == 2 and insight.total_relationships == 1
    assert "艾丽丝认识机构" in insight.semantic_facts
    assert tools.quick_search("display-1", "关系").edges[0]["uuid"] == uid(20)
    assert tools.get_node_detail(uid(10)).summary == "公开摘要"
    with pytest.raises(NeutralCapabilityError, match="unknown_entity"):
        tools.get_node_detail(uid(99))


@pytest.mark.parametrize("bad", [
    [{"id": uid(20), "kind": "edge", "score": 1.0, "bound_scope": SCOPE}] * 2,
    [{"id": uid(99), "kind": "edge", "score": 1.0, "bound_scope": SCOPE}],
    [{"id": uid(20), "kind": "node", "score": 1.0, "bound_scope": SCOPE}],
    [{"id": uid(20), "kind": "edge", "score": math.nan, "bound_scope": SCOPE}],
    [{"id": uid(20), "kind": "edge", "score": 1.0, "bound_scope": SCOPE,
      "fact": "fabricated by model"}],
    [{"id": uid(20), "kind": "edge", "score": 1.0,
      "bound_scope": {**SCOPE, "layer": "simulation"}}],
])
def test_invalid_semantic_selections_never_resolve_foreign_content(bad):
    agent, _, _ = agent_for(search=lambda **_kwargs: bad)
    with pytest.raises(NeutralCapabilityError, match="invalid_selection"):
        agent.zep_tools.search_graph("display-1", "query", limit=10)
    assert agent.zep_tools.retrieval_ledger == []


def test_missing_failed_search_and_partial_model_fail_report(tmp_path, monkeypatch):
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(tmp_path))
    for name, search, model in [
        ("missing", None, ScriptedModel()),
        ("failure", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("private token")), ScriptedModel()),
        ("outline", selector, ScriptedModel(partial_outline=True)),
        ("section", selector, ScriptedModel(empty_section=True)),
        ("partial", selector, ScriptedModel(partial_section=True)),
    ]:
        agent, _, _ = agent_for(model, search)
        report = agent.generate_report(report_id="report_" + name)
        assert report.status is ReportStatus.FAILED
        assert report.error in {"unsupported", "search_unavailable", "invalid_outline",
                                "section_failed", "section_incomplete"}
        assert "private token" not in json.dumps(report.to_dict())
        assert not (tmp_path / ("report_" + name) / "full_report.md").exists()


def test_graph_and_interview_binding_are_explicit():
    agent, _, facade = agent_for()
    with pytest.raises(NeutralCapabilityError, match="graph_mismatch"):
        agent.zep_tools.search_graph("other", "query")
    with pytest.raises(NeutralCapabilityError, match="graph_mismatch"):
        facade.report_agent("other", "sim_fixture", "requirement", model_client=ScriptedModel(),
                            search_selector=selector)
    with pytest.raises(NeutralCapabilityError, match="unsupported"):
        agent.zep_tools.interview_agents("sim_fixture", "topic")
    called = []
    def interview(**kwargs):
        called.append(kwargs)
        return InterviewResult(interview_topic=kwargs["interview_requirement"],
                               interview_questions=[], interviewed_count=0)
    bound, _, _ = agent_for(interview=interview)
    with pytest.raises(NeutralCapabilityError, match="simulation_mismatch"):
        bound.zep_tools.interview_agents("other", "topic")
    assert not called
    assert "深度采访报告" in bound._execute_tool("interview_agents", {"interview_topic": "topic"})
    assert called[0]["simulation_id"] == "sim_fixture"


def test_followup_search_failure_is_fixed_after_completed_report(tmp_path, monkeypatch):
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(tmp_path))
    model = ScriptedModel()
    agent, _, _ = agent_for(model)
    assert agent.generate_report(report_id="report_followup").status is ReportStatus.COMPLETED
    model.mode = "chat"
    agent.zep_tools.search_selector = lambda **_kwargs: (_ for _ in ()).throw(
        RuntimeError("private provider secret"))
    with pytest.raises(NeutralCapabilityError, match="search_unavailable"):
        agent.chat("后续问题")
    assert "private provider secret" not in (
        tmp_path / "report_followup" / "meta.json").read_text(encoding="utf-8")


def test_followup_refuses_failed_report_context(tmp_path, monkeypatch):
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(tmp_path))
    agent, _, _ = agent_for(ScriptedModel(partial_outline=True))
    assert agent.generate_report(report_id="report_failedchat").status is ReportStatus.FAILED
    with pytest.raises(NeutralCapabilityError, match="report_context_unavailable"):
        agent.chat("后续问题")


def test_fresh_agent_preserves_prior_evidence_and_pins_report(tmp_path, monkeypatch):
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(tmp_path))
    original, _, _ = agent_for(ScriptedModel())
    assert original.generate_report(report_id="report_restart").status is ReportStatus.COMPLETED
    sidecar = tmp_path / "report_restart" / "retrieval_evidence.json"
    before = json.loads(sidecar.read_text(encoding="utf-8"))["retrievals"]
    assert len(before) >= 10
    restarted_model = ScriptedModel()
    restarted_model.mode = "chat"
    restarted, _, _ = agent_for(restarted_model)
    lookup = ReportManager.get_report_by_simulation.__func__
    calls = []
    def once(cls, simulation_id):
        calls.append(simulation_id)
        if len(calls) != 1:
            raise AssertionError("report lookup changed during chat")
        return lookup(cls, simulation_id)
    monkeypatch.setattr(ReportManager, "get_report_by_simulation", classmethod(once))
    assert "继续研究" in restarted.chat("后续问题")["response"]
    after = json.loads(sidecar.read_text(encoding="utf-8"))["retrievals"]
    assert after[:len(before)] == before and len(after) > len(before)
    assert calls == ["sim_fixture"]


def test_foreign_scope_and_corrupt_sidecar_fail_before_followup_model(tmp_path, monkeypatch):
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(tmp_path))
    original, _, _ = agent_for(ScriptedModel())
    assert original.generate_report(report_id="report_scope").status is ReportStatus.COMPLETED
    foreign_scope = {**SCOPE, "project_id": uid(4)}
    foreign_model = ScriptedModel()
    foreign_model.mode = "chat"
    foreign_agent, _, _ = agent_for(foreign_model, scope=foreign_scope)
    with pytest.raises(NeutralCapabilityError, match="invalid_evidence"):
        foreign_agent.chat("后续问题")
    assert foreign_model.chat_calls == 0
    changed_model = ScriptedModel()
    changed_model.mode = "chat"
    changed_agent, _, _ = agent_for(changed_model, changed=True)
    with pytest.raises(NeutralCapabilityError, match="invalid_evidence"):
        changed_agent.chat("后续问题")
    assert changed_model.chat_calls == 0
    sidecar = tmp_path / "report_scope" / "retrieval_evidence.json"
    sidecar.write_text('{"broken":', encoding="utf-8")
    same_model = ScriptedModel()
    same_model.mode = "chat"
    same_agent, _, _ = agent_for(same_model)
    with pytest.raises(NeutralCapabilityError, match="invalid_evidence"):
        same_agent.chat("后续问题")
    assert same_model.chat_calls == 0


def test_sidecar_conflict_is_not_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(tmp_path))
    original, _, _ = agent_for(ScriptedModel())
    assert original.generate_report(report_id="report_conflict").status is ReportStatus.COMPLETED
    sidecar = tmp_path / "report_conflict" / "retrieval_evidence.json"
    changed = []
    def conflict_selector(**kwargs):
        text = json.dumps(json.loads(sidecar.read_text(encoding="utf-8")), ensure_ascii=False, indent=4)
        sidecar.write_text(text, encoding="utf-8")
        changed.append(text)
        return selector(**kwargs)
    model = ScriptedModel()
    model.mode = "chat"
    agent, _, _ = agent_for(model, search=conflict_selector)
    with pytest.raises(NeutralCapabilityError, match="evidence_conflict"):
        agent.chat("后续问题")
    assert changed and sidecar.read_text(encoding="utf-8") == changed[0]


def test_typed_private_error_is_allowlisted_in_state_logs_and_chat(tmp_path, monkeypatch):
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(tmp_path))
    class TypedFailure(ScriptedModel):
        def chat_json(self, messages, **kwargs):
            raise NeutralCapabilityError("PRIVATE_SENTINEL")
        def chat(self, messages, **kwargs):
            raise NeutralCapabilityError("PRIVATE_SENTINEL")
    failed_agent, _, _ = agent_for(TypedFailure())
    failed = failed_agent.generate_report(report_id="report_typed")
    assert failed.status is ReportStatus.FAILED and failed.error == "internal_error"
    folder = tmp_path / "report_typed"
    for name in ("meta.json", "progress.json", "agent_log.jsonl", "console_log.txt"):
        assert "PRIVATE_SENTINEL" not in (folder / name).read_text(encoding="utf-8")
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(tmp_path / "chat"))
    ready_agent, _, _ = agent_for(ScriptedModel())
    assert ready_agent.generate_report(report_id="report_typed_chat").status is ReportStatus.COMPLETED
    chat_agent, _, _ = agent_for(TypedFailure())
    with pytest.raises(NeutralCapabilityError, match="internal_error"):
        chat_agent.chat("后续问题")


def test_detail_reads_are_recorded_and_strict_insight_rethrows():
    agent, _, _ = agent_for()
    agent.zep_tools.get_node_detail(uid(10))
    assert any(item["access"] == "projection_read" and item["kind"] == "node"
               and item["id"] == uid(10) for item in agent.zep_tools.retrieval_ledger)
    model = ScriptedModel()
    model.json_calls = 1
    strict_agent, _, _ = agent_for(model)
    strict_agent.zep_tools.get_node_detail = lambda _uuid: (_ for _ in ()).throw(
        NeutralCapabilityError("limit_exceeded"))
    with pytest.raises(NeutralCapabilityError, match="limit_exceeded"):
        strict_agent.zep_tools.insight_forge("display-1", "关系", "研究关系")


def test_non_source_scope_is_rejected_before_graph_or_model():
    scope = {**SCOPE, "layer": "simulation"}
    transport = ByteClient(scope)
    settings = ReadHostSettings("python", "read_bootstrap.py", "fixture-token", "owner",
                                "display-1", scope, {})
    facade = KnowledgeReadFacade(settings, client_factory=lambda: transport)
    model = ScriptedModel()
    with pytest.raises(NeutralCapabilityError, match="unsupported_scope"):
        facade.report_agent("display-1", "sim_fixture", "research",
                            model_client=model, search_selector=selector)
    assert transport.calls == [] and model.json_calls == model.report_calls == 0


def test_fresh_process_real_report_blocks_legacy_sdk_imports(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    script = r'''
import builtins
import os
import sys
sys.path.insert(0, os.environ["TEST_BACKEND"])
sys.path.insert(0, os.environ["TEST_TESTS"])
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if any(name == prefix or name.startswith(prefix + ".") for prefix in
           ("zep_cloud", "openai", "app.utils.zep", "app.services.zep_entity_reader")):
        raise AssertionError("forbidden cold import: " + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from test_provider_neutral_reports import agent_for
from app.services.report_agent import ReportManager, ReportStatus
ReportManager.REPORTS_DIR = os.environ["TEST_OUTPUT"]
agent, _, _ = agent_for()
report = agent.generate_report(report_id="report_cold")
assert report.status is ReportStatus.COMPLETED
print("neutral report ready")
'''
    environment = os.environ.copy()
    for key in ("ZEP_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY"):
        environment.pop(key, None)
    environment.update({"TEST_BACKEND": str(backend), "TEST_TESTS": str(Path(__file__).parent),
                        "TEST_OUTPUT": str(tmp_path), "PYTHON_DOTENV_DISABLED": "1",
                        "PYTHONIOENCODING": "utf-8"})
    result = subprocess.run([sys.executable, "-I", "-c", script], env=environment,
                            cwd=backend, capture_output=True, text=True, encoding="utf-8",
                            errors="strict", timeout=45)
    assert result.returncode == 0, result.stderr
    assert "neutral report ready" in result.stdout

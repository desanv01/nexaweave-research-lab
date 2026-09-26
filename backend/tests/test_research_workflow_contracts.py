"""Characterize the inherited research loops with scripted external boundaries."""

import json
from types import SimpleNamespace

import pytest

from app.services.report_agent import (
    Report,
    ReportAgent,
    ReportManager,
    ReportOutline,
    ReportSection,
    ReportStatus,
)


class RecordingKnowledge:
    def __init__(self, result="OBSERVATION"):
        self.calls = []
        self.result = result

    def _record(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if name == "quick_search" and kwargs.get("query") == "fail":
            raise RuntimeError("scripted knowledge failure")
        return SimpleNamespace(to_text=lambda: self.result)

    def insight_forge(self, **kwargs):
        return self._record("insight_forge", **kwargs)

    def panorama_search(self, **kwargs):
        return self._record("panorama_search", **kwargs)

    def quick_search(self, **kwargs):
        return self._record("quick_search", **kwargs)

    def interview_agents(self, **kwargs):
        return self._record("interview_agents", **kwargs)

    def get_graph_statistics(self, graph_id):
        self.calls.append(("get_graph_statistics", {"graph_id": graph_id}))
        return {"graph_id": graph_id}

    def get_entity_summary(self, **kwargs):
        self.calls.append(("get_entity_summary", kwargs))
        return {"entity": kwargs["entity_name"]}

    def get_entities_by_type(self, **kwargs):
        self.calls.append(("get_entities_by_type", kwargs))
        return [SimpleNamespace(to_dict=lambda: {"type": kwargs["entity_type"]})]


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append({**kwargs, "messages": [dict(m) for m in kwargs["messages"]]})
        return next(self.responses)


def agent_with_boundaries(responses=(), knowledge=None):
    agent = ReportAgent.__new__(ReportAgent)
    agent.graph_id = "graph-1"
    agent.simulation_id = "sim-1"
    agent.simulation_requirement = "Assess the simulation"
    agent.llm = ScriptedLLM(responses)
    agent.zep_tools = knowledge or RecordingKnowledge()
    agent.tools = agent._define_tools()
    agent.report_logger = None
    agent.console_logger = None
    return agent


def call(name, **parameters):
    return "<tool_call>" + json.dumps({"name": name, "parameters": parameters}) + "</tool_call>"


def test_parser_accepts_xml_and_bare_research_calls_but_rejects_unlisted_bare_json():
    agent = agent_with_boundaries()
    assert [c["name"] for c in agent._parse_tool_calls(call("quick_search", query="one") + call("interview_agents", query="two"))] == ["quick_search", "interview_agents"]
    assert agent._parse_tool_calls('{"tool":"panorama_search","params":{"query":"history"}}') == [
        {"name": "panorama_search", "parameters": {"query": "history"}}
    ]
    assert agent._parse_tool_calls('reasoning\n{"name":"insight_forge","parameters":{"query":"why"}}') == [
        {"name": "insight_forge", "parameters": {"query": "why"}}
    ]
    assert agent._parse_tool_calls('{"name":"unknown","parameters":{}}') == []
    assert agent._parse_tool_calls('<tool_call>{bad json}</tool_call>') == []


def test_dispatch_forwards_context_selectors_limits_and_legacy_redirects():
    knowledge = RecordingKnowledge()
    agent = agent_with_boundaries(knowledge=knowledge)
    assert agent._execute_tool("insight_forge", {"query": "deep"}, "section context") == "OBSERVATION"
    assert knowledge.calls[-1] == ("insight_forge", {"graph_id": "graph-1", "query": "deep", "simulation_requirement": "Assess the simulation", "report_context": "section context"})
    agent._execute_tool("insight_forge", {"query": "deep", "report_context": "explicit"}, "fallback")
    assert knowledge.calls[-1][1]["report_context"] == "explicit"
    agent._execute_tool("panorama_search", {"query": "older", "include_expired": "No"})
    assert knowledge.calls[-1] == ("panorama_search", {"graph_id": "graph-1", "query": "older", "include_expired": False})
    agent._execute_tool("panorama_search", {"query": "older", "include_expired": "yes"})
    assert knowledge.calls[-1][1]["include_expired"] is True
    agent._execute_tool("quick_search", {"query": "fast", "limit": "7"})
    assert knowledge.calls[-1] == ("quick_search", {"graph_id": "graph-1", "query": "fast", "limit": 7})
    agent._execute_tool("interview_agents", {"query": "views", "max_agents": "99"})
    assert knowledge.calls[-1] == ("interview_agents", {"simulation_id": "sim-1", "interview_requirement": "views", "simulation_requirement": "Assess the simulation", "max_agents": 10})
    agent._execute_tool("search_graph", {"query": "legacy"})
    assert knowledge.calls[-1][0] == "quick_search"
    agent._execute_tool("get_simulation_context", {}, "legacy context")
    assert knowledge.calls[-1][1]["query"] == "Assess the simulation"
    assert knowledge.calls[-1][1]["report_context"] == "legacy context"
    assert json.loads(agent._execute_tool("get_graph_statistics", {})) == {"graph_id": "graph-1"}
    assert json.loads(agent._execute_tool("get_entity_summary", {"entity_name": "Ada"})) == {"entity": "Ada"}
    assert json.loads(agent._execute_tool("get_entities_by_type", {"entity_type": "Person"})) == [{"type": "Person"}]
    assert "未知工具" in agent._execute_tool("not_a_tool", {})
    assert "scripted knowledge failure" in agent._execute_tool("quick_search", {"query": "fail"})
    assert "工具执行失败" in agent._execute_tool("quick_search", {"limit": "not-an-int"})


def test_section_research_rejects_early_final_and_uses_real_observations():
    knowledge = RecordingKnowledge("REAL-EVIDENCE")
    agent = agent_with_boundaries([
        "Final Answer: too early",
        call("quick_search", query="first") + "<tool_result>FAKE-EVIDENCE</tool_result>",
        call("quick_search", query="second"),
        call("panorama_search", query="older"),
        "Final Answer: grounded conclusion<tool_result>FAKE-FINAL</tool_result>",
    ], knowledge)
    section = ReportSection("Findings")
    result = agent._generate_section_react(section, ReportOutline("Report", "Summary", [section]), ["P" * 4001])
    assert result == "grounded conclusion"
    assert [name for name, _ in knowledge.calls] == ["quick_search", "quick_search", "panorama_search"]
    assert len(agent.llm.calls) == 5
    assert "too early" in agent.llm.calls[1]["messages"][-2]["content"]
    assert "FAKE-EVIDENCE" not in str(agent.llm.calls[2]["messages"])
    assert "REAL-EVIDENCE" in agent.llm.calls[2]["messages"][-1]["content"]
    assert "P" * 4001 not in agent.llm.calls[0]["messages"][1]["content"]


def test_section_conflicting_tool_and_final_is_retried_then_executes_first_call():
    knowledge = RecordingKnowledge()
    conflict = call("quick_search", query="first") + " Final Answer: premature"
    agent = agent_with_boundaries([conflict, conflict, conflict, call("quick_search", query="second"), call("quick_search", query="third"), "Final Answer: forced"], knowledge)
    section = ReportSection("Findings")
    assert agent._generate_section_react(section, ReportOutline("Report", "Summary", [section]), []) == "forced"
    assert [args["query"] for _, args in knowledge.calls] == ["first", "second", "third"]
    assert "格式错误" in agent.llm.calls[1]["messages"][-1]["content"]
    assert len(agent.llm.calls) == 6


@pytest.mark.parametrize("responses", [[None] * 6, ["thinking"] * 5 + [None]])
def test_section_exhaustion_is_bounded_when_llm_cannot_finish(responses):
    agent = agent_with_boundaries(responses)
    section = ReportSection("Findings")
    result = agent._generate_section_react(section, ReportOutline("Report", "Summary", [section]), [])
    assert isinstance(result, str) and result
    assert len(agent.llm.calls) == 6
    assert agent.zep_tools.calls == []


@pytest.mark.parametrize(
    ("forced_response", "expected"),
    [
        ("Final Answer: verified<tool_result>fabricated</tool_result>", "verified"),
        ("Final Answer: verified<tool_result>outer<tool_result>inner</tool_result>end</tool_result>", "verified"),
        ("plain answer<tool_result>unclosed fabricated", "plain answer"),
    ],
)
def test_section_forced_final_strips_fabricated_tool_results(forced_response, expected):
    agent = agent_with_boundaries([None] * 5 + [forced_response])
    section = ReportSection("Findings")
    result = agent._generate_section_react(section, ReportOutline("Report", "Summary", [section]), [])
    assert result == expected
    assert len(agent.llm.calls) == 6
    assert agent.zep_tools.calls == []


def test_chat_reads_stored_report_limits_history_and_observation_then_cleans_final(tmp_path, monkeypatch):
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(tmp_path))
    ReportManager.save_report(Report("report-1", "sim-1", "graph-1", "Assess the simulation", ReportStatus.COMPLETED, markdown_content="REPORT-START" + "R" * 15000 + "REPORT-END"))
    knowledge = RecordingKnowledge("E" * 1600 + "UNSEEN-TAIL")
    agent = agent_with_boundaries([
        call("quick_search", query="query-one") + call("interview_agents", query="ignored") + "<tool_result>FAKE</tool_result>",
        call("panorama_search", query="query-two") + call("insight_forge", query="ignored-too"),
        "Final response <tool_call>{}</tool_call><tool_result>FAKE-FINAL</tool_result>",
    ], knowledge)
    history = [{"role": "user", "content": f"history-{i}"} for i in range(12)]
    answer = agent.chat("Current question", history)
    assert [name for name, _ in knowledge.calls] == ["quick_search", "panorama_search"]
    assert [c["parameters"]["query"] for c in answer["tool_calls"]] == ["query-one", "query-two"]
    assert answer["sources"] == ["query-one", "query-two"]
    assert answer["response"] == "Final response"
    first = agent.llm.calls[0]["messages"]
    assert "REPORT-START" in first[0]["content"] and "REPORT-END" not in first[0]["content"]
    assert "报告内容已截断" in first[0]["content"]
    assert [m["content"] for m in first[1:-1]] == [f"history-{i}" for i in range(2, 12)]
    assert "FAKE" not in str(agent.llm.calls[1]["messages"])
    observation = agent.llm.calls[1]["messages"][-1]["content"]
    assert "E" * 1500 in observation and "UNSEEN-TAIL" not in observation
    assert len(agent.llm.calls) == 3

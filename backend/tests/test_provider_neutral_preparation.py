"""Offline qualification inputs for inherited provider-neutral preparation."""

import builtins
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.services.knowledge_read_facade import KnowledgeReadFacade, ReadHostSettings
from app.services.oasis_profile_generator import OasisAgentProfile, OasisProfileGenerator
from app.services.simulation_manager import SimulationManager, SimulationStatus


def uid(number):
    return str(UUID(int=number))


SCOPE = {"schema_version": 1, "workspace_id": uid(1), "project_id": uid(2),
         "graph_id": uid(3), "run_id": None, "branch_id": None, "layer": "source"}


def fact(number, kind="node", **changes):
    result = {"schema_version": 1, "provider_id": uid(number), "scope": SCOPE,
              "kind": kind, "name": "艾丽丝" if kind == "node" else "KNOWS",
              "fact": None if kind == "node" else "艾丽丝认识机构",
              "source_node_id": None if kind == "node" else uid(10),
              "target_node_id": None if kind == "node" else uid(11),
              "episode_ids": [uid(800)], "evidence_ids": [uid(900)],
              "labels": ["Entity", "Person"] if kind == "node" else [],
              "summary": "公开摘要" if kind == "node" else None,
              "valid_at": "2024-01-01T00:00:00Z", "invalid_at": None,
              "expired_at": None, "created_at": "2024-01-02T00:00:00Z",
              "attributes": {}, "score": None}
    result.update(changes)
    return result


class ByteClient:
    def __init__(self):
        self.calls = []
        self.changed = False

    def call(self, raw):
        request = json.loads(raw)
        self.calls.append(request)
        facts = ([fact(10, summary="改变后的摘要" if self.changed else "公开摘要"),
                  fact(11, name="机构", labels=["Entity", "Organization"])]
                 if request["payload"]["kind"] == "node" else
                 [fact(20, "edge", fact="改变后的关系" if self.changed else "艾丽丝认识机构")])
        return json.dumps({"version": 1, "request_id": request["request_id"], "ok": True,
                           "result": {"schema_version": 1, "facts": facts,
                                      "next_cursor": None}}, ensure_ascii=False).encode()


class ScriptedChat:
    def __init__(self, *, incomplete=False, fail=False, broken_persona=False,
                 malformed_persona=False, broken_config=False,
                 persona_fields=None):
        self.chat = SimpleNamespace(completions=self)
        self.calls = []
        self.incomplete = incomplete
        self.fail = fail
        self.broken_persona = broken_persona
        self.malformed_persona = malformed_persona
        self.broken_config = broken_config
        self.persona_fields = persona_fields or {}

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("private scripted client detail")
        prompt = kwargs["messages"][-1]["content"]
        if self.broken_persona and not any(marker in prompt for marker in (
                "agent_configs", "total_simulation_hours", "hot_topics")):
            content, finish = '{"bio":"fabricated", "persona":', "length"
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content), finish_reason=finish)])
        if self.malformed_persona and not any(marker in prompt for marker in (
                "agent_configs", "total_simulation_hours", "hot_topics")):
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"bio":"fabricated", "persona":'),
                finish_reason="stop")])
        if self.broken_config and "total_simulation_hours" in prompt:
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"total_simulation_hours":'),
                finish_reason="length")])
        if "agent_configs" in prompt:
            payload = {"agent_configs": [] if self.incomplete else [{
                "agent_id": 0, "activity_level": 0.5, "posts_per_hour": 0.3,
                "comments_per_hour": 0.4, "active_hours": [9, 10],
                "response_delay_min": 5, "response_delay_max": 20,
                "sentiment_bias": 0, "stance": "neutral", "influence_weight": 1.0}]}
        elif "total_simulation_hours" in prompt:
            payload = {"total_simulation_hours": 24, "minutes_per_round": 60,
                       "agents_per_hour_min": 1, "agents_per_hour_max": 2,
                       "peak_hours": [19], "off_peak_hours": [0],
                       "morning_hours": [8], "work_hours": [9], "reasoning": "fixture"}
        elif "hot_topics" in prompt:
            payload = {"hot_topics": ["技术"], "narrative_direction": "讨论",
                       "initial_posts": [{"content": "初始帖", "poster_type": "Person"}],
                       "reasoning": "fixture"}
        else:
            payload = {"bio": "技术观察者", "persona": "艾丽丝关注技术与社会。",
                       **self.persona_fields}
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)),
            finish_reason="stop")])


def manager_for(tmp_path, chat):
    transport = ByteClient()
    settings = ReadHostSettings("python", "read_bootstrap.py", "fixture-token", "owner",
                                "display-1", SCOPE, {})
    facade = KnowledgeReadFacade(settings, client_factory=lambda: transport)
    dependencies = facade.preparation_dependencies(
        "display-1", chat_client=chat, model_name="scripted", base_url="injected://fixture")
    return SimulationManager(preparation=dependencies,
                             simulation_data_dir=tmp_path), transport, facade


def test_real_inherited_preparation_writes_both_formats_and_grounding(tmp_path):
    chat = ScriptedChat()
    manager, transport, _ = manager_for(tmp_path, chat)
    state = manager.create_simulation("project-1", "display-1")
    progress = []
    ready = manager.prepare_simulation(
        state.simulation_id, "讨论技术", "背景材料", defined_entity_types=["Person"],
        parallel_profile_count=1, progress_callback=lambda *args, **kwargs: progress.append(args[0]))
    assert ready.status is SimulationStatus.READY
    assert ready.profiles_count == ready.entities_count == 1
    root = tmp_path / state.simulation_id
    assert json.loads((root / "state.json").read_text(encoding="utf-8"))["status"] == "ready"
    reddit = json.loads((root / "reddit_profiles.json").read_text(encoding="utf-8"))
    twitter = manager.get_profiles(state.simulation_id, "twitter")
    config = json.loads((root / "simulation_config.json").read_text(encoding="utf-8"))
    grounding = json.loads((root / "source_grounding.json").read_text(encoding="utf-8"))
    assert reddit[0]["persona"] == "艾丽丝关注技术与社会。"
    assert all(field not in reddit[0] for field in
               ("age", "gender", "mbti", "country"))
    assert twitter[0]["name"] == "艾丽丝"
    assert config["agent_configs"][0]["entity_uuid"] == uid(10)
    assert config["event_config"]["initial_posts"][0]["poster_agent_id"] == 0
    assert grounding[uid(10)]["facts"][0]["evidence_ids"] == [uid(900)]
    assert progress[0] == "reading" and "generating_config" in progress
    assert all(call["scope"] == SCOPE for call in transport.calls)
    assert len(chat.calls) >= 4
    assert any("艾丽丝认识机构" in call["messages"][-1]["content"]
               and "图谱邻域" in call["messages"][-1]["content"]
               for call in chat.calls)


def test_strict_neutral_final_reddit_keeps_supplied_values_and_null_unknowns(tmp_path):
    supplied = {"age": 47, "gender": "nonbinary", "mbti": "ENTP",
                "country": "MY"}
    manager, _, _ = manager_for(tmp_path / "supplied",
                                ScriptedChat(persona_fields=supplied))
    state = manager.create_simulation("project-1", "display-1")
    ready = manager.prepare_simulation(state.simulation_id, "topic", "source",
                                       defined_entity_types=["Person"],
                                       parallel_profile_count=1)
    assert ready.status is SimulationStatus.READY
    row = json.loads((tmp_path / "supplied" / state.simulation_id /
                      "reddit_profiles.json").read_text(encoding="utf-8"))[0]
    assert {field: row[field] for field in supplied} == supplied

    manager, _, _ = manager_for(tmp_path / "nulls",
                                ScriptedChat(persona_fields={
                                    "age": None, "gender": None,
                                    "mbti": None, "country": None}))
    state = manager.create_simulation("project-1", "display-1")
    ready = manager.prepare_simulation(state.simulation_id, "topic", "source",
                                       defined_entity_types=["Person"],
                                       parallel_profile_count=1)
    assert ready.status is SimulationStatus.READY
    row = json.loads((tmp_path / "nulls" / state.simulation_id /
                      "reddit_profiles.json").read_text(encoding="utf-8"))[0]
    assert all(field not in row for field in supplied)


def test_legacy_reddit_serializer_keeps_inherited_defaults(tmp_path):
    generator = OasisProfileGenerator(basic_only=True)
    profile = OasisAgentProfile(user_id=0, user_name="legacy", name="Legacy",
                                bio="Legacy bio", persona="Legacy persona")
    path = tmp_path / "legacy_reddit.json"
    generator.save_profiles([profile], str(path), platform="reddit")
    row = json.loads(path.read_text(encoding="utf-8"))[0]
    assert {field: row[field] for field in ("age", "gender", "mbti", "country")} == {
        "age": 30, "gender": "other", "mbti": "ISTJ", "country": "中国"}


def test_wrong_graph_and_incomplete_config_never_ready(tmp_path):
    manager, _, facade = manager_for(tmp_path, ScriptedChat(incomplete=True))
    with pytest.raises(ValueError, match="graph binding mismatch"):
        facade.preparation_dependencies(
            "other", chat_client=ScriptedChat(), model_name="scripted", base_url="injected://fixture")
    wrong = manager.create_simulation("project-1", "other")
    with pytest.raises(ValueError, match="graph binding mismatch"):
        manager.prepare_simulation(wrong.simulation_id, "requirement", "document")
    state = manager.create_simulation("project-1", "display-1")
    with pytest.raises(RuntimeError, match="preparation_failed"):
        manager.prepare_simulation(state.simulation_id, "requirement", "document",
                                   defined_entity_types=["Person"], parallel_profile_count=1)
    failed = manager.get_simulation(state.simulation_id)
    assert failed.status is SimulationStatus.FAILED and failed.error == "preparation_failed"


def test_neutral_failure_has_no_zep_fallback_or_private_detail(tmp_path, monkeypatch):
    manager, _, _ = manager_for(tmp_path, ScriptedChat(fail=True))
    state = manager.create_simulation("project-1", "display-1")
    imported = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.startswith(("zep_cloud", "app.services.zep_entity_reader", "app.utils.zep")):
            raise AssertionError("unexpected Zep import")
        return imported(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    with pytest.raises(RuntimeError, match="preparation_failed"):
        manager.prepare_simulation(state.simulation_id, "requirement", "document",
                                   defined_entity_types=["Person"], parallel_profile_count=1)
    assert manager.get_simulation(state.simulation_id).status is SimulationStatus.FAILED
    assert "private" not in (tmp_path / state.simulation_id / "state.json").read_text(encoding="utf-8")


def test_one_projection_prevents_same_uuid_fact_and_summary_drift(tmp_path):
    chat = ScriptedChat()
    manager, transport, _ = manager_for(tmp_path, chat)
    assert [call["payload"]["kind"] for call in transport.calls] == ["node", "edge"]
    transport.changed = True
    state = manager.create_simulation("project-1", "display-1")
    ready = manager.prepare_simulation(state.simulation_id, "requirement", "document",
                                       defined_entity_types=["Person"], parallel_profile_count=1)
    assert ready.status is SimulationStatus.READY
    assert [call["payload"]["kind"] for call in transport.calls] == ["node", "edge"]
    grounding = json.loads((tmp_path / state.simulation_id / "source_grounding.json").read_text(
        encoding="utf-8"))
    assert grounding[uid(10)]["facts"][0]["fact"] == "艾丽丝认识机构"
    assert grounding[uid(10)]["summary"] == "公开摘要"
    assert any("公开摘要" in call["messages"][-1]["content"]
               and "艾丽丝认识机构" in call["messages"][-1]["content"]
               for call in chat.calls)
    assert all("改变后的" not in call["messages"][-1]["content"] for call in chat.calls)


@pytest.mark.parametrize("chat", [ScriptedChat(broken_persona=True),
                                   ScriptedChat(malformed_persona=True),
                                   ScriptedChat(broken_config=True)])
def test_truncated_repairable_json_never_fabricates_ready(tmp_path, chat):
    manager, _, _ = manager_for(tmp_path, chat)
    state = manager.create_simulation("project-1", "display-1")
    with pytest.raises(RuntimeError, match="preparation_failed"):
        manager.prepare_simulation(state.simulation_id, "requirement", "document",
                                   defined_entity_types=["Person"], parallel_profile_count=1)
    assert manager.get_simulation(state.simulation_id).status is SimulationStatus.FAILED
    assert not (tmp_path / state.simulation_id / "simulation_config.json").exists()


def test_neighborhood_callback_failure_has_no_model_or_zep_fallback(tmp_path):
    chat = ScriptedChat()
    manager, _, _ = manager_for(tmp_path, chat)
    bound = manager._preparation
    def broken_factory():
        generator = bound.profile_generator()
        def fail_context(_entity):
            raise RuntimeError("private callback detail")
        generator.context_callback = fail_context
        return generator
    manager._preparation = replace(bound, profile_generator=broken_factory)
    state = manager.create_simulation("project-1", "display-1")
    with pytest.raises(RuntimeError, match="preparation_failed"):
        manager.prepare_simulation(state.simulation_id, "requirement", "document",
                                   defined_entity_types=["Person"], parallel_profile_count=1)
    assert not chat.calls
    assert manager.get_simulation(state.simulation_id).status is SimulationStatus.FAILED
    assert "private" not in (tmp_path / state.simulation_id / "state.json").read_text(encoding="utf-8")


def test_fresh_interpreter_complete_preparation_without_zep_or_model_sdk(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    script = r'''
import builtins
import os
import pathlib
import sys
sys.path.insert(0, os.environ["TEST_BACKEND"])
sys.path.insert(0, os.environ["TEST_TESTS"])
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if any(name == prefix or name.startswith(prefix + ".") for prefix in
           ("zep_cloud", "openai", "graphiti_core", "app.services.zep_entity_reader", "app.utils.zep")):
        raise AssertionError("forbidden cold import: " + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from test_provider_neutral_preparation import ScriptedChat, manager_for
from app.services.simulation_manager import SimulationStatus
manager, _, _ = manager_for(pathlib.Path(os.environ["TEST_OUTPUT"]), ScriptedChat())
state = manager.create_simulation("project-1", "display-1")
ready = manager.prepare_simulation(state.simulation_id, "requirement", "document",
                                   defined_entity_types=["Person"], parallel_profile_count=1)
assert ready.status is SimulationStatus.READY
print("neutral preparation ready")
'''
    environment = os.environ.copy()
    for key in ("LLM_API_KEY", "ZEP_API_KEY", "OPENAI_API_KEY"):
        environment.pop(key, None)
    environment.update({"TEST_BACKEND": str(backend), "TEST_TESTS": str(Path(__file__).parent),
                        "TEST_OUTPUT": str(tmp_path), "PYTHON_DOTENV_DISABLED": "1",
                        "PYTHONIOENCODING": "utf-8"})
    result = subprocess.run([sys.executable, "-I", "-c", script], env=environment,
                            cwd=backend, capture_output=True, text=True, encoding="utf-8",
                            errors="strict", timeout=45)
    assert result.returncode == 0, result.stderr
    assert "neutral preparation ready" in result.stdout

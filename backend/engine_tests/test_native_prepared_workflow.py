"""Offline full native loader/environment/interview qualification.

Requires the locked OASIS/CAMEL engine. No external model or embeddings are used.
"""

import asyncio
import csv
import json
import sqlite3
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.services.native_simulation import NativeSimulationSession
from app.services.knowledge_report_tools import NeutralCapabilityError
from app.services.knowledge_read_facade import KnowledgeReadFacade, ReadHostSettings
from app.services.simulation_manager import SimulationManager, SimulationStatus


def _offline_model():
    from camel.models import BaseModelBackend
    from camel.utils.token_counting import BaseTokenCounter
    from openai.types.chat.chat_completion import (
        ChatCompletion, ChatCompletionMessage, Choice,
    )
    from openai.types.chat.chat_completion_message_tool_call import (
        ChatCompletionMessageToolCall, Function,
    )

    class Counter(BaseTokenCounter):
        def count_tokens_from_messages(self, messages):
            return sum(len(str(message.get("content", ""))) for message in messages) // 4 + 1

        def encode(self, value):
            return [ord(char) for char in value]

        def decode(self, values):
            return "".join(chr(value) for value in values)

    class OfflineModel(BaseModelBackend):
        def __init__(self):
            super().__init__(model_type="gpt-4o-mini")
            self.counter = Counter()
            self.calls = 0

        @property
        def token_counter(self):
            return self.counter

        def _response(self, tools):
            self.calls += 1
            tool_calls = None
            if tools:
                tool_calls = [ChatCompletionMessageToolCall(
                    id=f"offline-{self.calls}", type="function",
                    function=Function(name="create_post", arguments=json.dumps(
                        {"content": f"Synthetic native post {self.calls}"})),
                )]
            return ChatCompletion(
                id=f"offline-response-{self.calls}", object="chat.completion",
                created=1, model="offline-fixture",
                choices=[Choice(index=0, finish_reason="tool_calls" if tool_calls else "stop",
                                message=ChatCompletionMessage(
                                    role="assistant", content=None if tool_calls else
                                    "Synthetic native interview response",
                                    tool_calls=tool_calls))],
            )

        def _run(self, messages, response_format=None, tools=None):
            return self._response(tools)

        async def _arun(self, messages, response_format=None, tools=None):
            return self._response(tools)

    return OfflineModel()


def _prepared(tmp_path):
    root = tmp_path / "native-sim"
    root.mkdir()
    actors = [
        {"agent_id": 0, "entity_uuid": "source-a", "entity_name": "Mira Vale",
         "entity_type": "Person", "activity_level": 1.0, "active_hours": [0]},
        {"agent_id": 1, "entity_uuid": "source-b", "entity_name": "Harbor Labs",
         "entity_type": "Organization", "activity_level": 1.0, "active_hours": [0]},
    ]
    config = {
        "graph_id": "graph-fixture", "simulation_id": "sim-fixture",
        "agent_configs": actors,
        "time_config": {"total_simulation_hours": 1, "minutes_per_round": 60,
                        "agents_per_hour_min": 2, "agents_per_hour_max": 2,
                        "off_peak_activity_multiplier": 1.0},
        "event_config": {"initial_posts": [
            {"poster_agent_id": 0, "content": "Synthetic initial event"}]},
        "twitter_config": {"platform": "twitter"},
        "reddit_config": {"platform": "reddit"},
    }
    (root / "state.json").write_text(json.dumps({
        "status": "ready", "graph_id": "graph-fixture",
        "simulation_id": "sim-fixture", "enable_twitter": True,
        "enable_reddit": True, "profiles_generated": True,
        "config_generated": True}), encoding="utf-8")
    (root / "simulation_config.json").write_text(json.dumps(config), encoding="utf-8")
    (root / "source_grounding.json").write_text(json.dumps({
        "source-a": {"source_entity_uuid": "source-a"},
        "source-b": {"source_entity_uuid": "source-b"}}), encoding="utf-8")
    with (root / "twitter_profiles.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "user_id", "name", "username", "user_char", "description"])
        writer.writeheader()
        for item in actors:
            writer.writerow({"user_id": item["agent_id"], "name": item["entity_name"],
                             "username": f"actor{item['agent_id']}",
                             "user_char": "Synthetic profile",
                             "description": "Synthetic description"})
    (root / "reddit_profiles.json").write_text(json.dumps([{
        "user_id": item["agent_id"], "name": item["entity_name"],
        "username": f"actor{item['agent_id']}", "bio": "Synthetic description",
        "persona": "Synthetic persona", "mbti": "INTJ", "gender": "other",
        "age": 30, "country": "MY",
    } for item in actors]), encoding="utf-8")
    return root


def _connected_preparation(tmp_path):
    def uid(number):
        return str(UUID(int=number))

    scope = {"schema_version": 1, "workspace_id": uid(1),
             "project_id": uid(2), "graph_id": uid(3),
             "run_id": None, "branch_id": None, "layer": "source"}

    class ProjectionTransport:
        def call(self, raw):
            request = json.loads(raw)
            common = {"schema_version": 1, "scope": scope,
                      "episode_ids": [uid(800)], "evidence_ids": [uid(900)],
                      "valid_at": "2024-01-01T00:00:00Z", "invalid_at": None,
                      "expired_at": None, "created_at": "2024-01-02T00:00:00Z",
                      "attributes": {}, "score": None}
            nodes = [dict(common, provider_id=uid(10 + index), kind="node",
                          name=name, fact=None, source_node_id=None,
                          target_node_id=None, labels=["Entity", kind],
                          summary=f"Synthetic source for {name}")
                     for index, (name, kind) in enumerate(
                         (("Mira Vale", "Person"), ("Harbor Labs", "Organization")))]
            edge = dict(common, provider_id=uid(20), kind="edge", name="KNOWS",
                        fact="Mira knows Harbor Labs", source_node_id=uid(10),
                        target_node_id=uid(11), labels=[], summary=None)
            facts = nodes if request["payload"]["kind"] == "node" else [edge]
            return json.dumps({"version": 1, "request_id": request["request_id"],
                               "ok": True, "result": {"schema_version": 1,
                                                       "facts": facts,
                                                       "next_cursor": None}}).encode()

    class ScriptedPreparation:
        def __init__(self):
            self.chat = SimpleNamespace(completions=self)

        def create(self, **kwargs):
            prompt = kwargs["messages"][-1]["content"]
            if "agent_configs" in prompt:
                payload = {"agent_configs": [{
                    "agent_id": index, "activity_level": 1.0,
                    "posts_per_hour": 1.0, "comments_per_hour": 0.0,
                    "active_hours": [0], "response_delay_min": 0,
                    "response_delay_max": 0, "sentiment_bias": 0,
                    "stance": "neutral", "influence_weight": 1.0,
                } for index in range(2)]}
            elif "total_simulation_hours" in prompt:
                payload = {"total_simulation_hours": 1, "minutes_per_round": 60,
                           "agents_per_hour_min": 2, "agents_per_hour_max": 2,
                           "peak_hours": [0], "off_peak_hours": [],
                           "morning_hours": [0], "work_hours": [0]}
            elif "hot_topics" in prompt:
                payload = {"hot_topics": ["synthetic topic"],
                           "narrative_direction": "synthetic discussion",
                           "initial_posts": [{"content": "Initial fixture event",
                                              "poster_type": "Person"}]}
            else:
                payload = {"bio": "Explicitly synthetic profile",
                           "persona": "Synthetic participant for offline qualification",
                           "mbti": "INTJ", "gender": "other", "age": 30,
                           "country": "MY"}
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(payload)),
                finish_reason="stop")])

    settings = ReadHostSettings("python", "read_bootstrap.py", "fixture-token",
                                "owner", "display-1", scope, {})
    facade = KnowledgeReadFacade(settings, client_factory=ProjectionTransport)
    chat = ScriptedPreparation()
    dependencies = facade.preparation_dependencies(
        "display-1", chat_client=chat, model_name="scripted",
        base_url="injected://fixture")
    manager = SimulationManager(preparation=dependencies,
                                simulation_data_dir=tmp_path)
    state = manager.create_simulation("project-1", "display-1")
    ready = manager.prepare_simulation(
        state.simulation_id, "Synthetic discussion", "Synthetic source document",
        defined_entity_types=["Person", "Organization"],
        parallel_profile_count=1)
    assert ready.status is SimulationStatus.READY
    root = tmp_path / state.simulation_id
    profiles = json.loads((root / "reddit_profiles.json").read_text(encoding="utf-8"))
    assert len(profiles) == 2
    assert all(all(field in profile for field in
                   ("persona", "mbti", "gender", "age", "country"))
               for profile in profiles)
    return root, state, facade


def test_connected_preparation_native_rounds_and_report_tool(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root, state, facade = _connected_preparation(tmp_path)
    model = _offline_model()
    session = NativeSimulationSession(root, graph_id="display-1",
                                      simulation_id=state.simulation_id,
                                      models={"twitter": model, "reddit": model},
                                      seed=11, max_rounds=1)
    with session:
        for platform in ("twitter", "reddit"):
            with sqlite3.connect(root / f"{platform}_simulation.db") as connection:
                posts = [row[0] for row in connection.execute("SELECT content FROM post")]
                assert "Initial fixture event" in posts
                assert any(post.startswith("Synthetic native post ") for post in posts)
                assert connection.execute(
                    "SELECT COUNT(*) FROM trace WHERE action = 'create_post'"
                ).fetchone()[0] >= 2
            action_log = (root / platform / "actions.jsonl").read_text(encoding="utf-8")
            assert "Synthetic native post " in action_log

        class OfflineReportModel:
            def chat(self, messages, **kwargs):
                return "Offline report fixture"

            def chat_json(self, messages, **kwargs):
                return {"title": "Offline report", "summary": "Fixture", "sections": []}

        capability = session.report_interview_capability(
            graph_id="display-1", simulation_id=state.simulation_id,
            platform="reddit", agent_ids=[0, 1])
        agent = facade.report_agent("display-1", state.simulation_id,
                                   "Synthetic report", model_client=OfflineReportModel(),
                                   interview_capability=capability)
        result = agent.zep_tools.interview_agents(
            state.simulation_id, "What happened?", custom_questions=[
                "What happened?", "Which actor posted?"])
        assert result.interview_questions == ["What happened?", "Which actor posted?"]
        assert len(result.interviews) == 2
        assert all("Which actor posted?" in item.question for item in result.interviews)
        assert all(item.response == "Synthetic native interview response"
                   for item in result.interviews)


def test_prepared_profiles_execute_native_rounds_and_interview(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # OASIS itself opens ./log on first import.
    root = _prepared(tmp_path)
    model = _offline_model()
    session = NativeSimulationSession(root, graph_id="graph-fixture",
                                      simulation_id="sim-fixture",
                                      models={"twitter": model, "reddit": model},
                                      seed=7, max_rounds=1)
    with session:
        for platform in ("twitter", "reddit"):
            db = root / f"{platform}_simulation.db"
            with sqlite3.connect(db) as connection:
                assert connection.execute("SELECT COUNT(*) FROM user").fetchone()[0] == 2
                assert connection.execute("SELECT COUNT(*) FROM post").fetchone()[0] > 0
            actions = (root / platform / "actions.jsonl").read_text(encoding="utf-8")
            assert '"event_type": "simulation_end"' in actions
        result = session.interview(platform="twitter", agent_id=0,
                                   prompt="What did you observe?")
        assert result["response"] == "Synthetic native interview response"
        with pytest.raises(NeutralCapabilityError, match="invalid_request"):
            session.interview(platform="twitter", agent_id=99, prompt="Unknown?")
        with pytest.raises(NeutralCapabilityError, match="graph_mismatch"):
            session.report_interview_capability(
                graph_id="wrong", simulation_id="sim-fixture",
                platform="reddit", agent_ids=[1])
        with pytest.raises(NeutralCapabilityError, match="simulation_mismatch"):
            session.report_interview_capability(
                graph_id="graph-fixture", simulation_id="wrong",
                platform="reddit", agent_ids=[1])
        with pytest.raises(NeutralCapabilityError, match="invalid_request"):
            session.report_interview_capability(
                graph_id="graph-fixture", simulation_id="sim-fixture",
                platform="reddit", agent_ids=[99])
        with pytest.raises(NeutralCapabilityError, match="invalid_request"):
            session.batch_interview(platform="twitter", agent_ids=[0, 0],
                                    prompt="Duplicate?")
        capability = session.report_interview_capability(
            graph_id="graph-fixture", simulation_id="sim-fixture",
            platform="reddit", agent_ids=[0, 1])
        report_result = capability(simulation_id="sim-fixture",
                                   interview_requirement="What happened?",
                                   custom_questions=["What happened?", "Who acted?"])
        assert len(report_result.interviews) == 2
        assert report_result.interview_questions == ["What happened?", "Who acted?"]
        assert all("What happened?" in item.question and "Who acted?" in item.question
                   for item in report_result.interviews)
        assert all(interview.response == "Synthetic native interview response"
                   for interview in report_result.interviews)
        assert [item["agent_id"] for item in report_result.selected_agents] == [0, 1]
        with sqlite3.connect(root / "reddit_simulation.db") as connection:
            assert connection.execute(
                "SELECT COUNT(DISTINCT user_id) FROM trace WHERE action = 'interview'"
            ).fetchone()[0] == 2
        assert list((root / "ipc_responses").glob("*.json")) == []
        from scripts.run_parallel_simulation import ParallelIPCHandler
        original_result = ParallelIPCHandler._get_interview_result

        def missing_second_response(self, agent_id, native_platform):
            result = original_result(self, agent_id, native_platform)
            if native_platform == "reddit" and agent_id == 1:
                result["response"] = None
            return result

        with monkeypatch.context() as patch:
            patch.setattr(ParallelIPCHandler, "_get_interview_result",
                          missing_second_response)
            with pytest.raises(NeutralCapabilityError, match="interview_unavailable"):
                capability(simulation_id="sim-fixture",
                           interview_requirement="Is every response present?")
        assert list((root / "ipc_responses").glob("*.json")) == []
        for invalid in (["valid", ""], ["valid", "x" * 401], ["valid"] * 11):
            with pytest.raises(NeutralCapabilityError, match="invalid_request"):
                capability(simulation_id="sim-fixture", interview_requirement="topic",
                           custom_questions=invalid)
        with pytest.raises(NeutralCapabilityError, match="invalid_request"):
            capability(simulation_id="sim-fixture", interview_requirement="topic",
                       custom_questions=["x" * 400] * 10 + ["extra"])
        with pytest.raises(NeutralCapabilityError, match="invalid_request"):
            capability(simulation_id="sim-fixture", interview_requirement=" ")
        with pytest.raises(NeutralCapabilityError, match="invalid_request"):
            capability(simulation_id="sim-fixture", interview_requirement="topic",
                       max_agents=11)
        with pytest.raises(NeutralCapabilityError, match="invalid_request"):
            capability(simulation_id="sim-fixture", interview_requirement="topic",
                       simulation_requirement="x" * 4001)
        assert model.calls > 0
    with pytest.raises(NeutralCapabilityError):
        session.interview(platform="twitter", agent_id=0, prompt="Closed?")
    with pytest.raises(NeutralCapabilityError):
        capability(simulation_id="sim-fixture", interview_requirement="Closed?")
    with pytest.raises(NeutralCapabilityError, match="invalid_request"):
        NativeSimulationSession(root, graph_id="graph-fixture",
                                simulation_id="sim-fixture",
                                models={"twitter": model, "reddit": model},
                                seed=7, max_rounds=1)


def test_existing_native_database_is_never_reset(tmp_path):
    root = _prepared(tmp_path)
    db = root / "twitter_simulation.db"
    original = b"existing native results must survive"
    db.write_bytes(original)
    with pytest.raises(NeutralCapabilityError, match="invalid_request"):
        NativeSimulationSession(root, graph_id="graph-fixture",
                                simulation_id="sim-fixture",
                                models={"twitter": object(), "reddit": object()},
                                seed=7, max_rounds=1)
    assert db.read_bytes() == original


def test_preconstructed_sessions_cannot_both_claim_one_directory(tmp_path, monkeypatch):
    root = _prepared(tmp_path)
    model = _offline_model()
    first = NativeSimulationSession(root, graph_id="graph-fixture",
                                    simulation_id="sim-fixture",
                                    models={"twitter": model, "reddit": model},
                                    seed=7, max_rounds=1)
    second = NativeSimulationSession(root, graph_id="graph-fixture",
                                     simulation_id="sim-fixture",
                                     models={"twitter": model, "reddit": model},
                                     seed=7, max_rounds=1)
    from scripts import run_parallel_simulation as native

    def stop_before_engine():
        raise RuntimeError("fixture stop after claim")

    with monkeypatch.context() as patch:
        patch.setattr(native, "_load_native_engine", stop_before_engine)
        with pytest.raises(NeutralCapabilityError, match="internal_error"):
            first.start()
    with pytest.raises(NeutralCapabilityError, match="invalid_request"):
        second.start()
    assert (root / ".native_prepared_start_claim").exists()
    assert not (root / "twitter_simulation.db").exists()
    with pytest.raises(NeutralCapabilityError, match="invalid_request"):
        NativeSimulationSession(root, graph_id="graph-fixture",
                                simulation_id="sim-fixture",
                                models={"twitter": model, "reddit": model},
                                seed=7, max_rounds=1)


def test_preflight_rejects_wrong_graph_and_unknown_actor(tmp_path):
    root = _prepared(tmp_path)
    with pytest.raises(NeutralCapabilityError, match="invalid_request"):
        NativeSimulationSession(root, graph_id="wrong", simulation_id="sim-fixture",
                                models={"twitter": object(), "reddit": object()},
                                seed=1, max_rounds=1)


def test_preflight_rejects_reddit_profiles_without_native_optional_fields(tmp_path):
    root = _prepared(tmp_path)
    path = root / "reddit_profiles.json"
    profiles = json.loads(path.read_text(encoding="utf-8"))
    del profiles[0]["mbti"]
    path.write_text(json.dumps(profiles), encoding="utf-8")
    with pytest.raises(NeutralCapabilityError, match="invalid_request"):
        NativeSimulationSession(root, graph_id="graph-fixture",
                                simulation_id="sim-fixture",
                                models={"twitter": _offline_model(),
                                        "reddit": _offline_model()},
                                seed=1, max_rounds=1)


def test_native_failure_closes_adopted_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = _prepared(tmp_path)
    from oasis.environment.env import OasisEnv

    closed = []
    original_close = OasisEnv.close

    async def observed_close(self):
        closed.append(self)
        await original_close(self)

    monkeypatch.setattr(OasisEnv, "close", observed_close)
    async def fail_step(*_args, **_kwargs):
        raise RuntimeError("PRIVATE_PROVIDER_DETAIL")

    monkeypatch.setattr(OasisEnv, "step", fail_step)
    model = _offline_model()
    session = NativeSimulationSession(root, graph_id="graph-fixture",
                                      simulation_id="sim-fixture",
                                      models={"twitter": model, "reddit": model},
                                      seed=7, max_rounds=1)
    with pytest.raises(NeutralCapabilityError, match="internal_error"):
        session.start()
    assert closed and session._closed


def test_native_cancellation_closes_adopted_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = _prepared(tmp_path)
    from oasis.environment.env import OasisEnv

    closed = []
    original_close = OasisEnv.close

    async def observed_close(self):
        closed.append(self)
        await original_close(self)

    async def cancel_step(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(OasisEnv, "close", observed_close)
    monkeypatch.setattr(OasisEnv, "step", cancel_step)
    model = _offline_model()
    session = NativeSimulationSession(root, graph_id="graph-fixture",
                                      simulation_id="sim-fixture",
                                      models={"twitter": model, "reddit": model},
                                      seed=7, max_rounds=1)
    with pytest.raises(asyncio.CancelledError):
        session.start()
    assert closed and session._closed

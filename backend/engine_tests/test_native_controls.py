"""Actual prepared OASIS/CAMEL controls, offline only. Authored unverified."""

import json
import random
import sqlite3

import pytest

from app.services.knowledge_report_tools import NeutralCapabilityError
from app.services.native_simulation import NativeSimulationSession
from test_native_prepared_workflow import _offline_model, _prepared


_INPUTS = ("state.json", "simulation_config.json", "source_grounding.json",
           "twitter_profiles.csv", "reddit_profiles.json")


def _configured(tmp_path, controls, *, minutes=60, hours=1):
    root = _prepared(tmp_path)
    path = root / "simulation_config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["execution_controls"] = controls
    config["time_config"].update(minutes_per_round=minutes,
                               total_simulation_hours=hours)
    path.write_text(json.dumps(config), encoding="utf-8")
    return root


def _session(root, model, cls=NativeSimulationSession, rounds=1):
    return cls(root, graph_id="graph-fixture", simulation_id="sim-fixture",
               models={"twitter": model, "reddit": model}, seed=19, max_rounds=rounds)


def _controls(twitter="balanced_random", reddit="compact_popularity"):
    return {"schema_version": 1,
            "recommendation_presets": {"twitter": twitter, "reddit": reddit},
            "initial_follows": {platform: [
                {"follower_agent_id": 1, "followee_agent_id": 0}]
                for platform in ("twitter", "reddit")},
            "agent_activity": [{"agent_id": 0, "activity_probability": 0},
                               {"agent_id": 1, "activity_probability": 0}]}


class ObserveNetwork(NativeSimulationSession):
    async def apply_initial_network(self, platform, env, action_logger, agent_names):
        count = await super().apply_initial_network(platform, env, action_logger, agent_names)
        assert env.platform.db.execute("SELECT COUNT(*) FROM post").fetchone()[0] == 0
        assert env.platform.db.execute(
            "SELECT follower_id, followee_id FROM follow ORDER BY follow_id"
        ).fetchall() == [(1, 0)]
        assert env.platform.db.execute(
            "SELECT user_id, num_followings, num_followers FROM user ORDER BY user_id"
        ).fetchall() == [(0, 0, 1), (1, 1, 0)]
        assert all(model.calls == 0 for model in self._models.values())
        self.observed = getattr(self, "observed", []) + [platform]
        return count


def test_network_before_rounds_frozen_input_round_logging_and_one_shot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = _configured(tmp_path, _controls())
    original = {name: (root / name).read_bytes() for name in _INPUTS}
    model = _offline_model()
    session = _session(root, model, ObserveNetwork)
    duplicate = _session(root, model)
    before = random.getstate()
    with session:
        assert session.observed == ["twitter", "reddit"]
        assert model.calls == 0
        assert random.getstate() == before
        for platform in ("twitter", "reddit"):
            entries = [json.loads(line) for line in
                       (root / platform / "actions.jsonl").read_text(encoding="utf-8").splitlines()]
            starts = [item for item in entries if item.get("event_type") == "round_start"
                      and item["round"] == 0]
            ends = [item for item in entries if item.get("event_type") == "round_end"
                    and item["round"] == 0]
            follows = [item for item in entries if item.get("action_type") == "FOLLOW"]
            assert len(starts) == len(ends) == len(follows) == 1
            assert ends[0]["actions_count"] == 2  # one actual follow plus initial post
            assert follows[0]["action_args"] == {"followee_id": 0}
        from scripts import run_parallel_simulation as native
        assert len(native.TWITTER_ACTIONS) == 6
        assert len(native.REDDIT_ACTIONS) == 13
        with pytest.raises(NeutralCapabilityError, match="invalid_request"):
            duplicate.start()
    assert original == {name: (root / name).read_bytes() for name in _INPUTS}
    record = json.loads((root / "native_effective_controls.json").read_text(encoding="utf-8"))
    assert record["execution_status"] == "completed"
    assert all(edges[0]["status"] == "applied" for edges in record["initial_network"].values())
    assert (root / ".native_prepared_start_claim").exists()
    with pytest.raises(NeutralCapabilityError, match="invalid_request"):
        _session(root, model)


async def _feed(session, platform):
    native_platform = session._results[platform].env.platform
    for author in (0, 1):
        for index in range(12):
            result = await native_platform.create_post(author, f"synthetic author-{author} post-{index}")
            assert result["success"] is True
    await native_platform.update_rec_table()
    rec_ids = [row[0] for row in native_platform.db.execute(
        "SELECT post_id FROM rec WHERE user_id = 1 ORDER BY post_id").fetchall()]
    result = await native_platform.refresh(1)
    assert result["success"] is True
    assert all(post["content"] for post in result["posts"])
    return rec_ids, result["posts"]


def test_every_preset_has_actual_sqlite_and_refresh_effects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    feeds = {}
    for label, twitter, reddit in (
            ("small", "balanced_random", "compact_popularity"),
            ("large", "following_only", "broad_popularity")):
        parent = tmp_path / label
        parent.mkdir()
        root = _configured(parent, _controls(twitter, reddit))
        with _session(root, _offline_model()) as session:
            for platform in ("twitter", "reddit"):
                before = random.getstate()
                try:
                    random.seed(23)
                    feeds[label, platform] = session._runner.run(_feed(session, platform))
                finally:
                    random.setstate(before)
    small_rec, small_feed = feeds["small", "twitter"]
    large_rec, large_feed = feeds["large", "twitter"]
    assert len(small_rec) == len(large_rec) == 2
    assert 3 <= len(small_feed) <= 5
    assert len(large_feed) == 10
    assert all(post["user_id"] == 0 for post in large_feed)
    assert {post["post_id"] for post in small_feed} != {post["post_id"] for post in large_feed}
    small_rec, small_feed = feeds["small", "reddit"]
    large_rec, large_feed = feeds["large", "reddit"]
    assert len(small_rec) == 2 and len(large_rec) == 20
    assert len(small_feed) == 1 and len(large_feed) == 5
    assert {post["post_id"] for post in small_feed} <= set(small_rec)
    assert {post["post_id"] for post in large_feed} <= set(large_rec)
    assert {post["content"] for post in small_feed} != {post["content"] for post in large_feed}


def test_absent_controls_native_defaults_and_action_parity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    outcomes = []
    for label in ("absent", "empty"):
        parent = tmp_path / label
        parent.mkdir()
        root = (_prepared(parent) if label == "absent" else
                _configured(parent, {"schema_version": 1}))
        traces = {}
        with _session(root, _offline_model()) as session:
            for platform, expected in (("twitter", (2, 2, 3)), ("reddit", (5, 100, 3))):
                native_platform = session._results[platform].env.platform
                assert (native_platform.refresh_rec_post_count,
                        native_platform.max_rec_post_len,
                        native_platform.following_post_count) == expected
                traces[platform] = native_platform.db.execute(
                    "SELECT user_id, action, info FROM trace "
                    "WHERE action IN ('signup', 'create_post', 'follow') ORDER BY rowid").fetchall()
        assert (root / "native_effective_controls.json").exists() == (label == "empty")
        outcomes.append(traces)
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize("midnight_probability", [0, 1])
def test_local_half_hour_midnight_zero_one_actual_actions_and_seeded_rerun(
        tmp_path, monkeypatch, midnight_probability):
    monkeypatch.chdir(tmp_path)
    controls = {"schema_version": 1, "agent_activity": [
        {"agent_id": 0, "timezone_offset_minutes": 30, "active_hours": [1],
         "activity_probability": 1},
        {"agent_id": 1, "timezone_offset_minutes": -30, "active_hours": [23],
         "activity_probability": midnight_probability}]}
    runs = []
    previous = random.getstate()
    for label in ("first", "second"):
        parent = tmp_path / label
        parent.mkdir()
        root = _configured(parent, controls, minutes=30, hours=1)
        with _session(root, _offline_model(), rounds=2):
            assert random.getstate() == previous
        traces = {}
        for platform in ("twitter", "reddit"):
            with sqlite3.connect(root / f"{platform}_simulation.db") as db:
                traces[platform] = db.execute(
                    "SELECT user_id, action, info FROM trace WHERE action='create_post' ORDER BY rowid"
                ).fetchall()
            assert [row[0] for row in traces[platform]] == (
                [0, 1, 0] if midnight_probability else [0, 0])
            log = [json.loads(line) for line in (root / platform / "actions.jsonl").read_text(
                encoding="utf-8").splitlines()]
            rounds = {item["round"]: item["actions_count"] for item in log
                      if item.get("event_type") == "round_end"}
            assert rounds[1] == midnight_probability and rounds[2] == 1
        runs.append(traces)
    assert runs[0] == runs[1]
    assert random.getstate() == previous


@pytest.mark.parametrize("controls", [
    {"schema_version": True},
    {"schema_version": 1, "recommendation_presets": {"twitter": "twhin-bert"}},
    {"schema_version": 1, "initial_follows": {"reddit": [
        {"follower_agent_id": 1, "followee_agent_id": 1}]}},
    {"schema_version": 1, "agent_activity": [
        {"agent_id": 0, "activity_probability": float("nan")}]},
])
def test_malformed_before_claim_sqlite_or_model(tmp_path, controls):
    root = _configured(tmp_path, controls)
    class NeverRun:
        def run(self, *args, **kwargs):
            pytest.fail("model must never be used")
    with pytest.raises(NeutralCapabilityError, match="invalid_request"):
        _session(root, NeverRun())
    assert not (root / ".native_prepared_start_claim").exists()
    assert not list(root.glob("*.db"))
    assert not (root / "native_effective_controls.json").exists()


def test_partial_native_network_failure_closes_and_preserves_truth(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    controls = _controls()
    controls["initial_follows"]["twitter"] = [
        {"follower_agent_id": 0, "followee_agent_id": 1},
        {"follower_agent_id": 1, "followee_agent_id": 0}]
    root = _configured(tmp_path, controls)
    from oasis.social_agent.agent import SocialAgent
    original = SocialAgent.perform_action_by_data
    calls = []
    async def fail_after_native_effect(self, action_type, *args, **kwargs):
        response = await original(self, action_type, *args, **kwargs)
        calls.append(self.social_agent_id)
        return {"success": False} if len(calls) == 2 else response
    monkeypatch.setattr(SocialAgent, "perform_action_by_data", fail_after_native_effect)
    model = _offline_model()
    class CaptureSession(NativeSimulationSession):
        def adopt(self, platform, result):
            super().adopt(platform, result)
            self.retained_env = result.env
    session = _session(root, model, CaptureSession)
    previous = random.getstate()
    with pytest.raises(NeutralCapabilityError, match="internal_error"):
        session.start()
    assert session._closed and session._results == {}
    assert session.retained_env.platform_task.done()
    with pytest.raises(sqlite3.ProgrammingError):
        session.retained_env.platform.db.execute("SELECT 1")
    assert model.calls == 0 and random.getstate() == previous
    assert calls == [0, 1]
    with sqlite3.connect(root / "twitter_simulation.db") as db:
        assert db.execute("SELECT COUNT(*) FROM follow").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM post").fetchone()[0] == 0
    record = json.loads((root / "native_effective_controls.json").read_text(encoding="utf-8"))
    assert record["execution_status"] == "failed"
    assert [item["status"] for item in record["initial_network"]["twitter"]] == ["applied", "uncertain"]
    assert not (root / "reddit_simulation.db").exists()
    assert (root / ".native_prepared_start_claim").exists()
    with pytest.raises(NeutralCapabilityError, match="invalid_request"):
        _session(root, model)

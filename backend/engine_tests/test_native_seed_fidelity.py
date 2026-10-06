"""Locked OASIS/CAMEL seed proof, lazy SDK imports, no paid providers.

Main alone qualifies these authored fixtures. Real engine bodies stay outside
generic offline collection; no SDK mocks or replacement database outputs.
"""
import asyncio
import json
import random

import pytest


INPUTS = ("state.json", "simulation_config.json", "source_grounding.json",
          "twitter_profiles.csv", "reddit_profiles.json")
TEXT = "重复 café e\u0301 雨 🐟 <script>literal</script>\n"
SEEDS = [(0, TEXT), (1, "second"), (0, TEXT), (1, "শেষ")]


def _prepared_seeds(tmp_path, controls=None):
    from test_native_prepared_workflow import _prepared
    root = _prepared(tmp_path)
    path = root / "simulation_config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["event_config"]["initial_posts"] = [
        {"poster_agent_id": actor, "content": text} for actor, text in SEEDS]
    if controls is not None:
        config["execution_controls"] = controls
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return root, config


class VerifiedLog:
    def __init__(self, env):
        self.env = env
        self.records = []

    def log_action(self, **record):
        if record["success"]:
            post_id = json.loads(record["result"])["post_id"]
            assert self.env.platform.db.execute(
                "SELECT user_id,content FROM post WHERE post_id=?", (post_id,)
            ).fetchall() == [(record["agent_id"], record["action_args"]["content"])]
            assert not self.env.platform.db.in_transaction
        self.records.append(record)


async def _real_environment(root, platform, model):
    import oasis
    from oasis.social_platform.platform import Platform
    from app.services.native_reddit_profiles import build_reddit_agent_graph
    from scripts import run_parallel_simulation as runner
    runner._load_native_engine()
    if platform == "twitter":
        graph = await oasis.generate_twitter_agent_graph(
            profile_path=str(root / "twitter_profiles.csv"), model=model,
            available_actions=runner.TWITTER_ACTIONS)
        native_platform = Platform(
            db_path=str(root / "t.db"), recsys_type="random",
            refresh_rec_post_count=2, max_rec_post_len=2,
            following_post_count=3, use_openai_embedding=False)
    else:
        profiles = json.loads((root / "reddit_profiles.json").read_text(encoding="utf-8"))
        graph = build_reddit_agent_graph(profiles, model, list(runner.REDDIT_ACTIONS))
        native_platform = Platform(
            db_path=str(root / "r.db"), recsys_type="reddit",
            use_openai_embedding=False)
    env = oasis.make(agent_graph=graph, platform=native_platform,
                     database_path=native_platform.db_path, semaphore=30)
    try:
        await env.reset()
    except BaseException:
        from scripts.native_dependencies import close_environment
        await close_environment(env)
        raise
    return env


@pytest.mark.parametrize("platform", ["twitter", "reddit"])
@pytest.mark.parametrize("with_logger", [True, False])
def test_real_order_ids_clock_zero_models_and_next_round(tmp_path, monkeypatch, platform, with_logger):
    monkeypatch.chdir(tmp_path)
    from test_native_prepared_workflow import _offline_model
    from scripts.native_seed_posts import apply_native_seed_posts
    root, config = _prepared_seeds(tmp_path)
    before_inputs = {name: (root / name).read_bytes() for name in INPUTS}
    model = _offline_model()

    async def proof():
        import oasis
        from scripts.native_dependencies import close_environment
        env = await _real_environment(root, platform, model)
        try:
            log = VerifiedLog(env)
            rng = random.getstate()
            clock = env.platform.sandbox_clock.time_step
            result = await apply_native_seed_posts(
                env, config, platform, log if with_logger else None)
            assert model.calls == 0 and random.getstate() == rng
            assert result.successful_count == 4
            assert env.platform.sandbox_clock.time_step == clock + (platform == "twitter")
            posts = env.platform.db.execute(
                "SELECT post_id,user_id,content,created_at FROM post ORDER BY post_id").fetchall()
            assert [(actor, content) for _, actor, content, _ in posts] == SEEDS
            ids = [post_id for post_id, _, _, _ in posts]
            assert len(set(ids)) == 4 and all(type(i) is int and i > 0 for i in ids)
            traces = env.platform.db.execute(
                "SELECT rowid,user_id,info FROM trace WHERE action='create_post' ORDER BY rowid"
            ).fetchall()
            assert [(actor, json.loads(info)["content"]) for _, actor, info in traces] == SEEDS
            assert [json.loads(info)["post_id"] for _, _, info in traces] == ids
            assert result.trace_cursor == traces[-1][0]
            assert len(log.records) == (4 if with_logger else 0)
            if with_logger:
                assert [json.loads(r["result"])["post_id"] for r in log.records] == ids
                assert all(r["round_num"] == 0 and r["success"] is True for r in log.records)
            if platform == "twitter":
                assert {str(row[3]) for row in posts} == {"0"}
            # Real next native step, no seed replay. This manual next action
            # isolates the clock assertion from model scheduler variability.
            await env.step({env.agent_graph.get_agent(1): oasis.ManualAction(
                action_type=oasis.ActionType.CREATE_POST,
                action_args={"content": "next autonomous-stage witness"})})
            later = env.platform.db.execute(
                "SELECT post_id,content,created_at FROM post WHERE post_id > ?", (ids[-1],)
            ).fetchall()
            assert len(later) == 1 and later[0][1] == "next autonomous-stage witness"
            if platform == "twitter":
                assert str(later[0][2]) == "1"
            assert env.platform.db.execute(
                "SELECT COUNT(*) FROM trace WHERE rowid > ? AND action='create_post'",
                (result.trace_cursor,)).fetchone()[0] == 1
            assert model.calls == 0
        finally:
            await close_environment(env)
        assert env.platform_task.done()
        with pytest.raises(Exception):
            env.platform.db.execute("SELECT 1")

    asyncio.run(asyncio.wait_for(proof(), timeout=120))
    assert before_inputs == {name: (root / name).read_bytes() for name in INPUTS}


@pytest.mark.parametrize("platform", ["twitter", "reddit"])
def test_real_rejected_insertion_declared_failure_no_retry(tmp_path, monkeypatch, platform):
    monkeypatch.chdir(tmp_path)
    from test_native_prepared_workflow import _offline_model
    from scripts.native_seed_posts import NativeSeedError, apply_native_seed_posts
    root, config = _prepared_seeds(tmp_path)
    original = {name: (root / name).read_bytes() for name in INPUTS}
    model = _offline_model()

    async def proof():
        from scripts.native_dependencies import close_environment
        env = await _real_environment(root, platform, model)
        try:
            # Actual SQLite refuses the SECOND insertion through actual SDK.
            # The first native seed stays committed; no fabricated responses.
            env.platform.db.executescript(
                "CREATE TRIGGER reject_seed BEFORE INSERT ON post "
                "WHEN NEW.content = 'second' BEGIN "
                "SELECT RAISE(FAIL, 'fixture-private-error'); END;")
            log = VerifiedLog(env)
            with pytest.raises(NativeSeedError, match="^native_seed_response_failed$"):
                await apply_native_seed_posts(env, config, platform, log)
            assert env.platform.db.execute(
                "SELECT user_id,content FROM post ORDER BY post_id").fetchall() == SEEDS[:1]
            assert env.platform.db.execute(
                "SELECT COUNT(*) FROM trace WHERE action='create_post'").fetchone()[0] == 1
            assert len(log.records) == 2
            assert log.records[0]["success"] is True
            assert log.records[1]["success"] is False
            assert log.records[1]["result"] == "native_seed_response_failed"
            assert "fixture-private-error" not in json.dumps(log.records)
            assert env.platform.sandbox_clock.time_step == 0 and model.calls == 0
        finally:
            await close_environment(env)
        assert env.platform_task.done()
    asyncio.run(asyncio.wait_for(proof(), timeout=120))
    assert original == {name: (root / name).read_bytes() for name in INPUTS}


@pytest.mark.parametrize("controls", [None, {"schema_version": 1}])
def test_owned_both_platforms_no_round_replay_provenance_one_shot(tmp_path, monkeypatch, controls):
    monkeypatch.chdir(tmp_path)
    from app.services.native_simulation import NativeSimulationSession
    from app.services.knowledge_report_tools import NeutralCapabilityError
    from test_native_prepared_workflow import _offline_model
    root, _ = _prepared_seeds(tmp_path, controls)
    original = {name: (root / name).read_bytes() for name in INPUTS}
    models = {p: _offline_model() for p in ("twitter", "reddit")}
    session = NativeSimulationSession(
        root, graph_id="graph-fixture", simulation_id="sim-fixture",
        models=models, seed=19, max_rounds=1, timeout_seconds=120)
    before_rng = random.getstate()
    environments = []
    with session:
        assert random.getstate() == before_rng
        for platform in ("twitter", "reddit"):
            env = session._results[platform].env
            environments.append(env)
            rows = env.platform.db.execute(
                "SELECT post_id,user_id,content,created_at FROM post ORDER BY post_id").fetchall()
            assert [(actor, text) for _, actor, text, _ in rows[:4]] == SEEDS
            assert len(rows) > 4
            assert 0 < models[platform].calls <= 20
            entries = [json.loads(line) for line in
                       (root / platform / "actions.jsonl").read_text(encoding="utf-8").splitlines()]
            seeds = [r for r in entries if r.get("action_type") == "CREATE_POST" and r["round"] == 0]
            assert len(seeds) == 4
            assert [(r["agent_id"], r["action_args"]["content"]) for r in seeds] == SEEDS
            assert [json.loads(r["result"])["post_id"] for r in seeds] == [r[0] for r in rows[:4]]
            later = [r for r in entries if r.get("action_type") == "CREATE_POST" and r["round"] > 0]
            assert later and all(r["action_args"]["content"] != TEXT for r in later)
            assert len([r for r in entries if r.get("event_type") == "simulation_end"]) == 1
            end0 = [r for r in entries if r.get("event_type") == "round_end" and r["round"] == 0]
            assert len(end0) == 1 and end0[0]["actions_count"] == 4
            if platform == "twitter":
                assert {str(row[3]) for row in rows[:4]} == {"0"}
                assert {str(row[3]) for row in rows[4:]} == {"1"}
            assert env.platform.db.execute(
                "SELECT agent_id,user_id FROM user ORDER BY agent_id").fetchall() == [(0, 0), (1, 1)]
        assert session._results["reddit"].agent_graph.get_agent(0).user_info.name == "actor0"
        from scripts import run_parallel_simulation as runner
        assert len(runner.TWITTER_ACTIONS) == 6 and len(runner.REDDIT_ACTIONS) == 13
        with pytest.raises(NeutralCapabilityError, match="invalid_request"):
            session.start()
    assert original == {name: (root / name).read_bytes() for name in INPUTS}
    assert session._closed and all(env.platform_task.done() for env in environments)
    assert (root / ".native_prepared_start_claim").exists()
    with pytest.raises(NeutralCapabilityError, match="invalid_request"):
        NativeSimulationSession(root, graph_id="graph-fixture", simulation_id="sim-fixture",
                                models=models, seed=19, max_rounds=1)


@pytest.mark.parametrize("platform", ["twitter", "reddit"])
def test_owned_seed_failure_retains_claim_partial_output_no_end(tmp_path, monkeypatch, platform):
    monkeypatch.chdir(tmp_path)
    from app.services.native_simulation import NativeSimulationSession
    from app.services.knowledge_report_tools import NeutralCapabilityError
    from test_native_prepared_workflow import _offline_model
    root, config = _prepared_seeds(tmp_path)
    other = "reddit" if platform == "twitter" else "twitter"
    config.pop(f"{other}_config")
    (root / "simulation_config.json").write_text(json.dumps(config), encoding="utf-8")
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    state[f"enable_{other}"] = False
    (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
    original = {name: (root / name).read_bytes() for name in INPUTS}
    model = _offline_model()

    class RejectSecond(NativeSimulationSession):
        async def apply_initial_network(self, selected, env, action_logger, agent_names):
            count = await super().apply_initial_network(selected, env, action_logger, agent_names)
            self.observed_env = env
            env.platform.db.executescript(
                "CREATE TRIGGER reject_seed BEFORE INSERT ON post "
                "WHEN NEW.content = 'second' BEGIN "
                "SELECT RAISE(FAIL, 'fixture-private-error'); END;")
            return count

    session = RejectSecond(root, graph_id="graph-fixture", simulation_id="sim-fixture",
                           models={platform: model}, seed=19, max_rounds=1,
                           timeout_seconds=120)
    with pytest.raises(NeutralCapabilityError, match="internal_error"):
        session.start()
    assert model.calls == 0 and session._closed
    assert session.observed_env.platform_task.done()
    assert (root / ".native_prepared_start_claim").exists()
    import sqlite3
    with sqlite3.connect(root / f"{platform}_simulation.db") as db:
        assert db.execute("SELECT user_id,content FROM post ORDER BY post_id").fetchall() == SEEDS[:1]
        assert db.execute("SELECT COUNT(*) FROM trace WHERE action='create_post'").fetchone()[0] == 1
    entries = [json.loads(line) for line in
               (root / platform / "actions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert not any(r.get("event_type") == "simulation_end" for r in entries)
    records = [r for r in entries if r.get("action_type") == "CREATE_POST"]
    assert len(records) == 2 and [r["success"] for r in records] == [True, False]
    assert all(r["round"] == 0 for r in records)
    assert "fixture-private-error" not in json.dumps(entries)
    assert original == {name: (root / name).read_bytes() for name in INPUTS}
    with pytest.raises(NeutralCapabilityError, match="invalid_request"):
        NativeSimulationSession(root, graph_id="graph-fixture", simulation_id="sim-fixture",
                                models={platform: model}, seed=19, max_rounds=1)

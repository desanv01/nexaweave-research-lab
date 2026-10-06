"""Offline boundary cases. Native SDK proof lives in engine_tests separately."""
import asyncio
import json
import random
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from mirofish_execution import native_seed_contracts
from mirofish_execution.native_seed_contracts import validate_native_seed_plan
from scripts.native_seed_posts import NativeSeedError, apply_native_seed_posts


def _config(posts):
    return {"agent_configs": [{"agent_id": 0}, {"agent_id": 1}],
            "event_config": {"initial_posts": posts}}


def _post(actor=0, content="same 雨 🐟 <script>"):
    return {"poster_agent_id": actor, "content": content}


class Boundary:
    """Only the pure helper seam is mocked; these are not engine proofs."""
    def __init__(self, mode="success", fail_at=1):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(
            "CREATE TABLE post(post_id INTEGER PRIMARY KEY, user_id, content);"
            "CREATE TABLE trace(user_id, action, info);")
        self.calls = []
        self.refreshes = 0
        self.records = []
        self.mode = mode
        self.fail_at = fail_at
        self.clock = SimpleNamespace(time_step=0)
        self.env = SimpleNamespace(
            platform_type=SimpleNamespace(value="twitter"),
            platform=SimpleNamespace(db=self.db, sandbox_clock=self.clock,
                                     update_rec_table=self.refresh),
            agent_graph=SimpleNamespace(get_agent=self.actor))

    async def refresh(self):
        self.refreshes += 1
        assert self.calls == []

    def actor(self, actor_id):
        async def action(name, *, content):
            assert name == "create_post"
            self.calls.append((actor_id, content))
            mode = self.mode if len(self.calls) == self.fail_at else "success"
            if mode == "throw":
                raise RuntimeError("SECRET provider/path text")
            if mode == "cancel":
                raise asyncio.CancelledError()
            if mode == "failure":
                return {"success": False, "error": "SECRET provider/path text"}
            if mode == "malformed":
                return None
            cur = self.db.execute("INSERT INTO post(user_id,content) VALUES (?,?)",
                                  (actor_id, content))
            post_id = cur.lastrowid
            trace_content = "wrong" if mode == "wrong_trace" else content
            self.db.execute("INSERT INTO trace VALUES (?,?,?)", (
                actor_id, "create_post", json.dumps(
                    {"post_id": post_id, "content": trace_content})))
            if mode != "uncommitted":
                self.db.commit()
            if mode == "partial_failure":
                return {"success": False, "error": "SECRET"}
            if mode == "bool_id":
                return {"success": True, "post_id": True}
            if mode == "extra_response":
                return {"success": True, "post_id": post_id, "extra": "SECRET"}
            return {"success": True, "post_id": post_id}
        return SimpleNamespace(social_agent_id=actor_id, perform_action_by_data=action)

    def log_action(self, **record):
        if record["success"]:
            post_id = json.loads(record["result"])["post_id"]
            assert self.db.execute("SELECT COUNT(*) FROM post WHERE post_id=?",
                                   (post_id,)).fetchone()[0] == 1
            assert not self.db.in_transaction
        self.records.append(record)


@pytest.fixture
def boundary():
    item = Boundary()
    yield item
    item.db.close()


@pytest.mark.parametrize("bad", [
    {}, {"content": "missing actor"}, {"poster_agent_id": 0},
    _post(True), _post(-1), _post(2), _post(0, None), _post(0, 7),
    _post(0, "\ud800"), [],
])
def test_whole_batch_invalid_tail_has_no_effects(boundary, bad):
    with pytest.raises(ValueError, match="invalid native seed plan"):
        asyncio.run(apply_native_seed_posts(
            boundary.env, _config([_post(), bad]), "twitter", boundary))
    assert boundary.calls == [] and boundary.refreshes == 0
    assert boundary.records == [] and boundary.clock.time_step == 0


@pytest.mark.parametrize("actors", [None, [], [{"agent_id": True}],
                                   [{"agent_id": 1}], [{"agent_id": 0}, {"agent_id": 0}]])
def test_nonempty_plan_requires_exact_admitted_actor_ids(actors):
    config = _config([_post()])
    config["agent_configs"] = actors
    with pytest.raises(ValueError):
        validate_native_seed_plan(config)


@pytest.mark.parametrize("config", [{}, {"event_config": {}},
                                   {"event_config": {"initial_posts": []}}])
def test_empty_plan_keeps_minimal_binding_and_clock(boundary, config):
    boundary.db.execute("INSERT INTO trace VALUES (0,'follow','{}')")
    boundary.db.commit()
    result = asyncio.run(apply_native_seed_posts(boundary.env, config, "twitter"))
    assert result.successful_count == 0 and result.trace_cursor == 1
    assert boundary.refreshes == 0 and boundary.clock.time_step == 0


@pytest.mark.parametrize("logger", [True, False])
@pytest.mark.parametrize("platform", ["twitter", "reddit"])
def test_exact_interleaved_repeated_seeds_count_without_logger(boundary, logger, platform):
    boundary.env.platform_type.value = platform
    posts = [_post(0), _post(1, "其他\n"), _post(0), _post(1, "")]
    rng = random.getstate()
    result = asyncio.run(apply_native_seed_posts(
        boundary.env, _config(posts), platform, boundary if logger else None))
    assert boundary.calls == [(p["poster_agent_id"], p["content"]) for p in posts]
    assert result.successful_count == 4 and result.trace_cursor == 4
    assert boundary.refreshes == 1
    assert boundary.clock.time_step == (1 if platform == "twitter" else 0)
    assert random.getstate() == rng
    assert len(boundary.records) == (4 if logger else 0)
    if logger:
        assert [json.loads(r["result"])["post_id"] for r in boundary.records] == [1, 2, 3, 4]
        assert all(r["round_num"] == 0 and r["success"] is True for r in boundary.records)


@pytest.mark.parametrize("mode", ["failure", "partial_failure", "malformed", "throw",
                                  "bool_id", "extra_response", "wrong_trace", "uncommitted"])
def test_failed_or_unknown_seed_stops_no_retry_no_completion(boundary, mode):
    boundary.mode, boundary.fail_at = mode, 2
    with pytest.raises(NativeSeedError) as caught:
        asyncio.run(apply_native_seed_posts(
            boundary.env, _config([_post(0), _post(1), _post(0)]), "twitter", boundary))
    assert "SECRET" not in str(caught.value)
    assert len(boundary.calls) == 2 and boundary.refreshes == 1
    assert boundary.clock.time_step == 0
    assert len(boundary.records) == (2 if mode in ("failure", "partial_failure") else 1)
    assert boundary.records[0]["success"] is True
    if mode in ("failure", "partial_failure"):
        assert boundary.records[-1]["success"] is False
        assert boundary.records[-1]["result"] == "native_seed_response_failed"
    assert "SECRET" not in json.dumps(boundary.records)
    if mode == "partial_failure":
        assert boundary.db.execute("SELECT COUNT(*) FROM post").fetchone()[0] == 2


def test_cancelled_action_propagates_no_fabricated_record(boundary):
    boundary.mode = "cancel"
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(apply_native_seed_posts(
            boundary.env, _config([_post(), _post(1)]), "twitter", boundary))
    assert len(boundary.calls) == 1 and boundary.records == []
    assert boundary.clock.time_step == 0


def test_platform_mismatch_precedes_effects(boundary):
    with pytest.raises(NativeSeedError, match="native_seed_platform_mismatch"):
        asyncio.run(apply_native_seed_posts(boundary.env, _config([_post()]), "reddit"))
    assert boundary.calls == [] and boundary.refreshes == 0


def test_missing_actual_actor_refuses_whole_batch(boundary):
    original = boundary.env.agent_graph.get_agent
    def missing(actor_id):
        if actor_id == 1:
            raise KeyError("private path")
        return original(actor_id)
    boundary.env.agent_graph.get_agent = missing
    with pytest.raises(NativeSeedError):
        asyncio.run(apply_native_seed_posts(
            boundary.env, _config([_post(), _post(1)]), "twitter"))
    assert boundary.calls == [] and boundary.refreshes == 0


def test_contract_snapshot_and_no_text_normalization():
    posts = [_post(0, "e\u0301"), _post(0, "é")]
    plan = validate_native_seed_plan(_config(posts))
    posts[0]["content"] = "changed"
    assert [p.content for p in plan] == ["e\u0301", "é"]


def test_shared_contract_is_sdk_and_factory_cold():
    # A fresh isolated interpreter blocks SDK/backend imports, then admits and
    # refuses plans. No import cache can conceal an accidental eager dependency.
    source = '''
import sys
sys.path.insert(0, sys.argv[1])
class Block:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'oasis', 'camel', 'app', 'openai'}:
            raise AssertionError('hot dependency import')
sys.meta_path.insert(0, Block())
from mirofish_execution.native_seed_contracts import validate_native_seed_plan
assert validate_native_seed_plan({}) == ()
try:
    validate_native_seed_plan({'event_config': {'initial_posts': [{}]}})
except ValueError:
    pass
else:
    raise AssertionError('malformed plan admitted')
assert not any(n.split('.')[0] in {'oasis', 'camel', 'app', 'openai'} for n in sys.modules)
'''
    completed = subprocess.run(
        [sys.executable, "-I", "-c", source,
         str(Path(native_seed_contracts.__file__).resolve().parent.parent)],
        capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr

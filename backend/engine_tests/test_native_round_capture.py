"""One real offline native Twitter round and its materialized recording cut."""

import json
import sqlite3
from contextlib import closing
from hashlib import sha256
from itertools import islice

import pytest

from app.services.knowledge_report_tools import NeutralCapabilityError
from app.services.native_recording_contracts import RecordingAnchors
from app.services.native_simulation import NativeSimulationSession
from test_native_prepared_workflow import _offline_model, _prepared
from test_native_recordings import _offline_boundary  # noqa: F401 (autouse fixture)


_LOG_LIMIT = 1024 * 1024
_DATABASE_LIMIT = 64 * 1024 * 1024
_MANIFEST_LIMIT = 7200
_ROW_LIMIT = 128


def _read_bounded(path, limit):
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    assert 0 < len(data) <= limit
    return data


def _artifact(path, limit):
    before = path.stat()
    assert path.is_file() and not path.is_symlink()
    assert 0 < before.st_size <= limit
    digest, size = sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            size += len(chunk)
            assert size <= limit
            digest.update(chunk)
    after = path.stat()
    assert size == before.st_size == after.st_size
    assert (before.st_dev, before.st_ino, before.st_mtime_ns) == (
        after.st_dev, after.st_ino, after.st_mtime_ns)
    return size, digest.hexdigest(), after.st_dev, after.st_ino, after.st_mtime_ns


def _native_rows(path):
    """Read the native tables, not the capture module's evidence helpers."""
    assert path.is_file() and not path.is_symlink()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
        db.execute("BEGIN")
        max_rowid, = db.execute("SELECT MAX(rowid) FROM trace").fetchone()
        trace = db.execute(
            "SELECT rowid, user_id, action, info FROM trace ORDER BY rowid LIMIT ?",
            (_ROW_LIMIT + 1,),
        ).fetchall()
        posts = db.execute(
            "SELECT post_id, user_id, content FROM post ORDER BY post_id LIMIT ?",
            (_ROW_LIMIT + 1,),
        ).fetchall()
    assert 0 < len(trace) <= _ROW_LIMIT and 0 < len(posts) <= _ROW_LIMIT
    assert max_rowid == trace[-1][0]
    return max_rowid, trace, posts


def test_actual_twitter_round_capture_preserves_native_cut(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = _prepared(tmp_path)
    state_path = root / "state.json"
    state = json.loads(_read_bounded(state_path, _MANIFEST_LIMIT))
    state["enable_reddit"] = False
    state_path.write_text(json.dumps(state), encoding="utf-8")
    config_path = root / "simulation_config.json"
    config = json.loads(_read_bounded(config_path, _MANIFEST_LIMIT))
    del config["reddit_config"]
    config_path.write_text(json.dumps(config), encoding="utf-8")

    destination = tmp_path / "round-one"
    anchors = RecordingAnchors("graph-fixture", "sim-fixture", "capture-run",
                               "parent-branch", "capture-project", 1)
    model = _offline_model()
    session = NativeSimulationSession(
        root, graph_id=anchors.graph_id, simulation_id=anchors.simulation_id,
        models={"twitter": model}, seed=7, max_rounds=1,
        round_capture={"destination": destination, "anchors": anchors},
    )
    assert session.round_capture_receipt is None and not destination.exists()
    native = root / "twitter_simulation.db"
    observed_watermarks = []
    begin_capture_round = session.begin_capture_round

    def observe_before_round(*args, **kwargs):
        assert not observed_watermarks
        assert native.is_file() and not native.is_symlink()
        with closing(sqlite3.connect(native.as_uri() + "?mode=ro", uri=True, timeout=0)) as db:
            post_id, = db.execute("SELECT MAX(post_id) FROM post").fetchone()
        assert type(post_id) is int and post_id > 0  # The prepared fixture seeds an initial post.
        observed_watermarks.append(post_id)
        return begin_capture_round(*args, **kwargs)

    monkeypatch.setattr(session, "begin_capture_round", observe_before_round)
    with session:
        assert set(session._results) == {"twitter"}
        receipt = session.round_capture_receipt
        assert receipt is not None
        prior_post_id, = observed_watermarks
        assert type(receipt["prior_post_id"]) is int
        assert receipt["prior_post_id"] == prior_post_id
        assert receipt["anchors"] == anchors.wire()
        assert receipt["round"] == 1
        assert receipt["full_checkpoint"] is False
        assert receipt["continuation_supported"] is False
        detached = session.round_capture_receipt
        detached["anchors"]["run_id"] = "mutated-copy"
        assert session.round_capture_receipt == receipt

        database = destination / "native.sqlite3"
        manifest_name = receipt["manifest"]
        assert isinstance(manifest_name, str) and manifest_name not in {"", ".", ".."}
        manifest = destination / manifest_name
        assert manifest.parent == destination and manifest.name == manifest_name
        entries = sorted(islice(destination.iterdir(), 4))
        assert len(entries) == 3 and database in entries and manifest in entries
        log, = set(entries) - {database, manifest}
        limits = {database: _DATABASE_LIMIT, manifest: _MANIFEST_LIMIT, log: _LOG_LIMIT}
        copied_before = {file: _artifact(file, limit) for file, limit in limits.items()}
        assert copied_before[database][:2] == (receipt["database_bytes"], receipt["database_sha256"])
        assert copied_before[log][:2] == (receipt["log_bytes"], receipt["log_sha256"])
        materialized = json.loads(_read_bounded(manifest, _MANIFEST_LIMIT))
        assert type(materialized["prior_post_id"]) is int
        for key in ("anchors", "round", "prior_post_id", "prior_cursor", "cursor", "database_bytes",
                    "database_sha256", "log_bytes", "log_sha256", "post_count"):
            assert materialized[key] == receipt[key]

        source_rows = _native_rows(native)
        assert _native_rows(database) == source_rows
        cursor, trace, posts = source_rows
        before_cursor = receipt["prior_cursor"]
        assert type(before_cursor) is int and 0 < before_cursor < cursor == receipt["cursor"]
        assert before_cursor in {rowid for rowid, *_ in trace}
        selected = [(action, user, info) for rowid, user, action, info in trace
                    if before_cursor < rowid <= cursor]
        assert selected and {action for action, _, _ in selected} <= {"refresh", "create_post"}
        mutations = []
        for action, user, info in selected:
            if action == "create_post":
                payload = json.loads(info)
                mutations.append((payload["post_id"], user, payload["content"]))
        assert len(mutations) == receipt["post_count"] > 0
        assert all(type(post_id) is int and post_id > prior_post_id for post_id, _, _ in mutations)
        assert len({post_id for post_id, _, _ in mutations}) == len(mutations)
        assert all(post in posts and post[2].startswith("Synthetic native post ") for post in mutations)
        assert any(content == "Synthetic initial event" for _, _, content in posts)

        parent_log = root / "twitter" / "actions.jsonl"
        prefix = _read_bounded(log, _LOG_LIMIT)
        parent = _read_bounded(parent_log, _LOG_LIMIT)
        assert prefix.endswith(b"\n") and parent.startswith(prefix) and len(parent) > len(prefix)
        events = [json.loads(line) for line in prefix.splitlines()]
        end = events[-1]
        assert events[0]["event_type"] == "simulation_start"
        assert end["event_type"] == "round_end" and end["round"] == 1
        assert not any(event.get("event_type") == "simulation_end" for event in events)
        suffix = [json.loads(line) for line in parent[len(prefix):].splitlines()]
        assert len(suffix) == 1 and suffix[0]["event_type"] == "simulation_end"
        assert suffix[0]["platform"] == "twitter" and suffix[0]["total_rounds"] == 1

        round_actions = [event for event in events
                         if event.get("round") == 1 and "action_type" in event]
        logged_posts = [event for event in round_actions if event["action_type"] == "CREATE_POST"]
        assert end["actions_count"] == len(round_actions)
        assert len(logged_posts) == len(mutations)
        names = {0: "Mira Vale", 1: "Harbor Labs"}
        for event, (post_id, user, content) in zip(logged_posts, mutations, strict=True):
            assert event["success"] is True
            assert (event["agent_id"], event["agent_name"]) == (user, names[user])
            assert event["action_args"]["content"] == content
            assert event["action_args"]["post_id"] == post_id

        model_calls = model.calls
        assert model_calls > 0
        with pytest.raises(NeutralCapabilityError) as refused:
            session.start()
        assert refused.value.code == "invalid_request"
        assert observed_watermarks == [prior_post_id]
        assert model.calls == model_calls
        assert session.round_capture_receipt == receipt
        assert sorted(islice(destination.iterdir(), 4)) == entries
        assert {file: _artifact(file, limit) for file, limit in limits.items()} == copied_before
        assert _native_rows(native) == source_rows
        assert _read_bounded(parent_log, _LOG_LIMIT) == parent

    assert session._closed and session._results == {}
    assert not (root / "reddit_simulation.db").exists()

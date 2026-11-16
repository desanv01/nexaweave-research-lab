"""One real offline native Twitter round and its materialized recording cut."""

import json
import random
import sqlite3
import struct
from contextlib import closing
from hashlib import sha256
from itertools import islice
from uuid import UUID

import pytest
from camel.types import OpenAIBackendRole

from app.services.knowledge_report_tools import NeutralCapabilityError
from app.services.native_checkpoint_memory import restore_native_memory
from app.services.native_recording_contracts import RecordingAnchors
from app.services.native_simulation import NativeSimulationSession
from test_native_prepared_workflow import _offline_model, _prepared
from test_native_recordings import _offline_boundary  # noqa: F401 (autouse fixture)


_LOG_LIMIT = 1024 * 1024
_DATABASE_LIMIT = 64 * 1024 * 1024
_MANIFEST_LIMIT = 7200
_ROW_LIMIT = 128
_MEMORY_LIMIT = 2 * 1024 * 1024
_MEMORY_HEADER_LIMIT = 32
_RUNNER_BYTES = 2564


def _assert_runner_artifact(path, descriptor, cut_state, config_sha256, total_actions):
    assert set(descriptor) == {"version", "file", "bytes", "sha256"}
    assert type(descriptor["version"]) is int and descriptor["version"] == 1
    assert descriptor["file"] == path.name == "native-runner.bin"
    raw = _read_bounded(path, _RUNNER_BYTES)
    assert type(descriptor["bytes"]) is int and len(raw) == descriptor["bytes"] == _RUNNER_BYTES
    assert sha256(raw).hexdigest() == descriptor["sha256"]

    magic, version, completed, horizon, minutes, offset, actions, digest = struct.unpack_from(
        ">4sB5I32s", raw,
    )
    assert magic == b"NWR1" and version == 3
    assert (completed, horizon, minutes, offset, actions) == (1, 2, 60, 0, total_actions)
    assert completed + 1 == horizon and offset + minutes == 60
    assert digest == config_sha256

    words = struct.unpack_from(">624I", raw, 57)
    index, cache_tag, cache = struct.unpack_from(">HBd", raw, 2553)
    assert index <= 624 and cache_tag in (0, 1)
    # For an absent cache, require the canonical positive-zero bytes.
    assert cache_tag or raw[-8:] == bytes(8)
    recorded = (version, (*words, index), cache if cache_tag else None)
    assert recorded == cut_state
    expected, restored = random.Random(0), random.Random(0)
    expected.setstate(cut_state)
    restored.setstate(recorded)
    assert restored.sample((0, 1), 1) == expected.sample((0, 1), 1)
    assert restored.random() == expected.random()
    assert restored.getstate() == expected.getstate()


def _role_tag(value):
    if value is OpenAIBackendRole.SYSTEM:
        return {"$camel_role_name": "system"}
    raise TypeError("unexpected type in native memory fixture")


def _memory_wire(value, remaining):
    """Independent, ordered snapshot from live fixture values."""
    encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"),
                               allow_nan=False, default=_role_tag)
    result = bytearray()
    for part in encoder.iterencode(value):
        assert len(part) <= remaining - len(result)
        chunk = part.encode("utf-8")
        assert len(chunk) <= remaining - len(result)
        result.extend(chunk)
    return bytes(result)


def _same_parent_memories(observed):
    for agent, memory, agent_id, creator, records, context, has_tool in observed.values():
        assert agent.agent_id == memory.agent_id == agent_id
        assert agent.memory is memory and memory.get_context_creator() is creator
        live = memory._chat_history_block.storage.memory_list
        assert _memory_wire(live, len(records)) == records
        assert _memory_wire(memory.get_context(), len(context)) == context


def _decimal(field):
    assert field and field.isdigit() and (field == b"0" or not field.startswith(b"0"))
    return int(field)


def _json_without_duplicate_keys(raw):
    def unique(items):
        result = {}
        for key, value in items:
            assert key not in result
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def _assert_memory_artifact(path, descriptor, observed, active_id):
    assert set(descriptor) == {"version", "filename", "coverage", "agent_count", "bytes", "sha256"}
    assert type(descriptor["version"]) is int and descriptor["version"] == 1
    assert descriptor["filename"] == path.name == "native-memory.bin"
    assert descriptor["coverage"] == "all_registered_agents"
    assert type(descriptor["agent_count"]) is int and descriptor["agent_count"] == len(observed) == 2
    size = descriptor["bytes"]
    assert type(size) is int and 0 < size <= _MEMORY_LIMIT
    assert _artifact(path, _MEMORY_LIMIT)[:2] == (size, descriptor["sha256"])
    assert {agent_id for agent_id, snapshot in observed.items() if snapshot[-1]} == {active_id}
    with path.open("rb") as stream:
        assert stream.read(5) == b"NWM1\n"
        count_line = stream.readline(_MEMORY_HEADER_LIMIT + 1)
        assert len(count_line) <= _MEMORY_HEADER_LIMIT and count_line.endswith(b"\n")
        assert _decimal(count_line[:-1]) == len(observed)
        for expected_id in sorted(observed):
            header = stream.readline(_MEMORY_HEADER_LIMIT + 1)
            assert len(header) <= _MEMORY_HEADER_LIMIT and header.endswith(b"\n")
            raw_id, raw_count = header[:-1].split(b" ")
            assert _decimal(raw_id) == expected_id
            count = _decimal(raw_count)
            assert 0 < count <= size - stream.tell()
            raw = stream.read(count)
            assert len(raw) == count
            payload = _json_without_duplicate_keys(raw)
            _, parent_memory, actual_uuid, creator, records, context, has_tool = observed[expected_id]
            assert set(payload) == {"kind", "version", "agent_id", "window_size",
                                    "keep_rate", "token_limit", "records"}
            assert payload["kind"] == "native_camel_memory"
            assert type(payload["version"]) is int and payload["version"] == 1
            assert payload["agent_id"] == actual_uuid
            assert payload["window_size"] == parent_memory._window_size
            assert payload["keep_rate"] == parent_memory._chat_history_block.keep_rate
            assert payload["token_limit"] == creator.token_limit
            assert _memory_wire(payload["records"], len(records)) == records
            assert any(record["message"]["__class__"] == "FunctionCallingMessage"
                       for record in payload["records"]) is has_tool
            restored = restore_native_memory(raw, expected_agent_id=actual_uuid, context_creator=creator)
            assert restored is not parent_memory and restored.agent_id == actual_uuid
            assert _memory_wire(restored._chat_history_block.storage.memory_list, len(records)) == records
            assert _memory_wire(restored.get_context(), len(context)) == context
            del restored, payload, raw
        assert stream.tell() == size and stream.read(1) == b""


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
    config["time_config"]["agents_per_hour_min"] = 1
    config["time_config"]["agents_per_hour_max"] = 1
    config["time_config"]["total_simulation_hours"] = 2
    config["time_config"]["minutes_per_round"] = 60
    config_bytes = json.dumps(config).encode("utf-8")
    config_path.write_bytes(config_bytes)
    config_sha256 = sha256(config_bytes).digest()

    destination = tmp_path / "round-one"
    anchors = RecordingAnchors("graph-fixture", "sim-fixture", "capture-run",
                               "parent-branch", "capture-project", 1)
    model = _offline_model()
    ambient_rng = random.getstate()
    session = NativeSimulationSession(
        root, graph_id=anchors.graph_id, simulation_id=anchors.simulation_id,
        models={"twitter": model}, seed=7, max_rounds=1,
        round_capture={"destination": destination, "anchors": anchors,
                       "include_memory": True, "include_runner_state": True},
    )
    assert session.round_capture_receipt is None and not destination.exists()
    native = root / "twitter_simulation.db"
    observed_watermarks = []
    observed_selections = []
    begin_capture_round = session.begin_capture_round

    def observe_before_round(result, round_num, active_agents, prior_cursor):
        assert not observed_watermarks
        assert round_num == 1 and len(active_agents) == 1
        selected_id, = (agent_id for agent_id, _ in active_agents)
        assert type(selected_id) is int
        observed_selections.append(selected_id)
        assert native.is_file() and not native.is_symlink()
        with closing(sqlite3.connect(native.as_uri() + "?mode=ro", uri=True, timeout=0)) as db:
            post_id, = db.execute("SELECT MAX(post_id) FROM post").fetchone()
        assert type(post_id) is int and post_id > 0  # The prepared fixture seeds an initial post.
        observed_watermarks.append(post_id)
        return begin_capture_round(result, round_num, active_agents, prior_cursor)

    monkeypatch.setattr(session, "begin_capture_round", observe_before_round)
    owner = session._round_capture
    copy_at_cut = owner._copy_at_cut
    observed = {}
    observed_rng = []

    def observe_at_cut(*args, **kwargs):
        assert not observed
        env, barrier = owner._env, owner._barrier
        assert owner._sealed and barrier is not None
        assert barrier.acknowledged.done() and barrier.acknowledged.result() is True
        assert not barrier.release.is_set()
        assert not observed_rng
        observed_rng.append(random.getstate())
        assert not env.platform_task.done()
        assert len(env.agent_graph.agent_mappings) == 2
        agents = dict(env.agent_graph.get_agents())
        assert set(agents) == {0, 1}
        remaining = _MEMORY_LIMIT
        for platform_id, agent in agents.items():
            memory = agent.memory
            actual_uuid = agent.agent_id
            assert agent.social_agent_id == agent.env.action.agent_id == platform_id
            assert type(actual_uuid) is str and memory.agent_id == actual_uuid
            assert not any(actual_uuid == item[2] for item in observed.values())
            assert UUID(actual_uuid)
            creator = memory.get_context_creator()
            live = memory._chat_history_block.storage.memory_list
            records = _memory_wire(live, remaining)
            remaining -= len(records)
            context = _memory_wire(memory.get_context(), remaining)
            remaining -= len(context)
            has_tool = any(record["message"]["__class__"] == "FunctionCallingMessage"
                           for record in live)
            observed[platform_id] = (agent, memory, actual_uuid, creator, records, context, has_tool)
        result = copy_at_cut(*args, **kwargs)
        assert owner._barrier is barrier and owner._sealed and not env.platform_task.done()
        assert not barrier.release.is_set()
        assert observed_rng == [random.getstate()]
        _same_parent_memories(observed)
        return result

    monkeypatch.setattr(owner, "_copy_at_cut", observe_at_cut)
    with session:
        assert random.getstate() == ambient_rng
        assert set(session._results) == {"twitter"}
        assert set(observed) == {0, 1}
        _same_parent_memories(observed)
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
        memory_file = destination / "native-memory.bin"
        runner_file = destination / "native-runner.bin"
        known_files = {database, manifest, memory_file, runner_file}
        entries = sorted(islice(destination.iterdir(), 6))
        assert len(entries) == 5 and known_files <= set(entries)
        log, = set(entries) - known_files
        limits = {database: _DATABASE_LIMIT, manifest: _MANIFEST_LIMIT,
                  log: _LOG_LIMIT, memory_file: _MEMORY_LIMIT, runner_file: _RUNNER_BYTES}
        copied_before = {file: _artifact(file, limit) for file, limit in limits.items()}
        assert copied_before[database][:2] == (receipt["database_bytes"], receipt["database_sha256"])
        assert copied_before[log][:2] == (receipt["log_bytes"], receipt["log_sha256"])
        materialized = json.loads(_read_bounded(manifest, _MANIFEST_LIMIT))
        assert materialized["memory"] == receipt["memory"]
        assert materialized["runner_state"] == receipt["runner_state"]
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
        active_id, = {user for _, user, _ in mutations}
        assert active_id in observed
        assert observed_selections == [active_id]
        inactive_id, = set(observed) - set(observed_selections)
        assert not observed[inactive_id][-1]
        _assert_memory_artifact(memory_file, receipt["memory"], observed, active_id)
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
        cut_rng, = observed_rng
        _assert_runner_artifact(runner_file, receipt["runner_state"], cut_rng,
                                config_sha256, suffix[0]["total_actions"])
        assert random.getstate() == ambient_rng

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
        assert sorted(islice(destination.iterdir(), 6)) == entries
        assert {file: _artifact(file, limit) for file, limit in limits.items()} == copied_before
        assert _native_rows(native) == source_rows
        assert _read_bounded(parent_log, _LOG_LIMIT) == parent
        _same_parent_memories(observed)

    assert session._closed and session._results == {}
    assert random.getstate() == ambient_rng
    assert not (root / "reddit_simulation.db").exists()

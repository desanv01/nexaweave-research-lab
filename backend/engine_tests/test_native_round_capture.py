"""One real offline native Twitter round and its materialized recording cut."""

import asyncio
import json
import random
import sqlite3
import struct
from contextlib import closing
from datetime import datetime
from hashlib import sha256
from itertools import islice
from time import monotonic
from uuid import UUID

import pytest
from camel.types import OpenAIBackendRole

from app.services import native_checkpoint_memory
from app.services.knowledge_report_tools import NeutralCapabilityError
from app.services.native_recording_contracts import RecordingAnchors, RecordingError
from app.services.native_recordings import (
    _RoundMemoryLayout,
    _verify_twitter_round_recording_with_runner,
    verify_twitter_round_recording,
)
from app.services.native_simulation import (
    NativeSimulationSession,
    open_prestart_twitter_child,
    open_staged_twitter_child_writer,
)
from test_native_prepared_workflow import _offline_model, _prepared
from test_native_recordings import _offline_boundary  # noqa: F401 (autouse fixture)


_LOG_LIMIT = 1024 * 1024
_DATABASE_LIMIT = 64 * 1024 * 1024
_MANIFEST_LIMIT = 7200
_ROW_LIMIT = 128
_RECOMMENDATION_ROW_LIMIT = 256
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
    for agent, memory, agent_id, creator, records, context, record_count, _ in observed.values():
        assert agent.agent_id == memory.agent_id == agent_id
        assert agent.memory is memory and memory.get_context_creator() is creator
        live = memory._chat_history_block.storage.memory_list
        assert len(live) == record_count
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
    witnesses = []
    with path.open("rb") as stream:
        magic = stream.read(5)
        assert magic == b"NWM1\n"
        count_line = stream.readline(_MEMORY_HEADER_LIMIT + 1)
        assert len(count_line) <= _MEMORY_HEADER_LIMIT and count_line.endswith(b"\n")
        assert _decimal(count_line[:-1]) == len(observed)
        prefix = magic + count_line
        for expected_id in sorted(observed):
            header = stream.readline(_MEMORY_HEADER_LIMIT + 1)
            assert len(header) <= _MEMORY_HEADER_LIMIT and header.endswith(b"\n")
            raw_id, raw_count = header[:-1].split(b" ")
            assert _decimal(raw_id) == expected_id
            count = _decimal(raw_count)
            payload_offset = stream.tell()
            assert 0 < count <= size - payload_offset
            if not witnesses:
                prefix += header
            raw = stream.read(count)
            assert len(raw) == count
            payload = _json_without_duplicate_keys(raw)
            (_, parent_memory, actual_uuid, creator, records, context,
             record_count, has_tool) = observed[expected_id]
            witnesses.append((expected_id, payload_offset, count, sha256(raw).hexdigest(),
                              actual_uuid, creator.token_limit, record_count))
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
            restored = native_checkpoint_memory.restore_native_memory(
                raw, expected_agent_id=actual_uuid, context_creator=creator,
            )
            assert restored is not parent_memory and restored.agent_id == actual_uuid
            assert _memory_wire(restored._chat_history_block.storage.memory_list, len(records)) == records
            assert _memory_wire(restored.get_context(), len(context)) == context
            del restored, payload, raw
        assert stream.tell() == size and stream.read(1) == b""
    return tuple(witnesses), prefix


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
            "SELECT rowid, user_id, action, info, created_at FROM trace ORDER BY rowid LIMIT ?",
            (_ROW_LIMIT + 1,),
        ).fetchall()
        posts = db.execute(
            "SELECT post_id, user_id, content, created_at FROM post ORDER BY post_id LIMIT ?",
            (_ROW_LIMIT + 1,),
        ).fetchall()
        recommendations = db.execute(
            "SELECT CASE WHEN typeof(user_id) = 'integer' THEN user_id END, "
            "CASE WHEN typeof(post_id) = 'integer' THEN post_id END "
            "FROM rec INDEXED BY sqlite_autoindex_rec_1 ORDER BY rec.user_id, rec.post_id LIMIT ?",
            (_RECOMMENDATION_ROW_LIMIT + 1,),
        ).fetchall()
    assert 0 < len(trace) <= _ROW_LIMIT and 0 < len(posts) <= _ROW_LIMIT
    assert len(recommendations) <= _RECOMMENDATION_ROW_LIMIT
    assert all(type(value) is int and 0 <= value < 2**63
               for pair in recommendations for value in pair)
    assert max_rowid == trace[-1][0]
    return max_rowid, trace, posts, recommendations


def _observed_tick(clock):
    tick = clock.time_step
    assert type(tick) is int and 0 <= tick < 2**63
    assert clock.get_time_step() == str(tick)
    return tick


def _assert_prestart_handles_closed(db, cursor):
    with pytest.raises(sqlite3.ProgrammingError, match=r"^Cannot operate on a closed cursor\.$"):
        cursor.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError, match=r"^Cannot operate on a closed database\.$"):
        db.execute("SELECT 1")


def _assert_existing_child_prestart(destination, database, receipt, clock, trace_row, monkeypatch):
    from oasis.social_platform.platform import Platform, PlatformUtils

    start_time = datetime(2026, 1, 1)
    clock_tick = _observed_tick(clock)
    deadline = monotonic() + 10
    options = {
        "database_bytes": receipt["database_bytes"],
        "database_sha256": receipt["database_sha256"],
        "clock": clock,
        "start_time": start_time,
        "deadline_monotonic": deadline,
    }
    with open_prestart_twitter_child(destination, **options) as platform:
        assert isinstance(platform, Platform) and type(platform) is not Platform
        assert platform.running.__func__ is Platform.running
        assert type(platform.pl_utils) is PlatformUtils
        db, cursor = platform.db, platform.db_cursor
        assert type(db) is sqlite3.Connection and type(cursor) is sqlite3.Cursor
        assert cursor.connection is db
        assert platform.pl_utils.db is db and platform.pl_utils.db_cursor is cursor
        assert platform.sandbox_clock is clock and platform.pl_utils.sandbox_clock is clock
        assert platform.start_time is start_time and platform.pl_utils.start_time is start_time
        assert not db.in_transaction
        databases = cursor.execute("PRAGMA database_list").fetchmany(2)
        assert len(databases) == 1 and databases[0][1:] == ("main", str(database))
        assert cursor.execute(
            "SELECT rowid, user_id, action, info, created_at FROM trace WHERE rowid = ?",
            (trace_row[0],),
        ).fetchone() == trace_row
        assert not db.in_transaction and _observed_tick(clock) == clock_tick
    _assert_prestart_handles_closed(db, cursor)

    failed_handles = []

    def refuse_utils(_self, *args, **kwargs):
        values = (*args, *kwargs.values())
        failure_db, = (value for value in values if type(value) is sqlite3.Connection)
        failure_cursor, = (value for value in values if type(value) is sqlite3.Cursor)
        assert failure_cursor.connection is failure_db
        assert failure_db is not db and failure_cursor is not cursor
        failed_handles.append((failure_db, failure_cursor))
        raise RuntimeError("intentional test-only PlatformUtils initialization failure")

    with monkeypatch.context() as patch:
        patch.setattr(PlatformUtils, "__init__", refuse_utils)
        with pytest.raises(NeutralCapabilityError) as refused:
            with open_prestart_twitter_child(destination, **options):
                pytest.fail("native binding failure must not yield a platform")
        assert refused.value.code == "internal_error"
    (failure_db, failure_cursor), = failed_handles
    _assert_prestart_handles_closed(failure_db, failure_cursor)
    assert _observed_tick(clock) == clock_tick


def _stage_writer_child(child_dir, checkpoint_db, receipt, deadline):
    size = receipt["database_bytes"]
    assert type(size) is int and 0 < size <= _DATABASE_LIMIT
    assert checkpoint_db.is_file() and not checkpoint_db.is_symlink()
    assert checkpoint_db.stat().st_size == size
    child_dir.mkdir(mode=0o700)
    child_db = child_dir / "native.sqlite3"
    digest = sha256()
    left = size
    with checkpoint_db.open("rb") as source, child_db.open("xb") as target:
        while left:
            assert monotonic() < deadline
            chunk = source.read(min(left, 64 * 1024))
            assert chunk
            assert target.write(chunk) == len(chunk)
            digest.update(chunk)
            left -= len(chunk)
        assert source.read(1) == b""
    assert monotonic() < deadline
    assert digest.hexdigest() == receipt["database_sha256"]
    assert child_db.stat().st_size == size and child_db.stat().st_nlink == 1
    assert not child_db.samefile(checkpoint_db)
    return child_db


async def _assert_existing_child_writer(child_dir, child_db, receipt, clock, trace_row, deadline):
    from oasis.social_platform.platform import Platform

    clock_tick = _observed_tick(clock)
    start_time = datetime(2026, 1, 1)
    existing_tasks = asyncio.all_tasks()
    async with open_staged_twitter_child_writer(
        child_dir, database_bytes=receipt["database_bytes"],
        database_sha256=receipt["database_sha256"], clock=clock,
        start_time=start_time, deadline_monotonic=deadline,
    ) as (platform, writer):
        assert isinstance(platform, Platform) and platform.running.__func__ is Platform.running
        assert isinstance(writer, asyncio.Task) and not writer.done()
        assert asyncio.all_tasks() - existing_tasks == {writer}
        assert writer.get_coro().__qualname__ == Platform.running.__qualname__
        db, cursor = platform.db, platform.db_cursor
        assert cursor.connection is db
        assert platform.pl_utils.db is db and platform.pl_utils.db_cursor is cursor
        assert platform.sandbox_clock is clock and platform.start_time is start_time
        assert cursor.execute("PRAGMA query_only").fetchone() == (0,)
        databases = cursor.execute("PRAGMA database_list").fetchmany(2)
        assert len(databases) == 1 and databases[0][1:] == ("main", str(child_db))
        assert cursor.execute(
            "SELECT rowid, user_id, action, info, created_at FROM trace WHERE rowid = ?",
            (trace_row[0],),
        ).fetchone() == trace_row
        assert not db.in_transaction and _observed_tick(clock) == clock_tick
        assert not writer.done()
    assert writer.done() and not writer.cancelled() and writer.exception() is None
    assert asyncio.all_tasks() == existing_tasks
    _assert_prestart_handles_closed(db, cursor)
    assert _observed_tick(clock) == clock_tick


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
                       "include_memory": True, "include_runner_state": True,
                       "include_engine_clock": True, "include_recommendation_state": True,
                       "include_topology": True},
    )
    assert session.round_capture_receipt is None and not destination.exists()
    native = root / "twitter_simulation.db"
    observed_watermarks = []
    observed_selections = []
    observed_clock = []
    observed_setup = []
    begin_capture_round = session.begin_capture_round

    def observe_before_round(result, round_num, active_agents, prior_cursor):
        assert not observed_watermarks
        assert round_num == 1 and len(active_agents) == 1
        selected_id, = (agent_id for agent_id, _ in active_agents)
        assert type(selected_id) is int
        observed_selections.append(selected_id)
        assert native.is_file() and not native.is_symlink()
        with closing(sqlite3.connect(native.as_uri() + "?mode=ro", uri=True, timeout=0)) as db:
            db.execute("BEGIN")
            post_id, = db.execute("SELECT MAX(post_id) FROM post").fetchone()
            setup_posts = tuple(row[0] for row in db.execute(
                "SELECT post_id FROM post ORDER BY post_id LIMIT 3"
            ))
            users, = db.execute(
                'SELECT COUNT(*) FROM (SELECT 1 FROM "user" LIMIT ?)', (_ROW_LIMIT + 1,)
            ).fetchone()
        assert type(post_id) is int and post_id > 0  # The prepared fixture seeds an initial post.
        observed_watermarks.append(post_id)
        assert type(users) is int and users == 2
        assert 0 < len(setup_posts) <= 2 and setup_posts[-1] == post_id
        assert all(type(value) is int and 0 <= value < 2**63 for value in setup_posts)
        observed_setup.append((users, setup_posts))
        clock = result.env.platform.sandbox_clock
        observed_clock.append((clock, _observed_tick(clock)))
        return begin_capture_round(result, round_num, active_agents, prior_cursor)

    monkeypatch.setattr(session, "begin_capture_round", observe_before_round)
    owner = session._round_capture
    copy_at_cut = owner._copy_at_cut
    observed = {}
    observed_rng = []
    observed_topology = []

    def observe_at_cut(*args, **kwargs):
        assert not observed
        env, barrier = owner._env, owner._barrier
        assert owner._sealed and barrier is not None
        assert barrier.acknowledged.done() and barrier.acknowledged.result() is True
        assert not barrier.release.is_set()
        (before_clock, before_step), = observed_clock
        clock = env.platform.sandbox_clock
        assert clock is before_clock
        after_step = _observed_tick(clock)
        assert after_step == before_step + 1
        observed_clock.append((clock, after_step))
        assert not observed_rng
        observed_rng.append(random.getstate())
        assert not env.platform_task.done()
        assert len(env.agent_graph.agent_mappings) == 2
        agents = dict(env.agent_graph.get_agents())
        assert set(agents) == {0, 1}
        native_graph = env.agent_graph.graph
        assert native_graph.is_directed() is True
        vertex_count = native_graph.vcount()
        assert type(vertex_count) is int and vertex_count == len(agents) == 2
        edge_count = native_graph.ecount()
        assert type(edge_count) is int and edge_count == 0
        ordered_ids = []
        for index in range(vertex_count):
            vertex = native_graph.vs[index]
            agent_id = vertex["name"]
            assert vertex.index == index
            assert type(agent_id) is int and agent_id == index
            assert env.agent_graph.agent_mappings[agent_id] is agents[agent_id]
            assert agents[agent_id].social_agent_id == agent_id
            ordered_ids.append(agent_id)
        observed_topology.append({
            "version": 1, "directed": True, "vertex_count": vertex_count,
            "edge_count": edge_count, "sha256": sha256(b"NTZ1" + bytes(ordered_ids)).hexdigest(),
        })
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
            observed[platform_id] = (agent, memory, actual_uuid, creator, records, context,
                                     len(live), has_tool)
        result = copy_at_cut(*args, **kwargs)
        assert owner._barrier is barrier and owner._sealed and not env.platform_task.done()
        assert not barrier.release.is_set()
        assert env.platform.sandbox_clock is clock and _observed_tick(clock) == after_step
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
        topology, = observed_topology
        for record in (receipt["topology"], materialized["topology"]):
            assert record == topology
            assert all(type(record[key]) is type(value) for key, value in topology.items())
        (_, before_step), (_, after_step) = observed_clock
        clock_record = {"version": 1, "before_step": before_step, "after_step": after_step}
        for clock_evidence in (receipt["engine_clock"], materialized["engine_clock"]):
            assert clock_evidence == clock_record
            assert all(type(value) is int for value in clock_evidence.values())
        assert materialized["memory"] == receipt["memory"]
        assert materialized["runner_state"] == receipt["runner_state"]
        assert type(materialized["prior_post_id"]) is int
        for key in ("anchors", "round", "prior_post_id", "prior_cursor", "cursor", "database_bytes",
                    "database_sha256", "log_bytes", "log_sha256", "post_count"):
            assert materialized[key] == receipt[key]

        source_rows = _native_rows(native)
        backup_rows = _native_rows(database)
        assert backup_rows == source_rows
        cursor, trace, posts, recommendations = source_rows
        (users, setup_posts), = observed_setup
        expected_pairs = [(ordinal, setup_id)
                          for ordinal in range(users) for setup_id in setup_posts]
        assert recommendations == backup_rows[3] == expected_pairs
        encoded = b"NRC1" + b"".join(struct.pack(">QQ", *pair) for pair in expected_pairs)
        recommendation_record = {
            "version": 1, "mode": "random", "max_rec_post_len": 2,
            "refresh_rec_post_count": 2, "following_post_count": 3,
            "use_openai_embedding": False, "users": users,
            "rows": len(expected_pairs), "sha256": sha256(encoded).hexdigest(),
        }
        for record in (receipt["recommendation_state"], materialized["recommendation_state"]):
            assert record == recommendation_record
            assert all(type(record[key]) is type(value)
                       for key, value in recommendation_record.items())
        before_cursor = receipt["prior_cursor"]
        assert type(before_cursor) is int and 0 < before_cursor < cursor == receipt["cursor"]
        assert before_cursor in {rowid for rowid, *_ in trace}
        selected = [(action, user, info, created_at) for rowid, user, action, info, created_at in trace
                    if before_cursor < rowid <= cursor]
        assert selected and {action for action, _, _, _ in selected} <= {"refresh", "create_post"}
        mutations = []
        for action, user, info, created_at in selected:
            if action == "create_post":
                assert type(created_at) is int and created_at == before_step
                payload = json.loads(info)
                mutations.append((payload["post_id"], user, payload["content"]))
        assert len(mutations) == receipt["post_count"] > 0
        assert {post for _, post in recommendations}.isdisjoint(
            post_id for post_id, _, _ in mutations
        )
        active_id, = {user for _, user, _ in mutations}
        assert active_id in observed
        assert observed_selections == [active_id]
        inactive_id, = set(observed) - set(observed_selections)
        assert not observed[inactive_id][-1]
        expected_memory_witnesses, memory_prefix = _assert_memory_artifact(
            memory_file, receipt["memory"], observed, active_id,
        )
        assert all(type(post_id) is int and post_id > prior_post_id for post_id, _, _ in mutations)
        assert len({post_id for post_id, _, _ in mutations}) == len(mutations)
        for rowid, _, action, _, created_at in backup_rows[1]:
            if before_cursor < rowid <= cursor and action == "create_post":
                assert type(created_at) is int and created_at == before_step
        for post in mutations:
            for post_rows in (posts, backup_rows[2]):
                post_created_at, = [created_at for *identity, created_at in post_rows
                                    if tuple(identity) == post]
                assert type(post_created_at) is int and post_created_at == before_step
            assert post[2].startswith("Synthetic native post ")
        assert any(content == "Synthetic initial event" for _, _, content, _ in posts)

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
    parent_database = _artifact(native, _DATABASE_LIMIT)
    parent_rows = _native_rows(native)
    _assert_existing_child_prestart(destination, database, receipt, observed_clock[-1][0],
                                    trace[-1], monkeypatch)
    assert (parent_database[0] + sum(evidence[0] for evidence in copied_before.values())
            + receipt["database_bytes"]) <= 160 * 1024 * 1024
    writer_dir = tmp_path / "writer-child"
    writer_deadline = monotonic() + 10
    manifest_sha256 = copied_before[manifest][1]
    total_actions = suffix[0]["total_actions"]
    memory_bindings = tuple(
        (platform_id, camel_id, creator.token_limit)
        for platform_id, (_, _, camel_id, creator, _, _, _, _) in sorted(observed.items())
    )
    verified, (position, decoded_config_sha256, decoded_rng), memory_witnesses = (
        _verify_twitter_round_recording_with_runner(
            destination, expected_manifest_sha256=manifest_sha256,
            expected_anchors=anchors, deadline_monotonic=writer_deadline,
            expected_runner=(2, 60, total_actions, config_sha256),
            expected_memory_bindings=memory_bindings,
        )
    )
    assert verified == materialized
    assert position == (1, 2, 60, 0, total_actions)
    assert decoded_config_sha256 == config_sha256
    assert decoded_rng == cut_rng
    assert memory_witnesses == expected_memory_witnesses
    assert random.getstate() == ambient_rng
    wrong_order = _RoundMemoryLayout((1, 2), verified["memory"]["bytes"], writer_deadline)
    with pytest.raises(RecordingError):
        wrong_order.feed(memory_prefix)

    budget = native_checkpoint_memory._MemoryAdmissionBudget(writer_deadline, max_nodes=3)
    # Each document has two value nodes and the first leaves one node for the second.
    assert native_checkpoint_memory._parse_bounded_memory_json(
        r'{"label":"[\"x\"]"}', budget,
    ) == {"label": '["x"]'}

    def unexpected_memory_parse(*_args, **_kwargs):
        pytest.fail("shared memory budget must refuse the second document before strict parsing")

    with monkeypatch.context() as patch:
        patch.setattr(native_checkpoint_memory, "_strict_json_text", unexpected_memory_parse)
        with pytest.raises(RecordingError):
            native_checkpoint_memory._parse_bounded_memory_json("[0]", budget)
    _, owner_id, owner_token_limit = memory_bindings[0]
    with memoryview(b"\xff") as invalid_frame:
        with pytest.raises(RecordingError) as invalid_utf8:
            native_checkpoint_memory._admit_detached_memory_frame(
                invalid_frame, expected_agent_id=owner_id, token_limit=owner_token_limit,
                budget=native_checkpoint_memory._MemoryAdmissionBudget(writer_deadline),
            )
    assert invalid_utf8.value.code == "invalid_recording"
    assert invalid_utf8.value.__context__ is None
    assert monotonic() < writer_deadline

    different_anchors = RecordingAnchors(
        anchors.graph_id, anchors.simulation_id, "different-capture-run",
        anchors.branch_id, anchors.project_id, anchors.project_revision,
    )
    assert not writer_dir.exists() and monotonic() < writer_deadline
    with pytest.raises(RecordingError):
        verify_twitter_round_recording(
            destination, expected_manifest_sha256=manifest_sha256,
            expected_anchors=different_anchors, deadline_monotonic=writer_deadline,
        )
    assert not writer_dir.exists() and monotonic() < writer_deadline
    child_db = _stage_writer_child(writer_dir, database, verified, writer_deadline)
    assert not child_db.samefile(native)
    child_before = _artifact(child_db, _DATABASE_LIMIT)
    asyncio.run(_assert_existing_child_writer(writer_dir, child_db, verified,
                                              observed_clock[-1][0], trace[-1], writer_deadline))
    assert sorted(islice(writer_dir.iterdir(), 2)) == [child_db]
    assert _artifact(child_db, _DATABASE_LIMIT) == child_before
    assert _native_rows(child_db) == backup_rows
    assert sorted(islice(destination.iterdir(), 6)) == entries
    assert {file: _artifact(file, limit) for file, limit in limits.items()} == copied_before
    assert _native_rows(database) == backup_rows
    assert _artifact(native, _DATABASE_LIMIT) == parent_database
    assert _native_rows(native) == parent_rows
    assert _read_bounded(parent_log, _LOG_LIMIT) == parent
    assert model.calls == model_calls and random.getstate() == ambient_rng

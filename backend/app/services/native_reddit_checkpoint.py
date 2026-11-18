"""Owned Reddit elapsed checkpoint after signup and one seed post."""

from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timedelta
from hashlib import sha256
import json
import math
import os
import sqlite3
from time import monotonic

from nexaweave_execution.native_seed_contracts import validate_native_seed_plan

from .native_recording_contracts import canonical
from .native_recordings import _root
from .native_simulation import (
    _child_file_identity, _child_no_sidecars, _child_stat, _verify_child_database,
)
from .native_twitter_restore import _disjoint_native_paths, _new_native_directory, _read_lineage_input


_DATABASE = "native.sqlite3"
_MANIFEST = "reddit-checkpoint.json"
_DATABASE_LIMIT = 4 * 1024 * 1024
_MANIFEST_LIMIT = 7200
_INPUT_LIMIT = 2 * 1024 * 1024
_EPOCH = datetime(2026, 1, 1)
_COMMAND_SECONDS = 60
_MAX_ELAPSED = 86400
_PRESET = {
    "show_score": True, "allow_self_rating": True, "refresh_rec_post_count": 5,
    "max_rec_post_len": 100, "following_post_count": 3, "rec_prob": 0.7,
    "use_openai_embedding": False, "trend_num_days": 7, "trend_top_k": 1,
    "report_threshold": 2,
}
_EMPTY_AT_CUT = (
    "follow", "mute", "like", "dislike", "report", "rec", "comment",
    "comment_like", "comment_dislike", "product", "chat_group", "group_members", "group_messages",
)
_SCHEMA = "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name LIMIT 65"


class RedditElapsedClock:
    def __init__(self, epoch, elapsed_seconds):
        if (type(epoch) is not datetime or epoch != _EPOCH or epoch.tzinfo is not None
                or type(elapsed_seconds) is not int or not 0 <= elapsed_seconds <= _MAX_ELAPSED
                or elapsed_seconds % _COMMAND_SECONDS):
            raise ValueError("invalid Reddit elapsed clock")
        self.epoch = epoch
        self.elapsed_seconds = elapsed_seconds

    def time_transfer(self, now_time, start_time):
        if start_time != self.epoch or start_time.tzinfo is not None:
            raise ValueError("invalid Reddit clock epoch")
        return self.epoch + timedelta(seconds=self.elapsed_seconds)

    def advance(self):
        if self.elapsed_seconds > _MAX_ELAPSED - _COMMAND_SECONDS:
            raise ValueError("Reddit elapsed clock exhausted")
        self.elapsed_seconds += _COMMAND_SECONDS

    def wire(self):
        return {"epoch": self.epoch.isoformat(), "elapsed_seconds": self.elapsed_seconds}


def _remaining(deadline):
    left = deadline - monotonic()
    if not math.isfinite(left) or left <= 0:
        raise TimeoutError("Reddit checkpoint deadline exceeded")
    return left


def _write_new(path, data, deadline):
    _remaining(deadline)
    with path.open("xb") as stream:
        if stream.write(data) != len(data):
            raise OSError("incomplete Reddit checkpoint artifact")
    _remaining(deadline)


def _scope(prepared, request):
    return {"graph_id": prepared.graph_id, "request": request.to_wire(),
            "request_fingerprint": request.fingerprint}


def _inputs(prepared, request, deadline):
    from .native_reddit_profiles import validate_reddit_profile

    prepared.validate(request)
    if request.platforms != ("reddit",) or request.max_rounds != 1:
        raise ValueError("invalid Reddit continuation scope")
    root = _root(prepared.simulation_dir)
    raw, config_digest, _ = _read_lineage_input(root / "simulation_config.json", _INPUT_LIMIT, deadline)
    config = json.loads(raw)
    raw, profiles_digest, _ = _read_lineage_input(root / "reddit_profiles.json", _INPUT_LIMIT, deadline)
    profiles = json.loads(raw)
    prepared.validate(request)
    for name, digest in (("simulation_config.json", config_digest), ("reddit_profiles.json", profiles_digest)):
        if _read_lineage_input(root / name, _INPUT_LIMIT, deadline)[1] != digest:
            raise ValueError("changed Reddit preparation")
    plan = validate_native_seed_plan(config)
    content = config.get("reddit_checkpoint_comment")
    actors = config.get("agent_configs")
    if (len(plan) != 1 or type(profiles) is not list or not 2 <= len(profiles) <= 8
            or type(actors) is not list or len(actors) != len(profiles)
            or type(content) is not str or not 1 <= len(content.encode("utf-8")) <= 512
            or any(ord(char) < 32 or ord(char) == 127 for char in content)):
        raise ValueError("invalid bounded Reddit continuation plan")
    for index, (profile, actor) in enumerate(zip(profiles, actors, strict=True)):
        if type(actor) is not dict or type(actor.get("agent_id")) is not int or actor["agent_id"] != index:
            raise ValueError("invalid Reddit continuation actor")
        validate_reddit_profile(profile, index, actor.get("entity_name"))
    if not 0 <= plan[0].actor_id < len(profiles):
        raise ValueError("invalid Reddit seed actor")
    actor_id = request.seed % len(profiles)
    return root, config, config_digest, profiles, plan[0], actor_id, content


def _agent_graph(prepared, profiles):
    from scripts.run_parallel_simulation import REDDIT_ACTIONS

    from .native_reddit_profiles import build_reddit_agent_graph

    models = prepared.model_factory()
    if type(models) is not dict or set(models) != {"reddit"}:
        raise ValueError("invalid Reddit model binding")
    graph = build_reddit_agent_graph(profiles, models["reddit"], REDDIT_ACTIONS)
    if any(key != agent.social_agent_id for key, agent in graph.get_agents()):
        raise ValueError("invalid Reddit graph owner IDs")
    return graph


def _install_clock(platform, clock):
    platform.start_time = platform.pl_utils.start_time = clock.epoch
    platform.sandbox_clock = platform.pl_utils.sandbox_clock = clock


def _bound_schema(db, deadline):
    from oasis.social_platform.database import create_db

    reference, cursor = create_db(":memory:")
    with closing(reference), closing(cursor):
        expected = reference.execute(_SCHEMA).fetchall()
    if (not 17 <= len(expected) <= 64 or any(row[0] not in ("table", "index") for row in expected)
            or db.execute(_SCHEMA).fetchall() != expected
            or db.execute("PRAGMA quick_check(1)").fetchall() != [("ok",)]):
        raise ValueError("invalid Reddit database schema")
    _remaining(deadline)


def _prefix(db, profiles, seed, clock, deadline):
    if db.in_transaction or clock.elapsed_seconds != _COMMAND_SECONDS:
        raise ValueError("invalid Reddit checkpoint position")
    signup_time = clock.epoch.isoformat(sep=" ")
    seed_time = clock.time_transfer(None, clock.epoch).isoformat(sep=" ")
    users = db.execute("SELECT * FROM user ORDER BY user_id LIMIT 9").fetchall()
    expected_users = [(index, index, None, profile["username"], profile["bio"], signup_time, 0, 0)
                      for index, profile in enumerate(profiles)]
    post = db.execute("SELECT * FROM post LIMIT 2").fetchall()
    if users != expected_users or post != [(1, seed.actor_id, None, seed.content, None, seed_time, 0, 0, 0, 0)]:
        raise ValueError("invalid Reddit checkpoint owners or seed")
    trace = db.execute(
        "SELECT rowid, user_id, action, info, created_at FROM trace ORDER BY rowid LIMIT 10"
    ).fetchall()
    expected = [(index + 1, index, "sign_up",
                 {"name": profile["username"], "user_name": None, "bio": profile["bio"]}, signup_time)
                for index, profile in enumerate(profiles)]
    cursor = len(profiles) + 1
    expected.append((cursor, seed.actor_id, "create_post", {"content": seed.content, "post_id": 1}, seed_time))
    if [(i, owner, action, json.loads(info), tick) for i, owner, action, info, tick in trace] != expected:
        raise ValueError("invalid Reddit checkpoint trace")
    for table in _EMPTY_AT_CUT:
        if db.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone() is not None:
            raise ValueError("unexpected Reddit checkpoint records")
    _remaining(deadline)
    return cursor


async def _idle(platform):
    if (platform.db.in_transaction or not platform.channel.receive_queue.empty()
            or await platform.channel.send_dict.keys()):
        raise ValueError("pending Reddit writer work")


async def _advance(env, clock, cursor, actor_id, content):
    platform = env.platform
    await _idle(platform)
    clock.advance()
    await platform.update_rec_table()
    actor = env.agent_graph.get_agent(actor_id)
    result = await actor.perform_action_by_data("create_comment", post_id=1, content=content)
    await _idle(platform)
    tick = clock.time_transfer(None, clock.epoch).isoformat(sep=" ")
    if result != {"success": True, "comment_id": 1}:
        raise ValueError("Reddit continuation was not acknowledged")
    comment = platform.db.execute(
        "SELECT comment_id, post_id, user_id, content, created_at FROM comment LIMIT 2"
    ).fetchall()
    trace = platform.db.execute(
        "SELECT rowid, user_id, action, info, created_at FROM trace WHERE rowid > ? ORDER BY rowid LIMIT 2",
        (cursor,),
    ).fetchall()
    if (comment != [(1, 1, actor_id, content, tick)] or len(trace) != 1
            or trace[0][:3] != (cursor + 1, actor_id, "create_comment") or trace[0][-1] != tick
            or json.loads(trace[0][3]) != {"comment_id": 1, "content": content}):
        raise ValueError("invalid Reddit continuation suffix")
    return {"trace_cursor": cursor + 1, "actor_id": actor_id, "post_id": 1, "comment_id": 1,
            "clock": clock.wire()}


async def _close(env):
    task = env.platform_task
    try:
        if not task.done():
            await asyncio.wait_for(env.close(), 2)
        else:
            task.result()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        env.platform.db.close()


def _owned_database(directory, deadline):
    raw, digest, _ = _read_lineage_input(directory / _DATABASE, _DATABASE_LIMIT, deadline)
    _verify_child_database(directory, len(raw), digest, deadline)
    return raw, {"bytes": len(raw), "sha256": digest}


async def produce_reddit_checkpoint(prepared, request, producer_dir, *, checkpoint_dir, deadline):
    import oasis
    from scripts.native_seed_posts import apply_native_seed_posts

    root, config, config_digest, profiles, seed, actor_id, content = _inputs(prepared, request, deadline)
    producer = _new_native_directory(producer_dir)
    checkpoint = None if checkpoint_dir is None else _new_native_directory(checkpoint_dir)
    _disjoint_native_paths(root, producer, *(() if checkpoint is None else (checkpoint,)))
    graph = _agent_graph(prepared, profiles)
    _remaining(deadline)
    producer.mkdir(mode=0o700)
    path = producer / _DATABASE
    _write_new(path, b"", deadline)
    env = oasis.make(agent_graph=graph, platform=oasis.DefaultPlatformType.REDDIT,
                     database_path=str(path), semaphore=1)
    clock = RedditElapsedClock(_EPOCH, 0)
    _install_clock(env.platform, clock)
    env.platform.db.set_progress_handler(lambda: monotonic() >= deadline, 1000)
    try:
        async with asyncio.timeout(_remaining(deadline)):
            await env.reset()
            await _idle(env.platform)
            clock.advance()
            seeded = await apply_native_seed_posts(env, config, "reddit")
            await _idle(env.platform)
            cursor = _prefix(env.platform.db, profiles, seed, clock, deadline)
            if (seeded.successful_count, seeded.trace_cursor) != (1, cursor):
                raise ValueError("invalid Reddit seed acknowledgement")
            if checkpoint is None:
                result = await _advance(env, clock, cursor, actor_id, content)
    finally:
        if hasattr(env, "platform_task"):
            await _close(env)
        else:
            env.platform.db.close()

    raw, artifact = _owned_database(producer, deadline)
    if checkpoint is None:
        return {"pid": os.getpid(), "owner": _scope(prepared, request), "database": artifact,
                "step": result}
    manifest = _manifest(_scope(prepared, request), os.getpid(), config_digest, artifact, clock, profiles, actor_id)
    checkpoint.mkdir(mode=0o700)
    _write_new(checkpoint / _DATABASE, raw, deadline)
    _verify_child_database(checkpoint, artifact["bytes"], artifact["sha256"], deadline)
    data = canonical(manifest)
    if len(data) > _MANIFEST_LIMIT:
        raise ValueError("Reddit checkpoint manifest too large")
    _write_new(checkpoint / _MANIFEST, data, deadline)
    return sha256(data).hexdigest()


def _manifest(owner, pid, config_digest, artifact, clock, profiles, actor):
    return {"version": 1, "platform": "reddit", "owner": owner, "producer_pid": pid,
            "config_sha256": config_digest, "database": artifact, "trace_cursor": len(profiles) + 1,
            "clock": clock.wire(), "actor_ids": list(range(len(profiles))),
            "next_command": {"actor_id": actor, "post_id": 1, "action": "create_comment"}}


def _restore_manifest(checkpoint, expected_digest, producer_pid, producer_scope,
                      config_digest, profiles, actor_id, deadline):
    data, digest, _ = _read_lineage_input(checkpoint / _MANIFEST, _MANIFEST_LIMIT, deadline)
    if digest != expected_digest or type(producer_pid) is not int or producer_pid <= 0 or producer_pid == os.getpid():
        raise ValueError("invalid Reddit checkpoint authority")
    decoded = json.loads(data)
    if type(decoded) is not dict:
        raise ValueError("invalid Reddit checkpoint manifest")
    artifact = decoded.get("database")
    if type(artifact) is not dict or set(artifact) != {"bytes", "sha256"}:
        raise ValueError("invalid Reddit checkpoint database pin")
    clock = RedditElapsedClock(_EPOCH, _COMMAND_SECONDS)
    expected = _manifest(producer_scope, producer_pid, config_digest, artifact, clock, profiles, actor_id)
    if data != canonical(expected):
        raise ValueError("invalid or noncanonical Reddit checkpoint manifest")
    raw, observed = _owned_database(checkpoint, deadline)
    if observed != artifact:
        raise ValueError("changed Reddit checkpoint database")
    return expected, clock, raw


async def restore_reddit_checkpoint(prepared, request, *, producer_request, producer_pid,
                                    checkpoint_dir, expected_digest, child_dir, deadline):
    import oasis
    from oasis.social_agent.agents_generator import connect_platform_channel
    from oasis.social_platform.channel import Channel
    from oasis.social_platform.platform import Platform
    from oasis.social_platform.platform_utils import PlatformUtils
    from oasis.social_platform.typing import RecsysType

    root, _, config_digest, profiles, seed, actor_id, content = _inputs(prepared, request, deadline)
    prepared.validate(producer_request)
    if (request.run_id == producer_request.run_id
            or request.artifact_sha256 != producer_request.artifact_sha256):
        raise ValueError("invalid distinct Reddit restore owner")
    checkpoint = _root(checkpoint_dir)
    child = _new_native_directory(child_dir)
    _disjoint_native_paths(root, checkpoint, child)
    manifest, clock, raw = _restore_manifest(checkpoint, expected_digest, producer_pid, _scope(prepared, producer_request),
                                             config_digest, profiles, actor_id, deadline)
    graph = _agent_graph(prepared, profiles)
    _remaining(deadline)
    child.mkdir(mode=0o700)
    _write_new(child / _DATABASE, raw, deadline)
    artifact = manifest["database"]
    path, identity = _verify_child_database(child, artifact["bytes"], artifact["sha256"], deadline)
    # Require an existing copy and disable SQL writes during verification.
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=0)) as db:
        db.set_progress_handler(lambda: monotonic() >= deadline, 1000)
        db.execute("PRAGMA trusted_schema = OFF")
        db.execute("PRAGMA query_only = ON")
        if db.execute("PRAGMA journal_mode").fetchone() != ("delete",):
            raise ValueError("invalid Reddit checkpoint journal mode")
        _bound_schema(db, deadline)
        cursor = _prefix(db, profiles, seed, clock, deadline)
        if (cursor != manifest["trace_cursor"] or _child_file_identity(_child_stat(path)) != identity):
            raise ValueError("changed Reddit restore input")
        _child_no_sidecars(path)

        platform = Platform.__new__(Platform)
        platform.db_path, platform.db, platform.db_cursor = str(path), db, db.cursor()
        platform.channel = Channel()
        platform.recsys_type = RecsysType.REDDIT
        for key, value in _PRESET.items():
            setattr(platform, key, value)
        platform.pl_utils = PlatformUtils(db, platform.db_cursor, clock.epoch, clock,
                                          platform.show_score, platform.recsys_type, platform.report_threshold)
        _install_clock(platform, clock)
        env = oasis.make(agent_graph=graph, platform=platform, database_path=str(path), semaphore=1)
        connect_platform_channel(channel=platform.channel, agent_graph=graph)
        await _idle(platform)
        db.execute("PRAGMA query_only = OFF")
        try:
            async with asyncio.timeout(_remaining(deadline)):
                env.platform_task = asyncio.create_task(platform.running())
                step = await _advance(env, clock, cursor, actor_id, content)
        finally:
            if hasattr(env, "platform_task"):
                await _close(env)
            else:
                platform.db_cursor.close()

    _, output = _owned_database(child, deadline)
    return {"pid": os.getpid(), "owner": _scope(prepared, request), "producer_run_id": str(producer_request.run_id),
            "checkpoint_sha256": expected_digest, "database": output, "step": step}

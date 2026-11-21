"""Private, single-step continuation of an authenticated native Twitter cut."""

import asyncio
from contextlib import closing
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sqlite3
import stat
import time

from .native_recording_contracts import HEX, MAX_JSON, MAX_WIRE, RecordingError, canonical, strict_json


_CLEANUP_RESERVE_SECONDS = 1.0
_MODEL_POST_WIRE_LIMIT = 7200


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RecordingError()
    return remaining


def _wire_size(value, limit, depth=0):
    """Measure one native JSON envelope without making a second full response."""
    if limit < 1 or depth > 16:
        raise RecordingError("limit_exceeded")
    kind = type(value)
    if kind is dict or kind in (tuple, list):
        size = 2
        entries = value.items() if kind is dict else value
        for index, entry in enumerate(entries):
            size += bool(index)
            if kind is dict:
                key, item = entry
                if type(key) is not str:
                    raise RecordingError()
                size += _wire_size(key, limit - size, depth + 1) + 1
            else:
                item = entry
            size += _wire_size(item, limit - size, depth + 1)
    else:
        if kind is str:
            if len(value) + 2 > limit:
                raise RecordingError("limit_exceeded")
        elif kind is int:
            if not _integer(value, -(2**63), 2**63 - 1):
                raise RecordingError()
        elif value is not None and kind not in (bool, float):
            raise RecordingError()
        size = len(canonical(value))
    if size > limit:
        raise RecordingError("limit_exceeded")
    return size


def _model_post_digest(message_id, data):
    content = data[1]
    if type(content) is not str or not content:
        raise RecordingError()
    _wire_size((message_id, data), min(MAX_WIRE, _MODEL_POST_WIRE_LIMIT))
    try:
        content.encode("utf-8")
    except UnicodeEncodeError:
        raise RecordingError() from None
    return hashlib.sha256(canonical(content)).hexdigest()


class _RestoredTwitterChannel:
    """One native writer, one outstanding exchange, and a terminal EXIT."""

    def __init__(self, action_type, agent_id, post_content, deadline):
        self._agent_id = agent_id
        self._actions = (
            (None, action_type.REFRESH.value),
            (agent_id, action_type.LISTEN_FROM_GROUP.value),
            (post_content, action_type.CREATE_POST.value),
        )
        self._exit_action = action_type.EXIT
        self._deadline = deadline
        self._queue = asyncio.Queue(maxsize=1)
        self._completed = 0
        self._pending = None
        self._received = False
        self._post_id = None
        self.post_content_sha256 = None
        self._failed = False
        self._closing_task = None
        self._exit_queued = False
        self.exit_received = False
        self.writer = None

    def _refuse(self):
        self._failed = True
        raise RecordingError()

    def begin_shutdown(self):
        self._closing_task = asyncio.current_task()

    async def write_to_receive_queue(self, data):
        _remaining(self._deadline)
        if type(data) is not tuple or len(data) != 3:
            self._refuse()
        agent_id, payload, action = data
        if action is self._exit_action:
            if (self._closing_task is not asyncio.current_task() or self._exit_queued
                    or self._pending is not None or not self._queue.empty()
                    or agent_id is not None or payload is not None):
                self._refuse()
            self._exit_queued = True
            self._queue.put_nowait(("exit", data))
            return "exit"
        if (self._failed or self._closing_task is not None or self._pending is not None
                or not self._queue.empty() or self._completed >= len(self._actions)
                or type(agent_id) is not int or agent_id != self._agent_id):
            self._refuse()
        expected_payload, expected_action = self._actions[self._completed]
        if type(action) is not str or action != expected_action:
            self._refuse()
        message_id = str(self._completed)
        if expected_payload is None and self._completed == len(self._actions) - 1:
            try:
                self.post_content_sha256 = _model_post_digest(message_id, data)
            except RecordingError:
                self._failed = True
                raise
        else:
            if type(payload) is not type(expected_payload) or payload != expected_payload:
                self._refuse()
            _wire_size((message_id, data), MAX_WIRE)
        self._pending = (
            message_id, asyncio.current_task(), asyncio.get_running_loop().create_future(),
        )
        self._queue.put_nowait((message_id, data))
        return message_id

    async def receive_from(self):
        if asyncio.current_task() is not self.writer or self.exit_received:
            self._refuse()
        _remaining(self._deadline)
        message = await self._queue.get()
        _remaining(self._deadline)
        if message[0] == "exit":
            self.exit_received = True
        else:
            if self._received or self._pending is None or message[0] != self._pending[0]:
                self._refuse()
            self._received = True
        return message

    async def send_to(self, message):
        _remaining(self._deadline)
        if (asyncio.current_task() is not self.writer or not self._received
                or self._pending is None or type(message) is not tuple or len(message) != 3):
            self._refuse()
        message_id, agent_id, result = message
        expected_id, _, response = self._pending
        if (type(message_id) is not str or message_id != expected_id or response.done()
                or type(agent_id) is not int or agent_id != self._agent_id
                or type(result) is not dict or type(result.get("success")) is not bool):
            self._refuse()
        _wire_size(message, MAX_WIRE)
        if self._completed == len(self._actions) - 1:
            if (result.keys() != {"success", "post_id"} or result["success"] is not True
                    or not _integer(result["post_id"], 0, 2**63 - 1)):
                self._refuse()
        _remaining(self._deadline)
        response.set_result(message)

    async def read_from_send_queue(self, message_id):
        _remaining(self._deadline)
        if self._pending is None:
            self._refuse()
        expected_id, requester, response = self._pending
        if (type(message_id) is not str or message_id != expected_id
                or asyncio.current_task() is not requester):
            self._refuse()
        message = await response
        _remaining(self._deadline)
        self._pending = None
        self._received = False
        self._completed += 1
        if self._completed == len(self._actions):
            self._post_id = message[2]["post_id"]
        return message

    def completed_post(self):
        if (self._failed or self._completed != len(self._actions) or self._post_id is None
                or self._pending is not None or self._received or not self._queue.empty()):
            self._refuse()
        return self._post_id


def _check_hours(hours):
    if (type(hours) is not list or len(hours) > 24
            or not all(_integer(hour, 0, 23) for hour in hours)):
        raise RecordingError()


def _restore_schedule(manifest, runner_state, config_bytes, deadline):
    from nexaweave_execution.native_seed_contracts import validate_native_seed_plan

    if (type(manifest) is not dict or manifest.get("platform") != "twitter"
            or not _integer(manifest.get("round"), 1, 1)
            or manifest.get("full_checkpoint") is not False
            or manifest.get("continuation_supported") is not False
            or not _integer(manifest.get("memory", {}).get("agent_count"), 2, 2)
            or not _integer(manifest.get("database_bytes"), 1, 64 * 1024 * 1024)
            or type(manifest.get("database_sha256")) is not str
            or not HEX.fullmatch(manifest["database_sha256"])):
        raise RecordingError()
    clock = manifest["engine_clock"]
    before_tick = clock.get("before_step")
    if (not _integer(clock.get("version"), 1, 1)
            or not _integer(before_tick, 0, 2**63 - 3)
            or not _integer(clock.get("after_step"), before_tick + 1, before_tick + 1)):
        raise RecordingError()
    rec = manifest["recommendation_state"]
    for name, expected in (
        ("version", 1), ("max_rec_post_len", 2), ("refresh_rec_post_count", 2),
        ("following_post_count", 3), ("users", 2),
    ):
        if not _integer(rec.get(name), expected, expected):
            raise RecordingError()
    if (rec.get("mode") != "random" or rec.get("use_openai_embedding") is not False
            or not _integer(rec.get("rows"), 0, 4)):
        raise RecordingError()
    if type(runner_state) is not tuple or len(runner_state) != 3:
        raise RecordingError()
    position, config_sha256, rng_state = runner_state
    if type(position) is not tuple or len(position) != 5:
        raise RecordingError()
    completed, horizon, interval, offset, actions = position
    if (not _integer(completed, 1, 1) or not _integer(horizon, 2, 10080)
            or not _integer(interval, 1, 1440) or horizon * interval > 10080
            or not _integer(offset, 0, 0) or not _integer(actions, 0, 2**32 - 1)
            or type(config_sha256) is not bytes or len(config_sha256) != 32
            or type(config_bytes) is not bytes or not 0 < len(config_bytes) <= MAX_JSON
            or hashlib.sha256(config_bytes).digest() != config_sha256):
        raise RecordingError()
    _remaining(deadline)
    config = strict_json(config_bytes, MAX_JSON)
    _remaining(deadline)
    if type(config) is not dict or "execution_controls" in config:
        raise RecordingError()
    try:
        seed_plan = validate_native_seed_plan(config)
    except ValueError:
        raise RecordingError() from None
    if len(seed_plan) not in (1, 2):
        raise RecordingError()
    anchors = manifest["anchors"]
    if any(config.get(key) != anchors[key] for key in ("graph_id", "simulation_id")):
        raise RecordingError()
    agents = config.get("agent_configs")
    timing = config.get("time_config")
    if type(agents) is not list or len(agents) != 2 or type(timing) is not dict:
        raise RecordingError()
    hours = timing.get("total_simulation_hours", 72)
    if (not _integer(hours, 1, (2**63 - 1) // 60)
            or not _integer(timing.get("minutes_per_round", 30), interval, interval)
            or horizon > hours * 60 // interval):
        raise RecordingError()
    lower = timing.get("agents_per_hour_min", 5)
    upper = timing.get("agents_per_hour_max", 20)
    if not _integer(upper, 0, 2**63 - 1) or not _integer(lower, 0, upper):
        raise RecordingError()
    for hours_key, multiplier_key, default_hours, default_multiplier in (
        ("peak_hours", "peak_activity_multiplier", [9, 10, 11, 14, 15, 20, 21, 22], 1.5),
        ("off_peak_hours", "off_peak_activity_multiplier", [0, 1, 2, 3, 4, 5], 0.3),
    ):
        _check_hours(timing.get(hours_key, default_hours))
        multiplier = timing.get(multiplier_key, default_multiplier)
        if (type(multiplier) not in (int, float) or multiplier < 0
                or not math.isfinite(multiplier) or not math.isfinite(upper * multiplier)):
            raise RecordingError()
    for agent_id, agent in enumerate(agents):
        if type(agent) is not dict or not _integer(agent.get("agent_id"), agent_id, agent_id):
            raise RecordingError()
        _check_hours(agent.get("active_hours", list(range(8, 23))))
        activity = agent.get("activity_level", 0.5)
        if type(activity) not in (int, float) or not 0 <= activity <= 1:
            raise RecordingError()
    return config, seed_plan, rng_state, clock["after_step"], completed, offset + interval


def _owned_graph_channel(graph):
    entries = iter(graph.get_agents())
    channel = None
    for expected_id in range(2):
        agent_id, agent = next(entries, (None, None))
        if (not _integer(agent_id, expected_id, expected_id)
                or graph.get_agent(agent_id) is not agent or agent.channel is None
                or agent.env.action.channel is not agent.channel):
            raise RecordingError()
        if channel is None:
            channel = agent.channel
        elif agent.channel is not channel:
            raise RecordingError()
    if next(entries, None) is not None:
        raise RecordingError()
    return channel


def _check_checkpoint_posts(connection, seed_plan, round_tick, cut_cursor, deadline):
    posts = connection.execute(
        "SELECT post_id, user_id, content, created_at, original_post_id, quote_content"
        " FROM post ORDER BY post_id LIMIT 4",
    )
    with closing(posts), closing(connection.execute(
        "SELECT rowid, user_id, created_at, info FROM trace WHERE action = 'create_post'"
        " ORDER BY rowid LIMIT 4",
    )) as traces:
        for index, (post, trace) in enumerate(zip(posts, traces, strict=True)):
            _remaining(deadline)
            post_id, actor, content, tick, original, quote = post
            rowid, trace_actor, trace_tick, info = trace
            if (not _integer(post_id, 1, 2**63 - 1) or not _integer(actor, 0, 1)
                    or type(content) is not str or original is not None or quote is not None
                    or not _integer(trace_actor, actor, actor)):
                raise RecordingError()
            if index < len(seed_plan):
                seed = seed_plan[index]
                if (not _integer(tick, round_tick - 1, round_tick - 1)
                        or (actor, content) != (seed.actor_id, seed.content)):
                    raise RecordingError()
            elif not _integer(tick, round_tick, round_tick) or rowid != cut_cursor:
                raise RecordingError()
            payload = strict_json(info)
            if (not _integer(trace_tick, tick, tick) or type(payload) is not dict
                    or payload.keys() != {"content", "post_id"}
                    or not _integer(payload["post_id"], post_id, post_id)
                    or payload["content"] != content):
                raise RecordingError()


def _bound_restore_tables(platform, manifest, seed_plan, work_deadline):
    connection, cursor = platform.db, platform.db_cursor
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_JSON)
    connection.set_progress_handler(
        lambda: int(time.monotonic() >= work_deadline), 1000,
    )
    remaining_text = MAX_JSON
    # These are fixed tables used by the pinned default recommender and
    # three admitted native requests. The two-owner fixture has no groups,
    # follow edges or comments. Its checkpoint has the seeds and one round-one post.
    post_count = len(seed_plan) + 1
    recommendation_rows = manifest["recommendation_state"]["rows"]
    tables = (
        ("user", 2, 2),
        ("post", post_count, post_count),
        ("trace", 1, 128),
        ("rec", recommendation_rows, recommendation_rows),
        ("follow", 0, 0),
        ("comment", 0, 0),
        ("chat_group", 0, 0),
        ("group_members", 0, 0),
        ("group_messages", 0, 0),
    )
    for table, minimum, maximum in tables:
        _remaining(work_deadline)
        count = 0
        for row in cursor.execute(f'SELECT * FROM "{table}" LIMIT ?', (maximum + 1,)):
            _remaining(work_deadline)
            count += 1
            if count > maximum:
                raise RecordingError("limit_exceeded")
            remaining_text -= _wire_size(row, remaining_text)
        if count < minimum:
            raise RecordingError()
    _check_checkpoint_posts(connection, seed_plan, manifest["engine_clock"]["before_step"],
                            manifest["cursor"], work_deadline)
    _remaining(work_deadline)


def _enable_owned_writes(platform, clock, deadline):
    _remaining(deadline)
    connection, cursor = platform.db, platform.db_cursor
    utils = platform.pl_utils
    if (connection.in_transaction or cursor.connection is not connection
            or utils.db is not connection or utils.db_cursor is not cursor
            or utils.sandbox_clock is not clock or platform.sandbox_clock is not clock
            or cursor.execute("PRAGMA query_only").fetchone() != (1,)):
        raise RecordingError()
    cursor.execute("PRAGMA query_only = OFF")
    if cursor.execute("PRAGMA query_only").fetchone() != (0,) or connection.in_transaction:
        raise RecordingError()
    _remaining(deadline)


async def _run_owned_step(env, actions, channel, handles, clock, expected_tick,
                          work_deadline, deadline, stop_writer):
    _remaining(work_deadline)
    writer = asyncio.create_task(env.platform.running())
    channel.writer = writer
    try:
        async with asyncio.timeout(_remaining(work_deadline)):
            await env.step(actions)
        _remaining(work_deadline)
        post_id = channel.completed_post()
        if not _integer(clock.time_step, expected_tick, expected_tick):
            raise RecordingError()
    except BaseException:
        try:
            channel.begin_shutdown()
            await stop_writer(channel, writer, handles, deadline)
        except BaseException:
            pass  # Cleanup must preserve the primary failure.
        raise
    else:
        channel.begin_shutdown()
        await stop_writer(channel, writer, handles, deadline)
        _remaining(deadline)
        return post_id


async def _advance_restored_twitter_round(
    child_dir, *, manifest, runner_state, config_bytes, graph, expected_post_content,
    deadline_monotonic, intervention=None,
):
    """Drive one restored, scripted native action under the caller's owned lane."""
    ambient_rng = random.getstate()
    try:
        deadline = deadline_monotonic
        if (type(deadline) not in (int, float) or not math.isfinite(deadline)
                or (expected_post_content is not None
                    and (type(expected_post_content) is not str or not expected_post_content))):
            raise RecordingError("invalid_request")
        work_deadline = deadline - _CLEANUP_RESERVE_SECONDS
        _remaining(work_deadline)
        if expected_post_content is not None:
            _wire_size(expected_post_content, MAX_WIRE)
        config, seed_plan, rng_state, tick, round_index, minutes = _restore_schedule(
            manifest, runner_state, config_bytes, work_deadline,
        )
        original_channel = _owned_graph_channel(graph)

        from oasis import ActionType, LLMAction
        from oasis.clock.clock import Clock
        from oasis.environment.env import OasisEnv
        from oasis.social_agent.agents_generator import connect_platform_channel

        from .native_simulation import _open_bound_twitter_child, _stop_staged_twitter_writer

        if __package__.startswith("backend."):
            from ...scripts.run_parallel_simulation import get_active_agents_for_round
        else:
            from scripts.run_parallel_simulation import get_active_agents_for_round

        _remaining(work_deadline)
        clock = Clock()
        clock.time_step = tick
        with _open_bound_twitter_child(
            child_dir, database_bytes=manifest["database_bytes"],
            database_sha256=manifest["database_sha256"], clock=clock,
            start_time=datetime.now(), deadline_monotonic=work_deadline,
        ) as (platform, handles):
            if os.environ.get("OASIS_DB_PATH") != platform.db_path:
                raise RecordingError("invalid_request")
            _bound_restore_tables(platform, manifest, seed_plan, work_deadline)
            env = OasisEnv(agent_graph=graph, platform=platform,
                           database_path=platform.db_path, semaphore=1)
            _remaining(work_deadline)
            random.setstate(rng_state)
            active = get_active_agents_for_round(
                env, config, (minutes // 60) % 24, round_index,
                simulated_minutes=minutes, native_dependencies=None,
            )
            if type(active) is not list or len(active) != 1:
                raise RecordingError()
            agent_id, agent = active[0]
            if not _integer(agent_id, 0, 1) or graph.get_agent(agent_id) is not agent:
                raise RecordingError()
            if intervention is not None:
                agent_id = 1 - agent_id
                agent = graph.get_agent(agent_id)
            if expected_post_content is not None:
                _wire_size(("2", (agent_id, expected_post_content, ActionType.CREATE_POST.value)), MAX_WIRE)
            channel = _RestoredTwitterChannel(ActionType, agent_id, expected_post_content, deadline)
            platform.channel = env.channel = channel
            try:
                connect_platform_channel(channel=channel, agent_graph=graph)
                _enable_owned_writes(platform, clock, work_deadline)
                post_id = await _run_owned_step(
                    env, {agent: LLMAction()}, channel, handles, clock, tick + 1,
                    work_deadline, deadline, _stop_staged_twitter_writer,
                )
            except BaseException:
                try:
                    connect_platform_channel(channel=original_channel, agent_graph=graph)
                except BaseException:
                    pass
                raise
            else:
                connect_platform_channel(channel=original_channel, agent_graph=graph)
        _remaining(deadline)
        result = {"agent_id": agent_id, "post_id": post_id, "after_step": clock.time_step}
        if expected_post_content is None:
            if not _is_digest(channel.post_content_sha256):
                raise RecordingError()
            result["post_content_sha256"] = channel.post_content_sha256
        return result
    except RecordingError as error:
        refusal_code = error.code
    except Exception:
        refusal_code = "invalid_recording"
    finally:
        random.setstate(ambient_rng)
    raise RecordingError(refusal_code)


_LINEAGE_LIMIT = 7200
_RUNNER_STATE_BYTES = 2564
_STAGE_DATABASE_LIMIT = 64 * 1024 * 1024
_STAGE_TOTAL_LIMIT = 160 * 1024 * 1024
_STAGE_ACTIVE_SECONDS = 10
_PREPARED_FILES = (
    "state.json", "simulation_config.json", "source_grounding.json", "twitter_profiles.csv",
)
_PREPARED_KEYS = {"artifact_sha256", "config_sha256", "profile_sha256", "source_grounding_sha256"}
_RUNNER_KEYS = ("completed_round", "horizon", "minutes_per_round", "simulated_minutes", "total_actions")


def _is_digest(value):
    return type(value) is str and HEX.fullmatch(value) is not None


def _read_lineage_input(path, limit, deadline):
    """Read one fixed input through the same custody rules as round artifacts."""
    from .native_recordings import _cross_identity, _identity, _round_file_stat, _safe

    _remaining(deadline)
    before = _round_file_stat(_safe(path))
    if not _integer(before.st_size, 1, limit):
        raise RecordingError("limit_exceeded")
    data = bytearray()
    digest_state = hashlib.sha256()
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (_cross_identity(opened) != _cross_identity(before)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1):
            raise RecordingError()
        left = opened.st_size
        while left:
            _remaining(deadline)
            chunk = stream.read(min(left, 65536))
            if not chunk:
                raise RecordingError()
            data.extend(chunk)
            digest_state.update(chunk)
            left -= len(chunk)
        if stream.read(1):
            raise RecordingError()
        after = os.fstat(stream.fileno())
        path_after = _round_file_stat(_safe(path))
        if (_identity(after) != _identity(opened) or _identity(path_after) != _identity(before)
                or _cross_identity(after) != _cross_identity(path_after)
                or not stat.S_ISREG(after.st_mode) or after.st_nlink != 1):
            raise RecordingError()
    _remaining(deadline)
    return bytes(data), digest_state.hexdigest(), _identity(before)


def _prepared_twitter_lineage(root, anchors, deadline):
    from .native_recordings import _identity, _round_file_stat, _safe

    descriptors = []
    identities = {}
    for name in _PREPARED_FILES:
        raw, checksum, identities[name] = _read_lineage_input(root / name, MAX_JSON, deadline)
        descriptors.append({"name": name, "sha256": checksum, "size": len(raw)})
        if name == "state.json":
            state = strict_json(raw)
        elif name == "simulation_config.json":
            config = strict_json(raw)
            config_bytes = raw
        elif name == "source_grounding.json":
            grounding = strict_json(raw)
        else:
            profile_bytes = raw
        _remaining(deadline)
    if len({identity[:2] for identity in identities.values()}) != len(_PREPARED_FILES):
        raise RecordingError()
    for name, expected_identity in identities.items():
        _remaining(deadline)
        if _identity(_round_file_stat(_safe(root / name))) != expected_identity:
            raise RecordingError()

    if (type(state) is not dict or type(config) is not dict or type(grounding) is not dict
            or state.get("status") != "ready"
            or state.get("profiles_generated") is not True or state.get("config_generated") is not True
            or state.get("enable_twitter") is not True or state.get("enable_reddit") is not False
            or type(config.get("twitter_config")) is not dict
            or type(config.get("reddit_config")) is dict):
        raise RecordingError()
    for value in (state, config):
        if value.get("graph_id") != anchors.graph_id or value.get("simulation_id") != anchors.simulation_id:
            raise RecordingError()
    agents = config.get("agent_configs")
    if type(agents) is not list or len(agents) != 2 or len(grounding) != 2:
        raise RecordingError()
    names = []
    source_ids = set()
    for index, agent in enumerate(agents):
        if type(agent) is not dict or not _integer(agent.get("agent_id"), index, index):
            raise RecordingError()
        name = agent.get("entity_name")
        source_id = agent.get("entity_uuid")
        if (type(name) is not str or not 0 < len(name) <= _LINEAGE_LIMIT or not name.strip()
                or type(source_id) is not str or not source_id or source_id in source_ids
                or type(grounding.get(source_id)) is not dict
                or grounding[source_id].get("source_entity_uuid") != source_id):
            raise RecordingError()
        names.append(name)
        source_ids.add(source_id)
    if names[0] == names[1]:
        raise RecordingError()
    prepared = {
        "artifact_sha256": hashlib.sha256(
            canonical({"schema_version": 1, "files": descriptors}),
        ).hexdigest(),
        "config_sha256": descriptors[1]["sha256"],
        "source_grounding_sha256": descriptors[2]["sha256"],
        "profile_sha256": descriptors[3]["sha256"],
    }
    _remaining(deadline)
    return prepared, tuple(names), config, config_bytes, profile_bytes, sum(
        item["size"] for item in descriptors
    )


def _disjoint_native_paths(*paths):
    for index, path in enumerate(paths):
        for other in paths[:index]:
            if path == other or path in other.parents or other in path.parents:
                raise RecordingError("invalid_request")


def _new_native_directory(path):
    from .native_recordings import _root

    path = Path(path)
    if not path.is_absolute() or path != _root(path.parent) / path.name or not path.name:
        raise RecordingError("invalid_request")
    return path


def _capture_twitter_lineage(session, checkpoint_dir, lineage_dir, expected_anchors):
    """Seal the live producer's typed owner and first-cut runner bindings."""
    try:
        _capture_twitter_lineage_once(session, checkpoint_dir, lineage_dir, expected_anchors)
        return
    except RecordingError as error:
        refusal_code = error.code
    except Exception:
        refusal_code = "invalid_recording"
    session = None
    raise RecordingError(refusal_code)


def _capture_twitter_lineage_once(session, checkpoint_dir, lineage_dir, anchors):
    from .native_checkpoint_memory import resolve_native_memory_cohort
    from .native_recording_contracts import RecordingAnchors
    from .native_recordings import _root, _verify_twitter_round_recording_with_runner
    from .native_simulation import NativeSimulationSession

    if (type(session) is not NativeSimulationSession or not session._started or session._closed
            or session.max_rounds != 1 or session._platforms != ("twitter",)
            or session._controls is not None or type(anchors) is not RecordingAnchors
            or session.graph_id != anchors.graph_id or session.simulation_id != anchors.simulation_id):
        raise RecordingError("invalid_request")
    deadline = session._start_deadline
    _remaining(deadline)
    prepared_root = _root(session.simulation_dir)
    checkpoint = _root(checkpoint_dir)
    output = _new_native_directory(lineage_dir)
    _disjoint_native_paths(prepared_root, checkpoint, output)
    receipt = session.round_capture_receipt
    if (type(receipt) is not dict or receipt.get("anchors") != anchors.wire()
            or not _integer(receipt.get("round"), 1, 1)
            or session._round_capture._destination != checkpoint):
        raise RecordingError()
    prepared, names, config, config_bytes, _, _ = _prepared_twitter_lineage(
        prepared_root, anchors, deadline,
    )
    if config != session._config or prepared["config_sha256"] != session._runner_config_sha256:
        raise RecordingError()

    result = session._results["twitter"]
    graph = result.agent_graph
    channel = graph.get_agent(0).env.action.channel
    owners = resolve_native_memory_cohort(graph, result.env.agent_graph, channel, (0, 1), deadline)
    # The live model supplies the trusted limit. The framed semantic verifier
    # checks it against each actual captured CAMEL context.
    token_limit = session._models["twitter"].token_limit
    if not _integer(token_limit, 1, 2**63 - 1):
        raise RecordingError()
    bindings = tuple((owner.platform_id, owner.camel_id, token_limit) for owner in owners)

    timing = config["time_config"]
    interval = timing["minutes_per_round"]
    hours = timing["total_simulation_hours"]
    if not _integer(interval, 1, 1440) or not _integer(hours, 1, 168):
        raise RecordingError()
    actions = result.total_actions
    expected_runner = (hours * 60 // interval, interval, actions,
                       bytes.fromhex(prepared["config_sha256"]))
    manifest_bytes, manifest_sha256, _ = _read_lineage_input(
        checkpoint / "manifest.json", _LINEAGE_LIMIT, deadline,
    )
    manifest, runner_state, _ = _verify_twitter_round_recording_with_runner(
        checkpoint, expected_manifest_sha256=manifest_sha256, expected_anchors=anchors,
        deadline_monotonic=deadline, expected_runner=expected_runner,
        expected_memory_bindings=bindings,
    )
    if (canonical(manifest) != manifest_bytes
            or {key: value for key, value in manifest.items()
                if key not in {"version", "platform", "source_journal_mode"}} != receipt):
        raise RecordingError()
    _restore_schedule(manifest, runner_state, config_bytes, deadline)
    lineage = {
        "version": 1,
        "anchors": anchors.wire(),
        "producer_pid": os.getpid(),
        "manifest_sha256": manifest_sha256,
        "prepared": prepared,
        "owners": [
            {"platform_id": index, "name": names[index], "camel_id": camel_id,
             "token_limit": limit}
            for index, camel_id, limit in bindings
        ],
        "runner": dict(zip(_RUNNER_KEYS, runner_state[0], strict=True)),
    }
    raw = canonical(lineage)
    if len(raw) > _LINEAGE_LIMIT:
        raise RecordingError("limit_exceeded")
    _remaining(deadline)
    if os.path.lexists(output):
        _root(output)
        with os.scandir(output) as entries:
            if next(entries, None) is not None:
                raise RecordingError()
    else:
        output.mkdir(mode=0o700)
    with (output / "lineage.json").open("xb") as stream:
        if stream.write(raw) != len(raw):
            raise RecordingError()
        stream.flush()
        os.fsync(stream.fileno())
    _remaining(deadline)


def _checked_twitter_lineage(lineage):
    from .native_recording_contracts import IDENTIFIER, RecordingAnchors

    if (type(lineage) is not dict
            or lineage.keys() != {"version", "anchors", "producer_pid", "manifest_sha256",
                                  "prepared", "owners", "runner"}
            or not _integer(lineage["version"], 1, 1)
            or not _integer(lineage["producer_pid"], 1, 2**63 - 1)
            or not _is_digest(lineage["manifest_sha256"])
            or type(lineage["anchors"]) is not dict
            or lineage["anchors"].keys() != {"graph_id", "simulation_id", "run_id", "branch_id",
                                             "project_id", "project_revision"}):
        raise RecordingError("invalid_request")
    anchors = RecordingAnchors(**lineage["anchors"])
    prepared, owners, runner = lineage["prepared"], lineage["owners"], lineage["runner"]
    if (type(prepared) is not dict or prepared.keys() != _PREPARED_KEYS
            or not all(_is_digest(value) for value in prepared.values())
            or type(owners) is not list or len(owners) != 2
            or type(runner) is not dict or runner.keys() != set(_RUNNER_KEYS)):
        raise RecordingError()
    names = []
    bindings = []
    for index, owner in enumerate(owners):
        if (type(owner) is not dict or owner.keys() != {"platform_id", "name", "camel_id", "token_limit"}
                or not _integer(owner["platform_id"], index, index)
                or type(owner["name"]) is not str or not 0 < len(owner["name"]) <= _LINEAGE_LIMIT
                or not owner["name"].strip() or type(owner["camel_id"]) is not str
                or not IDENTIFIER.fullmatch(owner["camel_id"])
                or not _integer(owner["token_limit"], 1, 2**63 - 1)):
            raise RecordingError()
        names.append(owner["name"])
        bindings.append((index, owner["camel_id"], owner["token_limit"]))
    position = tuple(runner[key] for key in _RUNNER_KEYS)
    completed, horizon, interval, offset, actions = position
    if (names[0] == names[1] or bindings[0][1] == bindings[1][1]
            or not _integer(completed, 1, 1) or not _integer(horizon, 2, 10080)
            or not _integer(interval, 1, 1440) or horizon * interval > 10080
            or not _integer(offset, 0, 0) or not _integer(actions, 1, 2**32 - 1)
            or len(canonical(lineage)) > _LINEAGE_LIMIT):
        raise RecordingError()
    return anchors, tuple(names), tuple(bindings), position


def _fresh_twitter_models(factory, bindings, deadline):
    from .native_checkpoint_memory import ScoreBasedContextCreator, _token_limit

    models = []
    counters = []
    for _ in range(3):
        _remaining(deadline)
        mapping = factory()
        if type(mapping) is not dict or mapping.keys() != {"twitter"}:
            raise RecordingError()
        model = mapping["twitter"]
        if model is None or not callable(getattr(model, "run", None)):
            raise RecordingError()
        counter = model.token_counter
        if (counter is None or any(model is previous for previous in models)
                or any(counter is previous for previous in counters)):
            raise RecordingError()
        models.append(model)
        counters.append(counter)
    expected_limits = tuple(limit for _, _, limit in bindings)
    contexts = tuple(
        ScoreBasedContextCreator(token_counter=counter, token_limit=model.token_limit)
        for model, counter in zip(models[:2], counters[:2], strict=True)
    )
    if (tuple(_token_limit(creator) for creator in contexts) != expected_limits
            or any(not _integer(models[2].token_limit, limit, limit) for limit in expected_limits)):
        raise RecordingError()
    _remaining(deadline)
    return contexts, models[2]


def _retained_twitter_inventory(prepared_root, deadline):
    from .native_recordings import _identity, _round_file_stat, _safe

    inventory = {}
    for name, limit in (("twitter_simulation.db", _STAGE_DATABASE_LIMIT),
                        ("twitter/actions.jsonl", MAX_JSON)):
        _remaining(deadline)
        info = _round_file_stat(_safe(prepared_root / name))
        if not _integer(info.st_size, 1, limit):
            raise RecordingError("limit_exceeded")
        inventory[name] = _identity(info)
    _remaining(deadline)
    return inventory


def _stage_twitter_artifacts(prepared_root, checkpoint, child, manifest, inventory,
                             prepared_size, deadline):
    from .native_recordings import _round_inventory, _round_read

    files = (
        ("native.sqlite3", manifest["database_bytes"], manifest["database_sha256"]),
        ("actions.jsonl", manifest["log_bytes"], manifest["log_sha256"]),
    )
    producer_inventory = _retained_twitter_inventory(prepared_root, deadline)
    source_identities = (*inventory.values(), *producer_inventory.values())
    if len({identity[:2] for identity in source_identities}) != len(source_identities):
        raise RecordingError()
    source_size = sum(identity[2] for identity in source_identities)
    if (prepared_size + source_size + sum(size for _, size, _ in files) > _STAGE_TOTAL_LIMIT
            or not _integer(files[0][1], 1, _STAGE_DATABASE_LIMIT)
            or not _integer(files[1][1], 1, MAX_JSON)):
        raise RecordingError("limit_exceeded")
    _remaining(deadline)
    child.mkdir(mode=0o700)
    for name, size, checksum in files:
        with (child / name).open("xb") as stream:
            def write_chunk(chunk):
                if stream.write(chunk) != len(chunk):
                    raise RecordingError()

            _round_read(checkpoint, name, inventory[name], size, checksum, deadline,
                        observer=write_chunk)
            stream.flush()
            os.fsync(stream.fileno())
    if (_round_inventory(checkpoint, deadline) != inventory
            or _retained_twitter_inventory(prepared_root, deadline) != producer_inventory):
        raise RecordingError()
    return producer_inventory


def _read_acknowledged_twitter_action(native, database, manifest, result, names, content, deadline):
    from .native_recordings import _identity, _round_file_stat, _safe

    _remaining(deadline)
    identity = _identity(_round_file_stat(_safe(database)))
    if not _integer(identity[2], 1, _STAGE_DATABASE_LIMIT):
        raise RecordingError("limit_exceeded")
    observed, cursor = native.fetch_new_actions_from_db(
        str(database), manifest["cursor"], dict(enumerate(names)), capture_deadline=deadline,
    )
    if (_identity(_round_file_stat(_safe(database))) != identity
            or not _integer(cursor, manifest["cursor"] + 1, 2**63 - 1)
            or type(observed) is not list or len(observed) != 1):
        raise RecordingError()
    action = observed[0]
    agent_id = result["agent_id"]
    if (type(action) is not dict
            or action.keys() != {"agent_id", "agent_name", "action_type", "action_args"}
            or not _integer(action["agent_id"], agent_id, agent_id)
            or action["agent_name"] != names[agent_id] or action["action_type"] != "CREATE_POST"
            or type(action["action_args"]) is not dict
            or action["action_args"].keys() != {"content", "post_id"}
            or not _integer(action["action_args"]["post_id"], result["post_id"], result["post_id"])):
        raise RecordingError()
    logged_content = action["action_args"]["content"]
    if content is None:
        _remaining(deadline)
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0)) as connection:
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_JSON)
            connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            rows = connection.execute(
                "SELECT user_id, content FROM post WHERE post_id = ? LIMIT 2", (result["post_id"],),
            ).fetchmany(2)
        if (_identity(_round_file_stat(_safe(database))) != identity
                or len(rows) != 1 or not _integer(rows[0][0], agent_id, agent_id)
                or rows[0][1] != logged_content):
            raise RecordingError()
        digest = _model_post_digest("2", (agent_id, rows[0][1], "create_post"))
        if digest != result.get("post_content_sha256"):
            raise RecordingError()
    elif logged_content != content:
        raise RecordingError()
    _remaining(deadline)
    return action


def _append_restored_twitter_log(log_path, manifest, position, action, deadline):
    from .native_recording_contracts import MAX_EVENT_BYTES, MAX_EVENTS
    from .native_recordings import _cross_identity, _identity, _round_file_stat, _safe

    completed, _, interval, offset, _ = position
    round_num = completed + 1
    hour = ((offset + interval) // 60) % 24
    # Match the native PlatformActionLogger schema on the fixed staged path.
    events = (
        {"round": round_num, "timestamp": datetime.now().isoformat(), "event_type": "round_start",
         "simulated_hour": hour},
        {"round": round_num, "timestamp": datetime.now().isoformat(), **action,
         "result": None, "success": True},
        {"round": round_num, "timestamp": datetime.now().isoformat(), "event_type": "round_end",
         "actions_count": 1},
    )
    lines = tuple(
        (json.dumps(event, ensure_ascii=False) + os.linesep).encode("utf-8") for event in events
    )
    tail = b"".join(lines)
    prefix_size = manifest["log_bytes"]
    if (any(len(line) > MAX_EVENT_BYTES for line in lines) or prefix_size + len(tail) > MAX_JSON
            or any(strict_json(line, MAX_EVENT_BYTES) != event for line, event in zip(lines, events))):
        raise RecordingError("limit_exceeded")
    _remaining(deadline)
    before = _round_file_stat(_safe(log_path))
    if before.st_size != prefix_size:
        raise RecordingError()
    flags = (os.O_RDWR | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(os.open(log_path, flags), "r+b") as stream:
        opened = os.fstat(stream.fileno())
        if (_cross_identity(opened) != _cross_identity(before)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1):
            raise RecordingError()
        digest_state = hashlib.sha256()
        left = prefix_size
        count = 0
        last = None
        while left:
            _remaining(deadline)
            chunk = stream.read(min(left, 65536))
            if not chunk:
                raise RecordingError()
            digest_state.update(chunk)
            left -= len(chunk)
            count += chunk.count(b"\n")
            last = chunk[-1]
        if (last != 10 or count + len(events) > MAX_EVENTS or stream.read(1)
                or digest_state.hexdigest() != manifest["log_sha256"]
                or _identity(os.fstat(stream.fileno())) != _identity(opened)
                or _identity(_round_file_stat(_safe(log_path))) != _identity(before)):
            raise RecordingError()
        if stream.write(tail) != len(tail):
            raise RecordingError()
        stream.flush()
        os.fsync(stream.fileno())
        stream.seek(0)
        readback = hashlib.sha256()
        left = prefix_size
        while left:
            _remaining(deadline)
            chunk = stream.read(min(left, 65536))
            if not chunk:
                raise RecordingError()
            readback.update(chunk)
            left -= len(chunk)
        after = os.fstat(stream.fileno())
        if (readback.hexdigest() != manifest["log_sha256"]
                or stream.read(len(tail) + 1) != tail
                or after.st_size != prefix_size + len(tail) or after.st_nlink != 1
                or _identity(os.fstat(stream.fileno())) != _identity(after)
                or _cross_identity(_round_file_stat(_safe(log_path))) != _cross_identity(after)):
            raise RecordingError()
    _remaining(deadline)


async def _run_fresh_restored_twitter(
    prepared_dir, checkpoint_dir, child_dir, lineage, model_factory, content, deadline, *,
    intervention=None,
):
    from .native_recordings import _root, _round_inventory, _restore_twitter_round_recording_memory_with_runner
    from .native_twitter_child_graph import _rebuild_twitter_round_child_graph

    anchors, names, bindings, position = _checked_twitter_lineage(lineage)
    prepared_root, checkpoint = _root(prepared_dir), _root(checkpoint_dir)
    child = _new_native_directory(child_dir)
    _disjoint_native_paths(prepared_root, checkpoint, child)
    if os.path.lexists(child):
        raise RecordingError("invalid_request")
    prepared, prepared_names, _, config_bytes, profile_bytes, prepared_size = _prepared_twitter_lineage(
        prepared_root, anchors, deadline,
    )
    if prepared != lineage["prepared"] or prepared_names != names:
        raise RecordingError()
    manifest_bytes, saved_digest, saved_identity = _read_lineage_input(
        checkpoint / "manifest.json", _LINEAGE_LIMIT, deadline,
    )
    inventory = _round_inventory(checkpoint, deadline)
    if saved_digest != lineage["manifest_sha256"] or saved_identity != inventory["manifest.json"]:
        raise RecordingError()
    if intervention is not None:
        if saved_digest != intervention.manifest_sha256:
            raise RecordingError()
        admitted_manifest = strict_json(manifest_bytes, _LINEAGE_LIMIT)
        if type(admitted_manifest) is not dict:
            raise RecordingError()
        descriptor = admitted_manifest.get("runner_state")
        if type(descriptor) is not dict or descriptor.get("sha256") != intervention.runner_state_sha256:
            raise RecordingError()
        _, runner_digest, runner_identity = _read_lineage_input(
            checkpoint / "native-runner.bin", _RUNNER_STATE_BYTES, deadline,
        )
        if runner_digest != intervention.runner_state_sha256 or runner_identity != inventory["native-runner.bin"]:
            raise RecordingError()
    contexts, model = _fresh_twitter_models(model_factory, bindings, deadline)
    expected_runner = (position[1], position[2], position[4], bytes.fromhex(prepared["config_sha256"]))
    manifest, runner_state, _, restored = _restore_twitter_round_recording_memory_with_runner(
        checkpoint, expected_manifest_sha256=saved_digest, expected_anchors=anchors,
        deadline_monotonic=deadline, expected_runner=expected_runner,
        expected_memory_bindings=bindings, expected_memory_contexts=contexts,
    )
    if runner_state[0] != position or _round_inventory(checkpoint, deadline) != inventory:
        raise RecordingError()
    if intervention is not None and manifest["runner_state"]["sha256"] != intervention.runner_state_sha256:
        raise RecordingError()
    _restore_schedule(manifest, runner_state, config_bytes, deadline)

    # This is the same lazy import used by NativeSimulationSession.
    from scripts import run_parallel_simulation as native

    native._load_native_engine()
    _remaining(deadline)
    graph = await _rebuild_twitter_round_child_graph(
        profile_bytes, expected_profile_sha256=prepared["profile_sha256"],
        expected_owner_names=names, memory_bindings=bindings, restored_memories=restored,
        expected_topology=manifest["topology"], model=model,
        available_actions=tuple(native.TWITTER_ACTIONS), deadline_monotonic=deadline,
    )
    producer_inventory = _stage_twitter_artifacts(
        prepared_root, checkpoint, child, manifest, inventory, prepared_size, deadline,
    )
    database = child / "native.sqlite3"
    previous_path = os.environ.get("OASIS_DB_PATH")
    os.environ["OASIS_DB_PATH"] = str(database)
    try:
        active_deadline = min(deadline, time.monotonic() + _STAGE_ACTIVE_SECONDS)
        result = await _advance_restored_twitter_round(
            child, manifest=manifest, runner_state=runner_state, config_bytes=config_bytes,
            graph=graph, expected_post_content=content, deadline_monotonic=active_deadline,
            **({"intervention": intervention} if intervention is not None else {}),
        )
    finally:
        if previous_path is None:
            os.environ.pop("OASIS_DB_PATH", None)
        else:
            os.environ["OASIS_DB_PATH"] = previous_path
    action = _read_acknowledged_twitter_action(native, database, manifest, result, names, content, deadline)
    _append_restored_twitter_log(child / "actions.jsonl", manifest, position, action, deadline)
    if (_round_inventory(checkpoint, deadline) != inventory
            or _retained_twitter_inventory(prepared_root, deadline) != producer_inventory):
        raise RecordingError()
    _remaining(deadline)
    return result


def _start_fresh_restored_twitter(
    prepared_dir, checkpoint_dir, child_dir, lineage, model_factory, expected_post_content,
    timeout_seconds, *, intervention=None,
):
    """Run one dedicated child's synchronous restore boundary to ordinary closure."""
    ambient_rng = random.getstate()
    try:
        if (type(timeout_seconds) not in (int, float) or not 1 <= timeout_seconds <= 600
                or not callable(model_factory)
                or (expected_post_content is not None
                    and (type(expected_post_content) is not str or not expected_post_content))):
            raise RecordingError("invalid_request")
        deadline = time.monotonic() + timeout_seconds
        if expected_post_content is not None:
            _wire_size(expected_post_content, MAX_WIRE)
        if intervention is not None:
            from nexaweave_execution.native_run_contracts import SwapActiveActorV1
            if type(intervention) is not SwapActiveActorV1:
                raise RecordingError("invalid_request")
            intervention = SwapActiveActorV1.from_wire(intervention)
        return asyncio.run(_run_fresh_restored_twitter(
            prepared_dir, checkpoint_dir, child_dir, lineage, model_factory,
            expected_post_content, deadline, intervention=intervention,
        ))
    except RecordingError as error:
        refusal_code = error.code
    except Exception:
        refusal_code = "invalid_recording"
    finally:
        random.setstate(ambient_rng)
    raise RecordingError(refusal_code)

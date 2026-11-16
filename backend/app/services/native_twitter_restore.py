"""Private, single-step continuation of an authenticated native Twitter cut."""

import asyncio
from datetime import datetime
import hashlib
import math
import os
import random
import sqlite3
import time

from .native_recording_contracts import HEX, MAX_JSON, MAX_WIRE, RecordingError, canonical, strict_json


_CLEANUP_RESERVE_SECONDS = 1.0


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
        if (type(action) is not str or action != expected_action
                or type(payload) is not type(expected_payload) or payload != expected_payload):
            self._refuse()
        message_id = str(self._completed)
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
    return config, rng_state, clock["after_step"], completed, offset + interval


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


def _bound_restore_tables(platform, recommendation_rows, work_deadline):
    connection, cursor = platform.db, platform.db_cursor
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_JSON)
    connection.set_progress_handler(
        lambda: int(time.monotonic() >= work_deadline), 1000,
    )
    remaining_text = MAX_JSON
    # These are fixed tables used by the pinned default recommender and
    # three admitted native requests. The two-owner fixture has no groups,
    # follow edges or comments, and its checkpoint has three regular posts.
    tables = (
        ("user", 2, 2),
        ("post", 3, 3),
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
    deadline_monotonic,
):
    """Drive one restored, scripted native action under the caller's owned lane."""
    ambient_rng = random.getstate()
    try:
        deadline = deadline_monotonic
        if (type(deadline) not in (int, float) or not math.isfinite(deadline)
                or type(expected_post_content) is not str or not expected_post_content):
            raise RecordingError("invalid_request")
        work_deadline = deadline - _CLEANUP_RESERVE_SECONDS
        _remaining(work_deadline)
        _wire_size(expected_post_content, MAX_WIRE)
        config, rng_state, tick, round_index, minutes = _restore_schedule(
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
            _bound_restore_tables(platform, manifest["recommendation_state"]["rows"],
                                  work_deadline)
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
        return {"agent_id": agent_id, "post_id": post_id, "after_step": clock.time_step}
    except RecordingError as error:
        refusal_code = error.code
    except Exception:
        refusal_code = "invalid_recording"
    finally:
        random.setstate(ambient_rng)
    raise RecordingError(refusal_code)

"""Owned capture of one completed native Twitter round."""

from __future__ import annotations

import asyncio
from contextlib import closing, contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import random
import sqlite3
import struct
import time

from .native_recording_contracts import (
    HEX,
    IDENTIFIER,
    MAX_EVENT_BYTES,
    MAX_EVENTS,
    MAX_JSON,
    RecordingAnchors,
    RecordingError,
    canonical,
    strict_json,
)

_MAX_REQUESTS = 128
_MAX_ROWS = 128
_MAX_AGENTS = 128
_DATABASE_LIMIT = 64 * 1024 * 1024
_ARTIFACT_LIMIT = 160 * 1024 * 1024
_RECEIPT_LIMIT = 7200
_CAPTURE_SECONDS = 10
_BACKUP_PAGES = 64
_READ_BYTES = 64 * 1024
_SETUP_ACTIONS = {"sign_up", "follow", "create_post"}
_ROUND_ACTIONS = {"refresh", "listen_from_group", "create_post"}
_SIDECARS = ("-wal", "-shm", "-journal")
_MEMORY_FILE = "native-memory.bin"
_RUNNER_FILE = "native-runner.bin"
_RUNNER_STATE = struct.Struct(">4sB5I32s624IHBd")
_MAX_SIMULATED_MINUTES = 168 * 60


def _valid_id(value):
    return type(value) is int and 0 <= value < 2**63


@dataclass
class _Request:
    agent_id: int
    action: str
    content: str | None
    dequeued: bool = False
    replied: bool = False


@dataclass
class _Barrier:
    acknowledged: asyncio.Future
    release: asyncio.Event


@dataclass(frozen=True)
class _AgentMemoryOwner:
    platform_id: int
    camel_id: str
    agent: object
    action: object
    memory: object
    block: object
    storage: object

    def same_binding(self, other):
        return (self.platform_id == other.platform_id and self.camel_id == other.camel_id
                and self.agent is other.agent and self.action is other.action
                and self.memory is other.memory and self.block is other.block
                and self.storage is other.storage)


class _OwnedChannel:
    def __init__(self, delegate, owner):
        self._delegate = delegate
        self._owner = owner

    async def write_to_receive_queue(self, action_info):
        owner = self._owner
        if owner._passthrough or owner._receipt is not None:
            return await self._delegate.write_to_receive_queue(action_info)
        request = owner._reserve_request(action_info)
        # The unbounded queue completes put inline. Register the returned UUID
        # before the writer can dequeue the already validated request.
        try:
            message_id = await self._delegate.write_to_receive_queue(action_info)
            owner._register_request(message_id, request)
        except BaseException:
            owner._latch()
            raise
        return message_id

    async def receive_from(self):
        owner = self._owner
        while True:
            message_id, data = await self._delegate.receive_from()
            if type(data) is _Barrier:
                if data is owner._barrier:
                    valid = asyncio.current_task() is owner._env.platform_task
                    data.acknowledged.set_result(valid)
                    if valid:
                        await data.release.wait()
                continue
            if not owner._passthrough and owner._receipt is None:
                pending = owner._pending.get(message_id)
                if pending is None or pending.dequeued:
                    owner._latch()
                else:
                    pending.dequeued = True
            return message_id, data

    async def send_to(self, message):
        owner = self._owner
        if not owner._passthrough and owner._receipt is None:
            owner._reply(message)
        await self._delegate.send_to(message)

    async def read_from_send_queue(self, message_id):
        message = await self._delegate.read_from_send_queue(message_id)
        owner = self._owner
        if not owner._passthrough and owner._receipt is None:
            owner._consume(message_id, message)
        return message

    async def enqueue_barrier(self, barrier):
        await self._delegate.write_to_receive_queue(barrier)


class NativeRoundCapture:
    """Capture-only component for a single trusted native session."""

    def __init__(self, *, simulation_dir, destination, anchors,
                 include_memory=False, prepared_agent_ids=None, runner_config_sha256=None,
                 include_engine_clock=False):
        if type(include_engine_clock) is not bool:
            raise RecordingError()
        if (runner_config_sha256 is not None
                and (type(runner_config_sha256) is not str
                     or not HEX.fullmatch(runner_config_sha256))):
            raise RecordingError()
        if type(include_memory) is not bool:
            raise RecordingError()
        if include_memory:
            if type(prepared_agent_ids) is not tuple or not prepared_agent_ids:
                raise RecordingError()
            if len(prepared_agent_ids) > _MAX_AGENTS:
                raise RecordingError("limit_exceeded")
            if (not all(_valid_id(item) for item in prepared_agent_ids)
                    or len(set(prepared_agent_ids)) != len(prepared_agent_ids)):
                raise RecordingError()
        elif prepared_agent_ids is not None:
            raise RecordingError()

        try:
            self._root = Path(simulation_dir).resolve(strict=True)
            target = Path(destination)
            parent = target.parent.resolve(strict=True)
            self._destination = parent / target.name
            if (not self._root.is_dir() or parent != self._root.parent
                    or not target.name or target.name in (".", "..")
                    or self._destination == self._root
                    or os.path.lexists(self._destination)
                    or type(anchors) is not RecordingAnchors):
                raise RecordingError()
            self._anchors = RecordingAnchors(**anchors.wire()).wire()
        except (OSError, TypeError, ValueError):
            raise RecordingError() from None

        self._runner_config_digest = (
            bytes.fromhex(runner_config_sha256) if runner_config_sha256 is not None else None
        )
        self._runner_position = None
        self._include_engine_clock = include_engine_clock
        self._engine_clock = None
        self._clock_before_step = None
        self._clock_after_step = None
        self._include_memory = include_memory
        self._prepared_agent_ids = tuple(sorted(prepared_agent_ids)) if include_memory else None
        self._memory_graph = None
        self._memory_owners = ()
        self._platform = None
        self._channel = None
        self._env = None
        self._original_llm = None
        self._barrier = None
        self._sealed = False
        self._passthrough = False
        self._attempted = False
        self._receipt = None
        self._failure = None
        self._round = None
        self._prior_cursor = None
        self._prior_post_id = None
        self._source_db = None
        self._capture_deadline = None
        self._projection_cursor = None
        self._expected = {}
        self._started = set()
        self._running = set()
        self._completed = set()
        self._producer = ContextVar("native_round_capture_producer", default=None)
        self._pending = {}
        self._request_count = 0
        self._round_witnesses = []
        self._text_remaining = MAX_JSON

    @property
    def receipt(self):
        return deepcopy(self._receipt)

    def wrap_platform(self, platform):
        from oasis.social_platform.channel import Channel

        channel = getattr(platform, "channel", None)
        if (self._platform is not None or self._passthrough
                or type(channel) is not Channel
                or type(channel.receive_queue) is not asyncio.Queue
                or channel.receive_queue.maxsize != 0):
            raise RecordingError()
        self._platform = platform
        self._channel = _OwnedChannel(channel, self)
        platform.channel = self._channel
        return platform

    def adopt(self, env):
        if (self._env is not None or self._platform is None or self._passthrough
                or getattr(env, "platform", None) is not self._platform
                or getattr(env, "channel", None) is not self._channel
                or self._platform.channel is not self._channel):
            raise RecordingError()
        original = getattr(env, "_perform_llm_action", None)
        if not callable(original):
            raise RecordingError()
        self._env = env
        self._original_llm = original

        async def observe(agent):
            return await self._observe_llm(original, agent)

        env._perform_llm_action = observe

    async def _observe_llm(self, original, agent):
        if self._passthrough or self._receipt is not None:
            return await original(agent)
        self._require_healthy()
        identity = id(agent)
        expected = self._expected.get(identity)
        if (self._sealed or self._round != 1 or expected is None
                or expected[1] is not agent or identity in self._started):
            self._latch()
            raise RecordingError()
        self._started.add(identity)
        self._running.add(identity)
        producer = self._producer.set(identity)
        try:
            result = await original(agent)
            if isinstance(result, BaseException):
                self._latch()
            else:
                self._completed.add(identity)
            return result
        except BaseException:
            self._latch()
            raise
        finally:
            self._producer.reset(producer)
            self._running.discard(identity)

    def _latch(self, code="invalid_recording"):
        if self._failure is None:
            self._failure = code

    def _require_healthy(self):
        if self._failure is not None:
            raise RecordingError(self._failure)

    def _charge_text(self, amount):
        if amount > self._text_remaining:
            raise RecordingError("limit_exceeded")
        self._text_remaining -= amount

    def _reserve_request(self, action_info):
        self._require_healthy()
        try:
            if self._sealed or self._projection_cursor is not None:
                raise RecordingError()
            if self._request_count >= _MAX_REQUESTS:
                raise RecordingError("limit_exceeded")
            if type(action_info) is not tuple or len(action_info) != 3:
                raise RecordingError()
            agent_id, data, action_type = action_info
            from oasis import ActionType

            action = ActionType(action_type).value
            allowed = _SETUP_ACTIONS if self._round is None else _ROUND_ACTIONS
            if not _valid_id(agent_id) or action not in allowed:
                raise RecordingError()
            if self._round is not None:
                producer = self._producer.get()
                if producer not in self._running or self._expected[producer][0] != agent_id:
                    raise RecordingError()
            content = None
            if action == "create_post":
                if type(data) is not str:
                    raise RecordingError()
                if len(data) > self._text_remaining:
                    raise RecordingError("limit_exceeded")
                self._charge_text(len(data.encode("utf-8")))
                content = data
            self._request_count += 1
            return _Request(agent_id, action, content)
        except RecordingError as error:
            self._latch(error.code)
            raise
        except (TypeError, ValueError):
            self._latch()
            raise RecordingError() from None

    def _register_request(self, message_id, request):
        if type(message_id) is not str or message_id in self._pending:
            raise RecordingError()
        self._pending[message_id] = request

    def _reply(self, message):
        if type(message) is not tuple or len(message) != 3 or type(message[0]) is not str:
            self._latch()
            return
        message_id, agent_id, result = message
        pending = self._pending.get(message_id)
        if (pending is None or not pending.dequeued or pending.replied
                or type(agent_id) is not int or agent_id != pending.agent_id):
            self._latch()
            return
        pending.replied = True
        if type(result) is not dict or result.get("success") is not True:
            self._latch()
            return
        if pending.action == "create_post":
            post_id = result.get("post_id")
            if not _valid_id(post_id) or post_id == 0:
                self._latch()
                return
            if self._round is not None:
                if post_id <= self._prior_post_id:
                    self._latch()
                    return
                self._round_witnesses.append(("create_post", agent_id, pending.content, post_id))
        elif pending.action == "refresh" and self._round is not None:
            self._round_witnesses.append(("refresh", agent_id, None, None))

    def _consume(self, message_id, message):
        pending = self._pending.get(message_id)
        if (pending is None or not pending.replied or type(message) is not tuple
                or len(message) != 3 or message[0] != message_id
                or type(message[1]) is not int or message[1] != pending.agent_id):
            self._latch()
            return
        del self._pending[message_id]

    def _check_owner(self, env):
        writer = getattr(env, "platform_task", None)
        if (env is None or env is not self._env or env.platform is not self._platform
                or env.channel is not self._channel or env.platform.channel is not self._channel
                or not isinstance(writer, asyncio.Task) or writer.done()):
            raise RecordingError()

    @staticmethod
    def _read_engine_clock(env):
        from oasis.clock.clock import Clock
        from oasis.environment.env import DefaultPlatformType

        clock = getattr(env.platform, "sandbox_clock", None)
        if env.platform_type is not DefaultPlatformType.TWITTER or type(clock) is not Clock:
            raise RecordingError()
        tick = clock.time_step
        if not _valid_id(tick):
            raise RecordingError()
        label = clock.get_time_step()
        if type(label) is not str or label != str(tick):
            raise RecordingError()
        return clock, tick

    def begin_round(self, env, round_num, active_agents, prior_cursor, *, deadline_monotonic):
        deadline = self._checked_deadline(deadline_monotonic)
        self._check_owner(env)
        self._require_healthy()
        if (self._round is not None or self._passthrough or self._pending
                or type(round_num) is not int or round_num != 1 or not _valid_id(prior_cursor)
                or type(active_agents) is not list or not active_agents):
            raise RecordingError()
        if len(active_agents) > _MAX_AGENTS:
            raise RecordingError("limit_exceeded")
        expected = {}
        agent_ids = set()
        for entry in active_agents:
            if type(entry) is not tuple or len(entry) != 2:
                raise RecordingError()
            agent_id, agent = entry
            if (not _valid_id(agent_id) or agent_id in agent_ids or id(agent) in expected
                    or env.agent_graph.get_agent(agent_id) is not agent):
                raise RecordingError()
            agent_ids.add(agent_id)
            expected[id(agent)] = entry
        source = env.platform.db
        if self._include_memory:
            self._memory_graph = env.agent_graph
            self._memory_owners = self._resolve_memory_cohort(env, deadline)
        with self._source_queries(source, deadline):
            self._require_source_path(source)
            if self._trace_watermark(source) != prior_cursor:
                raise RecordingError()
            prior_post_id = source.execute("SELECT COALESCE(MAX(post_id), 0) FROM post").fetchone()[0]
            if not _valid_id(prior_post_id):
                raise RecordingError()
            if self._include_memory:
                self._verify_memory_users(source, deadline)

        if self._include_engine_clock:
            self._remaining(deadline)
            self._engine_clock, self._clock_before_step = self._read_engine_clock(env)
            self._remaining(deadline)
        self._expected = expected
        self._prior_cursor = prior_cursor
        self._prior_post_id = prior_post_id
        self._source_db = source
        self._round = round_num

    def _check_completed_round(self, env, round_num):
        self._check_owner(env)
        self._require_healthy()
        if (self._round != 1 or type(round_num) is not int or round_num != self._round
                or self._pending or self._running or self._completed != self._expected.keys()
                or self._passthrough or env.platform.db is not self._source_db
                or not any(action == "create_post" for action, *_ in self._round_witnesses)):
            raise RecordingError()

    def check_projection(self, env, round_num, *, deadline_monotonic):
        deadline = min(self._checked_deadline(deadline_monotonic),
                       time.monotonic() + _CAPTURE_SECONDS)
        if self._capture_deadline is not None:
            raise RecordingError()
        self._capture_deadline = deadline
        self._check_completed_round(env, round_num)
        with self._source_queries(self._source_db, deadline):
            cursor = self._trace_watermark(self._source_db)
            if cursor <= self._prior_cursor:
                raise RecordingError()
            _, text_bytes = self._trace_summary(self._source_db, cursor)
            # The ordinary reader holds raw rows while projecting decoded text.
            self._charge_text(2 * text_bytes)
        self._projection_cursor = cursor
        return deadline

    def _release_barrier(self):
        barrier, self._barrier = self._barrier, None
        self._sealed = False
        if barrier is not None:
            barrier.release.set()

    def prepare_close(self):
        self._passthrough = True
        self._release_barrier()
        if self._env is not None:
            self._env._perform_llm_action = self._original_llm
        self._env = self._platform = self._channel = self._original_llm = self._source_db = None
        self._expected.clear()
        self._memory_graph = None
        self._memory_owners = ()
        self._engine_clock = None
        self._pending.clear()
        self._round_witnesses.clear()

    @staticmethod
    def _remaining(deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RecordingError("limit_exceeded")
        return remaining

    @staticmethod
    def _checked_deadline(deadline):
        if type(deadline) not in (int, float):
            raise RecordingError()
        try:
            if not math.isfinite(deadline):
                raise RecordingError()
        except OverflowError:
            raise RecordingError() from None
        return deadline

    async def capture(self, env, round_num, cursor, log_path, agent_names, *,
                      deadline_monotonic, runner_position=None):
        if self._attempted:
            raise RecordingError()
        self._attempted = True
        self._check_completed_round(env, round_num)
        self._validate_runner_position(runner_position, round_num)
        if (self._capture_deadline is None or not _valid_id(cursor)
                or self._projection_cursor != cursor):
            raise RecordingError()
        deadline = min(self._capture_deadline, self._checked_deadline(deadline_monotonic))

        self._remaining(deadline)
        self._runner_position = runner_position
        self._sealed = True
        barrier = _Barrier(asyncio.get_running_loop().create_future(), asyncio.Event())
        self._barrier = barrier
        try:
            await self._channel.enqueue_barrier(barrier)
            writer = env.platform_task
            await asyncio.wait((barrier.acknowledged, writer),
                               timeout=self._remaining(deadline),
                               return_when=asyncio.FIRST_COMPLETED)
            self._remaining(deadline)
            self._check_owner(env)
            self._require_healthy()
            if (self._barrier is not barrier or self._pending
                    or not barrier.acknowledged.done() or not barrier.acknowledged.result()):
                raise RecordingError()
            if self._include_engine_clock:
                clock, tick = self._read_engine_clock(env)
                if clock is not self._engine_clock or tick != self._clock_before_step + 1:
                    raise RecordingError()
                self._clock_after_step = tick
                self._remaining(deadline)
            result = self._copy_at_cut(self._source_db, cursor, log_path, agent_names, deadline)
            self._remaining(deadline)
            self._receipt = result
            return self.receipt
        except RecordingError:
            raise
        except (OSError, sqlite3.Error, TypeError, ValueError):
            self._remaining(deadline)
            raise RecordingError() from None
        finally:
            self._runner_position = None
            self._release_barrier()

    def _check_destination_budget(self):
        paths = ["native.sqlite3", "actions.jsonl", "manifest.json"]
        if self._include_memory:
            paths.append(_MEMORY_FILE)
        if self._runner_config_digest is not None:
            paths.append(_RUNNER_FILE)
        paths.extend("native.sqlite3" + suffix for suffix in _SIDECARS)
        total = 0
        for name in paths:
            path = self._destination / name
            try:
                total += path.stat().st_size
            except FileNotFoundError:
                continue
        if total > _ARTIFACT_LIMIT:
            raise RecordingError("limit_exceeded")

    def _check_sidecars(self, database_path):
        for suffix in _SIDECARS:
            sidecar = database_path.with_name(database_path.name + suffix)
            try:
                if sidecar.stat().st_size:
                    raise RecordingError()
            except FileNotFoundError:
                continue

    def _set_query_deadline(self, connection, deadline):
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)

    @contextmanager
    def _source_queries(self, source, deadline):
        self._remaining(deadline)
        if type(source) is not sqlite3.Connection or source.in_transaction:
            raise RecordingError()
        try:
            previous_timeout = source.execute("PRAGMA busy_timeout").fetchone()[0]
            if not _valid_id(previous_timeout):
                raise RecordingError()
            installed = False
            try:
                self._remaining(deadline)
                source.execute("PRAGMA busy_timeout = 0").close()
                self._set_query_deadline(source, deadline)
                installed = True
                self._remaining(deadline)
                yield
            finally:
                try:
                    if installed:
                        source.set_progress_handler(None, 0)
                finally:
                    source.execute(f"PRAGMA busy_timeout = {previous_timeout}").close()
                self._remaining(deadline)
        except sqlite3.Error:
            self._remaining(deadline)
            raise RecordingError() from None

    def _require_source_path(self, source):
        expected = self._root / "twitter_simulation.db"
        databases = source.execute("PRAGMA database_list").fetchall()
        if (len(databases) != 1 or databases[0][1] != "main" or not databases[0][2]
                or Path(databases[0][2]).resolve(strict=True) != expected
                or expected.resolve(strict=True) != expected
                or source.execute("PRAGMA main.encoding").fetchone() != ("UTF-8",)):
            raise RecordingError()
        return expected

    def _db_geometry(self, connection):
        page_size = connection.execute("PRAGMA main.page_size").fetchone()[0]
        page_count = connection.execute("PRAGMA main.page_count").fetchone()[0]
        if (type(page_size) is not int or not 512 <= page_size <= 65536
                or type(page_count) is not int or page_count <= 0):
            raise RecordingError()
        if page_count > _DATABASE_LIMIT // page_size:
            raise RecordingError("limit_exceeded")
        return page_size, page_count

    @staticmethod
    def _trace_watermark(connection):
        value = connection.execute("SELECT COALESCE(MAX(rowid), 0) FROM trace").fetchone()[0]
        if not _valid_id(value):
            raise RecordingError()
        return value

    def _trace_summary(self, connection, cursor):
        parameters = (self._prior_cursor, cursor, _MAX_ROWS + 1)
        selection = "FROM trace WHERE rowid > ? AND rowid <= ? ORDER BY rowid LIMIT ?"
        count, text_bytes, unsupported = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(COALESCE(length(CAST(created_at AS BLOB)), 0)"
            " + COALESCE(length(CAST(action AS BLOB)), 0)"
            " + COALESCE(length(CAST(info AS BLOB)), 0)), 0),"
            " COALESCE(SUM(CASE WHEN action IN ('refresh', 'create_post') THEN 0 ELSE 1 END), 0)"
            " FROM (SELECT created_at, action, info " + selection + ")",
            parameters,
        ).fetchone()
        if count > _MAX_ROWS:
            raise RecordingError("limit_exceeded")
        if unsupported or count != len(self._round_witnesses) or not _valid_id(text_bytes):
            raise RecordingError()
        return count, text_bytes

    def _trace_snapshot(self, connection, cursor, deadline):
        self._remaining(deadline)
        if self._trace_watermark(connection) != cursor:
            raise RecordingError()
        count, text_bytes = self._trace_summary(connection, cursor)
        self._charge_text(text_bytes)
        rows = connection.execute(
            "SELECT rowid, user_id, created_at, action, info FROM trace"
            " WHERE rowid > ? AND rowid <= ? ORDER BY rowid LIMIT ?",
            (self._prior_cursor, cursor, _MAX_ROWS + 1),
        ).fetchall()
        if not rows or len(rows) != count or rows[-1][0] != cursor:
            raise RecordingError()
        return rows

    def _verified_posts(self, connection, rows, deadline):
        posts = []
        post_ids = set()
        previous = self._prior_cursor
        for row, witness in zip(rows, self._round_witnesses, strict=True):
            self._remaining(deadline)
            row_id, user_id, trace_created_at, action, info = row
            expected_action, expected_user, content, post_id = witness
            if (not _valid_id(row_id) or row_id <= previous or not _valid_id(user_id)
                    or user_id != expected_user or action != expected_action or type(info) is not str):
                raise RecordingError()
            previous = row_id
            if action == "refresh":
                continue
            payload = strict_json(info)
            if (type(payload) is not dict or payload.keys() != {"content", "post_id"}
                    or type(payload["content"]) is not str or payload["content"] != content
                    or type(payload["post_id"]) is not int or payload["post_id"] != post_id
                    or post_id <= self._prior_post_id or post_id in post_ids):
                raise RecordingError()
            query = "SELECT 1 FROM post WHERE post_id = ? AND user_id = ? AND content = ?"
            params = (post_id, user_id, content)
            if self._include_engine_clock:
                if type(trace_created_at) is not int or trace_created_at != self._clock_before_step:
                    raise RecordingError()
                query += " AND typeof(created_at) = 'integer' AND created_at = ?"
                params += (self._clock_before_step,)
            row_found = connection.execute(query, params).fetchone()
            if row_found != (1,):
                raise RecordingError()
            post_ids.add(post_id)
            posts.append((user_id, content, post_id))
        if not posts:
            raise RecordingError()
        return posts

    def _backup(self, source, destination_path, page_size, deadline):
        with destination_path.open("xb"):
            pass
        self._remaining(deadline)
        with closing(sqlite3.connect(destination_path.as_uri() + "?mode=rw", uri=True,
                                     timeout=0)) as destination:
            def progress(_status, _remaining_pages, total_pages):
                self._remaining(deadline)
                if total_pages > _DATABASE_LIMIT // page_size:
                    raise RecordingError("limit_exceeded")
                self._check_destination_budget()

            source.backup(destination, pages=_BACKUP_PAGES, progress=progress, sleep=0.01)
        self._remaining(deadline)
        self._check_sidecars(destination_path)
        self._check_destination_budget()

    def _file_digest(self, path, limit, deadline):
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while True:
                self._remaining(deadline)
                block = stream.read(min(_READ_BYTES, limit - size + 1))
                if not block:
                    break
                size += len(block)
                if size > limit:
                    raise RecordingError("limit_exceeded")
                digest.update(block)
        return size, digest.hexdigest()

    def _resolve_memory_cohort(self, env, deadline):
        from camel.memories import ChatHistoryMemory
        from camel.memories.blocks.chat_history_block import ChatHistoryBlock
        from camel.storages.key_value_storages.in_memory import InMemoryKeyValueStorage

        self._remaining(deadline)
        graph = env.agent_graph
        mappings = getattr(graph, "agent_mappings", None)
        if (graph is not self._memory_graph or getattr(graph, "backend", None) != "igraph"
                or type(mappings) is not dict or len(mappings) != len(self._prepared_agent_ids)
                or graph.get_num_nodes() != len(self._prepared_agent_ids)
                or not all(_valid_id(key) for key in mappings)
                or set(mappings) != set(self._prepared_agent_ids)):
            raise RecordingError()

        owners = []
        camel_ids = set()
        object_ids = set()
        for platform_id in self._prepared_agent_ids:
            self._remaining(deadline)
            agent = mappings[platform_id]
            if (graph.get_agent(platform_id) is not agent
                    or not _valid_id(getattr(agent, "social_agent_id", None))
                    or agent.social_agent_id != platform_id):
                raise RecordingError()
            action = getattr(getattr(agent, "env", None), "action", None)
            if (getattr(action, "channel", None) is not self._channel
                    or not _valid_id(getattr(action, "agent_id", None))
                    or action.agent_id != platform_id):
                raise RecordingError()

            camel_id = getattr(agent, "agent_id", None)
            memory = getattr(agent, "memory", None)
            if (type(camel_id) is not str or not IDENTIFIER.fullmatch(camel_id)
                    or camel_id in camel_ids or type(memory) is not ChatHistoryMemory
                    or type(memory.agent_id) is not str or memory.agent_id != camel_id):
                raise RecordingError()
            block = memory._chat_history_block
            if type(block) is not ChatHistoryBlock or type(block.storage) is not InMemoryKeyValueStorage:
                raise RecordingError()
            storage = block.storage
            for value in (agent, action, memory, block, storage):
                identity = id(value)
                if identity in object_ids:
                    raise RecordingError()
                object_ids.add(identity)
            camel_ids.add(camel_id)
            owners.append(_AgentMemoryOwner(platform_id, camel_id, agent, action, memory, block, storage))
        return tuple(owners)

    def _verify_memory_users(self, connection, deadline):
        self._remaining(deadline)
        candidates = self._prepared_agent_ids
        rows = connection.execute(
            "SELECT agent_id, user_id FROM user ORDER BY agent_id LIMIT ?",
            (len(candidates) + 1,),
        ).fetchall()
        if len(rows) != len(candidates):
            raise RecordingError()
        for (agent_id, user_id), platform_id in zip(rows, candidates, strict=True):
            if (not _valid_id(agent_id) or not _valid_id(user_id)
                    or agent_id != platform_id or user_id != platform_id):
                raise RecordingError()
        self._remaining(deadline)

    def _copy_memory_at_cut(self, deadline):
        from .native_checkpoint_memory import NativeMemoryCaptureBudget, capture_native_memory

        barrier = self._barrier
        if (not self._sealed or barrier is None or not barrier.acknowledged.done()
                or not barrier.acknowledged.result() or barrier.release.is_set()):
            raise RecordingError()
        owners = self._resolve_memory_cohort(self._env, deadline)
        if (len(owners) != len(self._memory_owners)
                or not all(before.same_binding(current)
                           for before, current in zip(self._memory_owners, owners, strict=True))):
            raise RecordingError()

        if self._text_remaining == 0:
            raise RecordingError("limit_exceeded")
        budget = NativeMemoryCaptureBudget(max_bytes=self._text_remaining, deadline_monotonic=deadline)
        prefix = f"NWM1\n{len(owners)}\n".encode("ascii")
        # Reserve the maximum count width for each frame before any payload
        # allocation. Charge the exact completed file size to the outer pool.
        max_count_width = len(str(MAX_JSON))
        header_allowance = len(prefix) + sum(
            len(str(owner.platform_id)) + max_count_width + 2 for owner in owners
        )
        budget.charge_bytes(header_allowance)

        path = self._destination / _MEMORY_FILE
        digest = hashlib.sha256()
        size = len(prefix)
        budget.check()
        with path.open("xb") as stream:
            stream.write(prefix)
            digest.update(prefix)
            for owner in owners:
                budget.check()
                raw = capture_native_memory(owner.memory, expected_agent_id=owner.camel_id, budget=budget)
                frame = f"{owner.platform_id} {len(raw)}\n".encode("ascii")
                stream.write(frame)
                stream.write(raw)
                digest.update(frame)
                digest.update(raw)
                size += len(frame) + len(raw)
                del raw
            budget.check()
        self._charge_text(size)
        checksum = digest.hexdigest()
        if self._file_digest(path, size, deadline) != (size, checksum):
            raise RecordingError()
        self._check_destination_budget()
        self._remaining(deadline)
        return {
            "version": 1,
            "filename": _MEMORY_FILE,
            "coverage": "all_registered_agents",
            "agent_count": len(owners),
            "bytes": size,
            "sha256": checksum,
        }

    def _validate_runner_position(self, position, round_num):
        if self._runner_config_digest is None:
            if position is not None:
                raise RecordingError()
            return
        if (type(position) is not tuple or len(position) != 5
                or any(type(value) is not int or not 0 <= value < 2**32 for value in position)):
            raise RecordingError()
        completed, horizon, interval, simulated_minutes, _ = position
        if (completed != round_num or not 1 <= completed <= horizon
                or not 1 <= interval <= 1440 or horizon * interval > _MAX_SIMULATED_MINUTES
                or simulated_minutes != (completed - 1) * interval):
            raise RecordingError()

    def _copy_runner_at_cut(self, deadline):
        self._remaining(deadline)
        barrier = self._barrier
        if (not self._sealed or barrier is None or not barrier.acknowledged.done()
                or not barrier.acknowledged.result() or barrier.release.is_set()):
            raise RecordingError()
        self._charge_text(_RUNNER_STATE.size)
        state = random.getstate()
        if (type(state) is not tuple or len(state) != 3
                or type(state[0]) is not int or state[0] != 3
                or type(state[1]) is not tuple or len(state[1]) != 625):
            raise RecordingError()
        version, vector, cache = state
        words, index = vector[:-1], vector[-1]
        if (any(type(word) is not int or not 0 <= word < 2**32 for word in words)
                or type(index) is not int or not 0 <= index <= 624
                or (cache is not None and (type(cache) is not float or not math.isfinite(cache)))):
            raise RecordingError()
        self._remaining(deadline)
        raw = _RUNNER_STATE.pack(
            b"NWR1", version, *self._runner_position, self._runner_config_digest,
            *words, index, int(cache is not None), 0.0 if cache is None else cache,
        )
        path = self._destination / _RUNNER_FILE
        checksum = hashlib.sha256(raw).hexdigest()
        with path.open("xb") as stream:
            stream.write(raw)
        if self._file_digest(path, _RUNNER_STATE.size, deadline) != (len(raw), checksum):
            raise RecordingError()
        self._check_destination_budget()
        self._remaining(deadline)
        return {"version": 1, "file": _RUNNER_FILE, "bytes": len(raw), "sha256": checksum}

    def _copy_at_cut(self, source, cursor, log_path, agent_names, deadline):
        expected_log = self._root / "twitter" / "actions.jsonl"
        if (expected_log.resolve(strict=True) != expected_log
                or Path(log_path).resolve(strict=True) != expected_log
                or type(agent_names) is not dict):
            raise RecordingError()
        with self._source_queries(source, deadline):
            expected_source = self._require_source_path(source)
            journal = source.execute("PRAGMA main.journal_mode").fetchone()[0]
            if journal not in {"delete", "truncate", "persist", "wal", "off"}:
                raise RecordingError()
            page_size, _ = self._db_geometry(source)
            if expected_source.stat().st_size > _DATABASE_LIMIT:
                raise RecordingError("limit_exceeded")
            rows = self._trace_snapshot(source, cursor, deadline)
            posts = self._verified_posts(source, rows, deadline)
            if self._include_memory:
                self._verify_memory_users(source, deadline)

            self._remaining(deadline)
            self._destination.mkdir(mode=0o700)
            database_path = self._destination / "native.sqlite3"
            self._backup(source, database_path, page_size, deadline)
            with closing(sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True,
                                         timeout=0)) as backup:
                self._set_query_deadline(backup, deadline)
                try:
                    self._db_geometry(backup)
                    backup_rows = self._trace_snapshot(backup, cursor, deadline)
                    if backup_rows != rows or self._verified_posts(backup, backup_rows, deadline) != posts:
                        raise RecordingError()
                    if self._include_memory:
                        self._verify_memory_users(backup, deadline)
                finally:
                    backup.set_progress_handler(None, 0)
            self._check_sidecars(database_path)
            if (source.in_transaction or self._trace_snapshot(source, cursor, deadline) != rows
                    or self._verified_posts(source, rows, deadline) != posts):
                raise RecordingError()
            log_size, log_digest = self._copy_log(expected_log, posts, agent_names, deadline)
            if self._file_digest(expected_log, MAX_JSON, deadline) != (log_size, log_digest):
                raise RecordingError()
            db_size, db_digest = self._file_digest(database_path, _DATABASE_LIMIT, deadline)
            if db_size == 0:
                raise RecordingError()

            receipt = {
                "anchors": self._anchors,
                "round": self._round,
                "prior_cursor": self._prior_cursor,
                "prior_post_id": self._prior_post_id,
                "cursor": cursor,
                "database_sha256": db_digest,
                "database_bytes": db_size,
                "log_sha256": log_digest,
                "log_bytes": log_size,
                "post_count": len(posts),
                "manifest": "manifest.json",
                "full_checkpoint": False,
                "continuation_supported": False,
            }
            if self._include_memory:
                receipt["memory"] = self._copy_memory_at_cut(deadline)
            if self._runner_config_digest is not None:
                receipt["runner_state"] = self._copy_runner_at_cut(deadline)
            if self._include_engine_clock:
                receipt["engine_clock"] = {
                    "version": 1,
                    "before_step": self._clock_before_step,
                    "after_step": self._clock_after_step,
                }
            manifest = canonical({
                **receipt, "version": 1, "platform": "twitter", "source_journal_mode": journal,
            })
            if len(manifest) > _RECEIPT_LIMIT:
                raise RecordingError("limit_exceeded")
            self._remaining(deadline)
            with (self._destination / "manifest.json").open("xb") as stream:
                stream.write(manifest)
            self._check_destination_budget()
            self._remaining(deadline)
            return receipt

    def _copy_log(self, source_path, posts, agent_names, deadline):
        digest = hashlib.sha256()
        size = 0
        started = False
        ended = False
        seen_posts = 0
        event_count = 0
        with source_path.open("rb") as source:
            bound_length = os.fstat(source.fileno()).st_size
            if not bound_length:
                raise RecordingError()
            self._charge_text(bound_length)
            with (self._destination / "actions.jsonl").open("xb") as destination:
                while True:
                    self._remaining(deadline)
                    raw = source.readline(min(MAX_EVENT_BYTES, bound_length - size) + 1)
                    if not raw:
                        break
                    size += len(raw)
                    event_count += 1
                    if len(raw) > MAX_EVENT_BYTES or size > bound_length or event_count > MAX_EVENTS:
                        raise RecordingError("limit_exceeded")
                    if ended or not raw.endswith(b"\n"):
                        raise RecordingError()
                    event = strict_json(raw, MAX_EVENT_BYTES)
                    if type(event) is not dict:
                        raise RecordingError()
                    round_num = event.get("round")
                    event_type = event.get("event_type")
                    if (round_num is None or (type(round_num) is int and round_num == 0)) and not started:
                        pass  # Setup prefix is preserved byte-for-byte.
                    elif type(round_num) is not int or round_num != self._round:
                        raise RecordingError()
                    elif not started:
                        if event_type != "round_start" or "action_type" in event:
                            raise RecordingError()
                        started = True
                    elif event_type == "round_end":
                        if ("action_type" in event or type(event.get("actions_count")) is not int
                                or event["actions_count"] != len(posts) or seen_posts != len(posts)):
                            raise RecordingError()
                        ended = True
                    else:
                        if event_type is not None or seen_posts >= len(posts):
                            raise RecordingError()
                        self._match_log_post(event, posts[seen_posts], agent_names)
                        seen_posts += 1
                    digest.update(raw)
                    destination.write(raw)
        if not ended or size != bound_length:
            raise RecordingError()
        self._check_destination_budget()
        return size, digest.hexdigest()

    @staticmethod
    def _match_log_post(event, witness, agent_names):
        agent_id, content, post_id = witness
        name = agent_names.get(agent_id)
        args = event.get("action_args")
        if (event.keys() != {"round", "timestamp", "agent_id", "agent_name", "action_type",
                             "action_args", "result", "success"}
                or event["action_type"] != "CREATE_POST" or event["success"] is not True
                or event["result"] is not None or type(event["timestamp"]) is not str
                or type(event["agent_id"]) is not int or event["agent_id"] != agent_id
                or type(name) is not str or event["agent_name"] != name
                or type(args) is not dict or args.keys() != {"content", "post_id"}
                or type(args["content"]) is not str or args["content"] != content
                or type(args["post_id"]) is not int or args["post_id"] != post_id):
            raise RecordingError()

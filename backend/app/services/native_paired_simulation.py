"""Owned paired native runs and fresh hydration from a common round cut."""

import asyncio
from contextlib import AsyncExitStack
from datetime import datetime, timedelta
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import random
import sqlite3
from time import monotonic

from nexaweave_execution.native_seed_contracts import seed_bridge_index, validate_native_seed_plan

from .knowledge_report_tools import NeutralCapabilityError
from .native_checkpoint_memory import (
    NativeMemoryCaptureBudget, capture_native_memory, resolve_native_memory_cohort,
    restore_native_memory,
)
from .native_paired_checkpoint_bundle import (
    ENGINE_VERSIONS, load_paired_checkpoint, paired_anchors, prepared_digest, publish_paired_checkpoint,
)
from .native_paired_rounds import PLATFORMS, PairedRoundCoordinator, PairedWriterChannel
from .native_recording_contracts import RecordingError
from .native_reddit_checkpoint import _bound_schema, _idle
from .native_reddit_profiles import build_reddit_agent_graph
from .native_simulation import NativeSimulationSession, _child_no_sidecars
from .native_startup_diagnostic import observe_startup
from .native_twitter_restore import _read_lineage_input


_EPOCH = datetime(2026, 1, 1)
_DB_LIMIT = 64 * 1024 * 1024
_LOG_LIMIT = 8 * 1024 * 1024
_MEMORY_LIMIT = 1024 * 1024
_ACTORS = (0, 1)


class _PairedClock:
    def __init__(self, minutes_per_round, tick=0):
        self.minutes_per_round = minutes_per_round
        self.time_step = tick

    def get_time_step(self):
        return str(self.time_step)

    def time_transfer(self, _now_time, start_time):
        if start_time != _EPOCH:
            raise RecordingError()
        return _EPOCH + timedelta(minutes=self.time_step * self.minutes_per_round)


def _platform_settings(platform):
    if platform == "twitter":
        return dict(recsys_type="random", show_score=False, refresh_rec_post_count=2,
                    max_rec_post_len=2, following_post_count=3)
    return dict(recsys_type="reddit", show_score=True, refresh_rec_post_count=5,
                max_rec_post_len=100, following_post_count=3)


def _rng_wire():
    algorithm, values, gaussian = random.getstate()
    return [algorithm, list(values), gaussian]


def _read_rng(value):
    if (type(value) is not list or len(value) != 3
            or type(value[0]) is not int or value[0] != 3
            or type(value[1]) is not list or len(value[1]) != 625
            or any(type(v) is not int or not 0 <= v < 2**32 for v in value[1])
            or not 0 <= value[1][-1] <= 624
            or (value[2] is not None and
                (type(value[2]) is not float or not math.isfinite(value[2])))):
        raise RecordingError()
    return value[0], tuple(value[1]), value[2]


def _topology(graph):
    native = graph.graph
    if (graph.backend != "igraph" or not native.is_directed()
            or native.vcount() != len(_ACTORS) or tuple(native.vs["name"]) != _ACTORS
            or native.ecount() > 2 or native.edge_attributes()
            or set(native.vertex_attributes()) != {"name"}):
        raise RecordingError()
    edges = [list(edge) for edge in graph.get_edges()]
    _validate_edges(edges)
    return edges


def _validate_edges(edges):
    if (type(edges) is not list or len(edges) > 2
            or any(type(edge) is not list or len(edge) != 2
                   or any(type(actor) is not int or actor not in _ACTORS for actor in edge)
                   or edge[0] == edge[1] for edge in edges)
            or len({tuple(edge) for edge in edges}) != len(edges)):
        raise RecordingError()


def _write_new(path, data):
    with path.open("xb") as stream:
        stream.write(data)


def _check_log_cut(raw, completed_round):
    last = raw.rstrip(b"\r\n").rsplit(b"\n", 1)[-1]
    if not 1 <= len(last) <= 8192:
        raise RecordingError()
    entry = json.loads(last)
    if (type(entry) is not dict or type(entry.get("round")) is not int
            or entry["round"] != completed_round or entry.get("event_type") != "round_end"):
        raise RecordingError()


def _validate_store(connection, cursor, deadline):
    if (type(cursor) is not int or cursor < len(_ACTORS)
            or connection.in_transaction
            or connection.execute("PRAGMA journal_mode").fetchone() != ("delete",)):
        raise RecordingError()
    _bound_schema(connection, deadline)
    if (connection.execute("SELECT MAX(rowid) FROM trace").fetchone() != (cursor,)
            or connection.execute("SELECT agent_id, user_id FROM user ORDER BY agent_id LIMIT 3").fetchall()
            != [(0, 0), (1, 1)]):
        raise RecordingError()


def _valid_round_state(state, completed_round, platform, seeded_twitter):
    initial_tick = int(platform == "twitter" and seeded_twitter)
    if (type(state) is not dict or set(state) != {"cursor", "actions", "tick", "edges", "memory"}
            or any(type(state[k]) is not int or not 0 <= state[k] < 2**63
                   for k in ("cursor", "actions", "tick"))
            or not initial_tick <= state["tick"] <= completed_round + initial_tick
            or (platform == "reddit" and state["tick"] != completed_round)
            or type(state["memory"]) is not list or len(state["memory"]) != len(_ACTORS)):
        raise RecordingError()
    _validate_edges(state["edges"])


class NativePairedCheckpointSession(NativeSimulationSession):
    """Run the paired round order with optional materialization or hydration.

    Admission takes prevalidated prepared files in an owned, fresh directory,
    both trusted models, and an authenticated checkpoint digest on restoration.
    The initial profile uses two dense actors with the cross-platform bridge
    and optional execution controls turned off.
    """

    def __init__(self, simulation_dir, *, runtime_sha256, scope, capture=None,
                 restore=None, **options):
        super().__init__(simulation_dir, **options)
        if (self._platforms != PLATFORMS or tuple(sorted(self._agents)) != _ACTORS
                or self._round_capture is not None or self._controls is not None
                or seed_bridge_index(self._config, PLATFORMS,
                                     len(validate_native_seed_plan(self._config))) is not None
                or type(runtime_sha256) is not str or len(runtime_sha256) != 64
                or any(c not in "0123456789abcdef" for c in runtime_sha256)
                or type(scope) is not dict or not scope
                or self.max_rounds < 2 or (capture is not None and restore is not None)):
            raise NeutralCapabilityError("invalid_request")
        self.minutes_per_round = self._config["time_config"]["minutes_per_round"]
        self._seeded_twitter = bool(validate_native_seed_plan(self._config))
        self.logical_rounds = (
            self._config["time_config"]["total_simulation_hours"] * 60 // self.minutes_per_round
        )
        if self.max_rounds > self.logical_rounds:
            raise NeutralCapabilityError("invalid_request")
        if capture is not None:
            if (type(capture) is not tuple or len(capture) != 2 or type(capture[1]) is not int
                    or not 1 <= capture[1] < self.max_rounds):
                raise NeutralCapabilityError("invalid_request")
        if restore is not None and (type(restore) is not tuple or len(restore) != 2):
            raise NeutralCapabilityError("invalid_request")
        transfer = capture if capture is not None else restore
        if transfer is not None:
            destination = Path(transfer[0])
            if (not destination.is_absolute() or destination.resolve() != destination
                    or destination.is_relative_to(self.simulation_dir)
                    or self.simulation_dir.is_relative_to(destination)):
                raise NeutralCapabilityError("invalid_request")
        self._capture_pair = capture
        self._restore_pair = restore
        self.paired_rounds = None
        self.checkpoint_sha256 = None
        if any(version(package) != expected for package, expected in ENGINE_VERSIONS.items()):
            raise NeutralCapabilityError("invalid_request")
        self._anchors = paired_anchors(self.simulation_dir, self.graph_id, self.simulation_id,
                                       self.seed, self.max_rounds, runtime_sha256, scope)

    def platform_for(self, platform, db_path, _oasis):
        from oasis.social_platform.platform import Platform

        return Platform(db_path=db_path, channel=PairedWriterChannel(), start_time=_EPOCH,
                        sandbox_clock=_PairedClock(self.minutes_per_round),
                        **_platform_settings(platform))

    @staticmethod
    def _before_turn(platform, state):
        if platform == "reddit":
            state.result.env.platform.sandbox_clock.time_step = state.completed_round + 1

    async def _record_cut(self, completed_round, twitter, reddit):
        if completed_round != self._capture_pair[1]:
            return
        async with AsyncExitStack() as writers:
            for cut in (twitter, reddit):
                env = cut.result.env
                if env.platform.channel is not env.channel:
                    raise RecordingError()
                await writers.enter_async_context(env.channel.hold(env.platform_task))
            await self._materialize_cut(completed_round, twitter, reddit)

    async def _materialize_cut(self, completed_round, twitter, reddit):
        deadline = self._start_deadline
        budget = NativeMemoryCaptureBudget(max_bytes=_MEMORY_LIMIT, deadline_monotonic=deadline)
        state = {"random": _rng_wire(), "platforms": {}}
        members = {}
        for platform, cut in zip(PLATFORMS, (twitter, reddit), strict=True):
            env = cut.result.env
            owner = env.platform
            if env.platform_task.done() or Path(owner.db_path) != Path(cut.db_path):
                raise RecordingError()
            await _idle(owner)
            _validate_store(owner.db, cut.cursor, deadline)
            db = self.simulation_dir / (platform + "_simulation.db")
            if Path(owner.db_path).resolve(strict=True) != db:
                raise RecordingError()
            _child_no_sidecars(db)
            owners = resolve_native_memory_cohort(env.agent_graph, cut.result.agent_graph, env.channel,
                                                 _ACTORS, deadline)
            history = [
                {"agent_id": agent.camel_id, "frame": capture_native_memory(
                    agent.memory, expected_agent_id=agent.camel_id, budget=budget).decode("utf-8")}
                for agent in owners
            ]
            platform_state = dict(cursor=cut.cursor, actions=cut.total_actions,
                                  tick=owner.sandbox_clock.time_step,
                                  edges=_topology(env.agent_graph), memory=history)
            _valid_round_state(platform_state, completed_round, platform, self._seeded_twitter)
            state["platforms"][platform] = platform_state
            members[platform + ".sqlite3"] = _read_lineage_input(db, _DB_LIMIT, deadline)[0]
            members[platform + ".jsonl"] = _read_lineage_input(
                Path(cut.action_logger.log_path), _LOG_LIMIT, deadline)[0]
            _check_log_cut(members[platform + ".jsonl"], completed_round)
            await _idle(owner)
        if random.getstate() != _read_rng(state["random"]):
            raise RecordingError()
        if prepared_digest(self.simulation_dir) != self._anchors["prepared_sha256"]:
            raise RecordingError()
        self.checkpoint_sha256 = publish_paired_checkpoint(
            self._capture_pair[0], anchors=self._anchors, completed_round=completed_round,
            runtime_state=state, members=members, deadline=deadline,
        )

    async def _start_async(self):
        from scripts import run_parallel_simulation as native

        if os.environ.get("OASIS_DB_PATH"):
            raise RecordingError()
        previous_random = random.getstate()
        try:
            native._load_native_engine()
            logs = native.SimulationLogManager(str(self.simulation_dir))
            self._reddit_actions = tuple(native.REDDIT_ACTIONS)
            random.seed(self.seed)
            self._reddit_graph = build_reddit_agent_graph(
                self._profiles["reddit"], self._models["reddit"], list(self._reddit_actions))
            if self._restore_pair is not None:
                await self._resume(native, logs)
                return
            self.paired_rounds = PairedRoundCoordinator()
            limit = self.max_rounds if self._capture_pair is None else self._capture_pair[1]

            async def start(platform):
                runner = (native.run_twitter_simulation if platform == "twitter"
                          else native.run_reddit_simulation)
                logger = getattr(logs, "get_" + platform + "_logger")()
                result = await observe_startup(
                    self, runner, platform, self._config, str(self.simulation_dir),
                    logger, logs, limit, native_dependencies=self,
                )
                if result.env is None or result.agent_graph is None:
                    raise RecordingError()
                return result

            # A capture run stops at its cut; an uninterrupted control uses the full limit.
            await self.paired_rounds.run(
                start, before_turn=self._before_turn,
                at_cut=self._record_cut if self._capture_pair is not None else None,
            )
        finally:
            random.setstate(previous_random)

    async def _open_restored(self, native, platform, state, members, logger):
        from oasis.environment.env import OasisEnv
        from oasis.social_agent.agents_generator import connect_platform_channel
        from oasis.social_platform.platform import Platform
        from oasis.social_platform.platform_utils import PlatformUtils
        from oasis.social_platform.typing import RecsysType

        deadline = self._start_deadline
        path = self.simulation_dir / (platform + "_simulation.db")
        _write_new(path, members[platform + ".sqlite3"])
        _child_no_sidecars(path)
        _write_new(Path(logger.log_path), members[platform + ".jsonl"])
        if platform == "twitter":
            graph = await native.generate_twitter_agent_graph(
                profile_path=str(self.simulation_dir / "twitter_profiles.csv"),
                model=self._models[platform], available_actions=native.TWITTER_ACTIONS,
            )
        else:
            graph = self._reddit_graph
        channel = PairedWriterChannel()
        connect_platform_channel(channel, graph)
        owners = resolve_native_memory_cohort(graph, graph, channel, _ACTORS, deadline)
        if _topology(graph):
            raise RecordingError()
        seen = set()
        for owner, saved in zip(owners, state["memory"], strict=True):
            if (type(saved) is not dict or set(saved) != {"agent_id", "frame"}
                    or type(saved["agent_id"]) is not str or saved["agent_id"] in seen
                    or type(saved["frame"]) is not str or len(saved["frame"]) > _MEMORY_LIMIT):
                raise RecordingError()
            agent_id = saved["agent_id"]
            seen.add(agent_id)
            raw = saved["frame"].encode("utf-8")
            if len(raw) > _MEMORY_LIMIT:
                raise RecordingError()
            memory = restore_native_memory(
                raw, expected_agent_id=agent_id, context_creator=owner.memory.get_context_creator(),
            )
            # The public setter clears the history it receives.
            attrs = vars(owner.agent)
            if attrs.get("_memory") is not owner.memory or attrs.get("agent_id") != owner.camel_id:
                raise RecordingError()
            attrs.update(agent_id=agent_id, _memory=memory)
        installed = resolve_native_memory_cohort(graph, graph, channel, _ACTORS, deadline)
        if tuple(agent.camel_id for agent in installed) != tuple(mem["agent_id"] for mem in state["memory"]):
            raise RecordingError()
        for source, target in state["edges"]:
            graph.add_edge(source, target)
        if _topology(graph) != state["edges"]:
            raise RecordingError()
        db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=0)
        try:
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA query_only=ON")
            db.set_progress_handler(lambda: monotonic() >= deadline, 1000)
            _validate_store(db, state["cursor"], deadline)
            db.execute("PRAGMA query_only=OFF")
            owner = Platform.__new__(Platform)
            owner.db_path, owner.db, owner.db_cursor = str(path), db, db.cursor()
            owner.channel = channel
            owner.start_time = _EPOCH
            owner.sandbox_clock = _PairedClock(self.minutes_per_round, state["tick"])
            for key, value in _platform_settings(platform).items():
                setattr(owner, key, value)
            owner.recsys_type = RecsysType(owner.recsys_type)
            owner.allow_self_rating = True
            owner.rec_prob = 0.7
            owner.use_openai_embedding = False
            owner.trend_num_days = 7
            owner.trend_top_k = 1
            owner.report_threshold = 2
            owner.pl_utils = PlatformUtils(db, owner.db_cursor, _EPOCH, owner.sandbox_clock,
                                              owner.show_score, owner.recsys_type, owner.report_threshold)
            env = OasisEnv(agent_graph=graph, platform=owner, database_path=str(path), semaphore=30)
            result = native.PlatformSimulation()
            result.env, result.agent_graph = env, graph
            self.adopt(platform, result)
            env.platform_task = asyncio.create_task(owner.running())
            return result, path
        except BaseException:
            db.close()
            raise

    async def _resume(self, native, logs):
        completed, snapshot, members = load_paired_checkpoint(
            self._restore_pair[0], expected_digest=self._restore_pair[1],
            expected_anchors=self._anchors, deadline=self._start_deadline,
        )
        if (not 1 <= completed < self.max_rounds or type(snapshot) is not dict
                or set(snapshot) != {"random", "platforms"}
                or type(snapshot["platforms"]) is not dict or set(snapshot["platforms"]) != set(PLATFORMS)):
            raise RecordingError()
        restored_random = _read_rng(snapshot["random"])
        states = snapshot["platforms"]
        for platform in PLATFORMS:
            _valid_round_state(states[platform], completed, platform, self._seeded_twitter)
            _check_log_cut(members[platform + ".jsonl"], completed)
        restored = {}
        for platform in PLATFORMS:
            logger = getattr(logs, "get_" + platform + "_logger")()
            result, db = await self._open_restored(native, platform, states[platform], members, logger)
            restored[platform] = (result, str(db), logger)
        random.setstate(restored_random)
        self.paired_rounds = PairedRoundCoordinator(start_round=completed)

        async def resume(platform):
            result, db, logger = restored[platform]
            state = states[platform]
            cursor, total = state["cursor"], state["actions"]
            names = native.get_agent_names_from_config(self._config)
            await self.paired_rounds.ready(platform, result, names, db, logger, cursor, total,
                                          self.logical_rounds, self.minutes_per_round, self.max_rounds)
            for index in range(completed, self.max_rounds):
                cursor, total, _ = await native.step_native_round(
                    platform, result, self._config, db, logger, names, index,
                    self.minutes_per_round, cursor, total, self.logical_rounds, self,
                )
                await self.paired_rounds.after_round(platform, index + 1, cursor, total)
            logger.log_simulation_end(self.max_rounds, total)
            result.total_actions = total
            return result

        await self.paired_rounds.run(resume, before_turn=self._before_turn)

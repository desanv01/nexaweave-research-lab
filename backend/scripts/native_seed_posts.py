"""Execute admitted seeds through the owned native agent/platform API.

No SDK import or SQLite connection creation occurs here. The caller supplies
its reset environment; evidence reads use that platform's existing connection.
"""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import json
from dataclasses import dataclass

from nexaweave_execution.native_seed_contracts import (SEED_BRIDGE_INDEX, NativeSeedPost,
    seed_bridge_index, validate_native_seed_plan)


class NativeSeedError(RuntimeError):
    """Fixed, safe code; native exception/response text stays out of diagnostics."""


@dataclass(frozen=True)
class NativeSeedResult:
    successful_count: int
    trace_cursor: int


@dataclass(frozen=True)
class _SeedTransfer:
    ordinal: int
    post: NativeSeedPost
    post_id: int
    trace_id: int


class _OwnedSeedBridge:
    def __init__(self, ordinal, config, plan, profiles, results):
        self.ordinal, self.config, self.plan = ordinal, config, plan
        self.profiles, self.results = profiles, results
        self.task = asyncio.current_task()
        self.source_cursor = None
        self.pending = None
        self.source_verified = False
        self.completed = False

    def require_owner(self, config, plan, platform, env, action_logger):
        result = self.results.get(platform)
        if (asyncio.current_task() is not self.task or config is not self.config or plan != self.plan
                or seed_bridge_index(config, ('twitter', 'reddit'), len(plan)) != self.ordinal
                or result is None or result.env is not env or result.agent_graph is not env.agent_graph
                or action_logger is None):
            raise NativeSeedError('native_seed_bridge_mismatch')

    def check_actor(self, env, platform, post):
        agent = env.agent_graph.get_agent(post.actor_id)
        username = self.profiles[platform][post.actor_id]['username']
        registered = env.platform.db.execute(
            'SELECT agent_id, name FROM user WHERE user_id = ?', (post.actor_id,),
        ).fetchone()
        if (type(agent.social_agent_id) is not int or agent.social_agent_id != post.actor_id
                or agent.user_info.name != username or registered != (post.actor_id, username)):
            raise NativeSeedError('native_seed_bridge_mismatch')

    def verify_source(self, result):
        transfer = self.pending
        if (result is not self.results.get('twitter') or type(transfer) is not _SeedTransfer
                or self.source_verified or self.source_cursor is None):
            raise NativeSeedError('native_seed_bridge_mismatch')
        env = result.env
        self.check_actor(env, 'twitter', transfer.post)
        db = env.platform.db
        trace = db.execute(
            "SELECT rowid, user_id, info FROM trace WHERE rowid > ? AND rowid <= ? "
            "AND action = 'create_post' ORDER BY rowid LIMIT 1 OFFSET ?",
            (self.source_cursor, transfer.trace_id, transfer.ordinal),
        ).fetchone()
        if (_committed_post(db, transfer.post, transfer.post_id, trace) != transfer.trace_id
                or transfer.ordinal != self.ordinal or transfer.post != self.plan[self.ordinal]):
            raise NativeSeedError('native_seed_bridge_mismatch')
        self.source_verified = True

    def destination(self, env):
        transfer = self.pending
        if (not self.source_verified or type(transfer) is not _SeedTransfer or self.completed
                or env.platform.db is self.results['twitter'].env.platform.db):
            raise NativeSeedError('native_seed_bridge_mismatch')
        self.check_actor(env, 'reddit', transfer.post)
        return transfer


_SEED_BRIDGE = ContextVar('owned_native_seed_bridge', default=None)


@contextmanager
def owned_seed_bridge(config, platforms, profiles, results):
    if SEED_BRIDGE_INDEX not in config:
        yield None
        return
    plan = validate_native_seed_plan(config)
    index = seed_bridge_index(config, platforms, len(plan))
    bridge = _OwnedSeedBridge(index, config, plan, profiles, results)
    token = _SEED_BRIDGE.set(bridge)
    try:
        yield bridge
        if not bridge.completed:
            raise NativeSeedError('native_seed_bridge_mismatch')
    finally:
        bridge.pending = None
        _SEED_BRIDGE.reset(token)


def native_trace_cursor(env):
    return env.platform.db.execute(
        "SELECT COALESCE(MAX(rowid), 0) FROM trace").fetchone()[0]


def _committed_post(db, post, post_id, trace):
    row = db.execute('SELECT user_id, content FROM post WHERE post_id = ?', (post_id,)).fetchone()
    if db.in_transaction or trace is None or row != (post.actor_id, post.content):
        raise NativeSeedError('native_seed_outcome_uncertain')
    trace_id, actor_id, info = trace
    info = json.loads(info)
    if (type(trace_id) is not int or trace_id <= 0 or actor_id != post.actor_id
            or type(info) is not dict or type(info.get('post_id')) is not int
            or info['post_id'] != post_id or info.get('content') != post.content):
        raise NativeSeedError('native_seed_outcome_uncertain')
    return trace_id


async def apply_native_seed_posts(env, config, platform, action_logger=None,
                                  agent_names=None):
    """Apply each seed once, in source order, then advance Twitter once.

    Success requires the exact native response plus a new committed post and
    matching trace beyond the pre-action cursor. Partial/unknown effects are
    retained and never retried. Only declared response failure gets a failure
    action record; unknown outcomes get no invented outcome record.
    """
    plan = validate_native_seed_plan(config)
    try:
        actual_platform = env.platform_type.value
        if platform not in ("twitter", "reddit") or actual_platform != platform:
            raise NativeSeedError("native_seed_platform_mismatch")
        bridge = _SEED_BRIDGE.get()
        transfer = None
        if bridge is not None:
            bridge.require_owner(config, plan, platform, env, action_logger)
            if platform == 'reddit':
                transfer = bridge.destination(env)
                plan += (transfer.post,)
            elif bridge.source_cursor is not None:
                raise NativeSeedError('native_seed_bridge_mismatch')
            else:
                bridge.source_cursor = native_trace_cursor(env)
        elif SEED_BRIDGE_INDEX in config:
            raise NativeSeedError('native_seed_bridge_mismatch')
        # Resolve the entire plan before refresh or the first seed effect.
        actors = []
        for post in plan:
            actor = env.agent_graph.get_agent(post.actor_id)
            if (actor.social_agent_id != post.actor_id
                    or type(actor.social_agent_id) is not int
                    or not callable(actor.perform_action_by_data)):
                raise NativeSeedError("native_seed_actor_mismatch")
            actors.append(actor)
        cursor = native_trace_cursor(env)
        if not plan:
            return NativeSeedResult(0, cursor)
        await env.platform.update_rec_table()
        successful = 0
        for ordinal, (post, actor) in enumerate(zip(plan, actors)):
            before_trace = native_trace_cursor(env)
            before_post = env.platform.db.execute(
                "SELECT COALESCE(MAX(post_id), 0) FROM post").fetchone()[0]
            transferring = transfer is not None and ordinal == len(plan) - 1
            if transferring:
                bridge.pending = None  # Consume immediately before the effect, even on failure.
            response = await actor.perform_action_by_data(
                "create_post", content=post.content)
            if type(response) is dict and response.get("success") is False:
                if action_logger is not None:
                    action_logger.log_action(
                        round_num=0, agent_id=post.actor_id,
                        agent_name=(agent_names or {}).get(post.actor_id,
                                                        f"Agent_{post.actor_id}"),
                        action_type="CREATE_POST",
                        action_args={"content": post.content},
                        result="native_seed_response_failed", success=False)
                raise NativeSeedError("native_seed_response_failed")
            if (type(response) is not dict or set(response) != {"success", "post_id"}
                    or response["success"] is not True
                    or type(response["post_id"]) is not int
                    or response["post_id"] <= max(0, before_post)):
                raise NativeSeedError("native_seed_outcome_uncertain")
            post_id = response["post_id"]
            db = env.platform.db
            traces = db.execute(
                "SELECT rowid, user_id, info FROM trace "
                "WHERE rowid > ? AND action = ? ORDER BY rowid LIMIT 2",
                (before_trace, "create_post")).fetchall()
            if len(traces) != 1:
                raise NativeSeedError("native_seed_outcome_uncertain")
            trace_id = _committed_post(db, post, post_id, traces[0])
            if bridge is not None and platform == 'twitter' and ordinal == bridge.ordinal:
                bridge.pending = _SeedTransfer(ordinal, post, post_id, trace_id)
            if action_logger is not None:
                args = {"content": post.content}
                if transferring:
                    args['seed_transfer'] = dict(source_ordinal=transfer.ordinal,
                        twitter_post_id=transfer.post_id, twitter_trace_id=transfer.trace_id,
                        reddit_post_id=post_id, reddit_trace_id=trace_id)
                action_logger.log_action(
                    round_num=0, agent_id=post.actor_id,
                    agent_name=(agent_names or {}).get(post.actor_id,
                                                    f"Agent_{post.actor_id}"),
                    action_type="CREATE_POST", action_args=args,
                    result=json.dumps(response), success=True)
            if transferring:
                bridge.completed = True
            successful += 1
            cursor = trace_id
        if platform == "twitter":
            env.platform.sandbox_clock.time_step += 1
        return NativeSeedResult(successful, native_trace_cursor(env))
    except asyncio.CancelledError:
        raise
    except NativeSeedError:
        raise
    except Exception:
        raise NativeSeedError("native_seed_outcome_uncertain") from None

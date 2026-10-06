"""Execute admitted seeds through the owned native agent/platform API.

No SDK import or SQLite connection creation occurs here. The caller supplies
its reset environment; evidence reads use that platform's existing connection.
"""
import asyncio
import json
from dataclasses import dataclass

from mirofish_execution.native_seed_contracts import validate_native_seed_plan


class NativeSeedError(RuntimeError):
    """Fixed, safe code; native exception/response text stays out of diagnostics."""


@dataclass(frozen=True)
class NativeSeedResult:
    successful_count: int
    trace_cursor: int


def native_trace_cursor(env):
    return env.platform.db.execute(
        "SELECT COALESCE(MAX(rowid), 0) FROM trace").fetchone()[0]


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
        for post, actor in zip(plan, actors):
            before_trace = native_trace_cursor(env)
            before_post = env.platform.db.execute(
                "SELECT COALESCE(MAX(post_id), 0) FROM post").fetchone()[0]
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
            if db.in_transaction:
                raise NativeSeedError("native_seed_outcome_uncertain")
            rows = db.execute(
                "SELECT user_id, content FROM post WHERE post_id = ?",
                (post_id,)).fetchall()
            traces = db.execute(
                "SELECT rowid, user_id, info FROM trace "
                "WHERE rowid > ? AND action = ? ORDER BY rowid",
                (before_trace, "create_post")).fetchall()
            if rows != [(post.actor_id, post.content)] or len(traces) != 1:
                raise NativeSeedError("native_seed_outcome_uncertain")
            trace_id, actor_id, info = traces[0]
            info = json.loads(info)
            if (actor_id != post.actor_id or type(info) is not dict
                    or type(info.get("post_id")) is not int
                    or info["post_id"] != post_id
                    or info.get("content") != post.content):
                raise NativeSeedError("native_seed_outcome_uncertain")
            if action_logger is not None:
                action_logger.log_action(
                    round_num=0, agent_id=post.actor_id,
                    agent_name=(agent_names or {}).get(post.actor_id,
                                                    f"Agent_{post.actor_id}"),
                    action_type="CREATE_POST", action_args={"content": post.content},
                    result=json.dumps(response), success=True)
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

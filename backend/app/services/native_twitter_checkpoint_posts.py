"""Exact post and create-post trace admission for a restored Twitter cut.

The restore boundary calls this after bounding its fixed SQLite tables, before
a restored action can run. It imports neither the restore module nor an engine,
so it keeps the caller's RecordingError and monotonic deadline contract while
staying loadable on the SDK-cold owned child path.
"""
from contextlib import closing
import time

from .native_recording_contracts import RecordingError, strict_json


_POST_QUERY = (
    "SELECT post_id, user_id, content, created_at, original_post_id, quote_content"
    " FROM post ORDER BY post_id LIMIT 4"
)
_TRACE_QUERY = (
    "SELECT rowid, user_id, created_at, info FROM trace WHERE action = 'create_post'"
    " ORDER BY rowid LIMIT 4"
)


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RecordingError()
    return remaining


def check_checkpoint_posts(connection, seed_plan, round_tick, cut_cursor, deadline):
    """Require the validated seeds in order plus one round-one post and traces.

    Posts are read in post_id order and create-post traces in rowid order. The
    counts must be exactly the admitted seeds plus one, so missing, extra,
    reordered or inconsistent rows refuse before a restored action executes.
    """
    _remaining(deadline)
    if not _integer(cut_cursor, 1, 2**63 - 1):
        raise RecordingError()
    with closing(connection.execute(_POST_QUERY)) as cursor:
        posts = cursor.fetchall()
    with closing(connection.execute(_TRACE_QUERY)) as cursor:
        traces = cursor.fetchall()
    _remaining(deadline)
    if len(posts) != len(seed_plan) + 1 or len(traces) != len(posts):
        raise RecordingError()
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

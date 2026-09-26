"""Real installed OASIS Platform primitives against disposable SQLite.

No SocialAgent, model client, env.step, or downloaded recommendation weights.
This file is intentionally outside the lean backend/tests discovery path.
"""

import json

import pytest


@pytest.fixture
def native_platform(tmp_path, monkeypatch):
    # The inherited Platform module opens ./log on import. Confine that
    # incidental output and the database to this test's temporary directory.
    monkeypatch.chdir(tmp_path)
    from oasis.social_platform.platform import Platform

    opened = []

    def create(name, recsys_type):
        platform = Platform(db_path=str(tmp_path / f"{name}.sqlite"), recsys_type=recsys_type, use_openai_embedding=False)
        opened.append(platform)
        return platform

    yield create
    for platform in opened:
        platform.db_cursor.close()
        platform.db.close()


def _rows(platform, query, params=()):
    return platform.db.execute(query, params).fetchall()


def _traces(platform):
    return [(user_id, action, json.loads(info)) for user_id, action, info in _rows(platform, "SELECT user_id, action, info FROM trace")]


async def _signed_up(platform):
    assert (await platform.sign_up(0, ("mira", "Mira Vale", "Synthetic person")))["success"] is True
    assert (await platform.sign_up(1, ("harbor", "Harbor Labs", "Synthetic organization")))["success"] is True
    assert _rows(platform, "SELECT user_id, agent_id FROM user ORDER BY user_id") == [(0, 0), (1, 1)]


@pytest.mark.asyncio
async def test_all_six_twitter_native_actions_and_rows(native_platform):
    platform = native_platform("twitter", "twhin-bert")
    await _signed_up(platform)

    created = await platform.create_post(0, "Mira announces a synthetic report")
    assert created["success"] is True
    post_id = created["post_id"]
    liked = await platform.like_post(1, post_id)
    assert liked["success"] is True
    reposted = await platform.repost(1, post_id)
    assert reposted["success"] is True
    followed = await platform.follow(1, 0)
    assert followed["success"] is True
    abstained = await platform.do_nothing(0)
    assert abstained["success"] is True
    quoted = await platform.quote_post(1, (post_id, "A synthetic response"))
    assert quoted["success"] is True

    assert _rows(platform, "SELECT user_id, content, num_likes, num_shares FROM post WHERE post_id = ?", (post_id,)) == [(0, "Mira announces a synthetic report", 1, 2)]
    assert _rows(platform, "SELECT user_id, original_post_id FROM post WHERE post_id = ?", (reposted["post_id"],)) == [(1, post_id)]
    assert _rows(platform, "SELECT user_id, original_post_id, quote_content FROM post WHERE post_id = ?", (quoted["post_id"],)) == [(1, post_id, "A synthetic response")]
    assert _rows(platform, "SELECT post_id, user_id FROM 'like'") == [(post_id, 1)]
    assert _rows(platform, "SELECT follower_id, followee_id FROM follow") == [(1, 0)]
    assert _rows(platform, "SELECT num_followings FROM user WHERE user_id = 1") == [(1,)]
    assert _rows(platform, "SELECT num_followers FROM user WHERE user_id = 0") == [(1,)]
    traces = _traces(platform)
    configured = {"create_post", "like_post", "repost", "follow", "do_nothing", "quote_post"}
    assert configured <= {action for _, action, _ in traces}
    assert any(action == "quote_post" and info["quoted_id"] == post_id for _, action, info in traces)
    assert any(action == "do_nothing" and info == {} for _, action, info in traces)


@pytest.mark.asyncio
async def test_all_thirteen_reddit_native_actions_and_rows(native_platform):
    platform = native_platform("reddit", "reddit")
    await _signed_up(platform)

    created = await platform.create_post(0, "Synthetic harbor report")
    assert created["success"] is True
    post_id = created["post_id"]
    liked = await platform.like_post(1, post_id)
    disliked = await platform.dislike_post(1, post_id)
    assert liked["success"] is True and disliked["success"] is True
    commented = await platform.create_comment(1, (post_id, "Synthetic comment"))
    assert commented["success"] is True
    comment_id = commented["comment_id"]
    comment_liked = await platform.like_comment(0, comment_id)
    comment_disliked = await platform.dislike_comment(0, comment_id)
    assert comment_liked["success"] is True and comment_disliked["success"] is True

    posts = await platform.search_posts(1, "harbor report")
    users = await platform.search_user(1, "Mira")
    trending = await platform.trend(1)
    assert posts["success"] is True and any(item["post_id"] == post_id for item in posts["posts"])
    assert users["success"] is True and any(item["user_id"] == 0 for item in users["users"])
    assert trending["success"] is True and any(item["post_id"] == post_id for item in trending["posts"])

    # Reddit's installed update is local; Twitter twhin-bert update would
    # require weights and is deliberately outside this primitive fixture.
    await platform.update_rec_table()
    assert _rows(platform, "SELECT user_id, post_id FROM rec ORDER BY user_id") == [(0, post_id), (1, post_id)]
    refreshed = await platform.refresh(1)
    abstained = await platform.do_nothing(1)
    followed = await platform.follow(1, 0)
    muted = await platform.mute(1, 0)
    assert refreshed["success"] is True and any(item["post_id"] == post_id for item in refreshed["posts"])
    assert abstained["success"] is True
    assert followed["success"] is True and muted["success"] is True

    assert _rows(platform, "SELECT num_likes, num_dislikes FROM post WHERE post_id = ?", (post_id,)) == [(1, 1)]
    assert _rows(platform, "SELECT post_id, user_id, content, num_likes, num_dislikes FROM comment WHERE comment_id = ?", (comment_id,)) == [(post_id, 1, "Synthetic comment", 1, 1)]
    assert _rows(platform, "SELECT comment_id, user_id FROM comment_like") == [(comment_id, 0)]
    assert _rows(platform, "SELECT comment_id, user_id FROM comment_dislike") == [(comment_id, 0)]
    assert _rows(platform, "SELECT follower_id, followee_id FROM follow") == [(1, 0)]
    assert _rows(platform, "SELECT muter_id, mutee_id FROM mute") == [(1, 0)]
    configured = {"like_post", "dislike_post", "create_post", "create_comment", "like_comment", "dislike_comment", "search_posts", "search_user", "trend", "refresh", "do_nothing", "follow", "mute"}
    assert configured <= {action for _, action, _ in _traces(platform)}


@pytest.mark.asyncio
async def test_native_rejects_only_observed_invalid_targets(native_platform):
    platform = native_platform("invalid-targets", "twhin-bert")
    await _signed_up(platform)
    assert (await platform.repost(1, 999999)) == {"success": False, "error": "Post not found."}
    assert (await platform.quote_post(1, (999999, "Missing"))) == {"success": False, "error": "Post not found."}
    invalid_like = await platform.like_post(1, 999999)
    assert invalid_like["success"] is False
    assert _rows(platform, "SELECT COUNT(*) FROM post") == [(0,)]
    assert _rows(platform, "SELECT COUNT(*) FROM 'like'") == [(0,)]
    duplicate_signup = await platform.sign_up(0, ("duplicate", "Duplicate", "Synthetic"))
    assert duplicate_signup["success"] is False
    assert _rows(platform, "SELECT COUNT(*) FROM user") == [(2,)]

"""Import-safe native engine and legacy CLI setup for simulation scripts."""

import os
import sys


class ActionTypeNames:
    """Names used for source catalogs until the native enum is loaded."""

    CREATE_POST = "create_post"
    LIKE_POST = "like_post"
    REPOST = "repost"
    FOLLOW = "follow"
    DO_NOTHING = "do_nothing"
    QUOTE_POST = "quote_post"
    DISLIKE_POST = "dislike_post"
    CREATE_COMMENT = "create_comment"
    LIKE_COMMENT = "like_comment"
    DISLIKE_COMMENT = "dislike_comment"
    SEARCH_POSTS = "search_posts"
    SEARCH_USER = "search_user"
    TREND = "trend"
    REFRESH = "refresh"
    MUTE = "mute"
    INTERVIEW = "interview"


def load_native():
    """Load OASIS/CAMEL only at native execution time."""
    from camel.models import ModelFactory
    from camel.types import ModelPlatformType
    import oasis
    from oasis import (ActionType, LLMAction, ManualAction,
                       generate_twitter_agent_graph, generate_reddit_agent_graph)
    return (ModelFactory, ModelPlatformType, oasis, ActionType, LLMAction,
            ManualAction, generate_twitter_agent_graph, generate_reddit_agent_graph)


async def close_environment(env):
    """Close a native environment even when reset failed before task startup."""
    if env is None:
        return
    if hasattr(env, 'platform_task'):
        await env.close()
    else:
        env.platform.db_cursor.close()
        env.platform.db.close()


def setup_legacy_cli(project_root, backend_dir, *, windows_utf8=False):
    """Retain legacy direct invocation behavior without changing import state."""
    if windows_utf8 and sys.platform == 'win32':
        os.environ.setdefault('PYTHONUTF8', '1')
        os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, 'reconfigure'):
                stream.reconfigure(encoding='utf-8', errors='replace')
        import builtins
        original_open = builtins.open

        def utf8_open(file, mode='r', buffering=-1, encoding=None, errors=None,
                      newline=None, closefd=True, opener=None):
            if encoding is None and 'b' not in mode:
                encoding = 'utf-8'
            return original_open(file, mode, buffering, encoding, errors,
                                 newline, closefd, opener)
        builtins.open = utf8_open

    from dotenv import load_dotenv
    for path in (os.path.join(project_root, '.env'),
                 os.path.join(backend_dir, '.env')):
        if os.path.isfile(path):
            load_dotenv(path)
            break

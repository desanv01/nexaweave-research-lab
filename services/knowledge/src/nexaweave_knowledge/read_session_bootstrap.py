"""Installed private read-session entry point, with a hard child lifetime."""
from __future__ import annotations

import asyncio
import os
import sys
import threading

def main() -> int:
    # asyncio executor shutdown can wait for a blocked pipe operation. The
    # parent owns the entire process tree; this independent watchdog ensures
    # even a stalled stream cannot keep this private child alive beyond 120.
    # Start before installed runtime imports as well as settings/pipe work.
    watchdog = threading.Timer(120, lambda: os._exit(1))
    watchdog.daemon = True
    watchdog.start()
    try:
        from nexaweave_knowledge.read_session import serve_read_session
        from nexaweave_knowledge.read_runtime import ReadSettings, dispatcher
        settings = ReadSettings.from_environment()
        asyncio.run(serve_read_session(dispatcher(settings), principal=settings.principal,
            scope=settings.scope, input_stream=sys.stdin.buffer, output_stream=sys.stdout.buffer))
        return 0
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        print("knowledge read session unavailable", file=sys.stderr)
        return 1
    finally:
        watchdog.cancel()


if __name__ == "__main__":
    raise SystemExit(main())

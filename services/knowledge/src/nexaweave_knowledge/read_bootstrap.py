"""Fixed installed one-shot read bootstrap for the isolated knowledge interpreter."""

from __future__ import annotations

import asyncio
import sys

from nexaweave_knowledge.read_runtime import ReadSettings, dispatcher
from nexaweave_knowledge.stdio import serve_once


def main() -> int:
    try:
        settings = ReadSettings.from_environment()
        asyncio.run(serve_once(dispatcher(settings), principal=settings.principal,
                               input_stream=sys.stdin.buffer, output_stream=sys.stdout.buffer))
        return 0
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        print("knowledge read unavailable", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

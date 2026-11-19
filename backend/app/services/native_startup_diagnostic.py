"""Bounded in-memory evidence for failures during native platform startup."""
import asyncio
import time
from typing import NamedTuple


class StartupFailure(NamedTuple):
    platform: str
    elapsed_ms: int
    remaining_ms: int
    failure_class: str
    deadline_elapsed: bool


def _bounded_ms(seconds):
    return int(min(600, max(0, seconds)) * 1000)


async def observe_startup(session, operation, platform, *args, **kwargs):
    started = time.monotonic()
    try:
        return await operation(*args, **kwargs)
    except BaseException as error:
        try:
            if platform in ("twitter", "reddit"):
                now = time.monotonic()
                deadline = session._start_deadline
                if isinstance(error, asyncio.CancelledError):
                    failure = "cancelled"
                elif isinstance(error, (KeyboardInterrupt, SystemExit)):
                    failure = "interrupted"
                else:
                    failure = "startup_exception"
                # A caller retaining the session can inspect this after start unwinds.
                session.startup_failure = StartupFailure(
                    platform, _bounded_ms(now - started), _bounded_ms(deadline - now),
                    failure, now >= deadline,
                )
        except BaseException:
            # Capture must leave the original exception in control.
            pass
        raise

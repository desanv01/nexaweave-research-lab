"""Backend-facing name for the trusted, spawn-safe native session binding.

The binding class lives in the standard-library-only execution package so
spawn can unpickle it before child environment scrubbing without importing the
backend application. Its create_session method imports NativeSimulationSession
only after the child has scrubbed inherited provider settings.
"""

from nexaweave_execution.native_owned_binding import (NativeOwnedSessionFactory,
    _manifest)

__all__ = ["NativeOwnedSessionFactory"]

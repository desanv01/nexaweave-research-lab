"""Explicit trusted Temporal host for PostgreSQL-owned prepared native runs.

This module has no eager Temporal, PostgreSQL, backend app, or native SDK import.
Only explicit construction loads the accepted host seams. Wire requests remain
identifier-only; all prepared paths and model factories come from trusted
bindings supplied by the caller.
"""
from __future__ import annotations

import asyncio
from types import MappingProxyType
from typing import Mapping


class TemporalPreparedHost:
    """Bind an immutable set of exact requests to trusted prepared factories.

    Each activity gets a fresh NativePreparedHost supervisor. Durable store
    registration precedes binding selection, artifact inspection, or launch.
    The accepted Temporal activity owns one-shot admission and cleanup.
    """

    def __init__(self, *, client, task_queue: str, trusted_principal: str,
                 connection_factory, bindings: Mapping,
                 allow_dispatch=None, native_dispatch_allowed=None,
                 native_options: Mapping[str, object] | None = None,
                 max_retained: int = 32, max_concurrent_activities: int = 4,
                 shutdown_grace_seconds: int = 30):
        from mirofish_execution.native_owned_binding import NativeOwnedSessionFactory
        from mirofish_execution.native_run_contracts import (
            InvalidNativeRun, NativeRunDenied, NativeRunRequest, principal_id)
        from mirofish_execution.native_run_store import NativeRunStore
        from mirofish_execution.temporal_native_host import TemporalNativeHost
        from .native_prepared_host import NativePreparedHost

        principal = principal_id(trusted_principal)
        if (not callable(connection_factory) or not isinstance(bindings, Mapping)
                or (allow_dispatch is not None and not callable(allow_dispatch))
                or (native_dispatch_allowed is not None
                    and not callable(native_dispatch_allowed))):
            raise InvalidNativeRun()
        prepared = {}
        for key, factory in bindings.items():
            request = NativeRunRequest.from_wire(key)
            if (request.principal != principal
                    or type(factory) is not NativeOwnedSessionFactory
                    or factory.principal != principal
                    or factory.project_id != str(request.project_id)
                    or factory.project_revision != request.project_revision
                    or factory.simulation_id != request.simulation_id
                    or factory.platforms != request.platforms
                    or factory.seed != request.seed
                    or factory.max_rounds != request.max_rounds
                    or factory.runtime_sha256 != request.runtime_sha256
                    or not callable(factory.model_factory)
                    or request in prepared):
                raise NativeRunDenied()
            prepared[request] = factory
        if not isinstance(native_options, (Mapping, type(None))):
            raise InvalidNativeRun()
        options = {} if native_options is None else dict(native_options)
        allowed_options = frozenset({"lease_seconds", "poll_seconds",
            "call_budget_seconds", "handshake_seconds", "observe_seconds",
            "grace_seconds", "join_seconds", "go_timeout_seconds"})
        if set(options) - allowed_options:
            raise InvalidNativeRun()

        self.principal = principal
        self.bindings = MappingProxyType(prepared)
        self.native_options = MappingProxyType(options)
        self.store = NativeRunStore(connection_factory)
        self._connection_factory = connection_factory
        self._native_dispatch_allowed = native_dispatch_allowed

        def supervisor_factory(value):
            request = NativeRunRequest.from_wire(value)
            if request.principal != principal:
                raise NativeRunDenied()
            # PostgreSQL checks principal and exact project revision here. A
            # foreign/missing revision never reaches a binder or artifact read.
            self.store.register(request)
            factory = self._bind(request)
            host = NativePreparedHost(principal=principal, request=request,
                session_factory=factory, connection_factory=connection_factory,
                dispatch_allowed=native_dispatch_allowed, **options)
            return host.supervisor

        self._temporal = TemporalNativeHost(client=client, task_queue=task_queue,
            trusted_principal=principal, supervisor_factory=supervisor_factory,
            allow_dispatch=allow_dispatch, max_retained=max_retained,
            max_concurrent_activities=max_concurrent_activities,
            shutdown_grace_seconds=shutdown_grace_seconds)

    def _bind(self, request):
        from mirofish_execution.native_run_contracts import NativeRunDenied
        try:
            return self.bindings[request]
        except KeyError:
            raise NativeRunDenied() from None

    def worker(self):
        return self._temporal.worker()

    async def start(self, value):
        return await self._temporal.start(value)

    async def status(self, value, ref):
        return await self._temporal.status(value, ref)

    async def result(self, value, ref):
        return await self._temporal.result(value, ref)

    async def cancel(self, value, ref):
        from mirofish_execution.native_run_contracts import NativeRunError
        from mirofish_execution.temporal_native_host import NativeTemporalHostError

        # The accepted host validates principal and exact workflow identity
        # without remote or filesystem I/O. The immutable host table then
        # requires this exact request to have a trusted binding.
        request, _ = self._temporal._handle(value, ref)
        if request not in self.bindings:
            raise NativeTemporalHostError("native_run_denied")

        def persist_intent():
            self.store.register(request)
            self.store.request_cancel(self.principal, request.run_id)

        try:
            # Accepted store transactions/connection settings bound DB work.
            # Do not hold the activity registry lock or inspect artifacts here.
            await asyncio.wait_for(asyncio.to_thread(persist_intent), 15)
        except asyncio.CancelledError:
            raise
        except NativeRunError as error:
            raise NativeTemporalHostError(error.code) from None
        except Exception:
            raise NativeTemporalHostError("native_run_unavailable") from None
        return await self._temporal.cancel(request, ref)

    async def local_status(self, value):
        return await self._temporal.local_status(value)

    async def retry_cleanup(self, value):
        return await self._temporal.retry_cleanup(value)

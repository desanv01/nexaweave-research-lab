"""Explicit trusted host for one PostgreSQL-owned prepared native run.

Importing this module does not import the native engine or PostgreSQL driver.
The caller supplies an already trusted prepared binding and connection factory;
identifier-only request data cannot select paths, models, or credentials.
"""
from __future__ import annotations

import math
from typing import Callable


class NativePreparedHost:
    """One request, driver and supervisor for the lifetime of this object.

    The accepted store authorizes the exact project revision in register(),
    before the driver can inspect prepared files or spawn a native child. The
    supervisor spends start() once even if its caller times out.
    """

    def __init__(self, *, principal: str, request, session_factory,
                 connection_factory: Callable, dispatch_allowed: Callable | None = None,
                 lease_seconds: int = 60, poll_seconds: float = 1.0,
                 call_budget_seconds: float = 20.0,
                 handshake_seconds: float = 10.0, observe_seconds: float = 0.05,
                 grace_seconds: float = 1.0, join_seconds: float = 2.0,
                 go_timeout_seconds: float = 20.0):
        # These imports are reached only by an explicit trusted host caller.
        from nexaweave_execution.native_owned_binding import NativeOwnedSessionFactory
        from nexaweave_execution.native_process_driver import NativeProcessDriver
        from nexaweave_execution.native_run_contracts import (
            InvalidNativeRun, NativeRunDenied, NativeRunRequest, principal_id)
        from nexaweave_execution.native_run_coordinator import NativeRunCoordinator
        from nexaweave_execution.native_run_store import NativeRunStore
        from nexaweave_execution.native_run_supervisor import NativeRunSupervisor

        bound_principal = principal_id(principal)
        bound_request = NativeRunRequest.from_wire(request)
        if bound_request.principal != bound_principal:
            raise NativeRunDenied()
        if (type(session_factory) is not NativeOwnedSessionFactory
                or not callable(connection_factory)
                or (dispatch_allowed is not None and not callable(dispatch_allowed))):
            raise InvalidNativeRun()
        # Check only scalar binding fields here. In particular, do not call
        # validate(): it reads artifacts before store authorization.
        if (session_factory.principal != bound_principal
                or session_factory.project_id != str(bound_request.project_id)
                or session_factory.project_revision != bound_request.project_revision
                or session_factory.simulation_id != bound_request.simulation_id
                or session_factory.platforms != bound_request.platforms
                or session_factory.seed != bound_request.seed
                or session_factory.max_rounds != bound_request.max_rounds):
            raise NativeRunDenied()

        driver_bounds = (handshake_seconds, observe_seconds, grace_seconds,
                         join_seconds, go_timeout_seconds)
        if (any(type(value) not in (int, float) or not math.isfinite(value)
                or not 0 < value <= 30 for value in driver_bounds)
                or type(call_budget_seconds) not in (int, float)
                or not math.isfinite(call_budget_seconds)
                or not 0 < call_budget_seconds <= 30
                or call_budget_seconds < max(
                    handshake_seconds + 3 * join_seconds,
                    observe_seconds + 3 * join_seconds,
                    grace_seconds + 2 * join_seconds)):
            raise InvalidNativeRun()

        driver = NativeProcessDriver(session_factory,
            handshake_seconds=handshake_seconds, observe_seconds=observe_seconds,
            grace_seconds=grace_seconds, join_seconds=join_seconds,
            go_timeout_seconds=go_timeout_seconds)
        try:
            store = NativeRunStore(connection_factory)
            coordinator = NativeRunCoordinator(bound_principal, store, driver,
                dispatch_allowed=dispatch_allowed, lease_seconds=lease_seconds)
            supervisor = NativeRunSupervisor(bound_request, coordinator,
                poll_seconds=poll_seconds, call_budget_seconds=call_budget_seconds)
        except BaseException:
            # Construction has not launched or claimed anything. Actual start
            # failures occur on a returned host whose owner retains cleanup.
            driver.close()
            raise
        self.request = bound_request
        self.driver = driver
        self.store = store
        self.supervisor = supervisor

    def start(self, wait_seconds: float = 5.0):
        return self.supervisor.start(wait_seconds)

    def status(self):
        return self.supervisor.status()

    def wait(self, wait_seconds: float = 30.0):
        return self.supervisor.wait(wait_seconds)

    def request_cancel(self):
        return self.supervisor.request_cancel()

    def close(self, wait_seconds: float = 5.0) -> bool:
        return self.supervisor.close(wait_seconds)

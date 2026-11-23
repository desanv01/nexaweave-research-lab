"""Explicit trusted host for one PostgreSQL-owned prepared native run.

Importing this module does not import the native engine or PostgreSQL driver.
The caller supplies an already trusted prepared binding and connection factory;
identifier-only request data cannot select paths, models, or credentials.
"""
from __future__ import annotations

from dataclasses import replace
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
            InvalidNativeRun, NativeRunDenied, NativeRunRequest, canonical_uuid, principal_id, sha256)
        from nexaweave_execution.native_paired_checkpoint_binding import NativePairedCheckpointFactory
        from nexaweave_execution.native_reddit_checkpoint_binding import NativeRedditCheckpointFactory
        from nexaweave_execution.native_run_coordinator import NativeRunCoordinator
        from nexaweave_execution.native_run_store import NativeRunStore, PairedCheckpointMode, RedditCheckpointMode
        from nexaweave_execution.native_run_supervisor import NativeRunSupervisor
        from nexaweave_execution.native_twitter_checkpoint_binding import (
            NativeRestoredTwitterFactory, NativeTwitterCaptureFactory)

        bound_principal = principal_id(principal)
        bound_request = NativeRunRequest.from_wire(request)
        if bound_request.principal != bound_principal:
            raise NativeRunDenied()
        if (not callable(connection_factory)
                or (dispatch_allowed is not None and not callable(dispatch_allowed))):
            raise InvalidNativeRun()
        factory_type = type(session_factory)
        version = bound_request.schema_version
        reddit_mode = paired_mode = None
        if version == 1 and factory_type is NativeOwnedSessionFactory:
            prepared = session_factory
        elif ((version == 1 and factory_type is NativeTwitterCaptureFactory)
              or (version in (2, 3) and factory_type is NativeRestoredTwitterFactory)):
            prepared = session_factory.prepared
            if (type(prepared) is not NativeOwnedSessionFactory
                    or bound_request.platforms != ("twitter",) or bound_request.max_rounds != 1):
                raise InvalidNativeRun()
        elif factory_type is NativeRedditCheckpointFactory:
            try:
                session_factory.check_request(bound_request)
            except ValueError:
                raise InvalidNativeRun() from None
            reddit_mode = RedditCheckpointMode.CAPTURE if version == 1 else RedditCheckpointMode.CONTINUE
            prepared = session_factory.prepared
        elif factory_type is NativePairedCheckpointFactory:
            session_factory.check_request(bound_request)
            paired_mode = PairedCheckpointMode.CAPTURE if version == 1 else PairedCheckpointMode.CONTINUE
            prepared = session_factory.prepared
        else:
            raise InvalidNativeRun()
        # Check only scalar binding fields here. In particular, do not call
        # validate(): it reads artifacts before store authorization.
        if (prepared.principal != bound_principal
                or prepared.project_id != str(bound_request.project_id)
                or prepared.project_revision != bound_request.project_revision
                or prepared.simulation_id != bound_request.simulation_id
                or prepared.platforms != bound_request.platforms
                or prepared.seed != bound_request.seed
                or prepared.max_rounds != bound_request.max_rounds
                or (factory_type is not NativeOwnedSessionFactory
                    and prepared.runtime_sha256 != bound_request.runtime_sha256)):
            raise NativeRunDenied()
        if version in (2, 3) and (
                canonical_uuid(session_factory.expected_producer_run) != bound_request.source_run_id
                or sha256(session_factory.expected_lineage_sha256) != bound_request.checkpoint_sha256):
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

        store = NativeRunStore(connection_factory)
        if reddit_mode is RedditCheckpointMode.CONTINUE or paired_mode is PairedCheckpointMode.CONTINUE:
            stored = store.get(bound_principal, bound_request.source_run_id)
            producer = session_factory.producer_record
            # Completed cancellation can update these two metadata fields
            # without changing the settled producer or its receipt.
            if replace(stored, cancel_requested=producer.cancel_requested,
                       updated_at=producer.updated_at) != producer:
                raise NativeRunDenied()

        driver = NativeProcessDriver(session_factory,
            handshake_seconds=handshake_seconds, observe_seconds=observe_seconds,
            grace_seconds=grace_seconds, join_seconds=join_seconds,
            go_timeout_seconds=go_timeout_seconds)
        try:
            coordinator = NativeRunCoordinator(bound_principal, store, driver,
                dispatch_allowed=dispatch_allowed, lease_seconds=lease_seconds,
                reddit_mode=reddit_mode, paired_mode=paired_mode)
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

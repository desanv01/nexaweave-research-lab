"""Trusted synchronous seam between durable ownership and a native-run driver.

The driver is injected by a trusted host. This module never selects an executable,
reads an artifact path, constructs a provider, or adopts an existing process.
"""
from __future__ import annotations

from typing import Callable, Protocol
from uuid import UUID, uuid4

from .native_run_contracts import (InvalidNativeRun, NativeChildIdentity, NativeRunBusy,
    NativeRunConflict, NativeRunDenied, NativeRunReceipt, NativeRunRequest,
    NativeRunUncertain, RunState, canonical_uuid, principal_id, NativeObservation)
from .native_run_store import NativeRunRecord, NativeRunStore


class TrustedNativeDriver(Protocol):
    def launch(self, request: NativeRunRequest, attempt_id: UUID) -> NativeChildIdentity:
        """Start exactly once under the host's existing prepared-start marker."""

    def observe(self, request: NativeRunRequest, attempt_id: UUID,
                child: NativeChildIdentity) -> NativeObservation:
        """Report only an observed state of this exact owned child."""

    def cancel(self, request: NativeRunRequest, attempt_id: UUID,
               child: NativeChildIdentity) -> NativeObservation:
        """Request cancellation of this owned child; return observed state."""


class NativeRunCoordinator:
    def __init__(self, principal: str, store: NativeRunStore, driver: TrustedNativeDriver,
                 *, owner_id: UUID | None = None,
                 dispatch_allowed: Callable[[NativeRunRequest], bool] | None = None,
                 lease_seconds: int = 30):
        self.principal = principal_id(principal)
        if not isinstance(store, NativeRunStore) or driver is None:
            raise InvalidNativeRun()
        if type(lease_seconds) is not int or not 5 <= lease_seconds <= 300:
            raise InvalidNativeRun()
        self.owner_id = uuid4() if owner_id is None else canonical_uuid(owner_id)
        self.store = store
        self.driver = driver
        self.dispatch_allowed = dispatch_allowed
        self.lease_seconds = lease_seconds

    def _fence(self, record: NativeRunRecord) -> None:
        if record.attempt_id is not None and record.owner_id == self.owner_id:
            try:
                self.store.mark_uncertain(self.principal, record.request.run_id,
                                          record.attempt_id, self.owner_id)
            except Exception:
                # The original lease still fences the attempt if acknowledgement
                # of this best-effort transition is unavailable.
                pass

    def start(self, request: NativeRunRequest) -> NativeRunRecord:
        request = NativeRunRequest.from_wire(request)
        if request.principal != self.principal:
            raise NativeRunDenied()
        record = self.store.register(request)
        if record.state in (RunState.completed, RunState.failed, RunState.cancelled):
            return record  # Durable result recovery never invokes the driver.
        if record.state == RunState.uncertain:
            raise NativeRunUncertain()
        if record.state != RunState.declared or record.cancel_requested:
            raise NativeRunBusy()
        try:
            allowed = self.dispatch_allowed is not None and self.dispatch_allowed(request) is True
        except Exception:
            allowed = False
        if not allowed:
            raise NativeRunDenied()
        claim = self.store.claim_start(self.principal, request.run_id, self.owner_id,
                                       self.lease_seconds)
        # A cancellation request may race with the claim. The launch permission
        # stays spent even if we can avoid calling the external driver.
        try:
            prelaunch = self.store.reconcile_expired(self.principal, request.run_id)
            if (prelaunch.state != RunState.starting
                    or prelaunch.attempt_id != claim.attempt_id
                    or prelaunch.owner_id != self.owner_id):
                self._fence(claim)
                raise NativeRunUncertain()
            if prelaunch.cancel_requested:
                self._fence(claim)
                raise NativeRunUncertain()
        except NativeRunUncertain:
            raise
        except Exception:
            self._fence(claim)
            raise NativeRunUncertain() from None
        try:
            child = NativeChildIdentity.from_wire(self.driver.launch(request, claim.attempt_id))
            attached = self.store.attach(self.principal, request.run_id, claim.attempt_id,
                                         self.owner_id, child, self.lease_seconds)
            current = self.store.reconcile_expired(self.principal, request.run_id)
            if (current.state != RunState.running or current.attempt_id != claim.attempt_id
                    or current.owner_id != self.owner_id or current.child != attached.child):
                raise NativeRunUncertain()
            if current.cancel_requested:
                return self._observe(current, cancel=True)
            return current
        except Exception:
            self._fence(claim)
            raise NativeRunUncertain() from None

    def status(self, run_id: UUID) -> NativeRunRecord:
        return self.store.reconcile_expired(self.principal, run_id)

    def _observe(self, record: NativeRunRecord, *, cancel: bool) -> NativeRunRecord:
        if (record.state != RunState.running or record.child is None
                or record.attempt_id is None or record.owner_id != self.owner_id):
            raise NativeRunBusy()
        try:
            call = self.driver.cancel if cancel else self.driver.observe
            observed = NativeObservation.validated(
                call(record.request, record.attempt_id, record.child))
            if observed.status in ("unknown", "absent"):
                self._fence(record)
                raise NativeRunUncertain()
            if observed.status == "running":
                return self.store.heartbeat(self.principal, record.request.run_id,
                                            record.attempt_id, self.owner_id,
                                            self.lease_seconds)
            return self.store.settle(self.principal, record.request.run_id,
                                     record.attempt_id, self.owner_id, observed.receipt)
        except (NativeRunBusy, NativeRunConflict):
            raise
        except Exception:
            self._fence(record)
            raise NativeRunUncertain() from None

    def observe(self, run_id: UUID) -> NativeRunRecord:
        record = self.status(run_id)
        if record.state in (RunState.completed, RunState.failed, RunState.cancelled):
            return record
        if record.state == RunState.uncertain:
            raise NativeRunUncertain()
        return self._observe(record, cancel=record.cancel_requested)

    def request_cancel(self, run_id: UUID) -> NativeRunRecord:
        record = self.store.request_cancel(self.principal, run_id)
        if record.state in (RunState.completed, RunState.failed, RunState.cancelled,
                            RunState.declared, RunState.uncertain):
            return record
        if record.state == RunState.starting or record.owner_id != self.owner_id:
            # Intent is durable; the active owner must observe and act on it.
            return record
        record = self.status(run_id)
        if record.state == RunState.uncertain:
            raise NativeRunUncertain()
        if record.state in (RunState.completed, RunState.failed, RunState.cancelled):
            return record
        return self._observe(record, cancel=True)

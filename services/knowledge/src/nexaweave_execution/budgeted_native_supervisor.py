"""Retain accepted native owner lifecycle; settle only PG terminal authority."""
import threading
from .native_run_supervisor import NativeRunSupervisor
from .native_run_contracts import NativeRunUncertain, RunState
from .budget import ReservationState


class BudgetedNativeSupervisor(NativeRunSupervisor):
    def __init__(self, request, coordinator, *, ledger, account_id, scope,
                 budget_attempt_id, launch_sha256, **kwargs):
        super().__init__(request, coordinator, **kwargs)
        self.ledger, self.account_id, self.scope = ledger, account_id, scope
        self.budget_attempt_id, self.launch_sha256 = budget_attempt_id, launch_sha256
        self._budget_lock = threading.Lock()

    def reconcile_budget(self):
        with self._budget_lock:
            record = self.coordinator.store.reconcile_expired(self.request.principal, self.request.run_id)
            if record.request != self.request:
                raise NativeRunUncertain()
            if record.receipt is not None and record.state in (RunState.completed,RunState.failed,RunState.cancelled):
                self.ledger.settle_native(self.request.principal,self.account_id,self.scope,self.request,
                    self.budget_attempt_id,self.launch_sha256,record.receipt)
            elif record.state == RunState.uncertain:
                try:
                    self.ledger.mark_uncertain(self.request.principal,self.account_id,self.request.run_id,
                                               self.budget_attempt_id,'dispatch_uncertain')
                except Exception:
                    # Unavailable acknowledgement never releases reserved money.
                    pass
            return record

    def _run(self):
        try:
            super()._run()
        finally:
            self.reconcile_budget()

    def status(self):
        result = super().status()
        if self._start_spent:
            self.reconcile_budget()
        return result

    def wait(self, wait_seconds=30.0):
        result = super().wait(wait_seconds)
        self.reconcile_budget()
        return result

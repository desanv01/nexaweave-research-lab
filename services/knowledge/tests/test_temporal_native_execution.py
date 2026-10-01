"""Pure Temporal native contracts and retained admission tests; no services."""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from mirofish_execution.native_run_contracts import (InvalidNativeRun, NativeRunReceipt,
    NativeRunRequest, RunState)
from mirofish_execution.native_run_supervisor import (NativeRunSupervisor,
    SupervisorStatus, SupervisorWaitTimeout)
from mirofish_execution.temporal_native_activities import (NativeActivityError,
    NativeExecutionActivities)
from mirofish_execution.temporal_native_contracts import (native_workflow_id,
    qualified_receipt)
from mirofish_execution.temporal_native_workflow import (ACTIVITY_NAME, HEARTBEAT_TIMEOUT,
    SCHEDULE_TO_CLOSE, START_TO_CLOSE, NativeExecutionWorkflow)


def request():
    return NativeRunRequest.from_wire(dict(schema_version=1, principal="owner",
        project_id=uuid4(), project_revision=1, simulation_id="sim_1",
        run_id=uuid4(), artifact_sha256="a" * 64, runtime_sha256="b" * 64,
        platforms=("twitter",), seed=7, max_rounds=1))


class FakeSupervisor(NativeRunSupervisor):
    """Activity-only fake. Real owner semantics are in the guarded PG suite."""

    def __init__(self, req, *, mode="terminal", fail_close=False):
        self.request = req
        self.coordinator = SimpleNamespace(principal=req.principal)
        self.mode = mode
        self.fail_close = fail_close
        self.starts = 0
        self.waits = 0
        self.closes = 0
        self.cancels = 0
        self.receipt = NativeRunReceipt(req.run_id, uuid4(), uuid4(),
                                        req.fingerprint, "completed", "e" * 64)

    def _status(self):
        return SupervisorStatus("completed", RunState.completed, self.cancels > 0,
            self.fail_close and self.closes > 0, False, self.receipt, None)

    def start(self, wait_seconds=5):
        self.starts += 1
        if self.mode == "timeout":
            raise SupervisorWaitTimeout()
        if self.mode == "secret_failure":
            raise RuntimeError("SENTINEL_PRIVATE_NATIVE_DETAIL")
        return self._status()

    def wait(self, wait_seconds=5):
        self.waits += 1
        return self._status()

    def status(self):
        return self._status()

    def request_cancel(self):
        self.cancels += 1
        return self._status()

    def close(self, wait_seconds=5):
        self.closes += 1
        if self.fail_close and self.closes == 1:
            return False
        self.fail_close = False
        return True


def test_pure_workflow_identity_and_receipt_binding():
    req = request()
    assert native_workflow_id(req) == native_workflow_id(req.to_wire())
    assert str(req.run_id).replace("-", "") in native_workflow_id(req)
    receipt = NativeRunReceipt(req.run_id, uuid4(), uuid4(), req.fingerprint,
                               "cancelled", "d" * 64)
    assert qualified_receipt(receipt.to_wire(), req) == receipt
    with pytest.raises(InvalidNativeRun):
        qualified_receipt({**receipt.to_wire(), "request_fingerprint": "f" * 64}, req)
    with pytest.raises(InvalidNativeRun):
        qualified_receipt({**receipt.to_wire(), "run_id": str(uuid4())}, req)


@pytest.mark.asyncio
async def test_workflow_single_attempt_timeouts_and_terminal_validation(monkeypatch):
    from mirofish_execution import temporal_native_workflow as module
    req = request()
    captured = {}
    async def execute(name, wire, **options):
        assert name == ACTIVITY_NAME and wire == req.to_wire()
        captured.update(options)
        return NativeRunReceipt(req.run_id, uuid4(), uuid4(), req.fingerprint,
                                "completed", "e" * 64).to_wire()
    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    flow = NativeExecutionWorkflow()
    assert (await flow.run(req.to_wire()))["request_fingerprint"] == req.fingerprint
    assert flow.stage() == "completed"
    assert captured["retry_policy"].maximum_attempts == 1
    assert captured["start_to_close_timeout"] == START_TO_CLOSE
    assert captured["schedule_to_close_timeout"] == SCHEDULE_TO_CLOSE
    assert captured["heartbeat_timeout"] == HEARTBEAT_TIMEOUT
    with pytest.raises(ApplicationError) as invalid:
        await NativeExecutionWorkflow().run({**req.to_wire(), "path": "private"})
    assert invalid.value.type == "invalid_request" and invalid.value.non_retryable
    async def wrong_result(*args, **kwargs):
        return {"private": "SENTINEL_PRIVATE_NATIVE_DETAIL"}
    monkeypatch.setattr(module.workflow, "execute_activity", wrong_result)
    with pytest.raises(ApplicationError) as malformed:
        await NativeExecutionWorkflow().run(req.to_wire())
    assert malformed.value.type == "invalid_receipt" and malformed.value.non_retryable
    assert "SENTINEL_PRIVATE_NATIVE_DETAIL" not in str(malformed.value)


@pytest.mark.asyncio
async def test_activity_default_off_foreign_principal_and_factory_failure():
    req = request()
    calls = []
    def construct(value):
        calls.append(value)
        raise RuntimeError("SENTINEL_PRIVATE_NATIVE_DETAIL")
    disabled = NativeExecutionActivities(trusted_principal="owner", supervisor_factory=construct)
    environment = ActivityEnvironment()
    with pytest.raises(ApplicationError) as denied:
        await environment.run(disabled.run_native, req.to_wire())
    assert denied.value.type == "dispatch_disabled" and calls == []
    enabled = NativeExecutionActivities(trusted_principal="owner",
        supervisor_factory=construct, allow_dispatch=lambda: True)
    with pytest.raises(ApplicationError) as foreign:
        await environment.run(enabled.run_native, {**req.to_wire(), "principal": "other"})
    assert foreign.value.type == "native_run_denied" and calls == []
    with pytest.raises(ApplicationError) as failed:
        await environment.run(enabled.run_native, req.to_wire())
    assert failed.value.type == "native_execution_failed"
    assert "SENTINEL_PRIVATE_NATIVE_DETAIL" not in str(failed.value)
    with pytest.raises(ApplicationError) as duplicate:
        await environment.run(enabled.run_native, req.to_wire())
    assert duplicate.value.type == "native_run_busy" and len(calls) == 1


def test_registry_atomic_one_shot_and_changed_request_conflict():
    req = request()
    adapter = NativeExecutionActivities(trusted_principal="owner",
        supervisor_factory=lambda value: FakeSupervisor(value), allow_dispatch=lambda: True)
    gate = threading.Barrier(2)
    def admit():
        gate.wait(timeout=5)
        try:
            return adapter._admit(req)
        except NativeActivityError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: admit(), range(2)))
    assert sum(not isinstance(value, str) for value in results) == 1
    assert results.count("native_run_busy") == 1
    changed = NativeRunRequest.from_wire({**req.to_wire(), "runtime_sha256": "f" * 64})
    with pytest.raises(NativeActivityError, match="native_run_conflict"):
        adapter._admit(changed)
    limited = NativeExecutionActivities(trusted_principal="owner",
        supervisor_factory=lambda value: FakeSupervisor(value),
        allow_dispatch=lambda: True, max_retained=1)
    limited._admit(req)
    with pytest.raises(NativeActivityError, match="native_registry_full"):
        limited._admit(request())


@pytest.mark.asyncio
async def test_start_timeout_spends_once_and_false_cleanup_retains_owner():
    req = request()
    supervisors = []
    def construct(value):
        result = FakeSupervisor(value, mode="timeout", fail_close=True)
        supervisors.append(result)
        return result
    adapter = NativeExecutionActivities(trusted_principal="owner",
        supervisor_factory=construct, allow_dispatch=lambda: True,
        heartbeat_seconds=0.2, wait_slice_seconds=0.2)
    environment = ActivityEnvironment()
    result = await environment.run(adapter.run_native, req.to_wire())
    assert qualified_receipt(result, req).outcome == "completed"
    entry = adapter._entry(req)
    if entry.cleanup_task is not None:
        await asyncio.wait_for(entry.cleanup_task, 5)
    assert len(supervisors) == 1 and supervisors[0].starts == 1
    assert supervisors[0].waits == 1 and supervisors[0].closes == 1
    pending = await adapter.local_status(req)
    assert pending.admission_spent and pending.supervisor_present and pending.cleanup_pending
    recovered = await adapter.retry_cleanup(req)
    assert recovered.supervisor_present and not recovered.cleanup_pending
    assert recovered.error_code is None
    assert supervisors[0].closes == 2


@pytest.mark.asyncio
async def test_local_status_includes_scheduled_cleanup_until_proven():
    req = request()
    entered, release = threading.Event(), threading.Event()
    class SlowClose(FakeSupervisor):
        def close(self, wait_seconds=5):
            self.closes += 1
            entered.set()
            assert release.wait(5)
            return True
    adapter = NativeExecutionActivities(trusted_principal="owner",
        supervisor_factory=lambda value: SlowClose(value),
        allow_dispatch=lambda: True)
    try:
        await ActivityEnvironment().run(adapter.run_native, req.to_wire())
        assert await asyncio.to_thread(entered.wait, 5)
        pending = await adapter.local_status(req)
        assert pending.cleanup_pending and pending.receipt_present
        release.set()
        entry = adapter._entry(req)
        await asyncio.wait_for(entry.cleanup_task, 5)
        assert not (await adapter.local_status(req)).cleanup_pending
    finally:
        release.set()


@pytest.mark.asyncio
async def test_cancel_while_factory_blocked_cannot_start_child():
    req = request()
    entered = threading.Event()
    release = threading.Event()
    made = []
    def construct(value):
        entered.set()
        assert release.wait(5)
        supervisor = FakeSupervisor(value)
        made.append(supervisor)
        return supervisor
    adapter = NativeExecutionActivities(trusted_principal="owner",
        supervisor_factory=construct, allow_dispatch=lambda: True,
        heartbeat_seconds=0.2, wait_slice_seconds=0.2)
    environment = ActivityEnvironment()
    running = asyncio.create_task(environment.run(adapter.run_native, req.to_wire()))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        pending = await adapter.request_local_cancel(req)
        assert pending.admission_spent and not pending.supervisor_present
        release.set()
        with pytest.raises(ApplicationError) as cancelled:
            await asyncio.wait_for(running, 5)
        assert cancelled.value.type == "native_cancel_pending"
        entry = adapter._entry(req)
        if entry.cleanup_task is not None:
            await asyncio.wait_for(entry.cleanup_task, 5)
        assert len(made) == 1 and made[0].starts == 0 and made[0].closes >= 1
    finally:
        release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["request", "principal"])
async def test_mismatched_real_supervisor_is_retained_closed_and_never_started(mismatch):
    req = request()
    made = []
    def construct(value):
        wrong = request() if mismatch == "request" else value
        supervisor = FakeSupervisor(wrong)
        if mismatch == "principal":
            supervisor.coordinator.principal = "other"
        made.append(supervisor)
        return supervisor
    adapter = NativeExecutionActivities(trusted_principal="owner",
        supervisor_factory=construct, allow_dispatch=lambda: True)
    with pytest.raises(ApplicationError) as invalid:
        await ActivityEnvironment().run(adapter.run_native, req.to_wire())
    assert invalid.value.type == "invalid_supervisor"
    entry = adapter._entry(req)
    assert entry.supervisor is made[0]
    if entry.cleanup_task is not None:
        await asyncio.wait_for(entry.cleanup_task, 5)
    local = await adapter.local_status(req)
    assert local.supervisor_present and not local.cleanup_pending
    assert local.error_code == "invalid_supervisor"
    after_cancel = await adapter.request_local_cancel(req)
    retry_task = entry.cleanup_task
    if retry_task is not None:
        await asyncio.wait_for(retry_task, 5)
    assert after_cancel.error_code == "invalid_supervisor"
    assert made[0].starts == 0 and made[0].cancels == 0 and made[0].closes >= 1


@pytest.mark.asyncio
async def test_retry_cleanup_during_factory_construction_prevents_first_start():
    req = request()
    entered, release = threading.Event(), threading.Event()
    made = []
    def construct(value):
        entered.set()
        assert release.wait(5)
        result = FakeSupervisor(value)
        made.append(result)
        return result
    adapter = NativeExecutionActivities(trusted_principal="owner",
        supervisor_factory=construct, allow_dispatch=lambda: True,
        heartbeat_seconds=0.2)
    running = asyncio.create_task(ActivityEnvironment().run(adapter.run_native,
                                                             req.to_wire()))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        pending = await adapter.retry_cleanup(req)
        assert pending.admission_spent and pending.cleanup_pending
        release.set()
        with pytest.raises(ApplicationError) as stopped:
            await asyncio.wait_for(running, 5)
        assert stopped.value.type == "native_cancel_pending"
        entry = adapter._entry(req)
        if entry.cleanup_task is not None:
            await asyncio.wait_for(entry.cleanup_task, 5)
        local = await adapter.local_status(req)
        assert local.supervisor_present and not local.cleanup_pending
        assert made[0].starts == 0 and made[0].closes >= 1
    finally:
        release.set()


@pytest.mark.asyncio
async def test_detached_start_failure_is_drained_after_activity_cancellation():
    req = request()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    made = []
    class LateFailure(FakeSupervisor):
        def start(self, wait_seconds=5):
            self.starts += 1
            entered.set()
            try:
                assert release.wait(5)
                raise RuntimeError("SENTINEL_PRIVATE_DETACHED_START")
            finally:
                finished.set()
    def construct(value):
        result = LateFailure(value)
        made.append(result)
        return result
    adapter = NativeExecutionActivities(trusted_principal="owner",
        supervisor_factory=construct, allow_dispatch=lambda: True,
        heartbeat_seconds=0.2)
    loop = asyncio.get_running_loop()
    unhandled = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    running = asyncio.create_task(ActivityEnvironment().run(adapter.run_native,
                                                             req.to_wire()))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        release.set()
        entry = adapter._entry(req)
        assert await asyncio.to_thread(finished.wait, 5)
        if entry.cleanup_task is not None:
            await asyncio.wait_for(entry.cleanup_task, 5)
        await asyncio.sleep(0)
        assert not unhandled
        assert entry.supervisor is made[0] and made[0].starts == 1
        assert "SENTINEL_PRIVATE_DETACHED_START" not in str(entry.error_code)
    finally:
        release.set()
        loop.set_exception_handler(previous)

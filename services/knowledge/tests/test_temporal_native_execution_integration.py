"""Opt-in Temporal + guarded PostgreSQL + owned offline child qualification."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio.client import Client
from temporalio.worker import Replayer

from nexaweave_execution.native_process_driver import NativeProcessDriver
from nexaweave_execution.native_run_coordinator import NativeRunCoordinator
from nexaweave_execution.native_run_store import NativeRunStore, migrate_native_runs
from nexaweave_execution.native_run_supervisor import NativeRunSupervisor
from nexaweave_execution.native_run_contracts import RunState
from nexaweave_execution.temporal_native_host import (NativeTemporalHostError,
    TemporalNativeHost)
from nexaweave_execution.temporal_native_workflow import NativeExecutionWorkflow
from test_native_run_supervisor_integration import GateFactory, await_file, owned
from test_project_store_postgres import factory  # guarded port 15432 fixture

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def native_schema(factory):
    with factory() as conn:
        migrate_native_runs(conn)


async def client():
    if os.getenv("TEMPORAL_EXECUTION_INTEGRATION") != "1":
        pytest.skip("explicit disposable Temporal integration disabled")
    address = os.getenv("TEMPORAL_TEST_ADDRESS")
    if address != "127.0.0.1:17233":
        pytest.fail("approved loopback Temporal address required")
    return await Client.connect(address)


def prepared_factory(factory, paths, created):
    """Trusted test mapping; no artifact path is inferred from workflow wire."""
    def construct(request):
        started, release, finished = paths[request.run_id]
        driver = NativeProcessDriver(GateFactory(str(started), str(release), str(finished)),
            observe_seconds=0.02, grace_seconds=0.1, join_seconds=0.2,
            go_timeout_seconds=30)
        coordinator = NativeRunCoordinator("owner", NativeRunStore(factory), driver,
            dispatch_allowed=lambda _: True, lease_seconds=20)
        supervisor = NativeRunSupervisor(request, coordinator, poll_seconds=0.1,
                                          call_budget_seconds=5)
        created.append((supervisor, driver))
        return supervisor
    return construct


def path_set(root: Path):
    return (root / "started", root / "release", root / "finished")


@pytest.mark.asyncio
async def test_real_native_temporal_completion_duplicate_replay_and_failure_sanitizing(factory, tmp_path):
    temporal = await client()
    request = owned(factory)
    paths = {request.run_id: path_set(tmp_path / "complete")}
    paths[request.run_id][0].parent.mkdir()
    created = []
    construct = prepared_factory(factory, paths, created)
    queue = "mirofish-native-" + uuid4().hex
    host = TemporalNativeHost(client=temporal, task_queue=queue,
        trusted_principal="owner", supervisor_factory=construct,
        allow_dispatch=lambda: True)
    wire = request.to_wire()
    try:
        async with host.worker():
            ref = await host.start(wire)
            await asyncio.to_thread(await_file, paths[request.run_id][0])
            paths[request.run_id][1].write_text("go", encoding="ascii")
            receipt = await asyncio.wait_for(host.result(wire, ref), 40)
            assert receipt.outcome == "completed" and receipt.run_id == request.run_id
            assert paths[request.run_id][2].read_text(encoding="ascii") == "finished"
            assert NativeRunStore(factory).get("owner", request.run_id).receipt == receipt
            with pytest.raises(NativeTemporalHostError) as duplicate:
                await host.start(wire)
            assert duplicate.value.code == "workflow_exists" and len(created) == 1
            history = await temporal.get_workflow_handle(ref.workflow_id,
                run_id=ref.temporal_run_id).fetch_history()
            await Replayer(workflows=[NativeExecutionWorkflow]).replay_workflow(history)
            assert len(created) == 1
            fresh = TemporalNativeHost(client=temporal, task_queue=queue,
                trusted_principal="owner", supervisor_factory=construct,
                allow_dispatch=lambda: True)
            assert await fresh.result(wire, ref) == receipt
            assert len(created) == 1
            local = await host.retry_cleanup(wire)
            assert not local.cleanup_pending and not local.owner_thread_alive
            assert created[0][1]._closed and created[0][1]._owned is None

        disabled = TemporalNativeHost(client=temporal, task_queue=queue,
            trusted_principal="owner", supervisor_factory=construct)
        with pytest.raises(NativeTemporalHostError) as denied:
            await disabled.start(wire)
        assert denied.value.code == "dispatch_disabled"
        with pytest.raises(NativeTemporalHostError) as foreign:
            await host.start({**wire, "principal": "other"})
        assert foreign.value.code == "native_run_denied" and len(created) == 1

        sentinel = "SENTINEL_PRIVATE_NATIVE_FACTORY_9a6d"
        def broken(_request):
            raise RuntimeError(sentinel)
        failed_request = owned(factory)
        failure_host = TemporalNativeHost(client=temporal,
            task_queue="mirofish-native-fail-" + uuid4().hex,
            trusted_principal="owner", supervisor_factory=broken,
            allow_dispatch=lambda: True)
        async with failure_host.worker():
            failure_ref = await failure_host.start(failed_request.to_wire())
            with pytest.raises(NativeTemporalHostError) as failed:
                await asyncio.wait_for(failure_host.result(failed_request.to_wire(),
                                                          failure_ref), 30)
            assert failed.value.code == "native_execution_failed"
            failure_history = await temporal.get_workflow_handle(
                failure_ref.workflow_id,
                run_id=failure_ref.temporal_run_id).fetch_history()
            serialized = json.dumps(json.loads(failure_history.to_json()))
            assert sentinel not in serialized
            assert "native_execution_failed" in serialized
    finally:
        for _, release, _ in paths.values():
            release.write_text("release", encoding="ascii")
        for supervisor, _ in created:
            await asyncio.to_thread(supervisor.close, 10)


@pytest.mark.asyncio
async def test_real_native_temporal_cancel_and_uncertain_fence(factory, tmp_path):
    temporal = await client()
    running_request = owned(factory)
    uncertain_request = owned(factory)
    paths = {running_request.run_id: path_set(tmp_path / "cancel"),
             uncertain_request.run_id: path_set(tmp_path / "uncertain")}
    for started, _, _ in paths.values():
        started.parent.mkdir()
    created = []
    construct = prepared_factory(factory, paths, created)
    store = NativeRunStore(factory)
    declared = store.register(uncertain_request)
    prior_owner = uuid4()
    claim = store.claim_start("owner", declared.request.run_id, prior_owner, 20)
    store.mark_uncertain("owner", declared.request.run_id, claim.attempt_id,
                         prior_owner)
    host = TemporalNativeHost(client=temporal,
        task_queue="mirofish-native-cancel-" + uuid4().hex,
        trusted_principal="owner", supervisor_factory=construct,
        allow_dispatch=lambda: True)
    try:
        async with host.worker():
            running_wire = running_request.to_wire()
            ref = await host.start(running_wire)
            await asyncio.to_thread(await_file, paths[running_request.run_id][0])
            intent = await host.cancel(running_wire, ref)
            assert intent.temporal_status == "cancel_requested"
            receipt = await asyncio.wait_for(host.result(running_wire, ref), 30)
            assert receipt.outcome == "cancelled"
            row = NativeRunStore(factory).get("owner", running_request.run_id)
            assert row.cancel_requested and row.state == RunState.cancelled
            assert row.receipt == receipt
            assert not paths[running_request.run_id][2].exists()
            assert not (await host.retry_cleanup(running_wire)).cleanup_pending

            uncertain_wire = uncertain_request.to_wire()
            uncertain_ref = await host.start(uncertain_wire)
            with pytest.raises(NativeTemporalHostError) as uncertain:
                await asyncio.wait_for(host.result(uncertain_wire, uncertain_ref), 30)
            assert uncertain.value.code == "native_run_uncertain"
            assert not paths[uncertain_request.run_id][0].exists()
            assert NativeRunStore(factory).get("owner", uncertain_request.run_id).state == RunState.uncertain
            assert not (await host.retry_cleanup(uncertain_wire)).cleanup_pending
    finally:
        for _, release, _ in paths.values():
            release.write_text("release", encoding="ascii")
        for supervisor, _ in created:
            await asyncio.to_thread(supervisor.close, 10)


@pytest.mark.asyncio
async def test_remote_temporal_sdk_cancel_retains_owner_cleanup(factory, tmp_path):
    temporal = await client()
    request = owned(factory)
    paths = {request.run_id: path_set(tmp_path / "sdk-cancel")}
    started, release, finished = paths[request.run_id]
    started.parent.mkdir()
    created = []
    construct = prepared_factory(factory, paths, created)
    queue = "mirofish-native-sdk-cancel-" + uuid4().hex
    owner = TemporalNativeHost(client=temporal, task_queue=queue,
        trusted_principal="owner", supervisor_factory=construct,
        allow_dispatch=lambda: True)
    remote = TemporalNativeHost(client=temporal, task_queue=queue,
        trusted_principal="owner", supervisor_factory=construct,
        allow_dispatch=lambda: True)
    wire = request.to_wire()
    try:
        async with owner.worker():
            ref = await owner.start(wire)
            await asyncio.to_thread(await_file, started)
            # This host has no retained entry, so cancel uses the actual SDK
            # handle and the owning activity receives its cancellation signal.
            requested = await remote.cancel(wire, ref)
            assert requested.temporal_status == "cancel_requested"
            # Observe the owning activity's SDK-delivered stop intent before
            # invoking any owner cancel/close/retry API. Temporal cancellation
            # alone does not prove that the local child received cleanup.
            entry = owner._activities._entry(request)
            sdk_deadline = asyncio.get_running_loop().time() + 35
            while True:
                with owner._activities._lock:
                    sdk_stop_seen = entry.stop_requested
                if sdk_stop_seen:
                    break
                if asyncio.get_running_loop().time() >= sdk_deadline:
                    pytest.fail("owning activity did not receive SDK cancellation")
                await asyncio.sleep(0.1)
            with pytest.raises(NativeTemporalHostError):
                await asyncio.wait_for(owner.result(wire, ref), 30)
            handle = temporal.get_workflow_handle(ref.workflow_id,
                                                  run_id=ref.temporal_run_id)
            assert (await handle.describe()).status.name.lower() == "canceled"

            deadline = asyncio.get_running_loop().time() + 20
            while True:
                local = await owner.retry_cleanup(wire)
                if not local.cleanup_pending and not local.owner_thread_alive:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    pytest.fail("retained owned cleanup did not finish by deadline")
                await asyncio.sleep(0.1)
            assert len(created) == 1
            assert created[0][1]._closed and created[0][1]._owned is None
            assert not finished.exists()
            with pytest.raises(NativeTemporalHostError) as duplicate:
                await owner.start(wire)
            assert duplicate.value.code == "workflow_exists" and len(created) == 1

            store = NativeRunStore(factory)
            row = store.reconcile_expired("owner", request.run_id)
            if row.state in (RunState.starting, RunState.running):
                # Cleanup of a local child is not a native receipt. Expire the
                # disposable row explicitly to show its conservative fence.
                with factory() as conn:
                    conn.execute("UPDATE mf_native_execution.runs SET "
                                 "lease_until=clock_timestamp()-interval '1 second' "
                                 "WHERE run_id=%s", (request.run_id,))
                row = store.reconcile_expired("owner", request.run_id)
            assert row.state in (RunState.cancelled, RunState.uncertain)
            if row.state == RunState.cancelled:
                assert row.receipt is not None and row.receipt.outcome == "cancelled"
            else:
                assert row.receipt is None
    finally:
        release.write_text("release", encoding="ascii")
        for supervisor, _ in created:
            await asyncio.to_thread(supervisor.close, 10)

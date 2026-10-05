"""Explicit Temporal worker/scheduler; no Flask import and no detached jobs."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from contextvars import copy_context
from datetime import timedelta
import re

from temporalio import activity
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker
from .preparation_contracts import PreparationAuthorityError, PreparationDispatch
from .temporal_preparation_workflow import ACTIVITY_NAME, PreparationWorkflow, result_identifiers


class TemporalPreparationHost:
    def __init__(self, *, client, task_queue, trusted_host, allow_dispatch=None,
                 max_concurrent_activities=1):
        if (not isinstance(client, Client) or type(task_queue) is not str
                or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,127}', task_queue)
                or not callable(getattr(trusted_host, 'generate', None))
                or type(max_concurrent_activities) is not int or not 1 <= max_concurrent_activities <= 4):
            raise PreparationAuthorityError('preparation_unavailable')
        self.client, self.queue, self.host = client, task_queue, trusted_host
        self.allow = allow_dispatch
        self.concurrency = max_concurrent_activities

    @activity.defn(name=ACTIVITY_NAME)
    def prepare(self, wire):
        dispatch = PreparationDispatch.from_wire(wire)
        try:
            if not callable(self.allow) or self.allow() is not True:
                raise PreparationAuthorityError('model_calls_disabled')
            activity.heartbeat()
            context = copy_context()
            # Inherited persona generation uses an owned inner executor. Carry
            # activity context to callbacks there without sharing one Context
            # concurrently between generator workers.
            result = self.host.generate(dispatch.to_wire(),
                heartbeat=lambda: context.copy().run(activity.heartbeat),
                cancelled=lambda: context.copy().run(activity.is_cancelled))
            return result_identifiers(result, dispatch)
        except BaseException:
            # Never expose exception causes, text, provider data or private paths
            # in Temporal activity failure/history. PG remains authority.
            raise ApplicationError('preparation_uncertain', type='preparation_uncertain', non_retryable=True) from None

    @asynccontextmanager
    async def worker(self):
        # Synchronous generator work lives only in this owned executor. Transport
        # bounds and cooperative cancellation permit finite worker drain.
        pool = ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix='mf-preparation')
        try:
            async with Worker(self.client, task_queue=self.queue, workflows=[PreparationWorkflow],
                activities=[self.prepare], activity_executor=pool,
                max_concurrent_activities=self.concurrency,
                graceful_shutdown_timeout=timedelta(seconds=30)) as worker:
                yield worker
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
            if not self.host.drain_cleanup():
                raise PreparationAuthorityError('preparation_uncertain')

    async def schedule(self, wire):
        dispatch = PreparationDispatch.from_wire(wire)
        if not callable(self.allow) or self.allow() is not True:
            raise PreparationAuthorityError('model_calls_disabled')
        row = self.host.store.get(self.host.principal, dispatch.operation_id, dispatch.plan_sha256)
        if row.attempt_id != dispatch.attempt_id or row.state != 'queued':
            raise PreparationAuthorityError('conflict')
        try:
            handle = await asyncio.wait_for(self.client.start_workflow(PreparationWorkflow.run,
                dispatch.to_wire(), id=dispatch.workflow_id, task_queue=self.queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
                execution_timeout=timedelta(seconds=780)), 10)
            return handle
        except Exception:
            raise PreparationAuthorityError('preparation_uncertain') from None

    def scheduler_for(self, owned_loop):
        """Adapter for HTTP threads to an ALREADY owned running Temporal loop.

        Do not call from that loop's thread. It creates no loop/thread/job.
        Lost response does not cancel the workflow or grant a retry.
        """
        if not owned_loop.is_running():
            raise PreparationAuthorityError('preparation_unavailable')

        def schedule(wire):
            try:
                if asyncio.get_running_loop() is owned_loop:
                    raise PreparationAuthorityError('preparation_unavailable')
            except RuntimeError:
                pass
            future = asyncio.run_coroutine_threadsafe(self.schedule(wire), owned_loop)
            try:
                return future.result(timeout=12)
            except Exception:
                # Retain potentially started scheduling; no cancel/re-dispatch.
                raise PreparationAuthorityError('preparation_uncertain') from None
        return schedule

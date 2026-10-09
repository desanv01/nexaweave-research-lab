"""One-shot nonretryable follow-up activity; journal owns recovery."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import timedelta
import re

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy, WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker
with workflow.unsafe.imports_passed_through():
    from .followup_contracts import FollowupError, dispatch, workflow_id

ACTIVITY='mf-connected-followup-v1'


@workflow.defn
class ConnectedFollowupWorkflow:
    @workflow.run
    async def run(self,wire):
        dispatch(wire)
        result=await workflow.execute_activity(ACTIVITY,wire,
            start_to_close_timeout=timedelta(seconds=630),heartbeat_timeout=timedelta(seconds=15),
            retry_policy=RetryPolicy(maximum_attempts=1))
        if (type(result) is not dict or set(result)!={'schema_version','turn_id','plan_sha256','state'}
                or type(result['schema_version']) is not int or result['schema_version']!=1
                or result['turn_id']!=wire['turn_id'] or result['plan_sha256']!=wire['plan_sha256']
                or result['state'] not in ('completed','failed','cancelled','uncertain')):
            raise ApplicationError('followup_uncertain',type='followup_uncertain',non_retryable=True)
        return result


class TemporalFollowupHost:
    def __init__(self,*,client,task_queue,trusted_host,allow_dispatch=None,max_concurrent_activities=1):
        if (not isinstance(client,Client) or type(task_queue) is not str
                or re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,127}',task_queue) is None
                or not callable(getattr(trusted_host,'generate',None))
                or type(max_concurrent_activities) is not int
                or not 1<=max_concurrent_activities<=4):
            raise FollowupError('followup_unavailable')
        self.client,self.queue,self.host=client,task_queue,trusted_host
        self.allow,self.concurrency=allow_dispatch,max_concurrent_activities

    @activity.defn(name=ACTIVITY)
    def followup(self,wire):
        try:
            dispatch(wire)
            if not callable(self.allow) or self.allow() is not True:
                raise FollowupError('model_calls_disabled')
            activity.heartbeat()
            result=self.host.generate(wire,heartbeat=activity.heartbeat,cancelled=activity.is_cancelled)
            return {key:result[key] for key in ('schema_version','turn_id','plan_sha256','state')}
        except BaseException:
            raise ApplicationError('followup_uncertain',type='followup_uncertain',non_retryable=True) from None

    @asynccontextmanager
    async def worker(self):
        pool=ThreadPoolExecutor(max_workers=self.concurrency,thread_name_prefix='mf-followup')
        try:
            async with Worker(self.client,task_queue=self.queue,workflows=[ConnectedFollowupWorkflow],
                activities=[self.followup],activity_executor=pool,
                max_concurrent_activities=self.concurrency,
                graceful_shutdown_timeout=timedelta(seconds=20)) as owned:
                yield owned
        finally:
            pool.shutdown(wait=True,cancel_futures=True)

    async def schedule(self,wire):
        dispatch(wire)
        if not callable(self.allow) or self.allow() is not True:
            raise FollowupError('model_calls_disabled')
        row=self.host.store.get(self.host.principal,wire['turn_id'],wire['plan_sha256'])
        if str(row.attempt_id)!=wire['attempt_id'] or row.state!='queued':
            raise FollowupError('turn_conflict')
        try:
            handle=await asyncio.wait_for(self.client.start_workflow(ConnectedFollowupWorkflow.run,wire,
                id=workflow_id(wire),task_queue=self.queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
                execution_timeout=timedelta(seconds=650)),10)
            return dict(workflow_id=handle.id,run_id=handle.first_execution_run_id)
        except Exception:
            raise FollowupError('followup_uncertain') from None

    def scheduler_for(self,owned_loop):
        if not owned_loop.is_running():
            raise FollowupError('followup_unavailable')
        def schedule(wire):
            try:
                if asyncio.get_running_loop() is owned_loop:
                    raise FollowupError('followup_unavailable')
            except RuntimeError:
                pass
            future=asyncio.run_coroutine_threadsafe(self.schedule(wire),owned_loop)
            try:
                return future.result(timeout=12)
            except Exception:
                raise FollowupError('followup_uncertain') from None
        return schedule

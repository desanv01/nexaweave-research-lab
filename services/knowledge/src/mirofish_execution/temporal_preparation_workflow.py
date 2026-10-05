"""One-shot preparation workflow. History contains identifiers and hashes only."""
from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from .preparation_contracts import PreparationDispatch, PreparationAuthorityError, sha

ACTIVITY_NAME = 'mirofish_prepare_artifacts_v1'
START_TO_CLOSE = timedelta(seconds=660)
SCHEDULE_TO_CLOSE = timedelta(seconds=720)
HEARTBEAT_TIMEOUT = timedelta(seconds=45)


def result_identifiers(value, dispatch):
    try:
        if (type(value) is not dict or set(value) != {'schema_version', 'operation_id', 'plan_sha256', 'state', 'artifact_sha256'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or value['operation_id'] != str(dispatch.operation_id)
                or value['plan_sha256'] != dispatch.plan_sha256 or value['state'] != 'ready'):
            raise ValueError
        sha(value['artifact_sha256'])
    except (ValueError, TypeError, KeyError):
        raise PreparationAuthorityError('preparation_uncertain') from None
    return value


@workflow.defn(name='mirofish_preparation_v1')
class PreparationWorkflow:
    @workflow.run
    async def run(self, wire):
        try:
            dispatch = PreparationDispatch.from_wire(wire)
            result = await workflow.execute_activity(ACTIVITY_NAME, dispatch.to_wire(),
                start_to_close_timeout=START_TO_CLOSE, schedule_to_close_timeout=SCHEDULE_TO_CLOSE,
                heartbeat_timeout=HEARTBEAT_TIMEOUT, retry_policy=RetryPolicy(maximum_attempts=1))
            return result_identifiers(result, dispatch)
        except PreparationAuthorityError as error:
            raise ApplicationError(error.code, type=error.code, non_retryable=True) from None

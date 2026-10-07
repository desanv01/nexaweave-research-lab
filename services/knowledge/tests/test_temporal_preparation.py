"""Identifier-only Temporal contracts and explicitly guarded real combined fixture."""
import asyncio
from copy import deepcopy
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from temporalio.client import Client, WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from temporalio.worker import Replayer

from nexaweave_execution.preparation_contracts import (PreparationAuthorityError,
    PreparationDispatch, PreparedBudgetReceipt)
from nexaweave_execution.temporal_preparation_workflow import (PreparationWorkflow,
    ACTIVITY_NAME, START_TO_CLOSE, SCHEDULE_TO_CLOSE, HEARTBEAT_TIMEOUT, result_identifiers)
from nexaweave_execution.temporal_preparation_host import TemporalPreparationHost
from test_source_bridge_postgres import factory


def dispatch():
    return PreparationDispatch(uuid4(), uuid4(), 'a' * 64)


def receipt(request):
    return {'schema_version': 1, 'operation_id': str(request.operation_id),
            'plan_sha256': request.plan_sha256, 'state': 'ready', 'artifact_sha256': 'b' * 64}


def test_dispatch_is_only_identifiers_and_distinct_budget_receipt():
    request = dispatch()
    assert set(request.to_wire()) == {'schema_version', 'operation_id', 'attempt_id', 'plan_sha256'}
    assert request.workflow_id == 'mf-preparation-v1-' + request.operation_id.hex
    for change in ({'source_text': 'PRIVATE'}, {'path': 'PRIVATE'}, {'principal': 'other'}, {'schema_version': True}):
        with pytest.raises(PreparationAuthorityError):
            PreparationDispatch.from_wire(dict(request.to_wire(), **change))
    proof = PreparedBudgetReceipt(request.operation_id, request.attempt_id, 'a' * 64, 'b' * 64)
    assert PreparedBudgetReceipt.from_wire(proof.json_value()) == proof
    with pytest.raises(PreparationAuthorityError):
        PreparedBudgetReceipt.from_wire({'group_id': 'mf1_' + 'a' * 64})


@pytest.mark.asyncio
async def test_workflow_uses_one_attempt_and_finite_identifier_activity(monkeypatch):
    request = dispatch()
    observed = []
    async def execute(name, wire, **kwargs):
        observed.append((name, wire, kwargs))
        return receipt(request)
    from nexaweave_execution import temporal_preparation_workflow as module
    monkeypatch.setattr(module.workflow, 'execute_activity', execute)
    assert await PreparationWorkflow().run(request.to_wire()) == receipt(request)
    name, wire, kwargs = observed[0]
    assert name == ACTIVITY_NAME and wire == request.to_wire()
    assert kwargs['retry_policy'].maximum_attempts == 1
    assert kwargs['start_to_close_timeout'] == START_TO_CLOSE
    assert kwargs['schedule_to_close_timeout'] == SCHEDULE_TO_CLOSE
    assert kwargs['heartbeat_timeout'] == HEARTBEAT_TIMEOUT
    assert START_TO_CLOSE.total_seconds() == 660 and HEARTBEAT_TIMEOUT.total_seconds() == 45


def test_result_rejects_private_fields_and_mismatched_identity():
    request = dispatch()
    for change in ({'source_text': 'PRIVATE'}, {'operation_id': str(uuid4())},
                   {'artifact_sha256': 'A' * 64}, {'state': 'failed'}):
        with pytest.raises(PreparationAuthorityError):
            result_identifiers(dict(receipt(request), **change), request)


def test_activity_failure_has_only_fixed_public_code():
    request = dispatch()
    # Bypass client construction ONLY to exercise Temporal activity sanitizer.
    host = object.__new__(TemporalPreparationHost)
    host.allow = lambda: True
    host.host = SimpleNamespace(generate=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('PRIVATE_SECRET_PATH')))
    with pytest.raises(ApplicationError) as failed:
        ActivityEnvironment().run(host.prepare, request.to_wire())
    assert failed.value.type == 'preparation_uncertain' and failed.value.message == 'preparation_uncertain'
    assert 'PRIVATE' not in str(failed.value)


@pytest.mark.postgres
@pytest.mark.preparation_temporal
@pytest.mark.asyncio
async def test_real_temporal_pg_inherited_artifacts_restart_history_and_cleanup(factory, tmp_path):
    if os.getenv('TEMPORAL_EXECUTION_INTEGRATION') != '1':
        pytest.skip('explicit disposable Temporal integration disabled')
    address = os.getenv('TEMPORAL_TEST_ADDRESS')
    if address != '127.0.0.1:17233':
        pytest.fail('approved loopback Temporal address required')
    from nexaweave_execution.budget import migrate as migrate_budget
    from nexaweave_execution.preparation_store import migrate, PreparationStore
    from test_preparation_store import real_host, request, reference
    with factory() as conn:
        migrate_budget(conn)
        migrate(conn)
    client = await asyncio.wait_for(Client.connect(address), 15)
    host, scope, retained, chat, wires, transport = real_host(factory, tmp_path)
    temporal = TemporalPreparationHost(client=client, task_queue='mf-prep-test-' + uuid4().hex,
                                      trusted_host=host, allow_dispatch=lambda: True)
    loop = asyncio.get_running_loop()
    host.scheduler = temporal.scheduler_for(loop)
    async with temporal.worker():
        planned = await asyncio.to_thread(host.plan, request(retained))
        queued = await asyncio.to_thread(host.start, reference(planned))
        row = PreparationStore(factory).get('owner', planned['operation_id'])
        handle = client.get_workflow_handle(row.dispatch.workflow_id)
        result = await asyncio.wait_for(handle.result(), 45)
        assert result_identifiers(result, row.dispatch) == result
        ready = await asyncio.to_thread(host.status, reference(planned))
        assert ready['state'] == 'ready' and ready['receipt']['artifact_sha256'] == result['artifact_sha256']
        assert len(chat.calls) == 4
        # A fresh store/host facade reads the retained PG authority. Duplicate
        # start cannot schedule/rebill; the activity itself cannot be reclaimed.
        assert await asyncio.to_thread(host.start, reference(planned)) == ready
        assert PreparationStore(factory).get('owner', row.operation_id).state == 'ready'
        with pytest.raises(PreparationAuthorityError):
            await asyncio.to_thread(host.generate, row.dispatch.to_wire())
        history = await asyncio.wait_for(handle.fetch_history(), 10)
        await asyncio.wait_for(Replayer(workflows=[PreparationWorkflow]).replay_workflow(history), 15)
        raw = history.to_json()
        assert 'PRIVATE_SOURCE_SENTINEL' not in raw and '艾丽丝关注技术' not in raw
        assert str(tmp_path) not in raw and '_attempts' not in raw
        assert len(chat.calls) == 4
        assert host.budget.status('owner', host.account_id).accounted_ceiling_microusd == 4
        from test_provider_neutral_preparation import ScriptedChat
        broken_chat = ScriptedChat(broken_persona=True)
        host.client_factory = lambda: broken_chat
        failed_plan = await asyncio.to_thread(host.plan, request(retained))
        await asyncio.to_thread(host.start, reference(failed_plan))
        failed_row = PreparationStore(factory).get('owner', failed_plan['operation_id'])
        failed_handle = client.get_workflow_handle(failed_row.dispatch.workflow_id)
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(failed_handle.result(), 45)
        failed = await asyncio.to_thread(host.status, reference(failed_plan))
        assert failed['state'] == 'uncertain' and failed['receipt'] is None and failed['model_calls_started']
        assert host.budget.status('owner', host.account_id).uncertain_microusd == 4
        failed_history = await asyncio.wait_for(failed_handle.fetch_history(), 10)
        await asyncio.wait_for(Replayer(workflows=[PreparationWorkflow]).replay_workflow(failed_history), 15)
        assert 'PRIVATE_SOURCE_SENTINEL' not in failed_history.to_json()
        assert len(broken_chat.calls) == 1
    # The worker context drains owned executor work; no native/model launch is
    # implied. Artifact files and private attempts are deliberately retained.
    assert host.status(reference(planned))['simulation_executed'] is False

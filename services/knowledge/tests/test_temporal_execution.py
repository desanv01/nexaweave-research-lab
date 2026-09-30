"""Pure Temporal wire, retry and activity boundary source tests."""
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from typing import Any

import pytest
from temporalio import activity as temporal_activity_sdk
from temporalio import workflow as temporal_workflow_sdk
from temporalio.converter import DataConverter
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

from mirofish_execution.temporal_activities import SourceIngestionActivities
from mirofish_execution.temporal_contracts import (InvalidTemporalRequest,
    TemporalIngestionRequest, TemporalReceipt)
from mirofish_execution.temporal_workflow import (HEARTBEAT_TIMEOUT,
    SCHEDULE_TO_CLOSE, START_TO_CLOSE, SourceIngestionWorkflow)
from mirofish_execution.budget import BudgetLedger
from mirofish_execution.budgeted_ingestion import BudgetedIngestion
from mirofish_knowledge.ingestion import KnowledgeIngestionCoordinator
from mirofish_knowledge.operations import Ledger
from mirofish_knowledge.source_bridge import SourceIngestionBridge


def request_wire():
    return {"schema_version": 1, "principal": "owner", "display_graph_id": "graph_1",
            "source_revision": str(uuid4()), "operation_id": str(uuid4()),
            "account_id": str(uuid4()), "ontology_revision": str(uuid4()),
            "ceiling_microusd": 7, "request_fingerprint": "a" * 64}


def test_bounded_wire_identity_and_receipt():
    wire = request_wire()
    request = TemporalIngestionRequest.from_wire(wire)
    assert TemporalIngestionRequest.from_wire(request.to_wire()) == request
    assert request.workflow_id == TemporalIngestionRequest.from_wire(wire).workflow_id
    changed = dict(wire, ceiling_microusd=8)
    assert TemporalIngestionRequest.from_wire(changed).workflow_id != request.workflow_id
    group = "mf1_" + "b" * 64
    episode = uuid5(NAMESPACE_URL,
                    f"mirofish:episode:v1:{group}:{UUID(request.operation_id)}")
    receipt = {"schema_version": 1, "group_id": group,
               "episode_id": str(episode), "fingerprint": wire["request_fingerprint"],
               "evidence_ids": [str(uuid4())]}
    assert TemporalReceipt.from_wire(receipt, request).fingerprint == wire["request_fingerprint"]
    with pytest.raises(InvalidTemporalRequest):
        TemporalReceipt.from_wire(dict(receipt, fingerprint="c" * 64), request)


@pytest.mark.asyncio
async def test_real_sdk_sandbox_preparation_without_server():
    # This invokes the same SDK preparation path as Worker construction. The
    # execution package initializer must not import Graphiti/NumPy here.
    definition = temporal_workflow_sdk._Definition.must_from_class(SourceIngestionWorkflow)
    SandboxedWorkflowRunner().prepare_workflow(definition)


@pytest.mark.asyncio
async def test_default_converter_uses_decorated_boundary_hints():
    bridge = SourceIngestionBridge(lambda: None)
    service = BudgetedIngestion(bridge, BudgetLedger(lambda: None),
        KnowledgeIngestionCoordinator(Ledger(lambda: None), object(), timeout_seconds=5))
    adapter = SourceIngestionActivities(trusted_principal="owner", bridge=bridge,
        service=service, resolve_ontology=lambda principal, revision: None)
    workflow_def = temporal_workflow_sdk._Definition.must_from_class(SourceIngestionWorkflow)
    activity_def = temporal_activity_sdk._Definition.must_from_callable(adapter.ingest_source)
    assert workflow_def.arg_types == [Any]
    assert activity_def.arg_types == [Any]
    converter = DataConverter.default
    valid = request_wire()
    for wire in (valid, ["malformed", "nonmapping"]):
        payloads = await converter.encode([wire])
        workflow_decoded = await converter.decode(payloads, workflow_def.arg_types)
        activity_decoded = await converter.decode(payloads, activity_def.arg_types)
        assert workflow_decoded == activity_decoded == [wire]
        if isinstance(wire, dict):
            assert TemporalIngestionRequest.from_wire(workflow_decoded[0]).to_wire() == wire
        else:
            with pytest.raises(InvalidTemporalRequest):
                TemporalIngestionRequest.from_wire(workflow_decoded[0])
    request = TemporalIngestionRequest.from_wire(valid)
    group = "mf1_" + "b" * 64
    receipt = {"schema_version": 1, "group_id": group,
               "episode_id": str(uuid5(NAMESPACE_URL,
                   f"mirofish:episode:v1:{group}:{UUID(request.operation_id)}")),
               "fingerprint": request.request_fingerprint, "evidence_ids": []}
    result_payloads = await converter.encode([receipt])
    from_activity = await converter.decode(result_payloads, [activity_def.ret_type])
    from_workflow = await converter.decode(result_payloads, [workflow_def.ret_type])
    assert TemporalReceipt.from_wire(from_activity[0], request) == TemporalReceipt.from_wire(
        from_workflow[0], request)


@pytest.mark.parametrize("change", [
    {"ceiling_microusd": 0}, {"ceiling_microusd": True},
    {"ceiling_microusd": 2**63}, {"principal": "owner\nsecret"},
    {"source_revision": "not-uuid"}, {"request_fingerprint": "A" * 64},
    {"schema_version": True}, {"display_graph_id": "../graph"},
    {"source_text": "must not enter history"},
])
def test_invalid_temporal_wire_denied(change):
    with pytest.raises(InvalidTemporalRequest):
        TemporalIngestionRequest.from_wire(dict(request_wire(), **change))


@pytest.mark.asyncio
async def test_workflow_one_attempt_and_finite_timeouts(monkeypatch):
    from mirofish_execution import temporal_workflow as module
    captured = {}
    async def execute(name, wire, **options):
        captured.update(options)
        assert name == module.ACTIVITY_NAME
        request = TemporalIngestionRequest.from_wire(wire)
        group = "mf1_" + "b" * 64
        return {"schema_version": 1, "group_id": group,
                "episode_id": str(uuid5(NAMESPACE_URL,
                    f"mirofish:episode:v1:{group}:{UUID(request.operation_id)}")),
                "fingerprint": request.request_fingerprint, "evidence_ids": []}
    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    workflow = SourceIngestionWorkflow()
    assert (await workflow.run(request_wire()))["fingerprint"] == "a" * 64
    assert workflow.stage() == "completed"
    assert captured["retry_policy"].maximum_attempts == 1
    assert captured["start_to_close_timeout"] == START_TO_CLOSE
    assert captured["schedule_to_close_timeout"] == SCHEDULE_TO_CLOSE
    assert captured["heartbeat_timeout"] == HEARTBEAT_TIMEOUT


@pytest.mark.asyncio
async def test_workflow_validation_is_terminal_and_nonretryable(monkeypatch):
    from mirofish_execution import temporal_workflow as module
    malformed = dict(request_wire(), source_text="not allowed")
    with pytest.raises(ApplicationError) as bad_input:
        await SourceIngestionWorkflow().run(malformed)
    assert bad_input.value.type == "invalid_request"
    assert bad_input.value.non_retryable is True
    async def bad_receipt(*args, **kwargs):
        return {"private": "must not be returned"}
    monkeypatch.setattr(module.workflow, "execute_activity", bad_receipt)
    with pytest.raises(ApplicationError) as bad_result:
        await SourceIngestionWorkflow().run(request_wire())
    assert bad_result.value.type == "invalid_receipt"
    assert bad_result.value.non_retryable is True


@pytest.mark.asyncio
async def test_activity_environment_default_denial_and_sanitized_error():
    bridge = SourceIngestionBridge(lambda: None)
    service = BudgetedIngestion(bridge, BudgetLedger(lambda: None),
        KnowledgeIngestionCoordinator(Ledger(lambda: None), object(), timeout_seconds=5))
    activity = SourceIngestionActivities(trusted_principal="owner", bridge=bridge,
        service=service, resolve_ontology=lambda principal, revision: None)
    environment = ActivityEnvironment()
    with pytest.raises(ApplicationError) as denied:
        await environment.run(activity.ingest_source, request_wire())
    assert denied.value.type == "budget_denied"
    enabled = SourceIngestionActivities(trusted_principal="owner", bridge=bridge,
        service=service, resolve_ontology=lambda principal, revision: (_ for _ in ()).throw(
            RuntimeError("SENTINEL_PRIVATE_PROVIDER_DETAIL")), allow_dispatch=lambda: True)
    with pytest.raises(ApplicationError) as failed:
        await environment.run(enabled.ingest_source, request_wire())
    assert "SENTINEL_PRIVATE_PROVIDER_DETAIL" not in str(failed.value)
    assert failed.value.type == "ingestion_failed"

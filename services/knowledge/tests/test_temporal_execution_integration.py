"""Opt-in disposable loopback Temporal + PostgreSQL + fake-provider path."""
import asyncio
import json
import os
from uuid import uuid4

import pytest
from temporalio.client import Client
from temporalio import activity
from temporalio.worker import Replayer, Worker

from mirofish_execution import BudgetBusy, BudgetLedger, migrate as migrate_budget
from mirofish_execution.temporal_host import TemporalHostError, TemporalIngestionHost
from mirofish_execution.temporal_workflow import ACTIVITY_NAME, SourceIngestionWorkflow
from mirofish_execution.budgeted_ingestion import BudgetedIngestion
from mirofish_knowledge.ingestion import KnowledgeIngestionCoordinator
from mirofish_knowledge.operations import Ledger
from mirofish_knowledge.source_bridge import SourceIngestionBridge
from test_source_bridge_postgres import (FakeProvider, factory, ontology,
                                         owned_fixture)

pytestmark = pytest.mark.postgres


def _history_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_history_text(key) + " " + _history_text(item)
                        for key, item in value.items())
    if isinstance(value, list):
        return " ".join(_history_text(item) for item in value)
    return ""


def wire(display, retained, operation, account, spec, fingerprint):
    return {"schema_version": 1, "principal": "owner",
            "display_graph_id": display, "source_revision": str(retained.source_revision),
            "operation_id": str(operation), "account_id": str(account),
            "ontology_revision": str(spec.revision), "ceiling_microusd": 4,
            "request_fingerprint": fingerprint}


@pytest.mark.asyncio
async def test_real_temporal_budgeted_ingestion_and_replay(factory):
    if os.getenv("TEMPORAL_EXECUTION_INTEGRATION") != "1":
        pytest.skip("explicit disposable Temporal integration disabled")
    address = os.getenv("TEMPORAL_TEST_ADDRESS")
    if address != "127.0.0.1:17233":
        pytest.fail("approved loopback Temporal address required")
    with factory() as conn:
        migrate_budget(conn)
    client = await Client.connect(address)
    display, scope, retained = owned_fixture(factory)
    account, budget = uuid4(), BudgetLedger(factory)
    budget.create_account("owner", scope.project_id, account, 12)
    bridge = SourceIngestionBridge(factory)
    spec, operation = ontology(), uuid4()
    plan = bridge.plan("owner", display, retained.source_revision, operation, spec)
    provider = FakeProvider()
    service = BudgetedIngestion(bridge, budget,
        KnowledgeIngestionCoordinator(Ledger(factory), provider, timeout_seconds=10))
    queue = "mirofish-test-" + uuid4().hex
    host = TemporalIngestionHost(client=client, task_queue=queue,
        trusted_principal="owner", bridge=bridge, service=service,
        resolve_ontology=lambda principal, revision: spec,
        allow_dispatch=lambda: True)
    request = wire(display, retained, operation, account, spec, plan.request_fingerprint)
    async with host.worker():
        ref = await host.start(request)
        receipt = await asyncio.wait_for(host.result(request, ref), 30)
        assert receipt.fingerprint == plan.request_fingerprint
        assert (await host.status(request, ref)).workflow_stage == "completed"
        with pytest.raises(TemporalHostError) as duplicate:
            await host.start(request)
        assert duplicate.value.code == "workflow_exists"
        assert provider.calls == 1
        assert budget.status("owner", account).accounted_ceiling_microusd == 4
        fresh = TemporalIngestionHost(client=client, task_queue=queue,
            trusted_principal="owner", bridge=bridge, service=service,
            resolve_ontology=lambda principal, revision: spec,
            allow_dispatch=lambda: True)
        assert await fresh.result(request, ref) == receipt
        history = await client.get_workflow_handle(ref.workflow_id,
                                                     run_id=ref.run_id).fetch_history()
        await Replayer(workflows=[SourceIngestionWorkflow]).replay_workflow(history)
        assert provider.calls == 1

        wrong = dict(request, operation_id=str(uuid4()), request_fingerprint="0" * 64)
        bad_ref = await host.start(wrong)
        with pytest.raises(TemporalHostError) as stale:
            await host.result(wrong, bad_ref)
        assert stale.value.code == "invalid_request"
        stale_revision = dict(request, operation_id=str(uuid4()),
                              ontology_revision=str(uuid4()))
        stale_ref = await host.start(stale_revision)
        with pytest.raises(TemporalHostError) as stale_ontology:
            await host.result(stale_revision, stale_ref)
        assert stale_ontology.value.code == "invalid_request"
        absent = dict(request, operation_id=str(uuid4()),
                      display_graph_id="missing_graph")
        absent_ref = await host.start(absent)
        with pytest.raises(TemporalHostError) as unowned:
            await host.result(absent, absent_ref)
        assert unowned.value.code == "source_not_found"
        with pytest.raises(TemporalHostError) as owner:
            await host.start(dict(request, principal="other"))
        assert owner.value.code == "source_denied"
        assert provider.calls == 1

        sentinel = "SENTINEL_RESOLVER_SECRET_7e8c"
        def broken_resolver(principal, revision):
            raise RuntimeError(sentinel)
        resolver_queue = "mirofish-resolver-" + uuid4().hex
        resolver_host = TemporalIngestionHost(client=client, task_queue=resolver_queue,
            trusted_principal="owner", bridge=bridge, service=service,
            resolve_ontology=broken_resolver, allow_dispatch=lambda: True)
        resolver_op = uuid4()
        resolver_plan = bridge.plan("owner", display, retained.source_revision,
                                    resolver_op, spec)
        resolver_request = wire(display, retained, resolver_op, account, spec,
                                resolver_plan.request_fingerprint)
        async with resolver_host.worker():
            resolver_ref = await resolver_host.start(resolver_request)
            with pytest.raises(TemporalHostError) as resolver_error:
                await asyncio.wait_for(resolver_host.result(resolver_request, resolver_ref), 30)
            assert resolver_error.value.code == "ingestion_failed"
            resolver_history = await client.get_workflow_handle(resolver_ref.workflow_id,
                                             run_id=resolver_ref.run_id).fetch_history()
            serialized = _history_text(json.loads(resolver_history.to_json()))
            assert sentinel not in serialized
            assert "ingestion_failed" in serialized
        assert provider.calls == 1

        entered = asyncio.Event()
        class WaitingProvider(FakeProvider):
            async def ingest(self, scope, source, ontology):
                self.calls += 1
                entered.set()
                await asyncio.Event().wait()
        waiting = WaitingProvider()
        held_service = BudgetedIngestion(bridge, budget,
            KnowledgeIngestionCoordinator(Ledger(factory), waiting, timeout_seconds=30))
        # A second explicit task queue isolates the cancellation fixture worker.
        held_queue = "mirofish-held-" + uuid4().hex
        held_host = TemporalIngestionHost(client=client, task_queue=held_queue,
            trusted_principal="owner", bridge=bridge, service=held_service,
            resolve_ontology=lambda principal, revision: spec,
            allow_dispatch=lambda: True)
        held_op = uuid4()
        held_plan = bridge.plan("owner", display, retained.source_revision, held_op, spec)
        held_request = wire(display, retained, held_op, account, spec,
                            held_plan.request_fingerprint)
        async with held_host.worker():
            held_ref = await held_host.start(held_request)
            await asyncio.wait_for(entered.wait(), 20)
            await held_host.cancel(held_request, held_ref)
            with pytest.raises(TemporalHostError):
                await asyncio.wait_for(held_host.result(held_request, held_ref), 20)
        assert waiting.calls == 1
        assert budget.status("owner", account).remaining_microusd == 4
        with pytest.raises(BudgetBusy):
            await held_service.ingest("owner", display, retained.source_revision,
                held_op, spec, account_id=account, ceiling_microusd=4)
        assert waiting.calls == 1

        # The cancelled knowledge scope is deliberately fenced. Use a distinct
        # owned scope for the independent provider-failure scenario.
        fail_display, fail_scope, fail_retained = owned_fixture(factory)
        fail_account, fail_spec = uuid4(), ontology()
        budget.create_account("owner", fail_scope.project_id, fail_account, 4)
        class SecretFailure(FakeProvider):
            async def ingest(self, scope, source, ontology):
                self.calls += 1
                raise RuntimeError("SENTINEL_PRIVATE_PROVIDER_DETAIL")
        failing = SecretFailure()
        fail_queue = "mirofish-fail-" + uuid4().hex
        fail_host = TemporalIngestionHost(client=client, task_queue=fail_queue,
            trusted_principal="owner", bridge=bridge,
            service=BudgetedIngestion(bridge, budget,
                KnowledgeIngestionCoordinator(Ledger(factory), failing, timeout_seconds=10)),
            resolve_ontology=lambda principal, revision: fail_spec,
            allow_dispatch=lambda: True)
        failed_op = uuid4()
        failed_plan = bridge.plan("owner", fail_display, fail_retained.source_revision,
                                  failed_op, fail_spec)
        failed_request = wire(fail_display, fail_retained, failed_op, fail_account, fail_spec,
                              failed_plan.request_fingerprint)
        async with fail_host.worker():
            failed_ref = await fail_host.start(failed_request)
            with pytest.raises(TemporalHostError) as failed:
                await asyncio.wait_for(fail_host.result(failed_request, failed_ref), 30)
            assert failed.value.code == "ingestion_uncertain"
            failed_history = await client.get_workflow_handle(failed_ref.workflow_id,
                                                run_id=failed_ref.run_id).fetch_history()
            assert "SENTINEL_PRIVATE_PROVIDER_DETAIL" not in str(failed_history.to_json())
        assert failing.calls == 1
        assert budget.status("owner", fail_account).remaining_microusd == 0
        assert budget.status("owner", account).remaining_microusd == 4


@pytest.mark.asyncio
async def test_real_sdk_terminal_validation_failures():
    if os.getenv("TEMPORAL_EXECUTION_INTEGRATION") != "1":
        pytest.skip("explicit disposable Temporal integration disabled")
    address = os.getenv("TEMPORAL_TEST_ADDRESS")
    if address != "127.0.0.1:17233":
        pytest.fail("approved loopback Temporal address required")
    client = await Client.connect(address)
    queue = "mirofish-validation-" + uuid4().hex

    @activity.defn(name=ACTIVITY_NAME)
    async def malformed_receipt(wire):
        return {"safe": True}

    valid = {"schema_version": 1, "principal": "owner",
             "display_graph_id": "graph_1", "source_revision": str(uuid4()),
             "operation_id": str(uuid4()), "account_id": str(uuid4()),
             "ontology_revision": str(uuid4()), "ceiling_microusd": 1,
             "request_fingerprint": "a" * 64}
    async with Worker(client, task_queue=queue, workflows=[SourceIngestionWorkflow],
                      activities=[malformed_receipt]):
        invalid = dict(valid, unexpected_field="benign")
        bad_input = await client.start_workflow(SourceIngestionWorkflow.run, invalid,
            id="mf-invalid-" + uuid4().hex, task_queue=queue)
        with pytest.raises(Exception):
            await asyncio.wait_for(bad_input.result(), 20)
        assert (await bad_input.describe()).status.name.lower() == "failed"
        input_history = await bad_input.fetch_history()
        assert "invalid_request" in _history_text(json.loads(input_history.to_json()))

        bad_receipt = await client.start_workflow(SourceIngestionWorkflow.run, valid,
            id="mf-invalid-receipt-" + uuid4().hex, task_queue=queue)
        with pytest.raises(Exception):
            await asyncio.wait_for(bad_receipt.result(), 20)
        assert (await bad_receipt.describe()).status.name.lower() == "failed"
        receipt_history = await bad_receipt.fetch_history()
        assert "invalid_receipt" in _history_text(json.loads(receipt_history.to_json()))

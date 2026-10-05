"""Pure tagged receipt semantics and three-purpose shared real-PG admission."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4
import pytest
from mirofish_execution.native_launch_contracts import NativeBudgetReceipt,LaunchAuthorityError,native_budget_fingerprint
from mirofish_execution.native_run_contracts import NativeRunReceipt,NativeChildIdentity
from mirofish_execution.native_run_store import NativeRunStore
from mirofish_execution.budget import BudgetDenied,BudgetConflict,BudgetUncertain,BudgetBusy,ReservationState
from mirofish_execution.preparation_contracts import PreparedBudgetReceipt
from mirofish_knowledge.operations import CompletionReceipt
from test_native_launch_store import factory,ready_host,launch_host,declaration


def test_native_receipt_domain_is_distinct_and_exact():
    operation,attempt=uuid4(),uuid4();launch='a'*64
    native=NativeRunReceipt(operation,uuid4(),uuid4(),'b'*64,'completed','c'*64)
    receipt=NativeBudgetReceipt(operation,attempt,native_budget_fingerprint(launch),launch,native)
    assert NativeBudgetReceipt.from_wire(receipt.json_value())==receipt
    assert receipt.kind=='native_run_budget_v1'
    prepared=PreparedBudgetReceipt(operation,attempt,'d'*64,'e'*64)
    with pytest.raises(LaunchAuthorityError):NativeBudgetReceipt.from_wire(prepared.json_value())
    for change in ({'kind':'prepared_budget_v1'},{'operation_id':str(uuid4())},{'fingerprint':'f'*64},{'bill_microusd':1}):
        with pytest.raises(LaunchAuthorityError):NativeBudgetReceipt.from_wire(dict(receipt.json_value(),**change))
    assert native_budget_fingerprint('a'*64)!=native_budget_fingerprint('b'*64)


@pytest.mark.postgres
def test_three_domain_cap_race_and_same_uuid_domain_isolation(factory,tmp_path):
    prep,scope,plan=ready_host(factory,tmp_path,cap=9)
    host=launch_host(prep,factory);dto=host.plan(declaration(plan));row=host.store.get('owner',dto['request']['run_id'])
    ledger=host.budget
    def reserve_native():return ledger.reserve_native('owner',prep.account_id,scope,row.request,row.launch_sha256,4)
    def reserve_prepared():return ledger.reserve_prepared('owner',prep.account_id,scope,uuid4(),'d'*64,4)
    def reserve_ingest():return ledger.reserve('owner',prep.account_id,scope,uuid4(),'e'*64,4,())
    def try_reserve(fn):
        try:return fn()
        except BudgetDenied:return None
    with ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(try_reserve,[reserve_native,reserve_prepared,reserve_ingest]))
    assert sum(row is not None for row in rows)==1
    assert ledger.status('owner',prep.account_id).accounted_ceiling_microusd==4
    assert ledger.status('owner',prep.account_id).remaining_microusd==1
    winner=next(row for row in rows if row is not None)
    if winner.operation_id==row.run_id:
        with pytest.raises(BudgetConflict):ledger.reserve_prepared('owner',prep.account_id,scope,row.run_id,row.launch_sha256,4)
        with pytest.raises(BudgetConflict):ledger.reserve('owner',prep.account_id,scope,row.run_id,winner.fingerprint,4,())


def authoritative_receipt(factory,request):
    """Store-only fixture: typed trusted observation, no engine claim."""
    store=NativeRunStore(factory);store.register(request);owner=uuid4()
    claim=store.claim_start('owner',request.run_id,owner,60)
    child=NativeChildIdentity(uuid4(),1,'d'*64)
    store.attach('owner',request.run_id,claim.attempt_id,owner,child,60)
    receipt=NativeRunReceipt(request.run_id,claim.attempt_id,child.instance_id,request.fingerprint,'completed','e'*64)
    store.settle('owner',request.run_id,claim.attempt_id,owner,receipt)
    return receipt


@pytest.mark.postgres
def test_native_full_ceiling_settlement_requires_exact_pg_proof_and_rejects_injection(factory,tmp_path):
    prep,scope,plan=ready_host(factory,tmp_path)
    host=launch_host(prep,factory);dto=host.plan(declaration(plan));row=host.store.get('owner',dto['request']['run_id'])
    ledger=host.budget;res=ledger.reserve_native('owner',prep.account_id,scope,row.request,row.launch_sha256,4)
    ledger.start('owner',prep.account_id,row.run_id,res.attempt_id)
    fake=NativeRunReceipt(row.run_id,uuid4(),uuid4(),row.request.fingerprint,'completed','e'*64)
    with pytest.raises(BudgetUncertain):ledger.settle_native('owner',prep.account_id,scope,row.request,res.attempt_id,row.launch_sha256,fake)
    with pytest.raises(BudgetUncertain):ledger.settle_prepared('owner',prep.account_id,scope,row.run_id,res.attempt_id,row.launch_sha256,'e'*64)
    graph_receipt=CompletionReceipt(scope.group_id,scope.episode_uuid(row.run_id),res.fingerprint,())
    with pytest.raises(BudgetUncertain):ledger.settle('owner',prep.account_id,scope,row.run_id,res.attempt_id,res.fingerprint,(),graph_receipt)
    ledger.mark_uncertain('owner',prep.account_id,row.run_id,res.attempt_id,'dispatch_uncertain')
    with pytest.raises(BudgetBusy):ledger.release_undispatched('owner',prep.account_id,row.run_id,res.attempt_id)
    native=authoritative_receipt(factory,row.request)
    settled=ledger.settle_native('owner',prep.account_id,scope,row.request,res.attempt_id,row.launch_sha256,native)
    assert type(settled.receipt) is NativeBudgetReceipt and settled.state==ReservationState.settled
    assert ledger.status('owner',prep.account_id).accounted_ceiling_microusd==8
    assert ledger.status('owner',prep.account_id).actual_usage_microusd is None
    assert ledger.settle_native('owner',prep.account_id,scope,row.request,res.attempt_id,row.launch_sha256,native)==settled
    with pytest.raises(BudgetUncertain):ledger.settle_native('owner',prep.account_id,scope,row.request,uuid4(),row.launch_sha256,native)
    with pytest.raises(BudgetUncertain):ledger.settle_native('owner',prep.account_id,scope,row.request,res.attempt_id,row.launch_sha256,replace(native,evidence_sha256='f'*64))

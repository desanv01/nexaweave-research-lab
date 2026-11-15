"""Fifth shared allowance purpose and typed immutable proof negatives."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from uuid import UUID,NAMESPACE_URL,uuid4,uuid5
import pytest


def proof():
    from nexaweave_execution.followup_contracts import budget_fingerprint,digest
    turn=str(UUID(int=80)); plan='a'*64
    receipt=dict(schema_version=1,turn_id=turn,plan_sha256=plan,
        parent_report_id=str(UUID(int=70)),parent_report_plan_sha256='b'*64,
        parent_report_receipt_sha256='c'*64,context_sha256='d'*64,
        history_head_sha256='e'*64,ordinal=1,manifest_sha256='f'*64,
        output_language='en',reference_integrity='validated',semantic_support_status='not_reviewed')
    return dict(kind='connected_followup_budget_v1',operation_id=turn,
        attempt_id=str(UUID(int=90)),fingerprint=budget_fingerprint(plan),
        plan_sha256=plan,followup_receipt=receipt,followup_receipt_sha256=digest(receipt))


def test_fifth_purpose_episode_and_detached_typed_receipt():
    from nexaweave_execution.followup_contracts import FollowupBudgetReceipt,budget_episode
    from nexaweave_execution.report_contracts import budget_episode as report_episode
    wire=proof(); saved=FollowupBudgetReceipt.from_wire(wire)
    original=deepcopy(wire)
    wire['followup_receipt']['context_sha256']='0'*64
    saved.followup_receipt['ordinal']=99
    exported=saved.json_value(); exported['followup_receipt']['ordinal']=2
    assert saved.json_value()==original
    group='mf1_'+'1'*64; operation=UUID(original['operation_id'])
    assert budget_episode(group,operation)==uuid5(NAMESPACE_URL,
        'mirofish:connected-followup-budget:v1:'+group+':'+str(operation))
    assert budget_episode(group,operation)!=report_episode(group,operation)


@pytest.mark.parametrize('change',[
    lambda value:value.update(kind='connected_report_budget_v1'),
    lambda value:value.update(fingerprint='0'*64),
    lambda value:value.update(operation_id=str(UUID(int=81))),
    lambda value:value['followup_receipt'].update(ordinal=True),
    lambda value:value['followup_receipt'].update(semantic_support_status='reviewed'),
    lambda value:value['followup_receipt'].update(extra=True),
])
def test_fifth_purpose_refuses_mutated_proof(change):
    from nexaweave_execution.followup_contracts import FollowupBudgetReceipt,FollowupError,digest
    value=proof(); change(value)
    value['followup_receipt_sha256']=digest(value['followup_receipt'])
    with pytest.raises(FollowupError): FollowupBudgetReceipt.from_wire(value)


@pytest.mark.postgres
def test_actual_shared_account_cap_covers_followup_and_prior_purposes(tmp_path):
    import os
    if os.environ.get('PROJECT_STORE_POSTGRES_INTEGRATION')!='1':
        pytest.fail('selected follow-up budget test requires guarded disposable PostgreSQL')
    from test_native_launch_store import factory as accepted_factory
    from test_followup_store import seed
    from nexaweave_execution.budget import BudgetDenied, BudgetLedger, ReservationState
    from nexaweave_execution.followup_contracts import FollowupError
    factory=accepted_factory.__wrapped__()
    store,prep,scope,parent,plan=seed(factory,tmp_path,cap=24)
    before=prep.budget.status('owner',prep.account_id).remaining_microusd
    assert before>=4
    # A separate prepared-purpose reservation consumes the SAME account,
    # leaving less than this follow-up's immutable four-microunit ceiling.
    if before>3:
        prep.budget.reserve_prepared('owner',prep.account_id,scope,uuid4(),'d'*64,before-3)
    first=plan()
    with pytest.raises(FollowupError) as error:
        store.queue('owner',first.turn_id,first.plan_sha256,scope,prep.account_id)
    assert error.value.code=='budget_denied'

    race_path = tmp_path / 'cross-purpose-race'
    race_path.mkdir()
    store, prep, scope, parent, plan = seed(factory, race_path, cap=24)
    account = prep.account_id
    remaining = prep.budget.status('owner', account).remaining_microusd
    assert remaining >= 4
    if remaining > 4:
        prep.budget.reserve_prepared('owner', account, scope, uuid4(), 'e'*64, remaining - 4)
    baseline = prep.budget.status('owner', account)
    assert baseline.remaining_microusd == 4 and baseline.actual_usage_microusd is None
    turn, preparation_id = plan(), uuid4()
    history = store.latest('owner', parent.report_id, parent.plan_sha256)

    def active_turn(conn):
        return conn.execute('SELECT active_turn_id FROM mf_followup.histories WHERE principal=%s AND report_id=%s',
                            ('owner', parent.report_id)).fetchone()[0]

    with factory() as conn:
        assert active_turn(conn) is None
        assert BudgetLedger._row(conn, account, turn.turn_id) is None
        assert BudgetLedger._row(conn, account, preparation_id) is None

    barrier = Barrier(2, timeout=5)

    def followup_queue():
        barrier.wait()
        try:
            return store.queue('owner', turn.turn_id, turn.plan_sha256, scope, account)
        except FollowupError as denied:
            if denied.code != 'budget_denied':
                raise
            return None

    def preparation_reserve():
        barrier.wait()
        try:
            return prep.budget.reserve_prepared('owner', account, scope, preparation_id, 'f'*64, 4)
        except BudgetDenied:
            return None

    # Each contender opens its own transaction through the existing fixture factory.
    with ThreadPoolExecutor(max_workers=2) as pool:
        queued_future = pool.submit(followup_queue)
        prepared_future = pool.submit(preparation_reserve)
        queued = queued_future.result(timeout=15)
        prepared = prepared_future.result(timeout=15)

    assert (queued is None) != (prepared is None)
    persisted = store.get('owner', turn.turn_id, turn.plan_sha256)
    assert store.latest('owner', parent.report_id, parent.plan_sha256) == history
    with factory() as conn:
        followup_hold = BudgetLedger._row(conn, account, turn.turn_id)
        preparation_hold = BudgetLedger._row(conn, account, preparation_id)
        if queued is not None:
            owned, claimed = queued
            assert claimed and owned == persisted and owned.state == 'queued' and owned.dispatch_claimed
            assert active_turn(conn) == turn.turn_id and preparation_hold is None
            assert followup_hold.state == ReservationState.started
            assert followup_hold.attempt_id == owned.attempt_id and followup_hold.ceiling_microusd == 4
        else:
            assert persisted == turn and not persisted.dispatch_claimed and persisted.attempt_id is None
            assert active_turn(conn) is None and followup_hold is None
            assert preparation_hold == prepared and prepared.state == ReservationState.reserved
            assert prepared.ceiling_microusd == 4
    final = prep.budget.status('owner', account)
    assert final.cap_microusd == baseline.cap_microusd
    assert final.accounted_ceiling_microusd == baseline.accounted_ceiling_microusd
    assert final.reserved_microusd == baseline.reserved_microusd + 4
    assert final.uncertain_microusd == baseline.uncertain_microusd
    assert final.remaining_microusd == 0 and final.actual_usage_microusd is None

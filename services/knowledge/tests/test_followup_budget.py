"""Fifth shared allowance purpose and typed immutable proof negatives."""
from copy import deepcopy
from uuid import UUID,NAMESPACE_URL,uuid5
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
    from nexaweave_execution.followup_contracts import FollowupError
    factory=accepted_factory.__wrapped__()
    store,prep,scope,parent,plan=seed(factory,tmp_path,cap=24)
    before=prep.budget.status('owner',prep.account_id).remaining_microusd
    assert before>=4
    # A separate prepared-purpose reservation consumes the SAME account,
    # leaving less than this follow-up's immutable four-microunit ceiling.
    if before>3:
        from uuid import uuid4
        prep.budget.reserve_prepared('owner',prep.account_id,scope,uuid4(),'d'*64,before-3)
    first=plan()
    with pytest.raises(FollowupError) as error:
        store.queue('owner',first.turn_id,first.plan_sha256,scope,prep.account_id)
    assert error.value.code=='budget_denied'

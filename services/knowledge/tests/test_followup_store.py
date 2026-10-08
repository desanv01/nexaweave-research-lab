"""Guarded actual PostgreSQL follow-up journal and shared-account races."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import os
from uuid import UUID,uuid4
import pytest

pytestmark=pytest.mark.postgres


@pytest.fixture(scope='module')
def factory():
    if os.environ.get('PROJECT_STORE_POSTGRES_INTEGRATION')!='1':
        pytest.fail('selected follow-up store requires guarded disposable PostgreSQL')
    from test_native_launch_store import factory as accepted_factory
    return accepted_factory.__wrapped__()


def seed(factory,tmp_path,*,cap=24):
    """SQL authority fixture only; never claims a generated narrative body."""
    from test_report_store import journal_fixture
    from nexaweave_execution.report_contracts import digest,BASE_NAMES
    from nexaweave_execution.followup_contracts import DEFAULT_LIMITS
    from nexaweave_execution.followup_store import FollowupStore,migrate
    from psycopg.types.json import Jsonb
    report_store,prep,scope,review=journal_fixture(factory,tmp_path,cap=cap)
    parent=review()
    names=BASE_NAMES+['section_01.md']
    raw=b'X'
    manifest=dict(schema_version=1,files=[dict(name=name,size=1,sha256=hashlib.sha256(raw).hexdigest()) for name in names])
    identity=parent.frozen['identity']
    receipt=dict(schema_version=1,report_id=str(parent.report_id),plan_sha256=parent.plan_sha256,
        context_sha256=identity['context_sha256'],manifest_sha256=digest(manifest),
        output_language='en',reference_integrity='validated',semantic_support_status='not_reviewed')
    with factory() as conn:
        migrate(conn)
        conn.execute("UPDATE mf_report.plans SET state='completed',dispatch_claimed=true,owner_claimed=true,first_possible_request=true,attempt_id=%s,receipt=%s,manifest=%s,progress=%s,cleanup=%s WHERE report_id=%s",
            (uuid4(),Jsonb(receipt),Jsonb(manifest),Jsonb(dict(stage='completed',percent=100,completed_sections=1,total_sections=1)),
             Jsonb(dict(known=True,pending=False,owner_thread_alive=False)),parent.report_id))
    parent=report_store.get('owner',parent.report_id)
    store=FollowupStore(factory)
    def plan(*,turn_id=None,expected=None):
        history=store.latest('owner',parent.report_id,parent.plan_sha256)
        if expected is None: expected=history['head_sha256']
        report=dict(report_id=str(parent.report_id),plan_sha256=parent.plan_sha256,
            receipt_sha256=digest(parent.receipt),manifest_sha256=digest(parent.manifest),
            full_report_sha256=manifest['files'][2]['sha256'])
        native=identity['binding']
        binding=dict(display_graph_id=native['display_graph_id'],principal='owner',scope=native['scope'],
            report=report,native_binding=deepcopy(native))
        provenance=dict(file_sha256=report['full_report_sha256'],prefix_sha256=report['full_report_sha256'],
            prefix_characters=1,total_characters=1,truncated=False)
        current=dict(schema_version=1,turn_id=str(turn_id or uuid4()),binding=binding,
            options=dict(question='What is supported?',output_language='en',expected_history_sha256=expected),
            history=history,report_context=provenance,context_sha256=digest(parent.frozen['context']),
            source_projection_sha256=digest(parent.frozen['context']['graph']),
            model_label='journal-only',limits=dict(DEFAULT_LIMITS),ceiling_microusd=4)
        return store.put('owner',current,parent.frozen['context'],
            dict(account_id=str(prep.account_id),factory_sha256='f'*64))
    return store,prep,scope,parent,plan


def wire(row):
    return dict(schema_version=1,turn_id=str(row.turn_id),plan_sha256=row.plan_sha256,
                attempt_id=str(row.attempt_id))


def test_actual_catalog_idempotence_drift_and_quiescent_rollback(factory):
    from nexaweave_execution.followup_contracts import FollowupError
    from nexaweave_execution.followup_store import _catalog,migrate,rollback

    def snapshot(conn):
        return dict(catalog=_catalog(conn),
            migrations=conn.execute('SELECT version,checksum,schema_checksum FROM mf_followup.schema_migrations ORDER BY version').fetchall(),
            histories=conn.execute('SELECT report_id,md5(row_to_json(h)::text) FROM mf_followup.histories h ORDER BY report_id').fetchall(),
            turns=conn.execute('SELECT turn_id,md5(row_to_json(t)::text) FROM mf_followup.turns t ORDER BY turn_id').fetchall())

    with factory() as conn:
        migrate(conn)
        before=snapshot(conn)
        namespaces=conn.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'mf_%' ORDER BY nspname").fetchall()
        migrate(conn)
        assert snapshot(conn)==before
        with conn.transaction(force_rollback=True):
            conn.execute('ALTER TABLE mf_followup.turns ADD COLUMN foreign_field text')
            with pytest.raises(FollowupError) as error:
                migrate(conn)
            assert error.value.code=='followup_unavailable'
        with conn.transaction(force_rollback=True):
            conn.execute('ALTER TABLE mf_followup.histories ALTER COLUMN total_completed SET DEFAULT 1')
            with pytest.raises(FollowupError) as error:
                migrate(conn)
            assert error.value.code=='followup_unavailable'
        with conn.transaction(force_rollback=True):
            conn.execute('DROP INDEX mf_followup.followup_principal_report')
            with pytest.raises(FollowupError) as error:
                migrate(conn)
            assert error.value.code=='followup_unavailable'
        with conn.transaction(force_rollback=True):
            conn.execute("UPDATE mf_followup.schema_migrations SET checksum=repeat('0',64)")
            with pytest.raises(FollowupError) as error:
                migrate(conn)
            assert error.value.code=='followup_unavailable'
        assert snapshot(conn)==before

        with conn.transaction(force_rollback=True):
            conn.execute("UPDATE mf_followup.turns SET state='failed' WHERE state IN ('queued','generating','uncertain')")
            quiescent=snapshot(conn)
            rollback(conn)
            assert conn.execute("SELECT to_regnamespace('mf_followup'),to_regclass('mf_followup.histories'),to_regclass('mf_followup.turns'),to_regclass('mf_followup.schema_migrations')").fetchone()==(None,None,None,None)
            assert conn.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'mf_%' ORDER BY nspname").fetchall()==[row for row in namespaces if row!=('mf_followup',)]
            migrate(conn)
            rebuilt=snapshot(conn)
            assert rebuilt['catalog']==quiescent['catalog']
            assert rebuilt['migrations']==quiescent['migrations']
            assert rebuilt['histories']==[] and rebuilt['turns']==[]
        assert snapshot(conn)==before
        assert conn.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'mf_%' ORDER BY nspname").fetchall()==namespaces


def test_actual_one_active_claim_same_id_replay_and_unknown_cleanup(factory,tmp_path):
    from nexaweave_execution.followup_contracts import FollowupError
    from nexaweave_execution.budget import BudgetLedger,ReservationState
    store,prep,scope,parent,plan=seed(factory,tmp_path)
    row=plan()
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies=list(pool.map(lambda _:store.queue('owner',row.turn_id,row.plan_sha256,scope,prep.account_id),range(2)))
    assert sum(claimed for _,claimed in replies)==1
    queued=store.get('owner',row.turn_id)
    assert queued.state=='queued' and queued.dispatch_claimed
    from nexaweave_execution.followup_store import rollback
    with factory() as conn:
        with pytest.raises(FollowupError) as rollback_error:
            rollback(conn)
    assert rollback_error.value.code=='busy'
    other=plan()
    with pytest.raises(FollowupError) as error:
        store.queue('owner',other.turn_id,other.plan_sha256,scope,prep.account_id)
    assert error.value.code=='followup_active'
    claimed=store.claim('owner',wire(queued))
    store.first_request('owner',wire(claimed))
    uncertain=store.finish('owner',wire(claimed),state='uncertain',error_code='followup_uncertain',
        cleanup=dict(known=False,pending=None,owner_thread_alive=None))
    assert uncertain.state=='uncertain'
    with factory() as conn:
        budget=BudgetLedger._row(conn,prep.account_id,row.turn_id)
    assert budget.state==ReservationState.uncertain and budget.receipt is None
    with pytest.raises(FollowupError) as error:
        store.queue('owner',other.turn_id,other.plan_sha256,scope,prep.account_id)
    assert error.value.code=='followup_active'


def test_actual_closed_no_call_failure_releases_only_active_claim(factory,tmp_path):
    from nexaweave_execution.budget import BudgetLedger,ReservationState
    store,prep,scope,parent,plan=seed(factory,tmp_path)
    row=plan(); queued,_=store.queue('owner',row.turn_id,row.plan_sha256,scope,prep.account_id)
    claimed=store.claim('owner',wire(queued))
    failed=store.finish('owner',wire(claimed),state='failed',error_code='followup_failed',
        cleanup=dict(known=True,pending=False,owner_thread_alive=False))
    assert failed.state=='failed'
    with factory() as conn:
        assert BudgetLedger._row(conn,prep.account_id,row.turn_id).state==ReservationState.released
    other=plan(); next_row,dispatched=store.queue('owner',other.turn_id,other.plan_sha256,scope,prep.account_id)
    assert dispatched and next_row.state=='queued'


def test_actual_completed_chain_budget_and_older_turn_stays_readable(factory,tmp_path):
    from nexaweave_execution.followup_contracts import FILE_NAMES,digest
    from nexaweave_execution.budget import BudgetLedger,ReservationState
    store,prep,scope,parent,plan=seed(factory,tmp_path)
    row=plan(); queued,_=store.queue('owner',row.turn_id,row.plan_sha256,scope,prep.account_id)
    claimed=store.claim('owner',wire(queued)); store.first_request('owner',wire(claimed))
    answer='Bound answer [['+row.frozen['identity']['binding']['native_binding']['reference_keys'][0]+']]'
    raw=answer.encode()
    manifest=dict(schema_version=1,files=[dict(name=name,size=1,sha256=hashlib.sha256(b'X').hexdigest())
        for name in FILE_NAMES])
    manifest['files'][1]=dict(name='answer.md',size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    identity=row.frozen['identity']; report=identity['binding']['report']
    receipt=dict(schema_version=1,turn_id=str(row.turn_id),plan_sha256=row.plan_sha256,
        parent_report_id=report['report_id'],parent_report_plan_sha256=report['plan_sha256'],
        parent_report_receipt_sha256=report['receipt_sha256'],context_sha256=identity['context_sha256'],
        history_head_sha256=identity['history']['head_sha256'],ordinal=1,
        manifest_sha256=digest(manifest),output_language='en',reference_integrity='validated',
        semantic_support_status='not_reviewed')
    completed=store.finish('owner',wire(claimed),state='completed',
        cleanup=dict(known=True,pending=False,owner_thread_alive=False),
        receipt=receipt,manifest=manifest,answer=answer,verify_output=lambda:True)
    assert completed.state=='completed' and completed.published_head_sha256
    page=store.history('owner',parent.report_id,parent.plan_sha256)
    assert page['total_completed']==1 and page['pairs'][0]['answer_prefix']==answer
    with factory() as conn:
        reservation=BudgetLedger._row(conn,prep.account_id,row.turn_id)
    assert reservation.state==ReservationState.settled
    assert reservation.receipt.followup_receipt_sha256==digest(receipt)
    next_row=plan()
    assert next_row.frozen['identity']['history']['head_sha256']==completed.published_head_sha256
    assert store.get('owner',row.turn_id,row.plan_sha256)==completed

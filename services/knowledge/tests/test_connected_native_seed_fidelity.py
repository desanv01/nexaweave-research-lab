"""Main-owned fresh rich publication -> actual PGTemporal/OASIS seed fidelity."""
import asyncio
from copy import deepcopy
import json
import sqlite3
from uuid import uuid4

import pytest

pytestmark=[pytest.mark.postgres,pytest.mark.native_seed_temporal]

SEEDS=[('Person','相同技术 seed\n<script>literal</script> 🚀'),
       ('Organization','Organisation seed B'),
       ('Person','相同技术 seed\n<script>literal</script> 🚀'),
       ('Organization','Organisation seed D'),('Person','Final same-actor seed E')]


@pytest.fixture(scope='module')
def factory():
    from test_native_launch_store import factory as actual_factory
    return actual_factory.__wrapped__()


@pytest.mark.asyncio
async def test_generated_interleaved_repeated_seeds_preserved_with_actual_receipt_and_no_replay(factory,tmp_path,monkeypatch):
    from test_connected_preparation_native_launch import RichConnectedChat
    from test_preparation_store import real_host,request,reference as prep_ref
    from test_native_launch_store import migrate_all,launch_host,declaration
    from test_native_launch_api import reference
    from test_temporal_connected_launch import loopback_client,temporal_bridge
    from app.services.preparation_client import PreparationError, validate_payload
    from nexaweave_execution.temporal_preparation_host import TemporalPreparationHost
    from nexaweave_execution.temporal_native_host import TemporalNativeHost,NativeWorkflowRef
    from nexaweave_execution.native_run_store import NativeRunStore
    from nexaweave_execution.native_launch_store import NativeLaunchStore
    from nexaweave_execution.native_launch_contracts import NativeBudgetReceipt

    class SeedChat(RichConnectedChat):
        def create(self,**kwargs):
            response=super().create(**kwargs)
            if 'hot_topics' in kwargs['messages'][-1]['content']:
                response.choices[0].message.content=json.dumps(dict(hot_topics=['技术'],
                    narrative_direction='Offline seed fidelity',initial_posts=[
                        dict(poster_type=kind,content=content) for kind,content in SEEDS]),ensure_ascii=False)
            return response

    monkeypatch.chdir(tmp_path)
    migrate_all(factory)
    temporal=await loopback_client()
    chat=SeedChat()
    prep,_,retained,_,_,_=real_host(factory,tmp_path,cap=20,chat=chat)
    old_alias=tmp_path/'seed-bridge-old-config.json'
    original_freeze=prep._freeze_seed_bridge
    captured=[]

    def retain_old_config(root,opts):
        path=root/'simulation_config.json'
        old_alias.hardlink_to(path)
        assert old_alias.samefile(path)
        captured.append((path,old_alias.read_bytes()))
        original_freeze(root,opts)

    monkeypatch.setattr(prep,'_freeze_seed_bridge',retain_old_config)
    prep_temporal=TemporalPreparationHost(client=temporal,task_queue='mf-seed-prep-'+uuid4().hex,
        trusted_host=prep,allow_dispatch=lambda:True)
    loop=asyncio.get_running_loop();prep.scheduler=prep_temporal.scheduler_for(loop)
    host=launch_host(prep,factory);created=[]
    def construct(native_request):
        supervisor=host.supervisor_factory(native_request);created.append(supervisor);return supervisor
    native=TemporalNativeHost(client=temporal,task_queue='mf-seed-native-'+uuid4().hex,
        trusted_principal='owner',supervisor_factory=construct,allow_dispatch=lambda:True)
    host.attach_temporal(native,temporal_bridge(native,loop));row=None
    try:
        async with prep_temporal.worker(),native.worker():
            payload=request(retained)
            payload['options'].update(types=['Person','Organization'],max_agents=2,max_rounds=1,
                                      twitter_to_reddit_seed_index=2)
            for bad_options in ({'twitter_to_reddit_seed_index': True}, {'twitter_to_reddit_seed_index': -1},
                                {'twitter_to_reddit_seed_index': 2.0}, {'platforms': ['reddit']}):
                bad=deepcopy(payload)
                bad['options'].update(bad_options)
                with pytest.raises(PreparationError,match='^invalid_request$'):
                    validate_payload('plan',bad)
            planned=await asyncio.to_thread(prep.plan,payload)
            assert planned['options']==payload['options']
            await asyncio.to_thread(prep.start,prep_ref(planned))
            prep_row=prep.store.get('owner',planned['operation_id'])
            await asyncio.wait_for(temporal.get_workflow_handle(prep_row.dispatch.workflow_id).result(),60)
            ready=await asyncio.to_thread(prep.status,prep_ref(planned))
            assert ready['state']=='ready' and ready['simulation_executed'] is False
            assert ready['plan_sha256']==planned['plan_sha256'] and ready['options']==planned['options']
            root=prep.root/ready['receipt']['simulation_id']
            inputs={file['name']:(root/file['name']).read_bytes() for file in ready['receipt']['files']}
            config=json.loads(inputs['simulation_config.json'])
            assert type(config['twitter_to_reddit_seed_index']) is int and config['twitter_to_reddit_seed_index']==2
            assert len(captured)==1
            replaced,old_bytes=captured[0]
            assert not old_alias.samefile(replaced) and old_alias.read_bytes()==old_bytes
            assert replaced.read_bytes()==inputs['simulation_config.json']
            old_config=json.loads(old_bytes)
            assert 'twitter_to_reddit_seed_index' not in old_config
            assert config=={**old_config,'twitter_to_reddit_seed_index':2}
            posts=config['event_config']['initial_posts']
            assert [(p['poster_type'],p['content']) for p in posts]==SEEDS
            ids=[p['poster_agent_id'] for p in posts]
            assert ids[0]==ids[2]==ids[4] and ids[1]==ids[3] and ids[0]!=ids[1]
            expected=list(zip(ids,[content for _,content in SEEDS]))
            dto=await asyncio.to_thread(host.plan,declaration(planned))
            assert not created and not (root/'.native_prepared_start_claim').exists()
            await asyncio.to_thread(host.start,reference(dto))
            row=host.store.get('owner',dto['request']['run_id'])
            receipt=await asyncio.wait_for(native.result(row.request,NativeWorkflowRef(**row.workflow)),180)
            assert receipt.outcome=='completed'
            assert NativeRunStore(factory).get('owner',row.run_id).receipt==receipt
            final=await asyncio.to_thread(host.status,reference(dto))
            assert final['state']=='completed' and final['receipt']==receipt.to_wire()
            assert NativeLaunchStore(factory).get('owner',row.run_id).receipt==receipt.to_wire()
            assert row.request.artifact_sha256==ready['receipt']['artifact_sha256']
            with factory() as conn:
                reservation=host.budget._row(conn,host.account_id,row.run_id)
                budget_ops=conn.execute('SELECT operation_id FROM mf_execution.reservations WHERE account_id=%s',
                                        (host.account_id,)).fetchall()
            assert set(budget_ops)=={(prep_row.operation_id,),(row.run_id,)}
            assert type(reservation.receipt) is NativeBudgetReceipt and reservation.receipt.native_receipt==receipt
            assert host.budget.status('owner',host.account_id).accounted_ceiling_microusd==8
            twitter_posts=twitter_trace=None
            for platform in ('twitter','reddit'):
                path=root/(platform+'_simulation.db')
                assert path.is_file()
                platform_seeds=expected if platform=='twitter' else [*expected,expected[2]]
                count=len(platform_seeds)
                with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
                    rows=db.execute('SELECT post_id,user_id,content,created_at FROM post ORDER BY post_id').fetchall()
                    assert [(r[1],r[2]) for r in rows[:count]]==platform_seeds
                    assert len(rows)>count
                    assert all(r[2].startswith('Scripted connected native post ') for r in rows[count:])
                    if platform=='twitter':
                        # The native post schema retains clock values in TEXT.
                        assert {str(r[3]) for r in rows[:count]}=={'0'} and {str(r[3]) for r in rows[count:]}=={'1'}
                    trace=db.execute("SELECT rowid,user_id,info FROM trace WHERE action='create_post' ORDER BY rowid").fetchall()
                    actual=[(actor,json.loads(info)['post_id'],json.loads(info)['content']) for _,actor,info in trace]
                    assert actual==[(r[1],r[0],r[2]) for r in rows]
                records=[json.loads(line) for line in (root/platform/'actions.jsonl').read_text(encoding='utf-8').splitlines()]
                seeds=[r for r in records if r.get('round')==0 and r.get('action_type')=='CREATE_POST']
                assert len(seeds)==count and all(r['success'] is True for r in seeds)
                assert [(r['agent_id'],r['action_args']['content']) for r in seeds]==platform_seeds
                assert [json.loads(r['result'])['post_id'] for r in seeds]==[r[0] for r in rows[:count]]
                assert [r['actions_count'] for r in records if r.get('round')==0 and r.get('event_type')=='round_end']==[count]
                lineage=[r['action_args']['seed_transfer'] for r in seeds if 'seed_transfer' in r['action_args']]
                if platform=='twitter':
                    twitter_posts,twitter_trace=rows,trace
                    assert not lineage and rows[0][0]!=rows[2][0] and trace[0][0]!=trace[2][0]
                else:
                    assert lineage==[dict(source_ordinal=2,twitter_post_id=twitter_posts[2][0],
                        twitter_trace_id=twitter_trace[2][0],reddit_post_id=rows[5][0],reddit_trace_id=trace[5][0])]
                    assert seeds[-1]['action_args']['seed_transfer']==lineage[0]
                later=[r for r in records if r.get('round',0)>0 and r.get('action_type')=='CREATE_POST']
                assert later and all(r['action_args']['content'] not in {c for _,c in SEEDS} for r in later)
            assert all((root/name).read_bytes()==data for name,data in inputs.items())
            assert (root/'.native_prepared_start_claim').is_file()
            local=await native.retry_cleanup(row.request)
            assert not local.cleanup_pending and not local.owner_thread_alive and len(created)==1
            outputs={name:(root/name).read_bytes() for platform in ('twitter','reddit')
                for name in (platform+'_simulation.db',platform+'/actions.jsonl')}
            recovered=await asyncio.to_thread(host.start,reference(dto))
            assert recovered['receipt']==receipt.to_wire() and len(created)==1
            assert all((root/name).read_bytes()==data for name,data in {**inputs,**outputs}.items())
            assert old_alias.read_bytes()==old_bytes
            with factory() as conn:
                assert set(conn.execute('SELECT operation_id FROM mf_execution.reservations WHERE account_id=%s',
                    (host.account_id,)).fetchall())==set(budget_ops)
    finally:
        for supervisor in created:assert await asyncio.to_thread(supervisor.close,20)
        if row is not None:
            local=await native.retry_cleanup(row.request)
            assert not local.cleanup_pending and not local.owner_thread_alive

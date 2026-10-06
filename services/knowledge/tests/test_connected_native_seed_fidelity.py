"""Main-owned fresh rich publication -> actual PGTemporal/OASIS seed fidelity."""
import asyncio
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
    from mirofish_execution.temporal_preparation_host import TemporalPreparationHost
    from mirofish_execution.temporal_native_host import TemporalNativeHost,NativeWorkflowRef
    from mirofish_execution.native_run_store import NativeRunStore
    from mirofish_execution.native_launch_store import NativeLaunchStore
    from mirofish_execution.native_launch_contracts import NativeBudgetReceipt

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
            payload['options'].update(types=['Person','Organization'],max_agents=2,max_rounds=1)
            planned=await asyncio.to_thread(prep.plan,payload)
            await asyncio.to_thread(prep.start,prep_ref(planned))
            prep_row=prep.store.get('owner',planned['operation_id'])
            await asyncio.wait_for(temporal.get_workflow_handle(prep_row.dispatch.workflow_id).result(),60)
            ready=await asyncio.to_thread(prep.status,prep_ref(planned))
            assert ready['state']=='ready' and ready['simulation_executed'] is False
            root=prep.root/ready['receipt']['simulation_id']
            inputs={file['name']:(root/file['name']).read_bytes() for file in ready['receipt']['files']}
            config=json.loads(inputs['simulation_config.json'])
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
            with factory() as conn:reservation=host.budget._row(conn,host.account_id,row.run_id)
            assert type(reservation.receipt) is NativeBudgetReceipt and reservation.receipt.native_receipt==receipt
            assert host.budget.status('owner',host.account_id).accounted_ceiling_microusd==8
            for platform in ('twitter','reddit'):
                path=root/(platform+'_simulation.db')
                assert path.is_file()
                with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
                    rows=db.execute('SELECT post_id,user_id,content,created_at FROM post ORDER BY post_id').fetchall()
                    assert [(r[1],r[2]) for r in rows[:5]]==expected
                    assert len(rows)>5
                    assert all(r[2].startswith('Scripted connected native post ') for r in rows[5:])
                    if platform=='twitter':
                        # The native post schema retains clock values in TEXT.
                        assert {str(r[3]) for r in rows[:5]}=={'0'} and {str(r[3]) for r in rows[5:]}=={'1'}
                    trace=db.execute("SELECT user_id,info FROM trace WHERE action='create_post' ORDER BY rowid").fetchall()
                    actual=[(actor,json.loads(info)['post_id'],json.loads(info)['content']) for actor,info in trace]
                    assert actual[:5]==[(r[1],r[0],r[2]) for r in rows[:5]]
                records=[json.loads(line) for line in (root/platform/'actions.jsonl').read_text(encoding='utf-8').splitlines()]
                seeds=[r for r in records if r.get('round')==0 and r.get('action_type')=='CREATE_POST']
                assert len(seeds)==5 and all(r['success'] is True for r in seeds)
                assert [(r['agent_id'],r['action_args']['content']) for r in seeds]==expected
                assert [json.loads(r['result'])['post_id'] for r in seeds]==[r[0] for r in rows[:5]]
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
    finally:
        for supervisor in created:assert await asyncio.to_thread(supervisor.close,20)
        if row is not None:
            local=await native.retry_cleanup(row.request)
            assert not local.cleanup_pending and not local.owner_thread_alive

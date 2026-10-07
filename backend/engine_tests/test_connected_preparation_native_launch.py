"""Actual new U07c publication → PG/Temporal → owned both-platform OASIS.

Scripted inherited rich persona/config and CAMEL responses, zero paid calls.
No replacement prepared files or unrelated experiment-manifest run.
"""
import asyncio
import json
import sqlite3
from types import SimpleNamespace
from uuid import uuid4
import pytest
from nexaweave_execution.temporal_preparation_host import TemporalPreparationHost
from nexaweave_execution.temporal_native_host import TemporalNativeHost,NativeWorkflowRef
from nexaweave_execution.native_run_store import NativeRunStore
from nexaweave_execution.native_launch_store import NativeLaunchStore
from nexaweave_execution.native_launch_contracts import NativeBudgetReceipt
from test_provider_neutral_preparation import ScriptedChat
from test_preparation_store import real_host,request,reference as preparation_reference
from test_native_launch_store import factory,migrate_all,launch_host,declaration
from test_native_launch_api import reference
from test_temporal_connected_launch import loopback_client,temporal_bridge

pytestmark=[pytest.mark.postgres,pytest.mark.native_launch_engine]


class RichConnectedChat(ScriptedChat):
    """Inherited generators still interpret/validate all fields and write files."""
    def create(self,**kwargs):
        reply=super().create(**kwargs)
        prompt=kwargs['messages'][-1]['content']
        if 'agent_configs' in prompt:
            payload={'agent_configs':[{'agent_id':index,'activity_level':1.0,'posts_per_hour':1.0,
                'comments_per_hour':0.0,'active_hours':list(range(24)),'response_delay_min':0,'response_delay_max':0,
                'sentiment_bias':0,'stance':'neutral','influence_weight':1.0} for index in range(2)]}
        elif 'total_simulation_hours' in prompt:
            payload={'total_simulation_hours':1,'minutes_per_round':60,'agents_per_hour_min':2,
                'agents_per_hour_max':2,'peak_hours':[0],'off_peak_hours':[],'morning_hours':[0],'work_hours':[0]}
        elif 'hot_topics' in prompt:
            payload={'hot_topics':['技术'],'narrative_direction':'offline connected discussion',
                     'initial_posts':[{'content':'Synthetic connected opening','poster_type':'Person'}]}
        else:
            payload={'bio':'技术观察者','persona':'Synthetic rich participant observes technology and social evidence.',
                     'age':32,'gender':'nonbinary','mbti':'INTJ','country':'MY'}
        reply.choices[0].message.content=json.dumps(payload,ensure_ascii=False)
        return reply


@pytest.mark.asyncio
async def test_new_inherited_ready_artifacts_run_actual_both_platforms_through_pg_temporal(factory,tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    migrate_all(factory)
    temporal=await loopback_client()
    chat=RichConnectedChat()
    prep,scope,retained,_,_,transport=real_host(factory,tmp_path,cap=20,chat=chat)
    prep_temporal=TemporalPreparationHost(client=temporal,task_queue='mf-connected-prep-'+uuid4().hex,
                                       trusted_host=prep,allow_dispatch=lambda:True)
    loop=asyncio.get_running_loop();prep.scheduler=prep_temporal.scheduler_for(loop)
    host=launch_host(prep,factory)
    created=[]
    def construct(native_request):
        supervisor=host.supervisor_factory(native_request);created.append(supervisor);return supervisor
    native=TemporalNativeHost(client=temporal,task_queue='mf-connected-oasis-'+uuid4().hex,
        trusted_principal='owner',supervisor_factory=construct,allow_dispatch=lambda:True)
    host.attach_temporal(native,temporal_bridge(native,loop))
    row=None
    try:
        async with prep_temporal.worker(),native.worker():
            payload=request(retained)
            payload['options'].update(types=['Person','Organization'],max_agents=2,max_rounds=1)
            planned=await asyncio.to_thread(prep.plan,payload)
            await asyncio.to_thread(prep.start,preparation_reference(planned))
            prep_row=prep.store.get('owner',planned['operation_id'])
            await asyncio.wait_for(temporal.get_workflow_handle(prep_row.dispatch.workflow_id).result(),60)
            ready=await asyncio.to_thread(prep.status,preparation_reference(planned))
            assert ready['state']=='ready' and len(ready['actors'])==2 and ready['simulation_executed'] is False
            root=prep.root/ready['receipt']['simulation_id']
            original={file['name']:(root/file['name']).read_bytes() for file in ready['receipt']['files']}
            profiles=json.loads(original['reddit_profiles.json'])
            assert len(profiles)==2 and all(profile['age']==32 and profile['country']=='MY' for profile in profiles)
            dto=await asyncio.to_thread(host.plan,declaration(planned))
            assert dto['preparation']['artifact_sha256']==ready['receipt']['artifact_sha256']
            assert not created and not (root/'.native_prepared_start_claim').exists()
            await asyncio.to_thread(host.start,reference(dto))
            row=host.store.get('owner',dto['request']['run_id'])
            receipt=await asyncio.wait_for(native.result(row.request,NativeWorkflowRef(**row.workflow)),180)
            assert receipt.outcome=='completed'
            actual=NativeRunStore(factory).get('owner',row.run_id)
            assert actual.receipt==receipt and actual.request==row.request
            final=await asyncio.to_thread(host.status,reference(dto))
            assert final['state']=='completed' and final['receipt']==receipt.to_wire()
            assert NativeLaunchStore(factory).get('owner',row.run_id).receipt==receipt.to_wire()
            with factory() as conn:reservation=host.budget._row(conn,host.account_id,row.run_id)
            assert type(reservation.receipt) is NativeBudgetReceipt and reservation.receipt.native_receipt==receipt
            assert host.budget.status('owner',host.account_id).accounted_ceiling_microusd==8
            for platform in ('twitter','reddit'):
                with sqlite3.connect(root/(platform+'_simulation.db')) as db:
                    assert db.execute('SELECT COUNT(*) FROM user').fetchone()[0]==2
                    posts=[value[0] for value in db.execute('SELECT content FROM post')]
                    assert any(post.startswith('Scripted connected native post ') for post in posts)
                lines=(root/platform/'actions.jsonl').read_text(encoding='utf-8').splitlines()
                assert lines and all(type(json.loads(line)) is dict for line in lines)
            assert all((root/name).read_bytes()==data for name,data in original.items())
            assert (root/'.native_prepared_start_claim').is_file()
            local=await native.retry_cleanup(row.request)
            assert not local.cleanup_pending and not local.owner_thread_alive
            recovered=await asyncio.to_thread(host.start,reference(dto))
            assert recovered['receipt']==receipt.to_wire() and len(created)==1
            assert all((root/name).read_bytes()==data for name,data in original.items())
    finally:
        for supervisor in created:assert await asyncio.to_thread(supervisor.close,20)
        if row is not None:
            local=await native.retry_cleanup(row.request)
            assert not local.cleanup_pending and not local.owner_thread_alive

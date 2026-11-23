"""Installed-package authority on explicitly guarded disposable PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from importlib.resources import files
import json
import os
import random
import struct
import threading
from types import SimpleNamespace
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
import pytest
from psycopg.types.json import Jsonb
from nexaweave_execution.native_launch_store import NativeLaunchStore,migrate
from nexaweave_execution.native_launch_contracts import (LaunchAuthorityError, NativeForkArchive, canonical,
    native_budget_episode, native_budget_fingerprint)
from nexaweave_execution.native_run_contracts import (NativeChildIdentity, NativeRunConflict,
    NativeRunBusy, NativeRunDenied, NativeRunReceipt, NativeRunRequest, RunState, SwapActiveActorV1)
from nexaweave_execution.native_run_store import NativeRunStore, RedditCheckpointMode, migrate_native_runs
from nexaweave_execution.budget import BudgetDenied, migrate as migrate_budget
from nexaweave_execution.preparation_store import migrate as migrate_preparation
from test_source_bridge_postgres import factory as original_factory

pytestmark=pytest.mark.postgres


@pytest.fixture(scope='module')
def factory():
    if os.getenv('PROJECT_STORE_POSTGRES_INTEGRATION')!='1':
        pytest.fail('selected native launch case requires disposable PostgreSQL')
    return original_factory.__wrapped__()


def migrate_all(factory):
    with factory() as conn:
        migrate_budget(conn);migrate_preparation(conn);migrate_native_runs(conn);migrate(conn)


def ready_host(factory,tmp_path,*,cap=40,checkpoint=False):
    migrate_all(factory)
    from test_preparation_store import real_host,request,reference
    prep,scope,retained,chat,wires,_=real_host(factory,tmp_path,cap=cap)
    payload=request(retained)
    if checkpoint:
        payload['options'].update(platforms=['twitter'],max_rounds=1)
    plan=prep.plan(payload);prep.start(reference(plan));prep.generate(wires[0])
    return prep,scope,plan


def launch_host(prep,factory,*,enabled=True,ceiling=4):
    assert type(ceiling) is int and 1 <= ceiling <= 4
    from app.services.durable_native_launch_host import DurableNativeLaunchHost
    from app.services.native_launch_models import BoundedNativeModelFactory
    from test_durable_native_launch import scripted_native_backends,LIMITS
    model=BoundedNativeModelFactory(('twitter','reddit'),dict(LIMITS),scripted_native_backends,cooperative_transport=True)
    return DurableNativeLaunchHost(preparation_host=prep,connection_factory=factory,runtime_sha256='c'*64,
        model_label='scripted',limits=LIMITS,model_factory=model,account_id=prep.account_id,
        ceiling_microusd=ceiling if enabled else None,authorize=lambda:enabled)


def declaration(plan):
    return {'schema_version':1,'launch_id':str(uuid4()),'preparation':{'operation_id':plan['operation_id'],'plan_sha256':plan['plan_sha256']}}


def test_reviews_are_immutable_bounded_and_do_not_register_native(factory,tmp_path):
    prep,scope,plan=ready_host(factory,tmp_path)
    host=launch_host(prep,factory,enabled=False)
    payload=declaration(plan);disabled=host.plan(payload)
    assert disabled['ceiling_microusd'] is None and not disabled['authorization']['model_calls_enabled']
    assert host.plan(payload)==disabled
    assert NativeLaunchStore(factory).get('owner',payload['launch_id']).state=='planned'
    with factory() as conn:
        assert conn.execute('SELECT count(*) FROM mf_native_execution.runs WHERE project_id=%s',(scope.project_id,)).fetchone()[0]==0
    host.ceiling=4
    new=host.plan(declaration(plan))
    assert new['request']['run_id']!=disabled['request']['run_id'] and new['ceiling_microusd']=='4'
    row=host.store.get('owner',new['request']['run_id'])
    wrong=deepcopy(row.frozen['identity']);wrong['model_label']='changed'
    with pytest.raises(LaunchAuthorityError):host.store.put('owner',row.frozen['declaration'],wrong)
    for _ in range(98):host.plan(declaration(plan))
    with pytest.raises(LaunchAuthorityError) as full:host.plan(declaration(plan))
    assert full.value.code=='busy'
    assert host.plan(payload)==disabled


def test_distinct_review_queue_race_has_one_permanent_claim(factory,tmp_path):
    prep,scope,plan=ready_host(factory,tmp_path)
    host=launch_host(prep,factory)
    plans=[host.plan(declaration(plan)) for _ in range(2)]
    def queue(dto):
        try:
            row=host.store.get('owner',dto['request']['run_id'])
            reservation=host.budget.reserve_native('owner',prep.account_id,scope,row.request,row.launch_sha256,4)
            queued,won=host.store.queue('owner',row.run_id,row.launch_sha256,reservation.attempt_id)
            return queued if won else None
        except LaunchAuthorityError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows=list(pool.map(queue,plans))
    assert sum(row is not None for row in rows)==1
    winner=next(row for row in rows if row is not None)
    assert winner.dispatch_claimed and NativeLaunchStore(factory).get('owner',winner.run_id).dispatch_claimed
    host.store.scheduling_uncertain('owner',winner.run_id,winner.launch_sha256)
    alternate=host.store.get('owner',host.plan(declaration(plan))['request']['run_id'])
    with pytest.raises(LaunchAuthorityError):host.store.queue('owner',alternate.run_id,alternate.launch_sha256,uuid4())


def test_project_revision_and_ready_receipt_reauthorization_before_queue(factory,tmp_path):
    from nexaweave_storage import ProjectStore
    from test_project_store import snapshot
    prep,scope,plan=ready_host(factory,tmp_path)
    host=launch_host(prep,factory);dto=host.plan(declaration(plan))
    row=host.store.get('owner',dto['request']['run_id'])
    changed=snapshot();changed['name']='new revision'
    ProjectStore(factory).update('owner',scope.project_id,1,changed)
    with pytest.raises(LaunchAuthorityError):host.store.queue('owner',row.run_id,row.launch_sha256,uuid4())
    assert not host.store.get('owner',row.run_id).dispatch_claimed
    root=prep.root/row.request.simulation_id
    assert not (root/'.native_prepared_start_claim').exists()


def _wire_bytes(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')


def _legacy_frozen_launch():
    workspace,project,graph,operation,run=(str(UUID(int=value,version=4)) for value in range(1,6))
    prep={'operation_id':operation,'plan_sha256':'a'*64}
    native={'schema_version':1,'principal':'owner','project_id':project,'project_revision':1,
        'simulation_id':'sim_'+UUID(operation).hex,'run_id':run,'artifact_sha256':'b'*64,
        'runtime_sha256':'c'*64,'platforms':['twitter'],'seed':7,'max_rounds':1}
    declaration={'schema_version':1,'launch_id':run,'preparation':prep}
    identity={'schema_version':1,'display_graph_id':'graph-fixture','scope':{
        'schema_version':1,'workspace_id':workspace,'project_id':project,'graph_id':graph,
        'run_id':None,'branch_id':None,'layer':'source'},
        'preparation':{**prep,'simulation_id':native['simulation_id'],'artifact_sha256':native['artifact_sha256']},
        'request':native,'limits':{'max_calls':1,'max_input_bytes':7200,'max_output_tokens':128,'max_run_seconds':30},
        'ceiling_microusd':'4','model_label':'scripted'}
    return {'declaration':declaration,'identity':identity,
        'configuration':{'account_id':None,'factory_sha256':'d'*64}}


def test_own_migration_catalog_drift_and_previous_checksums(factory):
    migrate_all(factory)
    sql=files('nexaweave_execution').joinpath('migrations/native_launch_0001.sql').read_text('utf-8')
    v2_sql=files('nexaweave_execution').joinpath('migrations/native_launch_0002.sql').read_text('utf-8')
    checksum=sha256(sql.encode('utf-8')).hexdigest()
    assert checksum=='5ae871d5d7f8824d357f6a8019ed2a12ab77ac7e5522886516d1594b2196218d'
    frozen=_legacy_frozen_launch();identity=frozen['identity'];native=identity['request'];run_id=UUID(native['run_id'])
    declared_bytes,identity_bytes=(_wire_bytes(frozen[key]) for key in ('declaration','identity'))
    declared_sha,identity_sha=(sha256(raw).hexdigest() for raw in (declared_bytes,identity_bytes))
    migrations='SELECT version,checksum,schema_checksum FROM mf_native_launch.schema_migrations ORDER BY version'
    select_row='SELECT * FROM mf_native_launch.plans WHERE run_id=%s'
    with factory() as conn:
        old=conn.execute('SELECT checksum FROM mf_execution.schema_migrations').fetchall()
        upgraded=conn.execute(migrations).fetchall()
        assert [row[0] for row in upgraded]==[1,2,3,4,5] and upgraded[0][1]==checksum
        with conn.transaction(force_rollback=True):
            conn.execute("SET LOCAL statement_timeout='10s'")
            conn.execute("SET LOCAL lock_timeout='5s'")
            conn.execute('SET LOCAL search_path=pg_catalog')
            conn.execute('ALTER SCHEMA mf_native_launch RENAME TO mf_native_launch_v2_fixture')
            conn.execute(sql)
            conn.execute('INSERT INTO mf_native_launch.schema_migrations VALUES(%s,%s,%s)',upgraded[0])
            conn.execute("INSERT INTO mf_native_launch.plans(run_id,principal,preparation_id,simulation_id,"
                "project_id,project_revision,request_sha256,launch_sha256,frozen,state) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'planned')",
                (run_id,'owner',UUID(identity['preparation']['operation_id']),native['simulation_id'],
                 UUID(native['project_id']),1,declared_sha,identity_sha,Jsonb(frozen)))
            original=conn.execute(select_row,(run_id,)).fetchone()
            assert original is not None and conn.execute(migrations).fetchall()==upgraded[:1]
            conn.execute(v2_sql)
            conn.execute('INSERT INTO mf_native_launch.schema_migrations VALUES(%s,%s,%s)',upgraded[1])
            assert conn.execute(select_row,(run_id,)).fetchone()==(*original,None)
            for drift in (
                'CREATE TABLE mf_native_launch.unexpected_v2(id integer)',
                "UPDATE mf_native_launch.schema_migrations SET checksum=repeat('0',64) WHERE version=2",
            ):
                with conn.transaction(force_rollback=True):
                    conn.execute(drift)
                    with pytest.raises(LaunchAuthorityError):
                        migrate(conn)
                    assert len(conn.execute(migrations).fetchall())==2
            migrate(conn);migrate(conn)
            assert conn.execute(migrations).fetchall()==upgraded
            assert conn.execute(select_row,(run_id,)).fetchone()==(*original,None,None)
            row=NativeLaunchStore(lambda:nullcontext(conn)).get('owner',run_id,identity_sha)
            assert row.frozen==frozen and row.request.to_wire()==native
            assert canonical(row.frozen['declaration'])==declared_bytes
            assert canonical(row.frozen['identity'])==identity_bytes
            assert (row.request_sha256,row.launch_sha256)==(declared_sha,identity_sha)
            assert row.request.fingerprint==sha256(_wire_bytes(native)).hexdigest()
        assert conn.execute(select_row,(run_id,)).fetchone() is None
        with conn.transaction(force_rollback=True):
            corrupt='0'*64 if upgraded[0][2]!='0'*64 else '1'*64
            conn.execute('UPDATE mf_native_launch.schema_migrations SET schema_checksum=%s WHERE version=1',(corrupt,))
            with pytest.raises(LaunchAuthorityError):
                migrate(conn)
        assert conn.execute(migrations).fetchall()==upgraded
        migrate(conn)
        with pytest.raises(LaunchAuthorityError):
            with conn.transaction():
                conn.execute('CREATE TABLE mf_native_launch.unexpected(id integer)');migrate(conn)
        with conn.transaction(force_rollback=True):
            conn.execute('ALTER TABLE mf_native_launch.plans DISABLE TRIGGER native_launch_fork_identity_guard')
            with pytest.raises(LaunchAuthorityError):
                migrate(conn)
        assert conn.execute('SELECT checksum FROM mf_execution.schema_migrations').fetchall()==old


def _twitter_authority(template, checkpoint=None):
    frozen=deepcopy(template.frozen)
    declaration,identity=frozen['declaration'],frozen['identity']
    request=identity['request']
    declaration['launch_id']=request['run_id']=str(uuid4())
    assert request['platforms']==['twitter'] and request['max_rounds']==1
    if checkpoint is not None:
        version=3 if checkpoint['mode']=='fork' else 2
        declaration.update(schema_version=version,checkpoint=deepcopy(checkpoint))
        identity.update(schema_version=version,checkpoint=deepcopy(checkpoint))
        if checkpoint['mode']!='capture':
            request.update(schema_version=version,source_run_id=checkpoint['source_run_id'],
                           checkpoint_sha256=checkpoint['checkpoint_sha256'])
        if version==3:
            request['intervention']=deepcopy(checkpoint['intervention'])
    return frozen


def _put_authority(store, frozen, archive=None):
    return store.put('owner',frozen['declaration'],frozen['identity'],configuration=frozen['configuration'],
                     fork_archive=archive)


def _terminal_source(factory, prep, scope, host, template, *, capture, archive_out=None):
    frozen=_twitter_authority(template,{'mode':'capture'} if capture else None)
    row=_put_authority(host.store,frozen)
    assert row.frozen==frozen and row.request.schema_version==1
    assert row.launch_sha256==sha256(_wire_bytes(frozen['identity'])).hexdigest()
    reserved=host.budget.reserve_native('owner',prep.account_id,scope,row.request,row.launch_sha256,4)
    queued,won=host.store.queue('owner',row.run_id,row.launch_sha256,reserved.attempt_id)
    assert won and queued.dispatch_claimed and queued.budget_attempt_id==reserved.attempt_id

    # Seed a dormant owned record through the lower-level store. This case has no native driver.
    native=NativeRunStore(factory)
    native.register(row.request)
    owner_id=uuid4()
    started=native.claim_start('owner',row.run_id,owner_id)
    child=NativeChildIdentity(uuid4(),12345,'f'*64)
    native.attach('owner',row.run_id,started.attempt_id,owner_id,child)
    evidence='e'*64
    if archive_out is not None:
        archive=_fork_archive(row,child)
        archive_out.append(archive)
        evidence=sha256(archive.lineage).hexdigest()
    receipt=NativeRunReceipt(row.run_id,started.attempt_id,child.instance_id,
        row.request.fingerprint,'completed',evidence)
    terminal=native.settle('owner',row.run_id,started.attempt_id,owner_id,receipt)
    completed=host.store.observe('owner',row.run_id,row.launch_sha256,terminal)
    assert completed.state=='completed' and completed.receipt==receipt.to_wire()
    with pytest.raises(NativeRunConflict):
        native.settle('owner',row.run_id,started.attempt_id,owner_id,replace(receipt,evidence_sha256='a'*64))
    assert native.get('owner',row.run_id)==terminal
    assert host.store.get('owner',row.run_id,row.launch_sha256)==completed
    return completed


def _continuation(source):
    return {'mode':'continue','source_run_id':str(source.run_id),
        'checkpoint_sha256':source.receipt['evidence_sha256']}


def _fork_archive(source, child):
    request=source.request
    anchors=dict(graph_id=source.frozen['identity']['display_graph_id'],simulation_id=request.simulation_id,
        run_id=str(request.run_id),project_id=str(request.project_id),project_revision=request.project_revision,
        branch_id=str(uuid5(NAMESPACE_URL,'mirofish:native-checkpoint:v1:'+str(request.run_id))))
    position=dict(completed_round=1,horizon=2,minutes_per_round=60,simulated_minutes=0,total_actions=2)
    config_digest=bytes.fromhex('1'*64)
    rng_version,vector,cache=random.Random(request.seed).getstate()
    assert cache is None
    runner=struct.pack('>4sB5I32s624IHBd',b'NWR1',rng_version,*position.values(),config_digest,
        *vector,0,0.0)
    manifest=canonical(dict(anchors=anchors,platform='twitter',version=1,round=1,post_count=1,
        prior_cursor=3,prior_post_id=1,cursor=5,source_journal_mode='delete',manifest='manifest.json',
        database_bytes=8192,database_sha256='5'*64,log_bytes=1024,log_sha256='6'*64,
        full_checkpoint=False,continuation_supported=False,memory=dict(version=1,filename='native-memory.bin',
            coverage='all_registered_agents',agent_count=2,bytes=1024,sha256='7'*64),
        topology=dict(version=1,directed=True,vertex_count=2,edge_count=0,sha256='8'*64),
        engine_clock=dict(version=1,before_step=1,after_step=2),
        recommendation_state=dict(version=1,mode='random',use_openai_embedding=False,max_rec_post_len=2,
            refresh_rec_post_count=2,following_post_count=3,users=2,rows=2,sha256='9'*64),
        runner_state=dict(version=1,file='native-runner.bin',bytes=len(runner),sha256=sha256(runner).hexdigest())))
    lineage=canonical(dict(version=1,anchors=anchors,producer_pid=child.process_id,
        manifest_sha256=sha256(manifest).hexdigest(),prepared=dict(artifact_sha256=request.artifact_sha256,
        config_sha256=config_digest.hex(),source_grounding_sha256='2'*64,profile_sha256='3'*64),
        owners=[dict(platform_id=index,name=f'Actor {index}',camel_id=f'actor-{index}',token_limit=4096)
                for index in range(2)],runner=position))
    return NativeForkArchive(lineage,manifest,runner)


def _fork_checkpoint(source, archive):
    return {**_continuation(source),'mode':'fork','intervention':dict(kind='swap_active_actor_v1',
        manifest_sha256=sha256(archive.manifest).hexdigest(),runner_state_sha256=sha256(archive.runner_state).hexdigest())}


def _refused_authority(store, frozen, archive=None):
    with pytest.raises(LaunchAuthorityError):
        _put_authority(store,frozen,archive)
    with pytest.raises(LaunchAuthorityError) as error:
        store.get('owner',frozen['declaration']['launch_id'])
    assert error.value.code=='not_found'


def test_explicit_checkpoint_capture_authorizes_one_continuation(factory,tmp_path):
    prep,scope,plan=ready_host(factory,tmp_path,checkpoint=True)
    host=launch_host(prep,factory)
    store=host.store
    template=store.get('owner',host.plan(declaration(plan))['request']['run_id'])
    invalid_capture=_twitter_authority(template,{'mode':'capture','source_run_id':str(uuid4())})
    _refused_authority(store,invalid_capture)
    changed_seed=_twitter_authority(template,{'mode':'capture'})
    changed_seed['identity']['request']['seed']+=1
    _refused_authority(store,changed_seed)
    archive_out=[]
    source=_terminal_source(factory,prep,scope,host,template,capture=True,archive_out=archive_out)
    archive,=archive_out
    checkpoint=_continuation(source)
    assert source.frozen['identity']['checkpoint']==source.frozen['declaration']['checkpoint']=={'mode':'capture'}

    wrong_digest=_twitter_authority(template,{**checkpoint,'checkpoint_sha256':'d'*64})
    _refused_authority(store,wrong_digest)
    incoherent=_twitter_authority(template,checkpoint)
    incoherent['declaration']['checkpoint']['checkpoint_sha256']='d'*64
    _refused_authority(store,incoherent)
    wrong_inner=_twitter_authority(template,checkpoint)
    wrong_inner['identity']['request'].update(schema_version=1)
    del wrong_inner['identity']['request']['source_run_id'],wrong_inner['identity']['request']['checkpoint_sha256']
    _refused_authority(store,wrong_inner)
    mismatched=_twitter_authority(template,checkpoint)
    mismatched['identity']['request']['runtime_sha256']='d'*64
    _refused_authority(store,mismatched)

    ordinary_path=tmp_path/'ordinary';ordinary_path.mkdir()
    legacy_prep,legacy_scope,legacy_plan=ready_host(factory,ordinary_path,checkpoint=True)
    legacy_host=launch_host(legacy_prep,factory)
    legacy_template=legacy_host.store.get('owner',legacy_host.plan(declaration(legacy_plan))['request']['run_id'])
    ordinary=_terminal_source(factory,legacy_prep,legacy_scope,legacy_host,legacy_template,capture=False)
    _refused_authority(store,_twitter_authority(legacy_template,_continuation(ordinary)))
    _refused_authority(store,_twitter_authority(legacy_template,checkpoint))
    with pytest.raises(LaunchAuthorityError):
        store.get('other',source.run_id,source.launch_sha256)

    payloads=[_twitter_authority(template,checkpoint) for _ in range(2)]
    rows=[_put_authority(store,payload) for payload in payloads]
    for row,payload in zip(rows,payloads,strict=True):
        assert row.frozen==payload and row.request.schema_version==2
        assert row.request.source_run_id==source.run_id and row.request.checkpoint_sha256==checkpoint['checkpoint_sha256']
        assert row.launch_sha256==sha256(_wire_bytes(payload['identity'])).hexdigest()
        assert row.request_sha256==sha256(_wire_bytes(payload['declaration'])).hexdigest()
        assert _put_authority(store,payload)==row
    # Exercise the durable slot with explicit attempt IDs. The product v2 budget binding is separate.
    attempts=[uuid4() for _ in rows]
    gate=threading.Barrier(2)
    def compete(pair):
        row,attempt=pair
        gate.wait(timeout=10)
        try:
            return store.queue('owner',row.run_id,row.launch_sha256,attempt)
        except LaunchAuthorityError as error:
            assert error.code=='conflict'
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims=list(pool.map(compete,zip(rows,attempts,strict=True)))
    (winner,won),=(claim for claim in claims if claim is not None)
    assert won and winner.state=='queued' and winner.dispatch_claimed
    winning_index=next(index for index,row in enumerate(rows) if row.run_id==winner.run_id)
    loser=rows[1-winning_index]
    assert winner.budget_attempt_id==attempts[winning_index]
    assert store.queue('owner',winner.run_id,winner.launch_sha256,winner.budget_attempt_id)==(winner,False)
    assert store.get('owner',loser.run_id)==loser and loser.budget_attempt_id is None
    assert store.get('owner',source.run_id,source.launch_sha256)==source
    with factory() as conn:
        native=conn.execute('SELECT run_id FROM mf_native_execution.runs WHERE project_id=%s',
                            (scope.project_id,)).fetchall()
        authority=conn.execute('SELECT run_id,source_run_id,dispatch_claimed,budget_attempt_id '
            'FROM mf_native_launch.plans WHERE run_id=ANY(%s)',
            ([source.run_id,winner.run_id,loser.run_id],)).fetchall()
    assert native==[(source.run_id,)]
    assert set(authority)=={
        (source.run_id,None,True,source.budget_attempt_id),
        (winner.run_id,source.run_id,True,winner.budget_attempt_id),
        (loser.run_id,source.run_id,False,None),
    }
    assert not (prep.root/source.simulation_id/'.native_prepared_start_claim').exists()

    fork_checkpoint=_fork_checkpoint(source,archive)
    fork=_twitter_authority(template,fork_checkpoint)
    _refused_authority(store,fork)
    for bad_archive in (replace(archive, lineage=archive.lineage+b' '),
                        replace(archive, manifest=archive.manifest+b' '),
                        replace(archive, runner_state=archive.runner_state[:-1]+b'!')):
        _refused_authority(store,fork,bad_archive)
    altered=_twitter_authority(template,fork_checkpoint)
    altered['identity']['request']['intervention']['runner_state_sha256']='d'*64
    _refused_authority(store,altered,archive)
    crossed=_twitter_authority(legacy_template,fork_checkpoint)
    _refused_authority(store,crossed,archive)
    wrong_owner_fork=deepcopy(fork)
    wrong_owner_fork['identity']['request']['principal']='other'
    _refused_authority(store,wrong_owner_fork,archive)
    changed_factory=deepcopy(fork)
    changed_factory['configuration']['factory_sha256']='0'*64
    _refused_authority(store,changed_factory,archive)

    native_store=NativeRunStore(factory)
    producer=native_store.get('owner',source.run_id)
    fork_request=NativeRunRequest.from_wire(fork['identity']['request'])
    colliding_v2=replace(fork_request,schema_version=2,intervention=None)
    # A standalone v2 run can claim the ID before fork planning.
    with factory() as conn, conn.transaction(force_rollback=True):
        local_factory=lambda:nullcontext(conn)
        assert NativeRunStore(local_factory).register(colliding_v2).request==colliding_v2
        _refused_authority(NativeLaunchStore(local_factory),fork,archive)
    planned_fork=_put_authority(store,fork,archive)
    assert planned_fork.request==fork_request
    assert fork_request.intervention == SwapActiveActorV1.from_wire(fork_checkpoint['intervention'])
    assert fork_request.to_wire() == fork['identity']['request']
    assert fork_request.fingerprint == sha256(_wire_bytes(fork_request.to_wire())).hexdigest()
    assert planned_fork.launch_sha256 == sha256(_wire_bytes(fork['identity'])).hexdigest()
    assert _put_authority(store,fork)==planned_fork
    assert _put_authority(store,fork,archive)==planned_fork
    assert all(secret not in canonical(planned_fork.frozen) for secret in (archive.lineage,archive.manifest,archive.runner_state))
    with pytest.raises(NativeRunDenied):
        native_store.register(fork_request)
    v1=replace(source.request,run_id=fork_request.run_id,simulation_id='ordinary_v1')
    for collision in (v1,colliding_v2):
        with pytest.raises(NativeRunConflict):
            native_store.register(collision)
    with factory() as conn, conn.transaction(force_rollback=True):
        conn.execute('INSERT INTO mf_native_execution.runs (run_id,principal,project_id,project_revision,'
            'simulation_id,artifact_sha256,runtime_sha256,platforms,seed,max_rounds,request_fingerprint,'
            'source_run_id,checkpoint_sha256) SELECT %s,principal,project_id,project_revision,simulation_id,'
            'artifact_sha256,runtime_sha256,platforms,seed,max_rounds,%s,run_id,%s '
            'FROM mf_native_execution.runs WHERE run_id=%s',
            (colliding_v2.run_id,colliding_v2.fingerprint,colliding_v2.checkpoint_sha256,source.run_id))
        local_store=NativeLaunchStore(lambda:nullcontext(conn))
        with pytest.raises(LaunchAuthorityError,match='^conflict$'):
            local_store.queue_fork('owner',planned_fork.run_id,planned_fork.launch_sha256)
        assert local_store.get('owner',planned_fork.run_id)==planned_fork
        assert conn.execute('SELECT 1 FROM mf_execution.reservations WHERE operation_id=%s',
                            (planned_fork.run_id,)).fetchone() is None
    with pytest.raises(LaunchAuthorityError):
        store.queue('owner',planned_fork.run_id,planned_fork.launch_sha256,winner.budget_attempt_id)
    extra=_twitter_authority(template,fork_checkpoint)
    _refused_authority(store,extra,archive)
    extra_request=NativeRunRequest.from_wire(extra['identity']['request'])
    with pytest.raises(NativeRunDenied):
        native_store.register(extra_request)
    with pytest.raises(BudgetDenied):
        host.budget.native_reservation('owner',prep.account_id,extra_request,
                                      sha256(_wire_bytes(extra['identity'])).hexdigest())

    queued_fork,won=store.queue_fork('owner',planned_fork.run_id,planned_fork.launch_sha256)
    assert won and queued_fork.state=='queued' and queued_fork.dispatch_claimed
    assert queued_fork.budget_attempt_id not in {source.budget_attempt_id,winner.budget_attempt_id}
    assert store.queue_fork('owner',queued_fork.run_id,queued_fork.launch_sha256)==(queued_fork,False)
    with pytest.raises(NativeRunConflict):
        native_store.register(colliding_v2)
    reserved=host.budget.native_reservation('owner',prep.account_id,fork_request,queued_fork.launch_sha256)
    assert reserved.attempt_id == queued_fork.budget_attempt_id and reserved.state == 'reserved'
    assert reserved.fingerprint == native_budget_fingerprint(queued_fork.launch_sha256)
    assert reserved.episode_id == native_budget_episode(scope.group_id,queued_fork.run_id)
    with pytest.raises(NativeRunDenied):
        native_store.register(fork_request)
    host.budget.start('owner',prep.account_id,queued_fork.run_id,queued_fork.budget_attempt_id)
    assert native_store.register(winner.request).request == winner.request
    registered=native_store.register(fork_request)
    assert registered.request == fork_request and native_store.register(fork_request) == registered
    assert _put_authority(store,fork)==queued_fork
    with pytest.raises(NativeRunConflict):
        native_store.register(replace(fork_request, intervention=replace(fork_request.intervention,
                                      runner_state_sha256='4'*64)))
    with factory() as conn:
        for schema,table in (('mf_native_execution','runs'),('mf_native_launch','plans')):
            with pytest.raises(Exception) as refused:
                with conn.transaction():
                    conn.execute(f'UPDATE {schema}.{table} SET intervention=NULL WHERE run_id=%s',
                                 (fork_request.run_id,))
            assert getattr(refused.value,'sqlstate',None)=='23514'
    fork_owner=uuid4()
    with factory() as conn, conn.transaction(force_rollback=True):
        local_factory=lambda:nullcontext(conn)
        local_store=NativeLaunchStore(local_factory)
        cancelled=local_store.cancel('owner',queued_fork.run_id,queued_fork.launch_sha256)
        assert cancelled.cancel_requested and cancelled.state=='queued'
        with pytest.raises(Exception) as refused:
            with conn.transaction():
                conn.execute('UPDATE mf_native_launch.plans SET cancel_requested=false WHERE run_id=%s',
                             (queued_fork.run_id,))
        assert getattr(refused.value,'sqlstate',None)=='23514'
        assert local_store.get('owner',queued_fork.run_id)==cancelled
        with pytest.raises(NativeRunDenied):
            NativeRunStore(local_factory).claim_start('owner',fork_request.run_id,fork_owner)

    claimed=native_store.claim_start('owner',fork_request.run_id,fork_owner)
    assert claimed.state=='starting' and claimed.attempt_id not in {None,producer.attempt_id}
    with pytest.raises(NativeRunBusy):
        native_store.claim_start('owner',fork_request.run_id,uuid4())
    fork_child=NativeChildIdentity(uuid4(),12346,'f'*64)
    running=native_store.attach('owner',fork_request.run_id,claimed.attempt_id,fork_owner,fork_child)
    with factory() as conn, conn.transaction(force_rollback=True):
        local_store=NativeLaunchStore(lambda:nullcontext(conn))
        observed=local_store.observe('owner',queued_fork.run_id,queued_fork.launch_sha256,running)
        assert observed.state=='running'
        assert local_store.observe('owner',queued_fork.run_id,queued_fork.launch_sha256,claimed)==observed
    uncertain=store.scheduling_uncertain('owner',queued_fork.run_id,queued_fork.launch_sha256)
    assert uncertain.state=='uncertain' and uncertain.receipt is None
    fork_receipt=NativeRunReceipt(fork_request.run_id,claimed.attempt_id,fork_child.instance_id,
                                  fork_request.fingerprint,'completed','a'*64)
    unconfirmed=replace(running,state=RunState.completed,receipt=fork_receipt)
    for stale in (running,unconfirmed):
        assert store.observe('owner',queued_fork.run_id,queued_fork.launch_sha256,stale)==uncertain
    finished=native_store.settle('owner',fork_request.run_id,claimed.attempt_id,fork_owner,fork_receipt)
    completed=store.observe('owner',queued_fork.run_id,queued_fork.launch_sha256,finished)
    assert completed.state=='completed' and completed.receipt==fork_receipt.to_wire()
    for replay in (registered,claimed,running,finished):
        assert store.observe('owner',queued_fork.run_id,queued_fork.launch_sha256,replay)==completed
    assert native_store.get('owner',source.run_id)==producer
    assert store.get('owner',source.run_id,source.launch_sha256)==source

    # Recheck current project authority while an otherwise eligible source has no continuation claim.
    from nexaweave_storage import ProjectStore
    from test_project_store import snapshot
    stale_path=tmp_path/'stale';stale_path.mkdir()
    stale_prep,stale_scope,stale_plan=ready_host(factory,stale_path,checkpoint=True)
    stale_host=launch_host(stale_prep,factory)
    stale_template=stale_host.store.get('owner',stale_host.plan(declaration(stale_plan))['request']['run_id'])
    stale_archives=[]
    stale_source=_terminal_source(factory,stale_prep,stale_scope,stale_host,stale_template,
                                  capture=True,archive_out=stale_archives)
    stale_frozen=_twitter_authority(stale_template,_continuation(stale_source))
    stale=_put_authority(store,stale_frozen)
    stale_archive,=stale_archives
    stale_fork=_put_authority(store,_twitter_authority(stale_template,
        _fork_checkpoint(stale_source,stale_archive)),stale_archive)
    stale_attempt=uuid4()
    changed=snapshot();changed['name']='updated after checkpoint authorization'
    ProjectStore(factory).update('owner',stale_scope.project_id,1,changed)
    wrong_revision=_twitter_authority(stale_template,{'mode':'capture'})
    wrong_revision['identity']['request']['project_revision']=2
    _refused_authority(store,wrong_revision)
    with pytest.raises(LaunchAuthorityError):
        store.queue('owner',stale.run_id,stale.launch_sha256,stale_attempt)
    assert store.get('owner',stale.run_id)==stale
    with pytest.raises(LaunchAuthorityError):
        store.queue_fork('owner',stale_fork.run_id,stale_fork.launch_sha256)
    assert store.get('owner',stale_fork.run_id)==stale_fork
    with pytest.raises(BudgetDenied):
        stale_host.budget.native_reservation('owner',stale_prep.account_id,stale_fork.request,stale_fork.launch_sha256)


def configure_offline_scheduler(host):
    """PG admission fixture seam, no Temporal/native execution claim."""
    from nexaweave_execution.temporal_native_host import NativeWorkflowRef
    calls=[]
    host.temporal=object()
    def call(method,request,ref):
        if method=='local_status':raise LaunchAuthorityError('native_launch_unavailable')
        calls.append(request)
        return NativeWorkflowRef('mf-native-v1-'+request.run_id.hex+'-'+request.fingerprint,str(uuid4()),str(request.run_id))
    host.temporal_call=call
    return calls


def test_pg_same_id_queue_error_does_not_release_a_concurrent_winner(factory,tmp_path):
    from test_native_launch_api import reference
    from nexaweave_execution.budget import ReservationState
    prep,scope,plan=ready_host(factory,tmp_path);host=launch_host(prep,factory)
    calls=configure_offline_scheduler(host);dto=host.plan(declaration(plan))
    failed_queue=threading.Event();winner_queued=threading.Event()
    actual_queue=host.store.queue;actual_close=host.store.close_undispatched;actual_call=host.temporal_call
    def queue(principal,run,sha,attempt):
        if threading.current_thread().name.startswith('failed-start'):
            failed_queue.set();raise LaunchAuthorityError('conflict')
        assert failed_queue.wait(5)
        return actual_queue(principal,run,sha,attempt)
    def close(principal,run,sha):
        assert winner_queued.wait(10)
        return actual_close(principal,run,sha)
    def call(method,request,ref):
        result=actual_call(method,request,ref)
        if method=='start':winner_queued.set()
        return result
    host.store.queue=queue;host.store.close_undispatched=close;host.temporal_call=call
    try:
        with ThreadPoolExecutor(max_workers=1,thread_name_prefix='failed-start') as first:
            failure=first.submit(host.start,reference(dto));assert failed_queue.wait(5)
            queued=host.start(reference(dto))
            with pytest.raises(LaunchAuthorityError):failure.result(timeout=10)
        row=host.store.get('owner',dto['request']['run_id'])
        reservation=host.budget.native_reservation('owner',host.account_id,row.request,row.launch_sha256)
        assert queued['state']=='queued' and len(calls)==1 and row.dispatch_claimed
        assert reservation.state==ReservationState.started and reservation.attempt_id==row.budget_attempt_id
        assert actual_close('owner',row.run_id,row.launch_sha256)[1] is False
    finally:winner_queued.set()


def test_pg_locked_close_wins_before_stale_same_id_queue_and_release(factory,tmp_path):
    from nexaweave_execution.budget import ReservationState
    prep,scope,plan=ready_host(factory,tmp_path);host=launch_host(prep,factory)
    dto=host.plan(declaration(plan));row=host.store.get('owner',dto['request']['run_id'])
    reservation=host.budget.reserve_native('owner',host.account_id,scope,row.request,row.launch_sha256,4)
    stale_read=threading.Event();closed=threading.Event()
    def stale_queue():
        stale=host.store.get('owner',row.run_id,row.launch_sha256)
        assert stale.state=='planned';stale_read.set();assert closed.wait(5)
        return host.store.queue('owner',stale.run_id,stale.launch_sha256,reservation.attempt_id)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            queued=pool.submit(stale_queue);assert stale_read.wait(5)
            proof,proven=host.store.close_undispatched('owner',row.run_id,row.launch_sha256)
            assert proven and proof.state=='cancelled' and proof.cancel_requested and not proof.dispatch_claimed
            assert proof.budget_attempt_id is None
            host.budget.release_undispatched('owner',host.account_id,row.run_id,reservation.attempt_id)
            closed.set();reply,won=queued.result(timeout=5)
        assert not won and reply.state=='cancelled' and not reply.dispatch_claimed
        assert host.budget.native_reservation('owner',host.account_id,row.request,row.launch_sha256).state==ReservationState.released
    finally:closed.set()


def test_pg_distinct_review_start_race_releases_only_permanently_closed_loser(factory,tmp_path):
    from test_native_launch_api import reference
    from nexaweave_execution.budget import ReservationState
    prep,scope,plan=ready_host(factory,tmp_path);host=launch_host(prep,factory)
    calls=configure_offline_scheduler(host);plans=[host.plan(declaration(plan)) for _ in range(2)]
    ready=threading.Barrier(2);actual=host.store.queue
    def queue(*args):
        ready.wait(timeout=5)
        return actual(*args)
    host.store.queue=queue
    def start(dto):
        try:return host.start(reference(dto))
        except LaunchAuthorityError as error:return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:replies=list(pool.map(start,plans))
    assert sum(type(reply) is dict for reply in replies)==1 and replies.count('conflict')==1 and len(calls)==1
    rows=[host.store.get('owner',dto['request']['run_id']) for dto in plans]
    winner=next(row for row in rows if row.dispatch_claimed)
    loser=next(row for row in rows if not row.dispatch_claimed)
    assert winner.state=='queued' and loser.state=='cancelled' and loser.cancel_requested and loser.budget_attempt_id is None
    assert host.budget.native_reservation('owner',host.account_id,winner.request,winner.launch_sha256).state==ReservationState.started
    assert host.budget.native_reservation('owner',host.account_id,loser.request,loser.launch_sha256).state==ReservationState.released


class _TwoActorChat:
    """Scoped fixture for the two actors a Reddit checkpoint requires.

    The shared scripted chat returns one strict agent config, which fits the
    single-entity cases. A Reddit checkpoint is admitted only with two or more
    actors, so this case supplies complete deterministic activity configs for
    both selected actors instead of weakening the strict generator or its
    production validation.
    """

    def __init__(self):
        from test_provider_neutral_preparation import ScriptedChat
        self._base = ScriptedChat()
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        reply = self._base.create(**kwargs)
        if 'agent_configs' in kwargs['messages'][-1]['content']:
            reply.choices[0].message.content = json.dumps({'agent_configs': [
                {'agent_id': 0, 'activity_level': 0.5, 'posts_per_hour': 0.3,
                 'comments_per_hour': 0.4, 'active_hours': [9, 10],
                 'response_delay_min': 5, 'response_delay_max': 20,
                 'sentiment_bias': 0, 'stance': 'neutral', 'influence_weight': 1.0},
                {'agent_id': 1, 'activity_level': 0.4, 'posts_per_hour': 0.2,
                 'comments_per_hour': 0.5, 'active_hours': [10, 11],
                 'response_delay_min': 10, 'response_delay_max': 30,
                 'sentiment_bias': 0, 'stance': 'neutral', 'influence_weight': 1.0},
            ]}, ensure_ascii=False)
        return reply


def test_reddit_checkpoint_store_binds_producer_before_budget_and_round_trips(factory, tmp_path, monkeypatch):
    from app.services.preparation_client import PreparationError
    from test_preparation_store import real_host, request, reference

    migrate_all(factory)
    prep, scope, retained, _, wires, _ = real_host(factory, tmp_path, cap=40, chat=_TwoActorChat())
    options = {
        'comment': 'Continue the bounded thread.',
        'seed_post': {'poster_agent_id': 0, 'content': 'Open the bounded thread.'},
    }
    payload = dict(request(retained), schema_version=2, source_revisions=[str(retained.source_revision)])
    payload['options'].update(types=None, max_agents=2, platforms=['reddit'], max_rounds=1,
                              reddit_checkpoint=options)
    plan = prep.plan(payload)
    prep.start(reference(plan))
    prep.generate(wires[0])
    ready = prep.store.get('owner', plan['operation_id'], plan['plan_sha256'])
    assert ready.state == 'ready'
    config = json.loads((prep.root / ready.receipt['simulation_id'] / 'simulation_config.json').read_bytes())
    assert config['reddit_checkpoint_comment'] == options['comment']
    assert config['event_config']['initial_posts'] == [options['seed_post']]

    def forbidden(*args, **kwargs):
        pytest.fail('checkpoint donor reached variant publication or row issuance')

    donor_budget = prep.budget.status('owner', prep.account_id)
    artifact_roots = set(prep.root.iterdir())
    variant = dict(reference(plan), idempotency_key=str(uuid4()))
    with monkeypatch.context() as denial:
        denial.setattr(prep, '_publish_seed_alternate', forbidden)
        denial.setattr(prep.store, 'issue_seed_pair', forbidden)
        denial.setattr(prep.store, 'issue_seed_ensemble', forbidden)
        for action, selection in ((prep.seed_pair, {'alternate_seed': 8}),
                                  (prep.seed_ensemble, {'seeds': [8, 9]})):
            with pytest.raises(PreparationError, match='^conflict$'):
                action(dict(variant, **selection))
    assert set(prep.root.iterdir()) == artifact_roots
    assert prep.budget.status('owner', prep.account_id) == donor_budget
    assert prep.store.get('owner', ready.operation_id) == ready

    host = launch_host(prep, factory)
    capture = dict(declaration(plan), schema_version=4, checkpoint={'mode': 'capture'})
    request_wire = {
        'schema_version': 1, 'principal': 'owner', 'project_id': str(scope.project_id),
        'project_revision': ready.project_revision, 'simulation_id': ready.receipt['simulation_id'],
        'run_id': capture['launch_id'], 'artifact_sha256': ready.receipt['artifact_sha256'],
        'runtime_sha256': 'c' * 64, 'platforms': ['reddit'],
        'seed': payload['options']['seed'], 'max_rounds': 1,
    }
    identity = {
        'schema_version': 4, 'display_graph_id': host.display_graph_id, 'scope': host.scope_dto,
        'preparation': host._preparation_descriptor(ready), 'request': request_wire, 'limits': host.limits,
        'ceiling_microusd': '4', 'model_label': host.model_label, 'checkpoint': capture['checkpoint'],
    }
    configuration = host._configuration()
    native = NativeRunStore(factory)
    capture_mode = RedditCheckpointMode.CAPTURE
    continuation_mode = RedditCheckpointMode.CONTINUE
    unbound = NativeRunRequest.from_wire(request_wire)
    with pytest.raises(NativeRunDenied):
        native.register(unbound, reddit_mode=capture_mode)
    with pytest.raises(NativeRunDenied):
        native.get('owner', unbound.run_id)
    producer = host.store.put('owner', capture, identity, configuration=configuration)
    with pytest.raises(NativeRunDenied):
        native.register(producer.request, reddit_mode=capture_mode)
    queued, won = host.store.queue_reddit('owner', producer.run_id, producer.launch_sha256)
    assert won
    host.budget.start('owner', prep.account_id, producer.run_id, queued.budget_attempt_id)
    with pytest.raises(NativeRunDenied):
        native.register(producer.request)
    with pytest.raises(NativeRunDenied):
        native.register(producer.request, reddit_mode=continuation_mode)
    assert native.register(producer.request, reddit_mode=capture_mode).request == producer.request
    owner_id = uuid4()
    for wrong_mode in (None, continuation_mode):
        with pytest.raises(NativeRunDenied):
            native.claim_start('owner', producer.run_id, owner_id, reddit_mode=wrong_mode)
    assert native.get('owner', producer.run_id).state is RunState.declared
    started = native.claim_start('owner', producer.run_id, owner_id, reddit_mode=capture_mode)
    child = NativeChildIdentity(uuid4(), 12345, 'f' * 64)
    native.attach('owner', producer.run_id, started.attempt_id, owner_id, child)
    receipt = NativeRunReceipt(producer.run_id, started.attempt_id, child.instance_id,
                               producer.request.fingerprint, 'completed', 'e' * 64)
    completed = native.settle('owner', producer.run_id, started.attempt_id, owner_id, receipt)
    producer = host.store.observe('owner', producer.run_id, producer.launch_sha256, completed)

    selector = _continuation(producer)
    continuation = dict(declaration(plan), schema_version=4, checkpoint=selector)
    request_wire = dict(request_wire, schema_version=4, run_id=continuation['launch_id'],
                        source_run_id=selector['source_run_id'], checkpoint_sha256=selector['checkpoint_sha256'])
    identity = dict(identity, checkpoint=selector, request=request_wire)
    before = host.budget.status('owner', prep.account_id)
    for key, invalid in (('checkpoint_sha256', '0' * 64), ('source_run_id', str(uuid4()))):
        wrong_selector = dict(selector, **{key: invalid})
        wrong_identity = dict(identity, checkpoint=wrong_selector, request=dict(request_wire, **{key: invalid}))
        with pytest.raises(LaunchAuthorityError, match='^conflict$'):
            host.store.put('owner', dict(continuation, checkpoint=wrong_selector), wrong_identity,
                           configuration=configuration)
        assert host.budget.status('owner', prep.account_id) == before
    row = host.store.put('owner', continuation, identity, configuration=configuration)
    assert row.request.to_wire() == request_wire
    assert row.request.fingerprint == sha256(_wire_bytes(request_wire)).hexdigest()
    for wrong_mode in (None, capture_mode, continuation_mode):
        with pytest.raises(NativeRunDenied):
            native.register(row.request, reddit_mode=wrong_mode)
    queued, won = host.store.queue_reddit('owner', row.run_id, row.launch_sha256)
    assert won
    host.budget.start('owner', prep.account_id, row.run_id, queued.budget_attempt_id)
    with pytest.raises(NativeRunDenied):
        native.register(row.request)
    registered = native.register(row.request, reddit_mode=continuation_mode)
    assert native.get('owner', row.run_id) == registered and registered.request == row.request
    with pytest.raises(NativeRunDenied):
        native.claim_start('owner', row.run_id, uuid4())
    assert native.get('owner', row.run_id).state is RunState.declared

    ordinary = replace(unbound, simulation_id='sim_ordinary_reddit', run_id=uuid4())
    assert native.register(ordinary).request == ordinary
    assert native.request_cancel('owner', ordinary.run_id).cancel_requested

"""Main: real Flask HTTP -> fixed installed PG-only child -> owned retained text.

No injected facade/transport/store. Owned process observations are not an OS
sandbox or native libpq egress policy. Main runs only on the guarded fixture.
"""
import base64
from contextlib import contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import pytest
from psycopg.conninfo import conninfo_to_dict
from nexaweave_knowledge.bindings import ScopeBindingStore
from nexaweave_knowledge.contracts import KnowledgeScope, Layer
from nexaweave_storage import ProjectStore, SourceStore
from test_source_bridge_postgres import factory
from test_project_store import snapshot
from test_document_source_integration import _package, _paragraph

pytestmark = pytest.mark.postgres

_HTTP_CHILD = r'''
import builtins,json,os,sys,threading,subprocess
from pathlib import Path
root=os.environ['SOURCE_HTTP_TEST_ROOT']
sys.path.insert(0,root)
sys.path.insert(0,str(Path(root)/'backend'))
from tools.run_unit_tests import LoopbackOnlySockets
guard=LoopbackOnlySockets();guard.install()
original=builtins.__import__
blocked=('nexaweave_knowledge','graphiti_core','openai','neo4j','camel','oasis','torch','transformers','temporalio','pymupdf','fitz')
def imports(name,*args,**kwargs):
    if any(name==p or name.startswith(p+'.') for p in blocked):
        raise AssertionError('backend provider/runtime import')
    return original(name,*args,**kwargs)
builtins.__import__=imports
lock=threading.Lock()
def emit(value):
    with lock:
        print(json.dumps(value,separators=(',',':')),flush=True)
def observe(frame,event,arg):
    module=frame.f_globals.get('__name__','');name=frame.f_code.co_name
    if event=='return' and module=='subprocess' and name=='__init__':
        child=frame.f_locals.get('self')
        if isinstance(child,subprocess.Popen):
            args=frame.f_locals.get('args',[]);env=frame.f_locals.get('env',{})
            allowed={'HOME','TMPDIR','TMP','TEMP','GRAPHITI_TELEMETRY_ENABLED','PYTHONNOUSERSITE',
                'PATH','LANG','SystemRoot','WINDIR','USERPROFILE','KNOWLEDGE_PRINCIPAL',
                'KNOWLEDGE_DISPLAY_GRAPH_ID','KNOWLEDGE_BOUND_SCOPE_JSON','KNOWLEDGE_PG_HOST',
                'KNOWLEDGE_PG_PORT','KNOWLEDGE_PG_DATABASE','KNOWLEDGE_PG_USER','KNOWLEDGE_PG_PASSWORD'}
            safe=(len(args)==4 and args[1:3]==['-I','-u']
                and Path(args[3]).name=='source_bootstrap.py' and 'site-packages' in Path(args[3]).parts
                and not set(env)-allowed)
            emit({'event':'spawn','pid':child.pid,'safe':safe})
    if event=='return' and module=='app.services.knowledge_transport' and name=='_stop_owned':
        owner=frame.f_locals.get('owner');child=getattr(owner,'process',None)
        threads=frame.f_locals.get('threads',[])
        emit({'event':'cleanup','closed':owner is not None and owner.closed
            and (os.name!='nt' or (owner.tree_empty and owner.job is None))
            and child is not None and child.poll() is not None
            and child.stdin.closed and child.stdout.closed and all(not t.is_alive() for t in threads)})
sys.setprofile(observe);threading.setprofile(observe)
from app import create_app
from werkzeug.serving import make_server
server=make_server('127.0.0.1',0,create_app(),threaded=True)
emit({'event':'ready','port':server.server_port})
def stop():
    sys.stdin.buffer.read(1);server.shutdown()
threading.Thread(target=stop,daemon=True).start()
try:
    server.serve_forever(poll_interval=0.1)
finally:
    server.server_close();sys.setprofile(None);threading.setprofile(None)
    emit({'event':'shutdown','loopback_blocked':guard.blocked_attempts});guard.restore()
'''


def _events(path):
    try:
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]
    except (OSError, ValueError):
        return []


def _exchange(port, method, route, payload=None, *, token=None, origin=None):
    headers={}
    if token is not None: headers['Authorization']='Bearer '+token
    if origin is not None: headers['Origin']=origin
    body=None if payload is None else json.dumps(payload,ensure_ascii=False,allow_nan=False).encode()
    if body is not None:
        headers.update({'Content-Type':'application/json','Content-Length':str(len(body))})
    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=65)
    try:
        connection.request(method,route,body=body,headers=headers)
        response=connection.getresponse()
        raw=response.read(4*1024*1024+1025)
        assert len(raw)<=4*1024*1024+1024
        return response.status,dict(response.getheaders()),json.loads(raw)
    finally:
        connection.close()


@contextmanager
def _host(tmp_path, scope, display, *, mode='research_local'):
    from tools.run_unit_tests import _unit_environment
    backend=os.environ.get('NEXAWEAVE_WORKBENCH_BACKEND_PYTHON')
    python=os.environ.get('KNOWLEDGE_PYTHON');bootstrap=os.environ.get('KNOWLEDGE_BOOTSTRAP_SCRIPT')
    assert backend and python and bootstrap, 'Main-supplied locked interpreters/installed bootstrap required'
    assert Path(backend).is_absolute() and Path(backend).is_file()
    assert 'site-packages' in Path(bootstrap).parts and Path(bootstrap).name=='read_bootstrap.py'
    assert Path(bootstrap).with_name('source_bootstrap.py').is_file()
    env=_unit_environment(tmp_path)
    for key in list(env):
        if key.startswith(('KNOWLEDGE_','LLM_','OPENAI_','DEEPSEEK_','ZEP_')) or key.upper() in {
            'PYTHONPATH','PYTHONHOME','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY',
            'PGHOSTADDR','PGSERVICE','PGSERVICEFILE','PGPASSFILE','PGOPTIONS'}:
            env.pop(key,None)
    pg=conninfo_to_dict(os.environ['PROJECT_STORE_POSTGRES_TEST_DSN'])
    token='0123456789abcdef'*4
    env.update(SOURCE_HTTP_TEST_ROOT=str(Path(__file__).resolve().parents[3]),
        NEXAWEAVE_APP_MODE=mode,PYTHON_DOTENV_DISABLED='1',FLASK_HOST='127.0.0.1',FLASK_DEBUG='0',
        NEXAWEAVE_ALLOWED_ORIGINS='http://localhost:3000',KNOWLEDGE_READ_TOKEN=token,
        KNOWLEDGE_PYTHON=python,KNOWLEDGE_BOOTSTRAP_SCRIPT=bootstrap,KNOWLEDGE_PRINCIPAL='owner',
        KNOWLEDGE_DISPLAY_GRAPH_ID=display,KNOWLEDGE_BOUND_SCOPE_JSON=scope.model_dump_json(),
        KNOWLEDGE_PG_HOST=pg['host'],KNOWLEDGE_PG_PORT=pg['port'],KNOWLEDGE_PG_DATABASE=pg['dbname'],
        KNOWLEDGE_PG_USER=pg['user'],KNOWLEDGE_PG_PASSWORD=pg['password'],
        KNOWLEDGE_NEO4J_URI='bolt://127.0.0.1:17687',KNOWLEDGE_NEO4J_USER='neo4j',
        KNOWLEDGE_NEO4J_PASSWORD='unused-owned-source-fixture')
    output=tmp_path/'source-http-events.jsonl';errors=tmp_path/'source-http-errors.log'
    process=None
    try:
        with output.open('wb') as out,errors.open('wb') as err:
            process=subprocess.Popen([backend,'-I','-u','-c',_HTTP_CHILD],stdin=subprocess.PIPE,
                stdout=out,stderr=err,cwd=tmp_path,env=env,shell=False,close_fds=True,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            deadline=time.monotonic()+20
            ready=[]
            while time.monotonic()<deadline and process.poll() is None:
                ready=[event for event in _events(output) if event.get('event')=='ready']
                if ready: break
                time.sleep(0.05)
            if not ready: pytest.fail('source HTTP readiness failed; private diagnostics retained',pytrace=False)
            yield ready[0]['port'],token,output
            process.stdin.write(b'x');process.stdin.flush();process.stdin.close()
            process.wait(timeout=10)
            assert process.returncode==0
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait(timeout=5)
            if process.stdin is not None and not process.stdin.closed: process.stdin.close()
    events=_events(output)
    spawned=[event for event in events if event.get('event')=='spawn']
    cleaned=[event for event in events if event.get('event')=='cleanup']
    assert spawned and len(spawned)==len(cleaned) and all(event['safe'] for event in spawned)
    assert all(event['closed'] for event in cleaned)
    assert {'event':'shutdown','loopback_blocked':0} in events


def _seed(factory):
    workspace,project,graph=uuid4(),uuid4(),uuid4()
    before=ProjectStore(factory).create('owner',workspace,project,'proj_1',snapshot())
    scope=KnowledgeScope(workspace_id=workspace,project_id=project,graph_id=graph,layer=Layer.source)
    display='source_'+graph.hex
    ScopeBindingStore(factory).bind('owner',display,scope)
    return scope,display,before


def _payload(revision, name, content, kind='text'):
    binary=content.encode() if kind=='text' else content
    return {'schema_version':1,'source_revision':str(revision),'source_name':name,'format':kind,
        'content':content if kind=='text' else base64.b64encode(content).decode('ascii'),
        'input_sha256':hashlib.sha256(binary).hexdigest()}


def test_actual_http_text_docx_retention_restart_collision_and_owned_children(factory,tmp_path):
    scope,display,before=_seed(factory)
    sources=SourceStore(factory)
    text='A😀猫\r\n'+('long 猫😀 passage '*2500)
    revision=uuid4()
    payload=_payload(revision,'owned Unicode text',text)
    route='/api/source/retain/'+display
    with _host(tmp_path,scope,display) as (port,token,events):
        assert _exchange(port,'POST',route,payload)[0]==401
        assert _exchange(port,'POST',route,payload,token=token,origin='http://denied.example')[0]==403
        assert _exchange(port,'POST',route,dict(payload,principal='other'),token=token)[0]==400
        assert _exchange(port,'POST','/api/source/retain/wrong_graph',payload,token=token)[0]==404
        assert not [e for e in _events(events) if e.get('event')=='spawn']
        status,headers,response=_exchange(port,'POST',route,payload,token=token,origin='http://localhost:3000')
        assert status==200 and response['success'] is True
        assert headers['Cache-Control']=='no-store' and headers['X-Content-Type-Options']=='nosniff'
        assert headers['Access-Control-Allow-Origin']=='http://localhost:3000'
        first=response['data'];record=sources.get_source('owner',scope.project_id,revision)
        assert record.text==text and len(record.passages)>1
        assert first['binary_retained'] is first['graph_ingestion_executed'] is False
        assert first['source']['text_sha256']==hashlib.sha256(text.encode()).hexdigest()
        assert first['source']['source_revision']==str(revision) and 'text' not in first
        assert ''.join(p.excerpt for p in record.passages)==text
        status,_,repeated=_exchange(port,'POST',route,payload,token=token)
        assert status==200 and repeated['data']==first
        changed=_payload(revision,'changed',text+' changed')
        assert _exchange(port,'POST',route,changed,token=token)[0]==409
        assert sources.get_source('owner',scope.project_id,revision)==record
        status,_,read=_exchange(port,'GET',f'/api/source/item/{display}/{revision}',token=token)
        assert status==200 and read['data']['text']==text and read['data']['source']==first['source']
        for actual,passage in zip(read['data']['passages'],record.passages,strict=True):
            assert actual['evidence_id']==str(passage.evidence_id)
            assert text[actual['start']:actual['end']]==passage.excerpt
            assert actual['excerpt_sha256']==hashlib.sha256(passage.excerpt.encode()).hexdigest()
        docx=_package(_paragraph('A😀猫')+'<w:tbl><w:tr><w:tc>'+_paragraph('cell β')+'</w:tc></w:tr></w:tbl>')
        docx_revision=uuid4()
        status,_,retained=_exchange(port,'POST',route,_payload(docx_revision,'owned document 猫',docx,'docx'),token=token)
        assert status==200
        docx_record=sources.get_source('owner',scope.project_id,docx_revision)
        assert docx_record.text=='A😀猫\n\ncell β'
        assert [p.excerpt for p in docx_record.passages]==['A😀猫','cell β']
        assert all(p.page is None for p in docx_record.passages)
        assert retained['data']['extraction']['format']=='docx'
        assert retained['data']['binary_retained'] is retained['data']['graph_ingestion_executed'] is False
        status,_,library=_exchange(port,'GET','/api/source/library/'+display,token=token)
        assert status==200 and library['data']['has_more'] is False
        assert {s['source_revision'] for s in library['data']['sources']}=={str(revision),str(docx_revision)}
        assert ProjectStore(factory).get('owner',scope.project_id)==before
        assert ProjectStore(factory).history('owner',scope.project_id)==[before]
    # New HTTP host / fresh installed children read the same persisted source.
    restarted=tmp_path/'restart';restarted.mkdir()
    with _host(restarted,scope,display) as (port,token,_):
        status,_,response=_exchange(port,'GET',f'/api/source/item/{display}/{revision}',token=token)
        assert status==200 and response['data']['text']==text


_PG_ONLY_PROBE = r'''
import importlib.abc,json,os,runpy,sys,threading
from pathlib import Path
root=os.environ.pop('SOURCE_HTTP_TEST_ROOT')
sys.path.insert(0,root)
from tools.run_unit_tests import LoopbackOnlySockets
sys.path.remove(root)
guard=LoopbackOnlySockets();guard.install()
blocked=('graphiti_core','openai','neo4j','nexaweave_knowledge.provider','nexaweave_knowledge.read_runtime','pymupdf','fitz')
class NoProviders(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if any(fullname==item or fullname.startswith(item+'.') for item in blocked):
            raise AssertionError('source-only child imported a provider')
sys.meta_path.insert(0,NoProviders())
connections=set();closed=set()
def observe(frame,event,arg):
    module=frame.f_globals.get('__name__','');name=frame.f_code.co_name
    if event=='return' and module=='psycopg.connection' and name=='connect' and arg is not None:
        connections.add(id(arg))
    if event=='return' and module=='psycopg.connection' and name=='close':
        connection=frame.f_locals.get('self')
        if connection is not None and connection.closed: closed.add(id(connection))
sys.setprofile(observe);threading.setprofile(observe)
status=98
try:
    import nexaweave_knowledge.source_bootstrap as module
    assert 'site-packages' in Path(module.__file__).resolve().parts
    assert all(not os.environ.get(key) for key in ('LLM_API_KEY','OPENAI_API_KEY','DEEPSEEK_API_KEY',
        'ZEP_API_KEY','KNOWLEDGE_NEO4J_URI','KNOWLEDGE_NEO4J_PASSWORD','KNOWLEDGE_READ_TOKEN'))
    status=module.main()
finally:
    sys.setprofile(None);threading.setprofile(None);guard.restore()
if guard.blocked_attempts or not connections or not connections<=closed: status=95
if any(name==item or name.startswith(item+'.') for item in blocked for name in sys.modules): status=96
raise SystemExit(status)
'''


def test_actual_installed_source_child_reads_owned_pg_without_provider_imports(factory,tmp_path):
    from tools.run_unit_tests import _unit_environment
    scope,display,_=_seed(factory)
    python=os.environ.get('KNOWLEDGE_PYTHON')
    assert python and Path(python).is_absolute() and Path(python).is_file()
    pg=conninfo_to_dict(os.environ['PROJECT_STORE_POSTGRES_TEST_DSN'])
    env=_unit_environment(tmp_path)
    for key in list(env):
        if key.startswith(('KNOWLEDGE_','LLM_','OPENAI_','DEEPSEEK_','ZEP_')) or key.upper() in {
            'PYTHONPATH','PYTHONHOME','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY',
            'PGHOSTADDR','PGSERVICE','PGSERVICEFILE','PGPASSFILE','PGOPTIONS'}:
            env.pop(key,None)
    env.update(SOURCE_HTTP_TEST_ROOT=str(Path(__file__).resolve().parents[3]),
        KNOWLEDGE_PRINCIPAL='owner',KNOWLEDGE_DISPLAY_GRAPH_ID=display,
        KNOWLEDGE_BOUND_SCOPE_JSON=scope.model_dump_json(),KNOWLEDGE_PG_HOST=pg['host'],
        KNOWLEDGE_PG_PORT=pg['port'],KNOWLEDGE_PG_DATABASE=pg['dbname'],
        KNOWLEDGE_PG_USER=pg['user'],KNOWLEDGE_PG_PASSWORD=pg['password'])
    request_id=str(uuid4())
    request=json.dumps({'version':1,'request_id':request_id,'method':'context',
        'scope':scope.model_dump(mode='json'),'payload':{}},separators=(',',':')).encode()
    completed=subprocess.run([python,'-I','-u','-c',_PG_ONLY_PROBE],
        input=len(request).to_bytes(4,'big')+request,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        cwd=tmp_path,env=env,timeout=65,check=False,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if completed.returncode or completed.stderr:
        (tmp_path/'source-probe-private-errors.log').write_bytes(completed.stderr)
        pytest.fail('installed PG-only source probe failed; private diagnostics retained',pytrace=False)
    assert 4<len(completed.stdout)<=4*1024*1024+1028
    assert int.from_bytes(completed.stdout[:4],'big')==len(completed.stdout)-4
    response=json.loads(completed.stdout[4:])
    assert response['request_id']==request_id and response['ok'] is True
    assert response['result']['scope']==scope.model_dump(mode='json')
    assert response['result']['binary_retained'] is response['result']['graph_ingestion_executed'] is False


def test_actual_http_pdf_fixed_child_pg_pages_restart_and_private_denials(factory,tmp_path):
    from test_pdf_source import pdf_bytes
    from nexaweave_storage.pdf import extract_pdf
    scope,display,before=_seed(factory)
    binary=pdf_bytes(['Beginning 猫','','Middle 雪','End 中文'])
    extracted=extract_pdf(binary)
    revision=uuid4()
    payload=_payload(revision,'owned PDF 猫',binary,'pdf')
    route='/api/source/retain/'+display
    sources=SourceStore(factory)
    with _host(tmp_path,scope,display) as (port,token,events):
        assert _exchange(port,'POST',route,payload)[0]==401
        assert _exchange(port,'POST',route,payload,token=token,origin='http://denied.example')[0]==403
        assert _exchange(port,'POST',route,dict(payload,input_sha256='0'*64),token=token)[0]==400
        assert _exchange(port,'POST',route,dict(payload,content=payload['content']+'='),token=token)[0]==400
        assert _exchange(port,'POST',route,dict(payload,path='private'),token=token)[0]==400
        assert not [e for e in _events(events) if e.get('event')=='spawn']
        status,headers,response=_exchange(port,'POST',route,payload,token=token)
        assert status==200 and response['success'] is True
        assert headers['Cache-Control']=='no-store' and headers['X-Content-Type-Options']=='nosniff'
        first=response['data'];receipt=first['extraction']
        record=sources.get_source('owner',scope.project_id,revision)
        assert record.text==extracted.text
        assert [p.page for p in record.passages]==[1,3,4]
        assert receipt['page_count']==4 and receipt['empty_page_count']==1
        assert receipt['declared_passage_count']==3 and receipt['coverage']==['page_text']
        assert '\n\n'.join(receipt['page_text'])==record.text
        assert first['binary_retained'] is first['graph_ingestion_executed'] is False
        for key in ('input_digest_persisted','blocks_persisted','original_document_verified',
                'binary_persistently_bound','ocr_performed'):
            assert receipt[key] is False
        assert receipt['input_sha256']==payload['input_sha256'] and receipt['input_hash_verified'] is True
        assert receipt['page_layout']==receipt['semantic_quality']=='unknown'
        status,_,repeat=_exchange(port,'POST',route,payload,token=token)
        assert status==200 and repeat['data']==first
        assert _exchange(port,'POST',route,dict(payload,source_name='changed'),token=token)[0]==409
        malformed=_payload(uuid4(),'malformed',b'%PDF-1.7\nprivate malformed\n%%EOF','pdf')
        status,_,error=_exchange(port,'POST',route,malformed,token=token)
        assert status==400 and error=={'success':False,'error':{'code':'invalid_request'}}
        assert sources.get_source('owner',scope.project_id,revision)==record
        assert len(sources.list_sources('owner',scope.project_id))==1
        assert ProjectStore(factory).get('owner',scope.project_id)==before
        assert ProjectStore(factory).history('owner',scope.project_id)==[before]
    restart=tmp_path/'pdf-restart';restart.mkdir()
    with _host(restart,scope,display) as (port,token,_):
        status,_,response=_exchange(port,'GET',f'/api/source/item/{display}/{revision}',token=token)
        assert status==200 and response['data']['text']==record.text
        assert response['data']['passages']==first['passages'] and 'extraction' not in response['data']
        assert 'input_sha256' not in response['data']['source']
        for actual,passage in zip(response['data']['passages'],record.passages,strict=True):
            assert record.text[actual['start']:actual['end']]==passage.excerpt
            assert actual['excerpt_sha256']==hashlib.sha256(passage.excerpt.encode()).hexdigest()
            evidence=sources.resolve_evidence('owner',scope.project_id,passage.evidence_id)
            assert evidence.excerpt_sha256==actual['excerpt_sha256'] and evidence.declared_page==actual['page']
        status,_,library=_exchange(port,'GET','/api/source/library/'+display,token=token)
        assert status==200 and library['data']['sources']==[first['source']]


def test_actual_http_v2_original_bytes_after_restart(factory, tmp_path):
    from test_pdf_source import pdf_bytes
    from nexaweave_storage.pdf import extract_pdf
    scope, display, _ = _seed(factory)
    binary = pdf_bytes(['Original 猫', '', 'End 雪'])
    revision = uuid4()
    payload = dict(_payload(revision, 'owned original.pdf', binary, 'pdf'), schema_version=2)
    retained = '/api/source/retain-original/' + display
    metadata_route = f'/api/source/original-metadata/{display}/{revision}'
    read_route = f'/api/source/original/{display}/{revision}'
    with _host(tmp_path, scope, display) as (port, token, _):
        assert _exchange(port, 'POST', retained, payload)[0] == 401
        assert _exchange(port, 'POST', retained, payload, token=token,
                         origin='http://denied.example')[0] == 403
        status, headers, first = _exchange(port, 'POST', retained, payload, token=token)
        assert status == 200 and first['success'] is True
        assert headers['Cache-Control'] == 'no-store' and headers['X-Content-Type-Options'] == 'nosniff'
        receipt = first['data']
        assert receipt['schema_version'] == 2 and receipt['binary_retained'] is True
        assert receipt['binary']['sha256'] == hashlib.sha256(binary).hexdigest()
        assert receipt['extraction']['ocr_performed'] is False
        assert _exchange(port, 'POST', retained, payload, token=token)[2]['data'] == receipt
        assert _exchange(port, 'POST', retained, dict(payload, source_name='changed'), token=token)[0] == 409
    restart = tmp_path / 'original-restart'; restart.mkdir()
    with _host(restart, scope, display) as (port, token, _):
        status, _, meta = _exchange(port, 'GET', metadata_route, token=token)
        assert status == 200 and meta['data']['binary'] == receipt['binary']
        status, headers, read = _exchange(port, 'GET', read_route, token=token)
        assert status == 200 and headers['Cache-Control'] == 'no-store'
        assert headers['X-Content-Type-Options'] == 'nosniff'
        assert base64.b64decode(read['data']['content_base64'], validate=True) == binary
        assert read['data']['binary'] == meta['data']['binary']
        assert _exchange(port, 'GET', read_route)[0] == 401
        assert _exchange(port, 'GET', read_route, token=token,
                         origin='http://denied.example')[0] == 403
        assert _exchange(port, 'GET', f'/api/source/item/{display}/{revision}', token=token)[2]['data']['text'] == extract_pdf(binary).text

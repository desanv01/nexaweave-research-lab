"""Main qualification: real HTTP process -> fixed isolated child -> native PG.

Explicit installed runtimes, offline native recordings and passive process
observations. Descriptive fixture statistics do not establish causal truth.
"""
import ast
from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import subprocess
import time

import pytest
from test_native_experiment_http import experiment_python, protected_fixture
from test_native_experiments import connection_factory, cohort_fixture, hashes

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]


def events(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]


@contextmanager
def server(settings, manifest, raw, tmp_path, *, bound=True):
    from app.services.native_experiment_http_client import CHILD_KEYS
    from app.services.native_experiment_contracts import sha
    from app.utils.owned_process import OwnedProcess
    from tools.run_unit_tests import _unit_environment
    python = os.environ.get('MIROFISH_EXPERIMENT_TEST_HTTP_PYTHON')
    bootstrap = os.environ.get('MIROFISH_EXPERIMENT_TEST_BOOTSTRAP')
    assert python and Path(python).is_absolute() and Path(python).is_file()
    assert bootstrap and Path(bootstrap).is_absolute() and Path(bootstrap).is_file()
    assert Path(bootstrap).name == 'read_bootstrap.py' and 'site-packages' in Path(bootstrap).parts
    # Reuse the accepted fixed HTTP observer source without importing its graph
    # fixtures or SDKs into this full native test parent.
    tree = ast.parse((ROOT/'services/knowledge/tests/test_workbench_http_integration.py').read_text(encoding='utf-8'))
    source = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == '_HTTP_CHILD' for t in n.targets))
    source = source.replace("blocked=('mirofish_knowledge'", "blocked=('mirofish_execution','mirofish_storage','mirofish_knowledge'")
    source = source.replace("Path(arguments[3]).name in {'read_bootstrap.py','evidence_bootstrap.py'}\n                  and 'site-packages' in Path(arguments[3]).parts",
        "Path(arguments[3]).resolve()==(Path(os.environ['WORKBENCH_TEST_ROOT'])/'backend/app/services/native_experiment_http_child.py').resolve()")
    start = source.index('and all(k in allowed or k in {')
    end = source.index(' for k in env))', start)
    source = source[:start] + 'and all(k in allowed or k in '+repr(set(CHILD_KEYS))+source[end:]
    source = source.replace('from werkzeug.serving import make_server',
        'from werkzeug.serving import make_server, WSGIRequestHandler\nclass QuietHandler(WSGIRequestHandler):\n    def log_request(self, *args, **kwargs): pass')
    source = source.replace('create_app(),threaded=True)', 'create_app(),threaded=True,request_handler=QuietHandler)')
    env = _unit_environment(tmp_path)
    for key in list(env):
        if key.startswith(('KNOWLEDGE_', 'LLM_', 'OPENAI_', 'DEEPSEEK_', 'ZEP_')) or key.upper() in {
                'PYTHONPATH','PYTHONHOME','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY'}:
            env.pop(key, None)
    env.update(WORKBENCH_TEST_ROOT=str(ROOT), MIROFISH_APP_MODE='research_local',
        FLASK_HOST='127.0.0.1', FLASK_DEBUG='0', PYTHON_DOTENV_DISABLED='1',
        MIROFISH_ALLOWED_ORIGINS='http://localhost:3000', KNOWLEDGE_READ_TOKEN=settings.token,
        KNOWLEDGE_PYTHON=settings.python, KNOWLEDGE_BOOTSTRAP_SCRIPT=bootstrap,
        KNOWLEDGE_PRINCIPAL=settings.principal, KNOWLEDGE_DISPLAY_GRAPH_ID=settings.display_graph_id,
        KNOWLEDGE_BOUND_SCOPE_JSON=json.dumps(settings.scope), KNOWLEDGE_NEO4J_URI='bolt://127.0.0.1:17687',
        KNOWLEDGE_NEO4J_USER='fixture', KNOWLEDGE_NEO4J_PASSWORD='explicit-unused-fixture')
    env.update(settings.child_environment)
    if bound:
        env.update(KNOWLEDGE_EXPERIMENT_MANIFEST=str(manifest), KNOWLEDGE_EXPERIMENT_MANIFEST_SHA256=sha(raw))
    log=tmp_path/'http-events.jsonl';error=tmp_path/'http-private-errors.log'
    owner=OwnedProcess();process=None
    with log.open('wb') as output,error.open('wb') as diagnostics:
        try:
            process=owner.start(subprocess.Popen,[python,'-I','-u','-c',source],cwd=tmp_path,env=env,
                stdin=subprocess.PIPE,stdout=output,stderr=diagnostics,shell=False,close_fds=True)
            deadline=time.monotonic()+20;ready=[]
            while time.monotonic()<deadline and process.poll() is None:
                ready=[v for v in events(log) if v.get('event')=='ready']
                if ready:break
                time.sleep(.05)
            assert ready, 'Main HTTP runtime did not become ready; private diagnostics retained'
            yield ready[0]['port'],log
        finally:
            try:
                if process is not None and process.poll() is None:
                    process.stdin.write(b'x');process.stdin.flush();process.stdin.close()
                    process.wait(timeout=15)
            finally:
                owner.stop([])
                assert owner.closed and (os.name!='nt' or owner.tree_empty)
    values=events(log)
    assert process.returncode==0 and error.read_bytes()==b''
    assert values[-1]=={'event':'shutdown','loopback_blocked':0}
    spawned=[v for v in values if v.get('event')=='spawn']
    closed=[v for v in values if v.get('event')=='cleanup']
    assert all(v['safe'] and v['bootstrap']=='native_experiment_http_child.py' for v in spawned)
    assert len(spawned)==len(closed) and all(v['closed'] for v in closed)


def exchange(port, method, path, *, token=None, origin=None, value=None):
    headers={}
    if token is not None:headers['Authorization']='Bearer '+token
    if origin is not None:headers['Origin']=origin
    body=None if value is None else json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
    if body is not None:headers.update({'Content-Type':'application/json','Content-Length':str(len(body))})
    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=95)
    try:
        connection.request(method,path,body=body,headers=headers)
        response=connection.getresponse();data=response.read(512*1024+1)
        assert len(data)<=512*1024
        return response.status,dict(response.getheaders()),json.loads(data)
    finally:connection.close()


def test_actual_separate_http_native_comparison_and_restart(protected_fixture, connection_factory, tmp_path):
    from app.services.native_experiment_http_client import catalog, comparison
    from mirofish_execution.native_run_store import NativeRunStore
    settings,_,cohort,runs,manifest,raw=protected_fixture
    store=NativeRunStore(connection_factory)
    before=[store.get('owner',m.request.run_id) for m in cohort.members]
    files=[hashes(root) for _,root,_ in runs]
    request={'version':1,'title':'Actual HTTP observations 猫','member_ids':[m.member_id for m in reversed(cohort.members)]}
    results=[]
    for restart in range(2):
        directory=tmp_path/str(restart);directory.mkdir()
        with server(settings,manifest,raw,directory) as (port,log):
            assert exchange(port,'GET','/api/experiments/catalog')[0]==401
            assert exchange(port,'GET','/api/experiments/catalog',token=settings.token,origin='https://foreign.example')[0]==403
            assert exchange(port,'GET','/api/experiments/catalog?invalid=1',token=settings.token)[0]==400
            assert not [v for v in events(log) if v.get('event')=='spawn']
            status,headers,body=exchange(port,'GET','/api/experiments/catalog',token=settings.token)
            assert status==200 and headers['Cache-Control']=='no-store'
            cat=catalog(body['data'],settings.scope['project_id'])
            status,headers,body=exchange(port,'POST','/api/experiments/compare',token=settings.token,value=request)
            assert status==200 and headers['X-Content-Type-Options']=='nosniff'
            result=comparison(body['data'],cat,request)
            assert result['accounting']==dict(successful=2,failed=1,pending=1,cancelled=1,uncertain=1)
            assert [m['seed'] for m in cat['members'][-3:]]==[str(-(2**63)),str(2**63-1),str(2**53+1)]
            for m in result['members']:
                if m['disposition']!='successful':assert m['metrics'] is None
                else:
                    expected=next(run[2] for run in runs if run[0].member_id==m['member_id'])
                    for platform in ('twitter','reddit'):
                        assert m['metrics'][platform]['logged_action_total']==expected[platform]['total']
                        assert m['metrics'][platform]['logged_action_by_type']==expected[platform]['actions']
            results.append((cat,result))
            assert len([v for v in events(log) if v.get('event')=='spawn'])==2
    assert results[0]==results[1]
    assert manifest.read_bytes()==raw and [hashes(root) for _,root,_ in runs]==files
    assert [store.get('owner',m.request.run_id) for m in cohort.members]==before


def test_actual_separate_http_optional_unavailable(protected_fixture,tmp_path):
    settings,_,_,_,manifest,raw=protected_fixture
    with server(settings,manifest,raw,tmp_path,bound=False) as (port,log):
        status,_,body=exchange(port,'GET','/api/experiments/catalog',token=settings.token)
        assert status==503 and body=={'success':False,'error':{'code':'experiment_unavailable'}}
        status,_,body=exchange(port,'GET','/health')
        assert status==200 and not any('experiment' in c for c in body['capabilities'])
        assert not [v for v in events(log) if v.get('event')=='spawn']

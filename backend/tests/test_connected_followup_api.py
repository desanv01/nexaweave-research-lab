"""Cold seven-route boundary and exact safe errors."""
import json
from types import SimpleNamespace
from flask import Flask
from test_connected_followup_client import planned


def test_cold_registration_and_strict_plan_body():
    from app.connected_followup_api import register_connected_followup_routes
    value=planned(); binding=value['binding']; report=binding['report']
    settings=SimpleNamespace(display_graph_id=binding['display_graph_id'],
        principal=binding['principal'],scope=binding['scope'])
    calls=[]
    class Facade:
        def execute(self,method,graph_id,payload):
            calls.append((method,graph_id,payload)); return value
    app=Flask(__name__)
    register_connected_followup_routes(app,settings,followup_facade=Facade())
    client=app.test_client()
    path='/api/connected-followup/plan/'+binding['display_graph_id']
    payload=dict(schema_version=1,turn_id=value['turn_id'],report_id=report['report_id'],
        report_plan_sha256=report['plan_sha256'],**value['options'])
    raw=json.dumps(payload,separators=(',',':')).encode()
    reply=client.post(path,data=raw,content_type='application/json')
    assert reply.status_code==200 and reply.json['data']==value
    assert reply.headers['Cache-Control']=='no-store' and len(calls)==1
    duplicate=raw[:-1]+b',"question":"untrusted"}'
    rejected=client.post(path,data=duplicate,content_type='application/json')
    assert rejected.status_code==400 and rejected.json==dict(success=False,error=dict(code='invalid_request'))
    assert len(calls)==1
    query=client.post(path+'?principal=other',data=raw,content_type='application/json')
    assert query.status_code==400 and len(calls)==1


def test_missing_optional_facade_refuses_only_followup_operation():
    from app.connected_followup_api import register_connected_followup_routes
    value=planned(); binding=value['binding']; report=binding['report']
    settings=SimpleNamespace(display_graph_id=binding['display_graph_id'],
        principal=binding['principal'],scope=binding['scope'])
    app=Flask(__name__); register_connected_followup_routes(app,settings)
    client=app.test_client()
    assert client.get('/').status_code==404
    payload=dict(schema_version=1,turn_id=value['turn_id'],report_id=report['report_id'],
        report_plan_sha256=report['plan_sha256'],**value['options'])
    result=client.post('/api/connected-followup/plan/'+binding['display_graph_id'],json=payload)
    assert result.status_code==503 and result.json==dict(success=False,error=dict(code='followup_unavailable'))

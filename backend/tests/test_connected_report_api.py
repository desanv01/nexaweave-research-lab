"""Route admission and hostile injected replies with no optional SDK imports."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from test_connected_report_client import declaration, public_result, reference


def client(facade=None):
    from flask import Flask
    from app.connected_report_api import register_connected_report_routes
    result, _ = public_result()
    settings = SimpleNamespace(principal='owner', display_graph_id='display-1', scope=result['binding']['scope'])
    app = Flask(__name__); app.config['TESTING'] = True
    register_connected_report_routes(app, settings, facade)
    return app.test_client()


def test_cold_default_unavailable_no_model_and_six_explicit_routes():
    cli = client()
    result, _ = public_result()
    for method in ('plan', 'start', 'status', 'cancel', 'read', 'download'):
        payload = declaration() if method == 'plan' else reference(result)
        if method == 'download':
            payload.update(kind='report', section_index=None)
        reply = cli.post('/api/connected-report/' + method + '/display-1', json=payload)
        assert reply.status_code == 503 and reply.json == dict(success=False, error=dict(code='report_unavailable'))
        assert reply.headers['Cache-Control'] == 'no-store'
        assert reply.headers['X-Content-Type-Options'] == 'nosniff'


@pytest.mark.parametrize('raw', [b'{"schema_version":1,"schema_version":1}', b'{"report_id":"\xff"}', b'[]', b'x'*32769],
                         ids=['duplicate-key', 'invalid-utf8', 'array', 'oversize'])
def test_bad_body_refuses_before_injected_facade(raw):
    def forbidden(*args):
        pytest.fail('malformed request reached trusted facade')
    reply = client(SimpleNamespace(execute=forbidden)).post('/api/connected-report/plan/display-1', data=raw, content_type='application/json')
    assert reply.status_code == 400 and reply.json['error']['code'] == 'invalid_request'


def test_injected_metadata_is_validated_and_raw_cause_never_exposed():
    result, _ = public_result()
    result['binding']['principal'] = 'attacker'
    facade = SimpleNamespace(execute=lambda *args: result)
    reply = client(facade).post('/api/connected-report/plan/display-1', json=declaration())
    assert reply.status_code == 502 and reply.json['error']['code'] == 'invalid_reply'
    def fail(*args):
        raise RuntimeError('api key and private path')
    reply = client(SimpleNamespace(execute=fail)).post('/api/connected-report/plan/display-1', json=declaration())
    assert reply.status_code == 500 and 'private' not in reply.text and 'key' not in reply.text


def test_mime_query_and_arbitrary_download_names_rejected():
    def forbidden(*args):
        pytest.fail('admission reached facade')
    cli = client(SimpleNamespace(execute=forbidden))
    assert cli.post('/api/connected-report/plan/display-1?q=1', json=declaration()).status_code == 400
    assert cli.post('/api/connected-report/plan/display-1', data='{}', content_type='text/plain').status_code == 400
    result, _ = public_result()
    for kind, index in (('../../secret', None), ('report', 1), ('section', True), ('section', 9)):
        reply = cli.post('/api/connected-report/download/display-1', json=dict(reference(result), kind=kind, section_index=index))
        assert reply.status_code == 400

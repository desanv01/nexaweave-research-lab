"""A genuinely fresh read-mode interpreter must not load legacy SDKs."""

import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID


def _uid(number):
    return str(UUID(int=number))


def _isolated_app_check(script):
    backend = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.update(TEST_BACKEND=str(backend), PYTHON_DOTENV_DISABLED='1')
    result = subprocess.run([sys.executable, '-I', '-c', script], env=environment,
                            cwd=backend, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'lazy app compatibility passed'


def test_process_target_imports_without_flask_or_model_sdks():
    _isolated_app_check(r'''
import importlib.abc, inspect, os, sys
sys.path.insert(0, os.environ['TEST_BACKEND'])
blocked = {'flask', 'openai', 'zep_cloud', 'graphiti_core', 'torch', 'transformers', 'camel', 'oasis'}
class DenyWebAndModels(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in blocked:
            raise AssertionError('eager web/model import: ' + fullname)
sys.meta_path.insert(0, DenyWebAndModels())
import app
from app import create_app, Config
from app.config import Config as OriginalConfig
from app.services.report_process import ReportProcess
assert Config is OriginalConfig and create_app.__defaults__ == (OriginalConfig,)
assert inspect.signature(create_app).parameters['config_class'].default is OriginalConfig
assert {'Flask', 'jsonify', 'request'} <= set(dir(app))
assert not {'Flask', 'jsonify', 'request'} & set(vars(app))
assert not any(name.split('.')[0] in blocked for name in sys.modules)
try:
    app.no_such_public_export
except AttributeError:
    pass
else:
    raise AssertionError('unknown app attribute was accepted')
print('lazy app compatibility passed')
''')


def test_lazy_public_exports_factory_signature_and_live_monkeypatch_compatibility():
    _isolated_app_check(r'''
import inspect, os, sys
from types import SimpleNamespace
sys.path.insert(0, os.environ['TEST_BACKEND'])
import app
from app.config import Config
assert app.create_app.__defaults__ == (Config,)
parameters = inspect.signature(app.create_app).parameters
expected = ('config_class', 'read_facade', 'evidence_facade', 'source_facade', 'ingestion_facade',
            'experiment_facade', 'preparation_facade', 'native_launch_facade',
            'native_observations_facade', 'connected_report_facade', 'connected_followup_facade')
assert tuple(parameters) == expected
assert all(parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
           and parameters[name].default is None for name in expected[1:])
assert 'flask' not in sys.modules
# A factory call must retain supplied globals rather than loading fallback Flask.
patched = {name: object() for name in ('Flask', 'jsonify', 'request')}
vars(app).update(patched)
original_mode = app.app_mode
app.app_mode = lambda *args: 'invalid'
try:
    app.create_app()
except ValueError as error:
    assert str(error) == 'invalid application mode'
else:
    raise AssertionError('invalid mode was accepted')
assert all(vars(app)[name] is value for name, value in patched.items())
assert 'flask' not in sys.modules
for name in patched: delattr(app, name)
app.app_mode = original_mode
from app import Flask, jsonify, request
import flask
assert Flask is flask.Flask and jsonify is flask.jsonify and request is flask.request
public = {}
exec('from app import *', public)
assert public['Config'] is Config and public['create_app'] is app.create_app
assert all(public[name] is getattr(flask, name) for name in ('Flask', 'jsonify', 'request'))
calls = []
class Logger:
    def info(self, *args): pass
    def debug(self, *args): calls.append('request-log')
app.setup_logger = lambda name: Logger()
app.get_logger = lambda name: (_ for _ in ()).throw(AssertionError('stale logger'))
sys.modules['app.services.simulation_runner'] = SimpleNamespace(
    SimulationRunner=SimpleNamespace(register_cleanup=lambda: calls.append('cleanup-registration')))
sys.modules['app.api'] = SimpleNamespace(**{name: flask.Blueprint(name, __name__)
    for name in ('graph_bp', 'simulation_bp', 'report_bp')})
app.app_mode = lambda *args: 'legacy'
seen = []
def flask_factory(*args, **kwargs):
    seen.append(True)
    return flask.Flask(*args, **kwargs)
app.Flask = flask_factory
class ExplicitConfig(Config):
    TESTING = True
    CUSTOM_COMPATIBILITY = 'retained'
    NEXAWEAVE_ALLOWED_ORIGINS = ('http://localhost:3000',)
application = app.create_app(ExplicitConfig)
assert app.Flask is flask_factory and seen == [True]
assert application.config['CUSTOM_COMPATIBILITY'] == 'retained'
assert calls == ['cleanup-registration']
# Callback lookup must see a patch installed AFTER factory construction.
app.get_logger = lambda name: Logger()
reply = application.test_client().get('/health')
assert reply.status_code == 200 and reply.json['service'] == 'NexaWeave Backend'
assert calls == ['cleanup-registration', 'request-log']
with application.app_context():
    original_jsonify = app.jsonify
    app.jsonify = lambda payload: calls.append('patched-jsonify') or original_jsonify(payload)
    response, status = app._origin_denied()
    assert status == 403 and response.json == {'success': False, 'error': 'Origin not allowed'}
assert calls[-1] == 'patched-jsonify'
print('lazy app compatibility passed')
''')


def test_readonly_cold_start_blocks_legacy_imports_without_model_keys(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    bootstrap = tmp_path / "site-packages" / "nexaweave_knowledge" / "read_bootstrap.py"
    bootstrap.parent.mkdir(parents=True)
    bootstrap.write_text("# trusted installed-layout fixture\n", encoding="utf-8")
    scope = {"schema_version": 1, "workspace_id": _uid(1), "project_id": _uid(2),
             "graph_id": _uid(3), "run_id": None, "branch_id": None, "layer": "source"}
    environment = os.environ.copy()
    for name in ("LLM_API_KEY", "ZEP_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
                 "FLASK_DEBUG", "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE",
                 "PGPASSFILE", "PGOPTIONS"):
        environment.pop(name, None)
    environment.update({
        "TEST_BACKEND": str(backend), "NEXAWEAVE_APP_MODE": "graphiti_readonly",
        "PYTHON_DOTENV_DISABLED": "1",
        "FLASK_HOST": "127.0.0.1", "KNOWLEDGE_PYTHON": sys.executable,
        "KNOWLEDGE_BOOTSTRAP_SCRIPT": str(bootstrap),
        "KNOWLEDGE_READ_TOKEN": "0123456789abcdef" * 4,
        "KNOWLEDGE_PRINCIPAL": "fixture-owner", "KNOWLEDGE_DISPLAY_GRAPH_ID": "display-1",
        "KNOWLEDGE_BOUND_SCOPE_JSON": json.dumps(scope, separators=(",", ":")),
        "KNOWLEDGE_PG_HOST": "127.0.0.1", "KNOWLEDGE_PG_PORT": "5432",
        "KNOWLEDGE_PG_DATABASE": "fixture", "KNOWLEDGE_PG_USER": "fixture",
        "KNOWLEDGE_PG_PASSWORD": "fixture-password", "KNOWLEDGE_NEO4J_URI": "bolt://localhost:7687",
        "KNOWLEDGE_NEO4J_USER": "neo4j", "KNOWLEDGE_NEO4J_PASSWORD": "fixture-password",
    })
    script = r'''
import builtins
import os
import sys
sys.path.insert(0, os.environ["TEST_BACKEND"])
original = builtins.__import__
blocked = ("openai", "zep_cloud", "graphiti_core", "torch", "transformers", "camel", "oasis")
def guarded(name, *args, **kwargs):
    if any(name == prefix or name.startswith(prefix + ".") for prefix in blocked):
        raise AssertionError("legacy SDK import during read-only cold start: " + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from app import create_app
app = create_app()
response = app.test_client().get("/health")
assert response.status_code == 200, response.get_data(as_text=True)
assert response.json["mode"] == "graphiti_readonly"
assert "population_preview" in response.json["capabilities"]
from app.services.knowledge_reader import EntityNode
from app.services.oasis_profile_generator import OasisProfileGenerator
generator = OasisProfileGenerator(basic_only=True)
profile = generator.generate_profile_from_entity(
    EntityNode("00000000-0000-0000-0000-000000000010", "Alice",
               ["Entity", "Person"], "Fixture summary", {}), 0, use_llm=False)
assert profile.source_entity_type == "Person"
assert generator.client is None and generator.zep_client is None
print("healthy cold read factory")
'''
    result = subprocess.run([sys.executable, "-I", "-c", script], env=environment,
                            cwd=backend, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "healthy cold read factory" in result.stdout

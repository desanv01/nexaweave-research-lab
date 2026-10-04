"""Main-run actual PG/recording/fixed-child plus Flask test-client sources.

Offline only. These sources do not qualify a separate lean socket HTTP runtime.
Main supplies the installed child interpreter and separate socket proof.
"""
from dataclasses import replace
import hashlib
import os
from pathlib import Path
from uuid import uuid4

import pytest

# Reuse accepted actual native fixtures; these imports occur only when Main runs.
from test_native_experiments import (cohort_fixture, connection_factory, hashes,
    install_offline_boundary)

pytestmark = pytest.mark.postgres


@pytest.fixture
def experiment_python():
    """Require Main's explicit installed runtime; never use the test runner."""
    value = os.environ.get('MIROFISH_EXPERIMENT_TEST_PYTHON')
    try:
        if (type(value) is not str or not 1 <= len(value) <= 4096 or '\x00' in value
                or not Path(value).is_absolute() or not Path(value).is_file()
                or not os.access(value, os.X_OK)):
            raise ValueError
    except (ValueError, TypeError, OSError):
        pytest.fail('MIROFISH_EXPERIMENT_TEST_PYTHON must explicitly name an absolute installed knowledge interpreter executable', pytrace=False)
    return value


@pytest.fixture
def protected_fixture(experiment_python, connection_factory, cohort_fixture, tmp_path, monkeypatch):
    from app.services.native_experiment_contracts import ExperimentCohort, ExperimentMember, canonical, sha
    from app.services.native_experiment_http_facade import ExperimentSettings, NativeExperimentFacade
    from mirofish_execution.native_run_contracts import NativeChildIdentity, NativeRunReceipt
    from mirofish_execution.native_run_store import NativeRunStore
    from psycopg.conninfo import conninfo_to_dict
    from types import SimpleNamespace
    install_offline_boundary(monkeypatch)
    base, runs, _ = cohort_fixture
    store = NativeRunStore(connection_factory)
    extras = []
    for index, state in enumerate(('declared', 'uncertain', 'cancelled')):
        request = replace(base.members[0].request, run_id=uuid4(),
            simulation_id='http-'+state+'-'+uuid4().hex, seed=(-(2**63), 2**63-1, 2**53+1)[index])
        store.register(request)
        if state == 'declared':
            store.request_cancel('owner', request.run_id)
        else:
            owner = uuid4()
            claimed = store.claim_start('owner', request.run_id, owner, 240)
            if state == 'uncertain':
                store.mark_uncertain('owner', request.run_id, claimed.attempt_id, owner)
            else:
                # Authoritative synthetic control-plane fixture, not a claim of
                # a real native cancellation. No process is launched/adopted.
                child = NativeChildIdentity(uuid4(), 2147483000, 'a'*64)
                store.attach('owner', request.run_id, claimed.attempt_id, owner, child, 240)
                store.request_cancel('owner', request.run_id)
                receipt = NativeRunReceipt(request.run_id, claimed.attempt_id, child.instance_id,
                    request.fingerprint, 'cancelled', 'b'*64)
                store.settle('owner', request.run_id, claimed.attempt_id, owner, receipt)
        extras.append(ExperimentMember('http-'+state, 'Observed '+state, 'declared-case', request))
    cohort = ExperimentCohort('owner', base.members+tuple(extras))
    manifest = tmp_path/'cohort.json'
    raw = canonical(cohort.wire())
    manifest.write_bytes(raw)
    pg = conninfo_to_dict(os.environ['PROJECT_STORE_POSTGRES_TEST_DSN'])
    environment = {f'KNOWLEDGE_PG_{k}': pg[v] for k, v in (
        ('HOST', 'host'), ('PORT', 'port'), ('DATABASE', 'dbname'), ('USER', 'user'), ('PASSWORD', 'password'))}
    scope = {'schema_version': 1, 'workspace_id': str(uuid4()),
        'project_id': str(cohort.members[0].request.project_id), 'graph_id': str(uuid4()),
        'run_id': None, 'branch_id': None, 'layer': 'source'}
    token = '0123456789abcdef'*4
    settings = SimpleNamespace(python=experiment_python, principal='owner', scope=scope,
        child_environment=environment, token=token, display_graph_id='http-fixture', bootstrap='unused')
    monkeypatch.setenv('KNOWLEDGE_EXPERIMENT_MANIFEST', str(manifest))
    monkeypatch.setenv('KNOWLEDGE_EXPERIMENT_MANIFEST_SHA256', sha(raw))
    binding = ExperimentSettings.capture(settings)
    assert binding is not None
    facade = NativeExperimentFacade(settings, binding=binding)
    return settings, facade, cohort, runs, manifest, raw


def lean_client(monkeypatch, settings, facade):
    from app import create_app
    from app.config import Config
    from app.services.knowledge_read_facade import ReadHostSettings
    monkeypatch.setenv('MIROFISH_APP_MODE', 'research_local')
    monkeypatch.delenv('FLASK_HOST', raising=False)
    monkeypatch.delenv('MIROFISH_ALLOWED_ORIGINS', raising=False)
    monkeypatch.setattr(ReadHostSettings, 'from_config', classmethod(lambda cls, config: settings))
    class C(Config):
        DEBUG = False
        MIROFISH_ALLOWED_ORIGINS = ('http://localhost:3000',)
    return create_app(C, experiment_facade=facade).test_client()


def test_actual_catalog_selected_comparison_no_writes(connection_factory, protected_fixture, monkeypatch):
    from app.services.native_experiment_http_client import comparison, catalog
    from mirofish_execution.native_run_store import NativeRunStore
    settings, facade, cohort, runs, manifest, raw = protected_fixture
    before_files = [hashes(root) for _, root, _ in runs]
    store = NativeRunStore(connection_factory)
    before_records = [store.get('owner', m.request.run_id) for m in cohort.members]
    client = lean_client(monkeypatch, settings, facade)
    auth = {'Authorization': 'Bearer '+settings.token}
    response = client.get('/api/experiments/catalog', headers=auth)
    assert response.status_code == 200
    cat = catalog(response.json['data'], settings.scope['project_id'])
    assert [m['member_id'] for m in cat['members']] == [m.member_id for m in cohort.members]
    assert [m['seed'] for m in cat['members'][-3:]] == [str(-(2**63)), str(2**63-1), str(2**53+1)]
    selected = [m.member_id for m in reversed(cohort.members)]
    value = {'version': 1, 'title': 'Actual offline observations 猫', 'member_ids': selected}
    response = client.post('/api/experiments/compare', headers=auth, json=value)
    assert response.status_code == 200
    comp = comparison(response.json['data'], cat, value)
    assert comp['accounting'] == dict(successful=2, failed=1, pending=1, cancelled=1, uncertain=1)
    assert comp['cancellation_intent'] == {'cancel_requested_count': 2, 'overlaps_disposition_accounting': True}
    assert all(m['metrics'] is None for m in comp['members'][:4])
    for m in comp['members'][4:]:
        source = next(run for run in runs if run[0].member_id == m['member_id'])
        for p in ('twitter', 'reddit'):
            assert m['metrics'][p]['logged_action_total'] == source[2][p]['total']
            assert m['metrics'][p]['logged_action_by_type'] == source[2][p]['actions']
    assert [hashes(root) for _, root, _ in runs] == before_files
    assert [store.get('owner', m.request.run_id) for m in cohort.members] == before_records
    assert manifest.read_bytes() == raw
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'


@pytest.mark.parametrize('kind', ['changed', 'missing', 'foreign', 'absent_run', 'conflict', 'stale_revision', 'pg'])
def test_actual_failed_closed_catalog_and_compare(protected_fixture, monkeypatch, kind, tmp_path):
    from app.services.native_experiment_contracts import ExperimentCohort, canonical, sha
    from app.services.native_experiment_http_facade import ExperimentSettings, NativeExperimentFacade
    settings, facade, cohort, _, manifest, raw = protected_fixture
    if kind == 'changed':
        manifest.write_bytes(raw+b' ')
    elif kind == 'missing':
        manifest.unlink()
    elif kind == 'pg':
        settings.child_environment['KNOWLEDGE_PG_PORT'] = '1'
        facade = NativeExperimentFacade(settings, binding=ExperimentSettings.capture(settings))
    else:
        member = cohort.members[0]
        request = member.request
        if kind == 'foreign':
            request = replace(request, principal='foreign')
            members = [replace(m, request=replace(m.request, principal='foreign')) for m in cohort.members]
            wrong = ExperimentCohort('foreign', tuple(members))
        else:
            if kind == 'absent_run':
                request = replace(request, run_id=uuid4())
            elif kind == 'conflict':
                request = replace(request, seed=request.seed+1)
            else:
                request = replace(request, project_revision=2)
            # No recording pin is needed because catalog must stop at authority.
            member = replace(member, request=request, recording=None)
            wrong = ExperimentCohort('owner', (member,))
        raw = canonical(wrong.wire())
        manifest.write_bytes(raw)
        monkeypatch.setenv('KNOWLEDGE_EXPERIMENT_MANIFEST_SHA256', sha(raw))
        facade = NativeExperimentFacade(settings, binding=ExperimentSettings.capture(settings))
    client = lean_client(monkeypatch, settings, facade)
    auth = {'Authorization': 'Bearer '+settings.token}
    for response in (client.get('/api/experiments/catalog', headers=auth),
            client.post('/api/experiments/compare', headers=auth, json={'version': 1, 'title': 'Denied', 'member_ids': [cohort.members[0].member_id]})):
        assert response.status_code == 503
        assert response.json == {'success': False, 'error': {'code': 'experiment_unavailable'}}
        assert str(tmp_path).encode() not in response.data
        assert settings.child_environment['KNOWLEDGE_PG_PASSWORD'].encode() not in response.data


def test_catalog_never_opens_recording_and_comparison_rejects_changed_bundle(protected_fixture, monkeypatch, tmp_path):
    from app.services.native_experiment_contracts import ExperimentCohort, canonical, sha
    from app.services.native_experiment_http_facade import ExperimentSettings, NativeExperimentFacade
    settings, _, cohort, _, manifest, _ = protected_fixture
    # Keep actual run authority but bind a missing immutable recording. Catalog
    # succeeds without opening it; comparison cannot publish successful metrics.
    member = cohort.members[0]
    pin = replace(member.recording, bundle=str(tmp_path/'absent-bundle'))
    wrong = ExperimentCohort('owner', (replace(member, recording=pin),))
    raw = canonical(wrong.wire()); manifest.write_bytes(raw)
    monkeypatch.setenv('KNOWLEDGE_EXPERIMENT_MANIFEST_SHA256', sha(raw))
    facade = NativeExperimentFacade(settings, binding=ExperimentSettings.capture(settings))
    client = lean_client(monkeypatch, settings, facade)
    auth = {'Authorization': 'Bearer '+settings.token}
    assert client.get('/api/experiments/catalog', headers=auth).status_code == 200
    response = client.post('/api/experiments/compare', headers=auth,
        json={'version': 1, 'title': 'Pinned missing recording', 'member_ids': [member.member_id]})
    assert response.status_code == 503 and not (tmp_path/'absent-bundle').exists()
    # A wrong revision against the real admitted bundle is also rejected.
    wrong = ExperimentCohort('owner', (replace(member, recording=replace(member.recording, revision='0'*64)),))
    raw = canonical(wrong.wire()); manifest.write_bytes(raw)
    monkeypatch.setenv('KNOWLEDGE_EXPERIMENT_MANIFEST_SHA256', sha(raw))
    facade = NativeExperimentFacade(settings, binding=ExperimentSettings.capture(settings))
    client = lean_client(monkeypatch, settings, facade)
    assert client.get('/api/experiments/catalog', headers=auth).status_code == 200
    assert client.post('/api/experiments/compare', headers=auth,
        json={'version': 1, 'title': 'Altered recording pin', 'member_ids': [member.member_id]}).status_code == 503


def test_unselected_missing_authority_fails_entire_operation(protected_fixture, monkeypatch, tmp_path):
    from app.services.native_experiment_contracts import ExperimentCohort, canonical, sha
    from app.services.native_experiment_http_facade import ExperimentSettings, NativeExperimentFacade
    settings, _, cohort, _, manifest, _ = protected_fixture
    valid = cohort.members[0]
    # Selected authority is valid; its recording path is deliberately missing.
    # The entire cohort authority admission must reject the unselected unknown
    # record rather than publish a partial catalog or partial comparison.
    valid = replace(valid, recording=replace(valid.recording, bundle=str(tmp_path/'not-opened')))
    missing = replace(cohort.members[1], request=replace(cohort.members[1].request, run_id=uuid4()), recording=None)
    wrong = ExperimentCohort('owner', (valid, missing))
    raw = canonical(wrong.wire()); manifest.write_bytes(raw)
    monkeypatch.setenv('KNOWLEDGE_EXPERIMENT_MANIFEST_SHA256', sha(raw))
    facade = NativeExperimentFacade(settings, binding=ExperimentSettings.capture(settings))
    client = lean_client(monkeypatch, settings, facade)
    auth = {'Authorization': 'Bearer '+settings.token}
    assert client.get('/api/experiments/catalog', headers=auth).status_code == 503
    assert client.post('/api/experiments/compare', headers=auth,
        json={'version': 1, 'title': 'Unknown unselected authority', 'member_ids': [valid.member_id]}).status_code == 503
    assert not (tmp_path/'not-opened').exists()

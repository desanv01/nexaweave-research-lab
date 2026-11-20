"""Main-run actual PG/recording/fixed-child plus Flask test-client sources.

Offline only. These sources do not qualify a separate lean socket HTTP runtime.
Main supplies the installed child interpreter and separate socket proof.
"""
from dataclasses import replace
import hashlib
from itertools import combinations
import json
from pathlib import Path
from uuid import uuid4

import pytest

from test_native_experiments import hashes

pytestmark = pytest.mark.postgres


def lean_client(monkeypatch, settings, facade):
    from app import create_app
    from app.config import Config
    from app.services.knowledge_read_facade import ReadHostSettings
    monkeypatch.setenv('NEXAWEAVE_APP_MODE', 'research_local')
    monkeypatch.delenv('FLASK_HOST', raising=False)
    monkeypatch.delenv('NEXAWEAVE_ALLOWED_ORIGINS', raising=False)
    monkeypatch.setattr(ReadHostSettings, 'from_config', classmethod(lambda cls, config: settings))
    class C(Config):
        DEBUG = False
        NEXAWEAVE_ALLOWED_ORIGINS = ('http://localhost:3000',)
    return create_app(C, experiment_facade=facade).test_client()


def test_actual_catalog_selected_comparison_no_writes(connection_factory, protected_fixture, monkeypatch):
    from app.services.native_experiment_http_client import comparison, catalog
    from nexaweave_execution.native_run_store import NativeRunStore
    settings, facade, cohort, runs, manifest, raw = protected_fixture
    before_files = [hashes(root) for _, root, _ in runs]
    recording_roots = [Path(member.recording.bundle) for member in cohort.members if member.recording is not None]
    before_recordings = [hashes(root) for root in recording_roots]
    store = NativeRunStore(connection_factory)
    before_records = [store.get('owner', m.request.run_id) for m in cohort.members]
    evaluations = []
    real_execute = facade.execute

    def observe_execute(method, request):
        evaluations.append(method)
        return real_execute(method, request)

    monkeypatch.setattr(facade, 'execute', observe_execute)
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
    assert response.mimetype == 'application/json'
    assert 'Content-Disposition' not in response.headers
    comp = comparison(response.json['data'], cat, value)
    assert comp['accounting'] == dict(successful=2, failed=1, pending=1, cancelled=1, uncertain=1)
    assert comp['cancellation_intent'] == {'cancel_requested_count': 2, 'overlaps_disposition_accounting': True}
    assert all(m['metrics'] is None for m in comp['members'][:4])
    for m in comp['members'][4:]:
        source = next(run for run in runs if run[0].member_id == m['member_id'])
        for p in ('twitter', 'reddit'):
            assert m['metrics'][p]['logged_action_total'] == source[2][p]['total']
            assert m['metrics'][p]['logged_action_by_type'] == source[2][p]['actions']

    media_type = 'application/vnd.nexaweave.comparison-report+json'
    denied = client.post('/api/experiments/compare', headers={'Accept': media_type}, json=value)
    assert denied.status_code == 401
    assert denied.json == {'success': False, 'error': {'code': 'unauthorized'}}
    assert 'Content-Disposition' not in denied.headers
    assert evaluations == ['catalog', 'compare']
    attachment = client.post('/api/experiments/compare', headers={**auth, 'Accept': media_type}, json=value)
    assert evaluations == ['catalog', 'compare', 'compare']
    assert attachment.status_code == 200 and attachment.mimetype == media_type
    assert attachment.headers['Content-Disposition'] == 'attachment; filename="nexaweave-comparison-report.json"'
    assert len(attachment.data) <= 512 * 1024
    for observed in (response, denied, attachment):
        assert observed.headers['Cache-Control'] == 'no-store'
        assert observed.headers['X-Content-Type-Options'] == 'nosniff'
    report = json.loads(attachment.data)
    report_digest = report.pop('report_digest')
    digest_input = json.dumps(report, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(',', ':'))
    assert report_digest == hashlib.sha256(digest_input.encode('ascii')).hexdigest()

    original = {member['member_id']: member for member in comp['members']}
    cohort_members = {member.member_id: member for member in cohort.members}
    expected_members = []
    for member_id in selected:
        member = cohort_members[member_id]
        expected_members.append({
            'member_id': member_id, 'member_label': member.member_label, 'case_label': member.case_label,
            'run_id': str(member.request.run_id), 'disposition': original[member_id]['disposition'],
            'project_revision': member.request.project_revision, 'request_fingerprint': member.request.fingerprint,
            'record_digest': original[member_id]['record_digest'],
            'recording_revision': member.recording.revision if member.recording is not None else None,
        })
    # The existing real-run fixture counts actions directly from each platform log.
    counts = {member.member_id: observed for member, _, observed in runs if observed is not None}
    fields = {(pair['left_member_id'], pair['right_member_id']): pair['fields']
              for pair in comp['comparability_matrix']}
    expected_pairs = []
    for left, right in combinations(selected, 2):
        common = set(cohort_members[left].request.platforms) & set(cohort_members[right].request.platforms)
        differences = []
        for platform in ('twitter', 'reddit'):
            if platform not in common:
                continue
            l_total = counts[left][platform]['total'] if left in counts else None
            r_total = counts[right][platform]['total'] if right in counts else None
            differences.append({
                'platform': platform, 'left_logged_action_total': l_total, 'right_logged_action_total': r_total,
                'right_minus_left': None if None in (l_total, r_total) else r_total - l_total,
            })
        expected_pairs.append({'left_member_id': left, 'right_member_id': right,
                               'fields': fields[left, right], 'platform_differences': differences})
    assert report == {
        'version': 1, 'title': value['title'], 'project_id': settings.scope['project_id'],
        'project_revision': cohort.members[0].request.project_revision,
        'cohort_manifest_digest': hashlib.sha256(raw).hexdigest(),
        'native_result_digest': comp['native_result_digest'],
        'public_projection_digest': comp['public_projection_digest'], 'coverage': comp['coverage'],
        'members': expected_members, 'pairs': expected_pairs,
    }
    pending_id = 'http-declared'
    pending_pairs = [pair for pair in report['pairs']
                     if pending_id in (pair['left_member_id'], pair['right_member_id'])]
    assert len(pending_pairs) == len(selected) - 1
    for pair in pending_pairs:
        row, = pair['platform_differences']
        assert row['platform'] == 'twitter'
        side = 'left' if pair['left_member_id'] == pending_id else 'right'
        assert row[f'{side}_logged_action_total'] is None
        assert row['right_minus_left'] is None
    assert [hashes(root) for _, root, _ in runs] == before_files
    assert [hashes(root) for root in recording_roots] == before_recordings
    assert [store.get('owner', m.request.run_id) for m in cohort.members] == before_records
    assert manifest.read_bytes() == raw


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

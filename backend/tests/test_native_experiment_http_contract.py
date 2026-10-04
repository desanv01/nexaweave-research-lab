"""Source-authored contracts; synthetic DTOs are not native acceptance evidence."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.services.native_experiment_http_client import (CAT_MEMBER, FLAGS,
    TABLES, MAX_REQUEST, NativeExperimentProcessClient, aggregates, catalog,
    comparison, digest, encoded, payload, project_native, reply, request_value, seed)
from app.services.native_experiment_http_facade import ExperimentSettings, NativeExperimentFacade
from app.services.knowledge_transport import KnowledgeInvalidRequest, KnowledgeTransportFailure

PROJECT = str(UUID(int=11))
PIN = 'a'*64


def selector(ids=None):
    return {'version': 1, 'title': 'Literal <b>猫😀</b>', 'member_ids': ids or ['one', 'two']}


def fixtures():
    members = []
    for ordinal, number in enumerate((-(2**63), 2**63-1)):
        m = {'member_id': ('one', 'two')[ordinal], 'member_label': 'Member <script>😀</script>',
            'case_label': 'Case 猫', 'run_id': str(UUID(int=ordinal+20)),
            'state': ('completed', 'declared')[ordinal], 'cancel_requested': bool(ordinal),
            'seed': str(number), 'max_rounds': 1, 'platforms': ['twitter', 'reddit']}
        members.append(m)
    cat = {'version': 1, 'project_id': PROJECT, 'project_revision': 1,
        'cohort_manifest_digest': 'c'*64, 'members': deepcopy(members)}
    cat['public_projection_digest'] = digest(cat)
    for m in members:
        m.update(disposition='successful' if m['state'] == 'completed' else 'pending',
            project_revision=1, runtime_sha256='d'*64, prepared_artifact_sha256='e'*64,
            request_fingerprint='f'*64, record_digest='1'*64, recording=None, metrics=None)
        if m['state'] == 'completed':
            m['recording'] = {'version': 1, 'recording_revision': '2'*64,
                'anchors': {'graph_id': 'graph-one', 'simulation_id': 'sim-one',
                    'run_id': m['run_id'], 'branch_id': 'branch-one', 'project_id': PROJECT, 'project_revision': 1},
                'platforms': m['platforms'][:], 'runtime_sha256': m['runtime_sha256'],
                'runtime_versions': dict(python='3.11', sqlite='3.0', oasis='1', camel='1'),
                'artifact_sha256': {name: '3'*64 for name in ('simulation_config.json', 'source_grounding.json', 'twitter_profiles.csv', 'reddit_profiles.json')}}
            m['metrics'] = {p: {'logged_action_total': 3, 'logged_action_by_type': {'CREATE_POST': 3},
                'final_table_counts': {t: (None if t == 'mute' else 0) for t in TABLES}} for p in m['platforms']}
    for m in members:
        m['seed'] = int(m['seed'])
    groups, matrix = aggregates(members)
    native = {'version': 1, 'title': selector()['title'], 'project_id': PROJECT,
        'project_revision': 1, 'cohort_manifest_digest': cat['cohort_manifest_digest'],
        'members': members, 'accounting': dict(successful=1, failed=0, cancelled=0, pending=1, uncertain=0),
        'distributions': groups, 'comparability_matrix': matrix,
        'cancellation_intent': {'cancel_requested_count': 1, 'overlaps_disposition_accounting': True},
        'distinct_declared_seed_count': 2, 'distinct_successful_seed_count': 1, **deepcopy(FLAGS)}
    native['result_digest'] = digest(native)
    public = project_native(native, cat, selector())
    return cat, native, public


def envelope(method='compare', value=None):
    return {'version': 1, 'request_id': str(UUID(int=100)), 'method': method,
        'scope': {'project_id': PROJECT, 'manifest_sha256': PIN},
        'payload': selector() if value is None and method == 'compare' else value or {}}


def success():
    cat, _, public = fixtures()
    return {'version': 1, 'request_id': envelope()['request_id'], 'ok': True,
        'result': {'manifest_sha256': PIN, 'catalog': cat, 'comparison': public}}


def test_exact_signed64_projection_and_nullable_tables():
    cat, native, public = fixtures()
    assert catalog(cat, PROJECT) is cat
    assert comparison(public, cat, selector()) is public
    assert [m['seed'] for m in public['members']] == [str(-(2**63)), str(2**63-1)]
    assert public['comparability_matrix'][0]['fields']['seed'] == {'left': str(-(2**63)), 'right': str(2**63-1), 'equal': False}
    assert public['native_result_digest'] == native['result_digest']
    assert public['public_projection_digest'] != public['native_result_digest']
    for group in public['distributions']:
        missing = group['metrics']['final_table_counts']['mute']
        empty = group['metrics']['final_table_counts']['post']
        assert missing['sample_count'] == 0 and missing['missing_count'] == 2 and missing['arithmetic_mean'] is None
        assert empty['sample_count'] == 1 and empty['missing_count'] == 1 and empty['arithmetic_mean'] == 0
    assert reply(encoded(success()), envelope())['ok'] is True


@pytest.mark.parametrize('number', ['-0', '+1', '01', ' 1', '1.0', str(2**63), str(-(2**63)-1), 1, True])
def test_seed_strings_are_canonical(number):
    with pytest.raises((ValueError, TypeError)):
        seed(number)


@pytest.mark.parametrize('value', [None, [], {}, {'version': True, 'title': 'x', 'member_ids': ['one']},
    {'version': 1, 'title': '', 'member_ids': ['one']},
    {'version': 1, 'title': 'x\x00', 'member_ids': ['one']},
    {'version': 1, 'title': '猫'*54, 'member_ids': ['one']},
    {'version': 1, 'title': 'x', 'member_ids': []},
    {'version': 1, 'title': 'x', 'member_ids': ['one']*2},
    {'version': 1, 'title': 'x', 'member_ids': ['a/b']},
    {'version': 1, 'title': 'x', 'member_ids': [True]},
    {'version': 1, 'title': 'x', 'member_ids': [str(i) for i in range(17)]},
    {'version': 1, 'title': 'x', 'member_ids': ['one'], 'principal': 'owner'}])
def test_selector_admission(value):
    with pytest.raises((ValueError, TypeError)):
        payload('compare', value)


@pytest.mark.parametrize('raw', [b'{"version":1,"version":1}', b'{}{}', b'{"x":NaN}',
    b'{"x":1e400}', b'{"x":'+b'['*40+b'0'+b']'*40+b'}', b'\xff', b' '*10000])
def test_transport_admission_before_spawn(raw):
    client = object.__new__(NativeExperimentProcessClient)
    with pytest.raises(KnowledgeInvalidRequest):
        client._validate_request(raw)


@pytest.mark.parametrize('mutate', [
    lambda v: v.update(request_id=str(UUID(int=101))),
    lambda v: v.update(ok=1), lambda v: v.update(version=True),
    lambda v: v.update(secret='private'),
    lambda v: v['result'].update(manifest_sha256='0'*64),
    lambda v: v['result']['catalog'].update(project_id=str(UUID(int=999))),
    lambda v: v['result']['comparison'].update(title='Other'),
    lambda v: v['result']['comparison']['members'].reverse(),
    lambda v: v['result']['comparison']['members'][0].update(state='failed'),
    lambda v: v['result']['comparison']['members'][0].update(cancel_requested=True),
    lambda v: v['result']['comparison']['members'][0].update(seed='-0'),
    lambda v: v['result']['comparison']['members'][0].update(max_rounds=True),
    lambda v: v['result']['comparison']['members'][0].update(principal='private'),
    lambda v: v['result']['comparison']['members'][0]['recording'].update(bundle='/private'),
    lambda v: v['result']['comparison']['members'][0]['metrics']['twitter'].update(logged_action_total=4),
    lambda v: v['result']['comparison']['members'][1].update(metrics={}),
    lambda v: v['result']['comparison']['accounting'].update(successful=True),
    lambda v: v['result']['comparison']['cancellation_intent'].update(cancel_requested_count=0),
    lambda v: v['result']['comparison']['distributions'][0]['metrics']['logged_action_total'].update(arithmetic_mean=0),
    lambda v: v['result']['comparison']['comparability_matrix'].clear(),
    lambda v: v['result']['comparison']['coverage'].update(missing_metrics_are_zero=True),
    lambda v: v['result']['comparison'].update(causal_attribution_supported=True),
    lambda v: v['result']['comparison'].update(native_result_digest='0'*64),
])
def test_corruption_rejected_even_with_rehashed_public_projection(mutate):
    v = success()
    mutate(v)
    comp = v.get('result', {}).get('comparison')
    if comp:
        comp['public_projection_digest'] = digest({k: x for k, x in comp.items() if k != 'public_projection_digest'})
    with pytest.raises((ValueError, TypeError, KeyError)):
        reply(encoded(v), envelope())


def test_corrupt_public_projection_digest_is_rejected():
    v = success()
    v['result']['comparison']['public_projection_digest'] = '0'*64
    # Preserve the corrupted digest: recomputing it would undo this mutation.
    with pytest.raises((ValueError, TypeError, KeyError)):
        reply(encoded(v), envelope())


def test_native_digest_is_checked_before_projection():
    cat, native, _ = fixtures()
    native['members'][0]['metrics']['twitter']['logged_action_total'] = 5
    with pytest.raises(ValueError):
        project_native(native, cat, selector())


def test_literal_config_capture_and_allowlisted_environment(monkeypatch, tmp_path):
    path = str(tmp_path/'private.json')
    monkeypatch.setenv('KNOWLEDGE_EXPERIMENT_MANIFEST', path)
    monkeypatch.setenv('KNOWLEDGE_EXPERIMENT_MANIFEST_SHA256', PIN)
    settings = SimpleNamespace(python=sys.executable, principal='owner', scope={'project_id': PROJECT},
        child_environment={**{f'KNOWLEDGE_PG_{k}': 'literal' for k in ('HOST', 'PORT', 'DATABASE', 'USER', 'PASSWORD')},
            'KNOWLEDGE_NEO4J_PASSWORD': 'excluded', 'KNOWLEDGE_READ_TOKEN': 'excluded'})
    binding = ExperimentSettings.capture(settings)
    assert binding is not None and not Path(path).exists()
    assert not any('NEO4J' in k or 'TOKEN' in k for k, _ in binding.environment)
    monkeypatch.setenv('KNOWLEDGE_EXPERIMENT_MANIFEST_SHA256', '0'*64)
    calls = []
    class Client:
        def call(self, raw):
            calls.append(request_value(raw))
            v = success()
            v['request_id'] = calls[-1]['request_id']
            return encoded(v)
    f = NativeExperimentFacade(settings, binding=binding, client_factory=Client)
    assert f.execute('compare', selector())['comparison']['title'] == selector()['title']
    assert len(calls) == 1 and calls[0]['scope']['manifest_sha256'] == PIN


def test_redacted_failure_no_retry(monkeypatch):
    calls = []
    binding = ExperimentSettings('python', PROJECT, PIN, ())
    class Client:
        def call(self, raw):
            calls.append(raw)
            raise RuntimeError('private password C:/private')
    f = NativeExperimentFacade(None, binding=binding, client_factory=Client)
    with pytest.raises(Exception, match='experiment unavailable') as error:
        f.execute('catalog', {})
    assert len(calls) == 1 and 'private' not in str(error.value)


def test_process_finally_cleanup_and_private_cwd(tmp_path):
    # Test-only executable script. Production facade always uses its fixed sibling.
    marker = tmp_path/'cwd.txt'
    child = tmp_path/'probe.py'
    child.write_text('import sys,json,os\nfrom pathlib import Path\n'
        f'Path({str(marker)!r}).write_text(os.getcwd())\n'
        'n=int.from_bytes(sys.stdin.buffer.read(4),"big")\n'
        'v=json.loads(sys.stdin.buffer.read(n))\n'
        'r=json.dumps({"version":1,"request_id":v["request_id"],"ok":False,"error":{"code":"experiment_unavailable"}}).encode()\n'
        'sys.stdout.buffer.write(len(r).to_bytes(4,"big")+r)\n', encoding='utf-8')
    client = NativeExperimentProcessClient(sys.executable, str(child), timeout_seconds=5)
    assert reply(client.call(encoded(envelope())), envelope())['ok'] is False
    cwd = Path(marker.read_text())
    assert cwd != tmp_path and not cwd.exists()


def test_owned_timeout_closes_private_directory(tmp_path):
    marker = tmp_path/'cwd.txt'
    child = tmp_path/'timeout.py'
    child.write_text('import os,time\nfrom pathlib import Path\n'
        f'Path({str(marker)!r}).write_text(os.getcwd())\ntime.sleep(30)\n', encoding='utf-8')
    client = NativeExperimentProcessClient(sys.executable, str(child), timeout_seconds=1)
    with pytest.raises(KnowledgeTransportFailure):
        client.call(encoded(envelope()))
    assert marker.exists() and not Path(marker.read_text()).exists()

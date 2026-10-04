"""Stdlib-only admission and public projection for owned experiment observations."""
from copy import deepcopy
import hashlib
import json
import re
import statistics

try:
    from .knowledge_transport import (KnowledgeProcessClient, KnowledgeInvalidRequest,
        KnowledgeTransportFailure, _json_object, _uuid)
except ImportError:
    from knowledge_transport import (KnowledgeProcessClient, KnowledgeInvalidRequest,
        KnowledgeTransportFailure, _json_object, _uuid)

MAX_REQUEST = 8192
MAX_RESULT = 512 * 1024
OVERHEAD = 16384
HASH = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
STATES = {'declared', 'starting', 'running', 'completed', 'failed', 'cancelled', 'uncertain'}
TABLES = ('post', 'follow', 'like', 'dislike', 'comment', 'comment_like',
          'comment_dislike', 'mute', 'trace')
COVERAGE = {'atomic_cohort_snapshot': False,
    'statistics': 'descriptive_completed_available_observations_only',
    'labels_prove_controlled_intervention': False, 'hashes_prove_semantic_equivalence': False,
    'digests_are_signatures': False, 'possible_initial_log_duplicates': True,
    'post_log_interviews_may_exist_in_trace': True, 'exact_event_row_links': False,
    'historical_or_causal_truth': False, 'missing_metrics_are_zero': False}
FLAGS = {'causal_attribution_supported': False, 'provider_quality_assessed': False,
    'shared_budget_enforcement_supported': False, 'actual_provider_spend': None,
    'ensemble_launch_supported': False, 'coverage': COVERAGE}
CAT_MEMBER = {'member_id', 'member_label', 'case_label', 'run_id', 'state',
              'cancel_requested', 'seed', 'max_rounds', 'platforms'}
CHILD_KEYS = ('KNOWLEDGE_PRINCIPAL', 'KNOWLEDGE_EXPERIMENT_PROJECT_ID',
    'KNOWLEDGE_EXPERIMENT_MANIFEST', 'KNOWLEDGE_EXPERIMENT_MANIFEST_SHA256',
    'KNOWLEDGE_PG_HOST', 'KNOWLEDGE_PG_PORT', 'KNOWLEDGE_PG_DATABASE',
    'KNOWLEDGE_PG_USER', 'KNOWLEDGE_PG_PASSWORD')


def encoded(value):
    # Match the accepted native comparator's canonical bytes.
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      allow_nan=False, separators=(',', ':')).encode('ascii')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def keys(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError


def integer(value, maximum=2**53-1, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError


def text(value):
    if (type(value) is not str or not 1 <= len(value.encode('utf-8')) <= 160
            or not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError


def hash_value(value):
    if type(value) is not str or not HASH.fullmatch(value):
        raise ValueError


def seed(value):
    if type(value) is not str:
        raise ValueError
    number = int(value)
    if str(number) != value or not -(2**63) <= number < 2**63:
        raise ValueError
    return number


def payload(method, value):
    if method == 'catalog':
        keys(value, set())
    elif method == 'compare':
        keys(value, {'version', 'title', 'member_ids'})
        if type(value['version']) is not int or value['version'] != 1:
            raise ValueError
        text(value['title'])
        ids = value['member_ids']
        if (type(ids) is not list or not 1 <= len(ids) <= 16
                or any(type(i) is not str or not ID.fullmatch(i) for i in ids)
                or len(set(ids)) != len(ids) or len(encoded(value)) > MAX_REQUEST):
            raise ValueError
    else:
        raise ValueError
    return value


def request_value(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_REQUEST + 1024:
        raise ValueError
    v = _json_object(raw)
    keys(v, {'version', 'request_id', 'method', 'scope', 'payload'})
    if type(v['version']) is not int or v['version'] != 1:
        raise ValueError
    _uuid(v['request_id'])
    keys(v['scope'], {'project_id', 'manifest_sha256'})
    _uuid(v['scope']['project_id'])
    hash_value(v['scope']['manifest_sha256'])
    payload(v['method'], v['payload'])
    return v


def catalog(value, project):
    keys(value, {'version', 'project_id', 'project_revision', 'cohort_manifest_digest',
                 'members', 'public_projection_digest'})
    if type(value['version']) is not int or value['version'] != 1 or value['project_id'] != project:
        raise ValueError
    _uuid(value['project_id'])
    integer(value['project_revision'], 2147483647, minimum=1)
    hash_value(value['cohort_manifest_digest'])
    members = value['members']
    if type(members) is not list or not 1 <= len(members) <= 16:
        raise ValueError
    for m in members:
        keys(m, CAT_MEMBER)
        if type(m['member_id']) is not str or not ID.fullmatch(m['member_id']):
            raise ValueError
        text(m['member_label']); text(m['case_label']); _uuid(m['run_id'])
        if type(m['state']) is not str or m['state'] not in STATES or type(m['cancel_requested']) is not bool:
            raise ValueError
        seed(m['seed']); integer(m['max_rounds'], 24, minimum=1)
        if type(m['platforms']) is not list or m['platforms'] not in (['twitter'], ['reddit'], ['twitter', 'reddit']):
            raise ValueError
    if len({m['member_id'] for m in members}) != len(members) or len({m['run_id'] for m in members}) != len(members):
        raise ValueError
    hash_value(value['public_projection_digest'])
    if value['public_projection_digest'] != digest({k: v for k, v in value.items() if k != 'public_projection_digest'}):
        raise ValueError
    return value


def recording(value, member, project):
    keys(value, {'version', 'recording_revision', 'anchors', 'platforms', 'runtime_sha256',
                 'runtime_versions', 'artifact_sha256'})
    if type(value['version']) is not int or value['version'] != 1:
        raise ValueError
    hash_value(value['recording_revision'])
    if value['platforms'] != member['platforms'] or value['runtime_sha256'] != member['runtime_sha256']:
        raise ValueError
    anchors = value['anchors']
    keys(anchors, {'graph_id', 'simulation_id', 'run_id', 'branch_id', 'project_id', 'project_revision'})
    for k in ('graph_id', 'simulation_id', 'branch_id'):
        if type(anchors[k]) is not str or not ID.fullmatch(anchors[k]):
            raise ValueError
    if (anchors['run_id'] != member['run_id'] or anchors['project_id'] != project
            or type(anchors['project_revision']) is not int or anchors['project_revision'] != member['project_revision']):
        raise ValueError
    versions = value['runtime_versions']
    keys(versions, {'python', 'sqlite', 'oasis', 'camel'})
    for v in versions.values():
        text(v)
    artifacts = value['artifact_sha256']
    expected = {'simulation_config.json', 'source_grounding.json'} | {
        'twitter_profiles.csv' if p == 'twitter' else 'reddit_profiles.json' for p in member['platforms']}
    keys(artifacts, expected)
    for v in artifacts.values():
        hash_value(v)


def distribution(values, eligible):
    n = len(values)
    return {'sample_count': n, 'missing_count': eligible-n,
        'min': min(values) if n else None, 'max': max(values) if n else None,
        'arithmetic_mean': statistics.mean(values) if n else None,
        'median': statistics.median(values) if n else None,
        'population_standard_deviation': statistics.pstdev(values) if n else None}


def aggregates(members):
    groups = []
    for case in dict.fromkeys(m['case_label'] for m in members):
        for platform in ('twitter', 'reddit'):
            eligible = [m for m in members if m['case_label'] == case and platform in m['platforms']]
            if not eligible:
                continue
            completed = [m['metrics'][platform] for m in eligible if m['metrics'] is not None]
            actions = sorted({a for m in completed for a in m['logged_action_by_type']})
            groups.append({'case_label': case, 'platform': platform, 'member_count': len(eligible),
                'successful_count': len(completed), 'non_successful_count': len(eligible)-len(completed),
                'distinct_declared_seed_count': len({m['seed'] for m in eligible}),
                'distinct_successful_seed_count': len({m['seed'] for m in eligible if m['metrics'] is not None}),
                'metrics': {'logged_action_total': distribution([m['logged_action_total'] for m in completed], len(eligible)),
                    'logged_action_by_type': {a: distribution([m['logged_action_by_type'].get(a, 0) for m in completed], len(eligible)) for a in actions},
                    'final_table_counts': {t: distribution([m['final_table_counts'][t] for m in completed if m['final_table_counts'][t] is not None], len(eligible)) for t in TABLES}}})
    matrix = []
    fields = ('seed', 'max_rounds', 'runtime_sha256', 'platforms', 'project_revision', 'prepared_artifact_sha256')
    for index, left in enumerate(members):
        for right in members[index+1:]:
            comparisons = {f: {'left': left[f], 'right': right[f], 'equal': left[f] == right[f]} for f in fields}
            for f in ('artifact_sha256', 'runtime_versions'):
                l = None if left['recording'] is None else left['recording'][f]
                r = None if right['recording'] is None else right['recording'][f]
                comparisons[f] = {'left': l, 'right': r, 'equal': None if l is None or r is None else l == r}
            matrix.append({'left_member_id': left['member_id'], 'right_member_id': right['member_id'], 'fields': comparisons})
    return groups, matrix


def comparison(value, cat, selection):
    expected = {'version', 'title', 'project_id', 'project_revision', 'cohort_manifest_digest',
        'members', 'accounting', 'distributions', 'cancellation_intent',
        'distinct_declared_seed_count', 'distinct_successful_seed_count', 'comparability_matrix',
        'native_result_digest', 'public_projection_digest'} | set(FLAGS)
    keys(value, expected)
    if (type(value['version']) is not int or value['version'] != 1 or value['title'] != selection['title']
            or any(encoded(value[k]) != encoded(cat[k]) for k in ('project_id', 'project_revision', 'cohort_manifest_digest'))):
        raise ValueError
    for k, v in FLAGS.items():
        if encoded(value[k]) != encoded(v):
            raise ValueError
    members = value['members']
    if type(members) is not list or [m.get('member_id') for m in members if type(m) is dict] != selection['member_ids']:
        raise ValueError
    lookup = {m['member_id']: m for m in cat['members']}
    accounting = dict.fromkeys(('successful', 'failed', 'cancelled', 'pending', 'uncertain'), 0)
    for m in members:
        keys(m, CAT_MEMBER | {'disposition', 'project_revision', 'runtime_sha256',
            'prepared_artifact_sha256', 'request_fingerprint', 'record_digest', 'recording', 'metrics'})
        if m['member_id'] not in lookup or any(encoded(m[k]) != encoded(lookup[m['member_id']][k]) for k in CAT_MEMBER):
            raise ValueError
        if type(m['project_revision']) is not int or m['project_revision'] != cat['project_revision']:
            raise ValueError
        for k in ('runtime_sha256', 'prepared_artifact_sha256', 'request_fingerprint', 'record_digest'):
            hash_value(m[k])
        disposition = 'successful' if m['state'] == 'completed' else (m['state'] if m['state'] in ('failed', 'cancelled', 'uncertain') else 'pending')
        if m['disposition'] != disposition:
            raise ValueError
        accounting[disposition] += 1
        if disposition != 'successful':
            if m['metrics'] is not None or m['recording'] is not None:
                raise ValueError
        else:
            recording(m['recording'], m, cat['project_id'])
            keys(m['metrics'], m['platforms'])
            for metric in m['metrics'].values():
                keys(metric, {'logged_action_total', 'logged_action_by_type', 'final_table_counts'})
                integer(metric['logged_action_total'])
                actions = metric['logged_action_by_type']
                if type(actions) is not dict or len(actions) > 128:
                    raise ValueError
                for action, n in actions.items():
                    if type(action) is not str or not 1 <= len(action.encode('utf-8')) <= 256 or any(ord(c) < 32 or ord(c) == 127 for c in action):
                        raise ValueError
                    integer(n)
                if sum(actions.values()) != metric['logged_action_total']:
                    raise ValueError
                keys(metric['final_table_counts'], TABLES)
                for n in metric['final_table_counts'].values():
                    if n is not None:
                        integer(n)
    # Canonical equality rejects booleans masquerading as counts and extra fields.
    groups, matrix = aggregates(members)
    checks = {'accounting': accounting, 'distributions': groups, 'comparability_matrix': matrix,
        'cancellation_intent': {'cancel_requested_count': sum(m['cancel_requested'] for m in members), 'overlaps_disposition_accounting': True},
        'distinct_declared_seed_count': len({m['seed'] for m in members}),
        'distinct_successful_seed_count': len({m['seed'] for m in members if m['disposition'] == 'successful'})}
    for k, v in checks.items():
        if encoded(value[k]) != encoded(v):
            raise ValueError
    hash_value(value['native_result_digest']); hash_value(value['public_projection_digest'])
    # Reconstruct the exact native result and verify its digest independently.
    native = deepcopy({k: v for k, v in value.items() if k not in ('native_result_digest', 'public_projection_digest')})
    for m in native['members']:
        m['seed'] = seed(m['seed'])
    for pair in native['comparability_matrix']:
        for side in ('left', 'right'):
            pair['fields']['seed'][side] = seed(pair['fields']['seed'][side])
    if digest(native) != value['native_result_digest'] or digest({k: v for k, v in value.items() if k != 'public_projection_digest'}) != value['public_projection_digest']:
        raise ValueError
    if len(encoded(value)) > MAX_RESULT:
        raise ValueError
    return value


def project_native(value, cat, selection):
    native = deepcopy(value)
    native_digest = native.pop('result_digest')
    hash_value(native_digest)
    if digest(native) != native_digest:
        raise ValueError
    native['native_result_digest'] = native_digest
    for m in native['members']:
        integer(m['seed'], 2**63-1, -(2**63))
        m['seed'] = str(m['seed'])
    for pair in native['comparability_matrix']:
        for side in ('left', 'right'):
            n = pair['fields']['seed'][side]
            integer(n, 2**63-1, -(2**63))
            pair['fields']['seed'][side] = str(n)
    native['public_projection_digest'] = digest(native)
    return comparison(native, cat, selection)


def reply(raw, request):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_RESULT + OVERHEAD:
        raise ValueError
    value = _json_object(raw)
    common = {'version', 'request_id', 'ok'}
    if type(value.get('version')) is not int or value['version'] != 1 or value.get('request_id') != request['request_id'] or type(value.get('ok')) is not bool:
        raise ValueError
    if not value['ok']:
        keys(value, common | {'error'})
        keys(value['error'], {'code'})
        if value['error']['code'] != 'experiment_unavailable':
            raise ValueError
        return value
    keys(value, common | {'result'})
    result = value['result']
    keys(result, {'manifest_sha256', 'catalog'} | ({'comparison'} if request['method'] == 'compare' else set()))
    if result['manifest_sha256'] != request['scope']['manifest_sha256']:
        raise ValueError
    cat = catalog(result['catalog'], request['scope']['project_id'])
    if request['method'] == 'compare':
        comparison(result['comparison'], cat, request['payload'])
    return value


class NativeExperimentProcessClient(KnowledgeProcessClient):
    def call(self, raw):
        try:
            request = request_value(raw)
        except Exception:
            raise KnowledgeInvalidRequest() from None
        response = super().call(raw)
        try:
            reply(response, request)
        except Exception:
            raise KnowledgeTransportFailure(outcome_unknown=True) from None
        return response

    def _validate_request(self, raw):
        try:
            return request_value(raw)['request_id']
        except Exception:
            raise KnowledgeInvalidRequest() from None

    def _response_limit(self, raw):
        return MAX_RESULT + OVERHEAD

    def _validate_reply(self, raw, request_id):
        # Complete request-bound validation follows in the facade. No per-call
        # mutable request is stored here, preserving inherited one-call locking.
        try:
            v = _json_object(raw)
            if v.get('request_id') != request_id or type(v.get('version')) is not int or v['version'] != 1 or type(v.get('ok')) is not bool:
                raise ValueError
            keys(v, {'version', 'request_id', 'ok', 'result' if v['ok'] else 'error'})
        except Exception:
            raise KnowledgeTransportFailure(outcome_unknown=True) from None

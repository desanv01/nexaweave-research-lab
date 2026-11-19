"""Research-local experiment routes under the inherited bearer/origin boundary."""
import threading

from flask import Response, jsonify, request

from .services.native_experiment_http_client import (MAX_REQUEST, MAX_RESULT, OVERHEAD,
    encoded, digest, payload, catalog, comparison, _json_object)
from .services.native_experiment_http_facade import ExperimentSettings, NativeExperimentFacade


_REPORT_MEDIA_TYPE = 'application/vnd.nexaweave.comparison-report+json'
_REPORT_FILENAME = 'nexaweave-comparison-report.json'
_PLATFORMS = ('twitter', 'reddit')
_REPORT_MEMBER_FIELDS = ('member_id', 'member_label', 'case_label', 'run_id',
    'disposition', 'project_revision', 'request_fingerprint', 'record_digest')


def _comparison_report(comp, cat):
    """Project the validated comparison and its catalog platform admissions."""
    members = {member['member_id']: member for member in comp['members']}
    admitted = {member['member_id']: member['platforms'] for member in cat['members']}
    identities = []
    for member in members.values():
        identity = {key: member[key] for key in _REPORT_MEMBER_FIELDS}
        recording = member['recording']
        identity['recording_revision'] = None if recording is None else recording['recording_revision']
        identities.append(identity)
    pairs = []
    for pair in comp['comparability_matrix']:
        left_id, right_id = pair['left_member_id'], pair['right_member_id']
        left = members[left_id]['metrics'] or {}
        right = members[right_id]['metrics'] or {}
        differences = []
        for platform in _PLATFORMS:
            if platform not in admitted[left_id] or platform not in admitted[right_id]:
                continue
            left_total = left[platform]['logged_action_total'] if platform in left else None
            right_total = right[platform]['logged_action_total'] if platform in right else None
            differences.append({
                'platform': platform,
                'left_logged_action_total': left_total,
                'right_logged_action_total': right_total,
                'right_minus_left': None if left_total is None or right_total is None else right_total - left_total,
            })
        pairs.append({**pair, 'platform_differences': differences})
    report = {key: comp[key] for key in ('title', 'project_id', 'project_revision',
        'cohort_manifest_digest', 'native_result_digest', 'public_projection_digest', 'coverage')}
    report.update(version=1, members=identities, pairs=pairs)
    report['report_digest'] = digest(report)
    return report


def register_experiment_routes(app, settings, *, experiment_facade=None):
    binding = ExperimentSettings.capture(settings)
    facade = experiment_facade
    lock = threading.Lock()

    def failure(code, status):
        return jsonify({'success': False, 'error': {'code': code}}), status

    @app.after_request
    def experiment_headers(response):
        if request.path.startswith('/api/experiments/'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def call(method):
        nonlocal facade
        try:
            if (request.method != ('GET' if method == 'catalog' else 'POST') or request.query_string
                    or request.headers.get('Transfer-Encoding') is not None
                    or request.headers.get('Content-Encoding') is not None):
                raise ValueError
            lengths = request.headers.getlist('Content-Length')
            if len(lengths) > 1 or (lengths and (
                    not lengths[0].isascii() or not lengths[0].isdecimal()
                    or str(int(lengths[0])) != lengths[0])):
                raise ValueError
            if method == 'catalog':
                if request.content_length not in (None, 0) or request.stream.read(1):
                    raise ValueError
                value = {}
            else:
                if (len(lengths) != 1 or request.mimetype != 'application/json' or request.content_length is None
                        or not 0 < request.content_length <= MAX_REQUEST):
                    raise ValueError
                raw = request.stream.read(MAX_REQUEST+1)
                if len(raw) != request.content_length:
                    raise ValueError
                value = _json_object(raw)
            payload(method, value)
        except Exception:
            return failure('invalid_request', 400)
        try:
            with lock:
                if facade is None:
                    facade = NativeExperimentFacade(settings, binding=binding)
            result = facade.execute(method, value)
            # Validate even explicitly injected test facades, never shape-only.
            expected = {'manifest_sha256', 'catalog'} | ({'comparison'} if method == 'compare' else set())
            if type(result) is not dict or set(result) != expected or binding is None or result['manifest_sha256'] != binding.manifest_sha256:
                raise ValueError
            cat = catalog(result['catalog'], settings.scope['project_id'])
            public = cat if method == 'catalog' else comparison(result['comparison'], cat, value)
            if method == 'compare' and request.headers.getlist('Accept') == [_REPORT_MEDIA_TYPE]:
                raw = encoded(_comparison_report(public, cat))
                if len(raw) > MAX_RESULT:
                    raise ValueError
                return Response(raw, content_type=_REPORT_MEDIA_TYPE, headers={
                    'Content-Disposition': f'attachment; filename="{_REPORT_FILENAME}"',
                })
            raw = b'{"success":true,"data":'+encoded(public)+b'}'
            if len(raw) > MAX_RESULT+OVERHEAD:
                raise ValueError
            return Response(raw, content_type='application/json')
        except Exception:
            return failure('experiment_unavailable', 503)

    app.add_url_rule('/api/experiments/catalog', 'experiment_catalog', lambda: call('catalog'), methods=['GET'])
    app.add_url_rule('/api/experiments/compare', 'experiment_compare', lambda: call('compare'), methods=['POST'])

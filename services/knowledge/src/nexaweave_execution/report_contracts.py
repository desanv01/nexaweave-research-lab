"""Cold, strict connected narrative contracts; no SDK or service imports."""
import base64
import hashlib
import json
import re
from dataclasses import dataclass
from uuid import UUID, uuid5, NAMESPACE_URL

REQUEST_BYTES, RESULT_BYTES, CONTENT_BYTES = 32768, 262144, 4194304
CONTEXT_BYTES, FILE_BYTES, TOTAL_BYTES = 2097152, 2097152, 16777216
CODES = frozenset(('invalid_request invalid_reply unauthorized origin_denied not_found conflict '
    'tombstoned busy result_too_large report_unavailable model_calls_disabled budget_denied '
    'report_failed report_cancelled report_uncertain timeout transport_failure internal_error').split())
STATES = frozenset('planned queued generating completed failed cancelled uncertain'.split())
IDENTITY = ('schema_version', 'report_id', 'binding', 'options', 'context_sha256',
            'source_projection_sha256', 'model_label', 'limits', 'ceiling_microusd')
KEYS = set(IDENTITY) | {'plan_sha256', 'authorization', 'state', 'progress', 'workflow',
    'receipt', 'receipt_sha256', 'manifest', 'cleanup', 'cancel_requested', 'error_code'}
DEFAULT_LIMITS = dict(max_calls=64, max_input_bytes=262144, max_output_tokens=4096, max_run_seconds=600)
BASE_NAMES = ['meta.json', 'outline.json', 'full_report.md', 'retrieval_evidence.json', 'native_evidence.json']


class ReportError(RuntimeError):
    def __init__(self, code='invalid_request'):
        self.code = code if type(code) is str and code in CODES else 'internal_error'
        super().__init__(self.code)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def canonical(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('ascii')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def exact(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError
    return value


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError
    return value


def text(value, maximum, byte_maximum=None):
    if (type(value) is not str or not value.strip() or '\x00' in value
            or len(value) > maximum or len(value.encode('utf-8')) > (byte_maximum or maximum * 4)):
        raise ValueError
    return value


def sha(value):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError
    return value


def uuid_string(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError
    return value


def scalar_tree(value, depth=0):
    if type(value) in (dict, list):
        if depth >= 16:
            raise ValueError
        if type(value) is dict:
            for key, child in value.items():
                if type(key) is not str:
                    raise ValueError
                key.encode('utf-8'); scalar_tree(child, depth + 1)
        else:
            for child in value:
                scalar_tree(child, depth + 1)
    elif type(value) is str:
        value.encode('utf-8')
    elif value is not None and type(value) not in (int, float, bool):
        raise ValueError


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    value = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    scalar_tree(value)
    encoded(value)  # Reject overflowed/nonfinite numeric lexemes too.
    return value


def validate_limits(value):
    exact(value, DEFAULT_LIMITS)
    for key, high in [('max_calls', 128), ('max_input_bytes', CONTEXT_BYTES),
                      ('max_output_tokens', 4096), ('max_run_seconds', 600)]:
        integer(value[key], 1, high)
    return value


def windows(value):
    if value is None:
        return value
    if type(value) is not list or not 1 <= len(value) <= 2:
        raise ValueError
    seen = set()
    for window in value:
        exact(window, ('platform', 'offset', 'count'))
        if window['platform'] not in ('twitter', 'reddit') or window['platform'] in seen:
            raise ValueError
        seen.add(window['platform'])
        integer(window['offset'], 0, 9999); integer(window['count'], 1, 1000)
    return value


def options(value):
    exact(value, ('requirement', 'output_language', 'native_windows'))
    text(value['requirement'], 4000, 16000)
    if value['output_language'] not in ('en', 'zh', 'ms'):
        raise ValueError
    windows(value['native_windows'])
    return value


def validate_payload(method, value):
    try:
        if method not in ('plan', 'start', 'status', 'cancel', 'read', 'download'):
            raise ValueError
        keys = {'schema_version', 'report_id'}
        keys |= {'launch_id', 'launch_sha256', 'requirement', 'output_language', 'native_windows'} if method == 'plan' else {'plan_sha256'}
        if method == 'download':
            keys |= {'kind', 'section_index'}
        exact(value, keys); scalar_tree(value)
        integer(value['schema_version'], 1, 1); uuid_string(value['report_id'])
        if method == 'plan':
            uuid_string(value['launch_id']); sha(value['launch_sha256'])
            options({k: value[k] for k in ('requirement', 'output_language', 'native_windows')})
        else:
            sha(value['plan_sha256'])
        if method == 'download':
            artifact_name(value['kind'], value['section_index'])
        if len(encoded(value)) > REQUEST_BYTES:
            raise ValueError
        return json.loads(encoded(value))
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ReportError('invalid_request') from None


def artifact_name(kind, section_index):
    names = dict(report='full_report.md', outline='outline.json', evidence='retrieval_evidence.json',
                 native_evidence='native_evidence.json', metadata='meta.json')
    if kind == 'section':
        return 'section_%02d.md' % integer(section_index, 1, 8)
    if type(kind) is not str or kind not in names or section_index is not None:
        raise ValueError
    return names[kind]


def reference_key(value):
    text(value, 160)
    if value.startswith('source:'):
        uuid_string(value[7:])
    elif re.fullmatch(r'native:(twitter|reddit):(0|[1-9][0-9]{0,3}):[0-9a-f]{64}', value) is None:
        raise ValueError
    return value


def validate_binding(value):
    exact(value, ('display_graph_id', 'principal', 'scope', 'project_revision', 'source',
                  'preparation', 'native', 'coverage', 'reference_keys'))
    text(value['display_graph_id'], 200)
    if type(value['principal']) is not str or not re.fullmatch(r'[\x20-\x7e]{1,128}', value['principal']) or not value['principal'].strip():
        raise ValueError
    scope = exact(value['scope'], ('schema_version', 'workspace_id', 'project_id', 'graph_id', 'layer', 'run_id', 'branch_id'))
    integer(scope['schema_version'], 1, 1)
    for key in ('workspace_id', 'project_id', 'graph_id'):
        uuid_string(scope[key])
    if scope['layer'] != 'source' or scope['run_id'] is not None or scope['branch_id'] is not None:
        raise ValueError
    integer(value['project_revision'], 1, 2147483647)
    source = exact(value['source'], ('source_revision', 'source_name', 'source_sha256'))
    uuid_string(source['source_revision']); text(source['source_name'], 256); sha(source['source_sha256'])
    prep = exact(value['preparation'], ('operation_id', 'plan_sha256', 'simulation_id', 'artifact_sha256'))
    uuid_string(prep['operation_id']); sha(prep['plan_sha256']); sha(prep['artifact_sha256'])
    if prep['simulation_id'] != 'sim_' + UUID(prep['operation_id']).hex:
        raise ValueError
    native = exact(value['native'], ('run_id', 'launch_sha256', 'request_fingerprint', 'evidence_sha256', 'platforms'))
    uuid_string(native['run_id'])
    for key in ('launch_sha256', 'request_fingerprint', 'evidence_sha256'):
        sha(native[key])
    if native['platforms'] not in [['twitter'], ['reddit'], ['twitter', 'reddit']]:
        raise ValueError
    if type(value['coverage']) is not list or len(value['coverage']) != len(native['platforms']):
        raise ValueError
    for platform, coverage in zip(native['platforms'], value['coverage']):
        exact(coverage, ('platform', 'total_records', 'selected_records', 'complete', 'windows'))
        total = integer(coverage['total_records'], 0, 10000)
        selected = integer(coverage['selected_records'], 0, total)
        if coverage['platform'] != platform or type(coverage['complete']) is not bool or type(coverage['windows']) is not list:
            raise ValueError
        if len(coverage['windows']) > 1:
            raise ValueError
        declared = 0
        for window in coverage['windows']:
            exact(window, ('offset', 'count'))
            offset = integer(window['offset'], 0, 10000)
            count = integer(window['count'], 0, total)
            if offset + count > total:
                raise ValueError
            declared += count
        if declared != selected or coverage['complete'] != (selected == total):
            raise ValueError
    refs = value['reference_keys']
    if type(refs) is not list or len(refs) > 2048 or len(set(refs)) != len(refs):
        raise ValueError
    for key in refs:
        reference_key(key)
    return value


def validate_identity(value):
    exact(value, IDENTITY)
    integer(value['schema_version'], 1, 1); uuid_string(value['report_id'])
    validate_binding(value['binding']); options(value['options'])
    declared = value['options']['native_windows']
    native = value['binding']['native']
    if declared is not None and any(w['platform'] not in native['platforms'] for w in declared):
        raise ValueError
    for coverage in value['binding']['coverage']:
        if declared is None:
            if not coverage['complete']:
                raise ValueError
        else:
            window = next((w for w in declared if w['platform'] == coverage['platform']), None)
            expected = [] if window is None else [dict(offset=window['offset'], count=window['count'])]
            if coverage['windows'] != expected:
                raise ValueError
    sha(value['context_sha256']); sha(value['source_projection_sha256'])
    text(value['model_label'], 200); validate_limits(value['limits'])
    if value['ceiling_microusd'] is not None:
        integer(value['ceiling_microusd'], 1, 2**63 - 1)
    return value


def validate_manifest(value):
    exact(value, ('schema_version', 'files')); integer(value['schema_version'], 1, 1)
    files = value['files']
    if type(files) is not list or not 6 <= len(files) <= 13:
        raise ValueError
    names = BASE_NAMES + ['section_%02d.md' % i for i in range(1, len(files) - 4)]
    for file, name in zip(files, names):
        exact(file, ('name', 'size', 'sha256'))
        if file['name'] != name:
            raise ValueError
        integer(file['size'], 1, FILE_BYTES); sha(file['sha256'])
    if sum(f['size'] for f in files) > TOTAL_BYTES:
        raise ValueError
    return value


def validate_result(value, graph_id, scope, payload, method, principal=None, known=None):
    try:
        scalar_tree(value); exact(value, KEYS)
        if len(encoded(value)) > RESULT_BYTES:
            raise ReportError('result_too_large')
        identity = validate_identity({k: value[k] for k in IDENTITY})
        binding = value['binding']
        if (binding['display_graph_id'] != graph_id or binding['scope'] != scope
                or principal is not None and binding['principal'] != principal
                or value['report_id'] != payload['report_id'] or sha(value['plan_sha256']) != digest(identity)):
            raise ValueError
        if method == 'plan':
            if (binding['native']['run_id'] != payload['launch_id']
                    or binding['native']['launch_sha256'] != payload['launch_sha256']
                    or value['options'] != {k: payload[k] for k in ('requirement', 'output_language', 'native_windows')}):
                raise ValueError
        elif value['plan_sha256'] != payload['plan_sha256']:
            raise ValueError
        if known is not None and any(value[k] != known[k] for k in IDENTITY):
            raise ValueError
        auth = exact(value['authorization'], ('model_calls_enabled', 'budget_configured'))
        if any(type(v) is not bool for v in auth.values()) or auth['model_calls_enabled'] and not auth['budget_configured']:
            raise ValueError
        if auth['budget_configured'] and value['ceiling_microusd'] is None:
            raise ValueError
        state = value['state']
        if type(state) is not str or state not in STATES or type(value['cancel_requested']) is not bool:
            raise ValueError
        progress = exact(value['progress'], ('stage', 'percent', 'completed_sections', 'total_sections'))
        if progress['stage'] not in STATES | {'planning', 'researching', 'writing', 'publishing'}:
            raise ValueError
        integer(progress['percent'], 0, 100)
        total = integer(progress['total_sections'], 0, 8); integer(progress['completed_sections'], 0, total)
        workflow = value['workflow']
        if workflow is not None:
            exact(workflow, ('workflow_id', 'run_id'))
            text(workflow['workflow_id'], 200); text(workflow['run_id'], 200)
        cleanup = exact(value['cleanup'], ('known', 'pending', 'owner_thread_alive'))
        if type(cleanup['known']) is not bool:
            raise ValueError
        if cleanup['known']:
            if any(type(cleanup[k]) is not bool for k in ('pending', 'owner_thread_alive')):
                raise ValueError
        elif cleanup['pending'] is not None or cleanup['owner_thread_alive'] is not None:
            raise ValueError
        code = value['error_code']
        if code is not None and code not in CODES or (state in {'planned', 'queued', 'generating', 'completed'} and code is not None):
            raise ValueError
        if state in {'failed', 'uncertain'} and code is None:
            raise ValueError
        if state == 'completed':
            manifest = validate_manifest(value['manifest'])
            receipt = exact(value['receipt'], ('schema_version', 'report_id', 'plan_sha256', 'context_sha256',
                'manifest_sha256', 'output_language', 'reference_integrity', 'semantic_support_status'))
            integer(receipt['schema_version'], 1, 1)
            expected = dict(schema_version=1, report_id=value['report_id'], plan_sha256=value['plan_sha256'],
                context_sha256=value['context_sha256'], manifest_sha256=digest(manifest),
                output_language=value['options']['output_language'], reference_integrity='validated', semantic_support_status='not_reviewed')
            if (receipt != expected or sha(value['receipt_sha256']) != digest(receipt)
                    or cleanup != dict(known=True, pending=False, owner_thread_alive=False)
                    or total != len(manifest['files']) - 5 or progress['completed_sections'] != total
                    or progress['percent'] != 100):
                raise ValueError
        elif any(value[k] is not None for k in ('receipt', 'receipt_sha256', 'manifest')):
            raise ValueError
        return json.loads(encoded(value))
    except ReportError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ReportError('invalid_reply') from None


def validate_read(value, graph_id, scope, payload, principal=None, known=None):
    try:
        exact(value, ('schema_version', 'report', 'content')); integer(value['schema_version'], 1, 1)
        report = validate_result(value['report'], graph_id, scope, payload, 'read', principal, known)
        if report['state'] != 'completed' or type(value['content']) is not str or len(encoded(value)) > CONTENT_BYTES:
            raise ValueError
        raw = value['content'].encode('utf-8')
        file = report['manifest']['files'][2]
        if len(raw) != file['size'] or hashlib.sha256(raw).hexdigest() != file['sha256']:
            raise ValueError
        return json.loads(encoded(value))
    except ReportError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ReportError('invalid_reply') from None


def validate_download(value, payload, known):
    try:
        exact(value, ('schema_version', 'report_id', 'plan_sha256', 'receipt_sha256', 'artifact'))
        integer(value['schema_version'], 1, 1)
        if known['state'] != 'completed' or any(value[k] != known[k] for k in ('report_id', 'plan_sha256', 'receipt_sha256')):
            raise ValueError
        artifact = exact(value['artifact'], ('name', 'mime', 'size', 'sha256', 'content_base64'))
        integer(artifact['size'], 1, FILE_BYTES); sha(artifact['sha256'])
        name = artifact_name(payload['kind'], payload['section_index'])
        file = next(f for f in known['manifest']['files'] if f['name'] == name)
        if (any(artifact[k] != file[k] for k in ('name', 'size', 'sha256'))
                or artifact['mime'] != ('text/markdown' if name.endswith('.md') else 'application/json')
                or type(artifact['content_base64']) is not str or len(encoded(value)) > CONTENT_BYTES):
            raise ValueError
        raw = base64.b64decode(artifact['content_base64'], validate=True)
        if (base64.b64encode(raw).decode('ascii') != artifact['content_base64']
                or len(raw) != file['size'] or hashlib.sha256(raw).hexdigest() != file['sha256']):
            raise ValueError
        raw.decode('utf-8', errors='strict')
        return json.loads(encoded(value))
    except (ValueError, TypeError, KeyError, StopIteration, UnicodeError):
        raise ReportError('invalid_reply') from None


def dispatch(value):
    exact(value, ('schema_version', 'report_id', 'plan_sha256', 'attempt_id'))
    integer(value['schema_version'], 1, 1); uuid_string(value['report_id']); sha(value['plan_sha256']); uuid_string(value['attempt_id'])
    return value


def workflow_id(value):
    dispatch(value)
    return 'mf-report-v1-' + UUID(value['report_id']).hex + '-' + value['plan_sha256']


def budget_fingerprint(plan_sha256):
    return hashlib.sha256(('connected_report_budget_v1:' + sha(plan_sha256)).encode('ascii')).hexdigest()


def budget_episode(scope_group_id, report_id):
    """Fourth purpose; existing source/preparation/native UUID mappings stay intact."""
    if type(scope_group_id) is not str or re.fullmatch(r'mf1_[0-9a-f]{64}', scope_group_id) is None:
        raise ValueError
    operation = report_id if type(report_id) is UUID else UUID(uuid_string(report_id))
    return uuid5(NAMESPACE_URL, 'mirofish:connected-report-budget:v1:' + scope_group_id + ':' + str(operation))


report_budget_episode = budget_episode


@dataclass(frozen=True)
class ReportReceiptProof:
    """Immutable scalar copy of the actual completed public report receipt."""
    report_id: UUID
    plan_sha256: str
    context_sha256: str
    manifest_sha256: str
    output_language: str
    schema_version: int = 1
    reference_integrity: str = 'validated'
    semantic_support_status: str = 'not_reviewed'

    def __post_init__(self):
        if type(self.report_id) is not UUID:
            raise ValueError
        integer(self.schema_version, 1, 1)
        for value in (self.plan_sha256, self.context_sha256, self.manifest_sha256):
            sha(value)
        if (type(self.output_language) is not str or self.output_language not in ('en', 'zh', 'ms')
                or type(self.reference_integrity) is not str or self.reference_integrity != 'validated'
                or type(self.semantic_support_status) is not str or self.semantic_support_status != 'not_reviewed'):
            raise ValueError

    @classmethod
    def from_wire(cls, value):
        if type(value) is cls:
            value = value.json_value()
        exact(value, ('schema_version', 'report_id', 'plan_sha256', 'context_sha256', 'manifest_sha256',
                      'output_language', 'reference_integrity', 'semantic_support_status'))
        integer(value['schema_version'], 1, 1)
        return cls(UUID(uuid_string(value['report_id'])), sha(value['plan_sha256']), sha(value['context_sha256']),
            sha(value['manifest_sha256']), value['output_language'], value['schema_version'],
            value['reference_integrity'], value['semantic_support_status'])

    def json_value(self):
        return dict(schema_version=self.schema_version, report_id=str(self.report_id), plan_sha256=self.plan_sha256,
            context_sha256=self.context_sha256, manifest_sha256=self.manifest_sha256, output_language=self.output_language,
            reference_integrity=self.reference_integrity, semantic_support_status=self.semantic_support_status)


@dataclass(frozen=True)
class ReportBudgetReceipt:
    operation_id: UUID
    attempt_id: UUID
    fingerprint: str
    plan_sha256: str
    _proof: ReportReceiptProof
    report_receipt_sha256: str
    kind: str = 'connected_report_budget_v1'

    def __post_init__(self):
        if (type(self.operation_id) is not UUID or type(self.attempt_id) is not UUID
                or type(self._proof) is not ReportReceiptProof
                or type(self.kind) is not str or self.kind != 'connected_report_budget_v1'):
            raise ValueError
        sha(self.plan_sha256); sha(self.fingerprint); sha(self.report_receipt_sha256)
        if (self.operation_id != self._proof.report_id or self.plan_sha256 != self._proof.plan_sha256
                or self.fingerprint != budget_fingerprint(self.plan_sha256)
                or self.report_receipt_sha256 != digest(self._proof.json_value())):
            raise ValueError

    @classmethod
    def from_wire(cls, value):
        try:
            if type(value) is cls:
                value = value.json_value()
            exact(value, ('kind', 'operation_id', 'attempt_id', 'fingerprint', 'plan_sha256', 'report_receipt', 'report_receipt_sha256'))
            scalar_tree(value)
            if len(encoded(value)) > 4096:
                raise ValueError
            return cls(UUID(uuid_string(value['operation_id'])), UUID(uuid_string(value['attempt_id'])),
                sha(value['fingerprint']), sha(value['plan_sha256']), ReportReceiptProof.from_wire(value['report_receipt']),
                sha(value['report_receipt_sha256']), value['kind'])
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise ReportError('report_uncertain') from None

    def json_value(self):
        # Fresh dictionaries; callers cannot mutate the immutable saved proof.
        return dict(kind=self.kind, operation_id=str(self.operation_id), attempt_id=str(self.attempt_id),
            fingerprint=self.fingerprint, plan_sha256=self.plan_sha256,
            report_receipt=self.report_receipt, report_receipt_sha256=self.report_receipt_sha256)

    @property
    def report_receipt(self):
        return self._proof.json_value()

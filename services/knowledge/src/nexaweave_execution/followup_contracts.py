"""Cold, strict contracts for a grounded connected report follow-up."""
import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from uuid import UUID, NAMESPACE_URL, uuid5

from .report_contracts import (ReportError, canonical, digest, encoded, exact,
    integer, scalar_tree, sha, text, uuid_string, validate_binding,
    validate_limits, validate_manifest as validate_parent_manifest,
    strict_json, reference_key)

REQUEST_BYTES, RESULT_BYTES, CONTENT_BYTES = 32768, 262144, 4194304
FILE_BYTES, TOTAL_BYTES, CONTEXT_BYTES = 2097152, 16777216, 2097152
FILE_NAMES = ('turn.json', 'answer.md', 'conversation.json',
              'retrieval_evidence.json', 'native_evidence.json', 'tool_trace.json')
KINDS = dict(answer='answer.md', metadata='turn.json', conversation='conversation.json',
             evidence='retrieval_evidence.json', native_evidence='native_evidence.json',
             tools='tool_trace.json')
IDENTITY = ('schema_version', 'turn_id', 'binding', 'options', 'history',
            'report_context', 'context_sha256', 'source_projection_sha256',
            'model_label', 'limits', 'ceiling_microusd')
COMMON = set(IDENTITY) | {'plan_sha256', 'authorization', 'state', 'progress',
    'workflow', 'receipt', 'receipt_sha256', 'manifest',
    'published_history_head_sha256', 'cleanup', 'cancel_requested', 'error_code'}
CODES = frozenset(('invalid_request invalid_reply unauthorized origin_denied not_found '
    'conflict tombstoned busy result_too_large followup_unavailable model_calls_disabled '
    'budget_denied followup_failed followup_cancelled followup_uncertain timeout '
    'transport_failure internal_error history_changed followup_active turn_conflict').split())
STATES = frozenset('planned queued generating completed failed cancelled uncertain'.split())
DEFAULT_LIMITS = dict(max_calls=8, max_input_bytes=1048576,
                      max_output_tokens=4096, max_run_seconds=600)
ZERO_HEAD_FIELDS = ('schema_version', 'report_id', 'report_plan_sha256', 'turns')


class FollowupError(RuntimeError):
    def __init__(self, code='invalid_request'):
        self.code = code if type(code) is str and code in CODES else 'internal_error'
        super().__init__(self.code)


def _fail(code, function, *args):
    try:
        return function(*args)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError,
            ReportError, OverflowError, binascii.Error):
        raise FollowupError(code) from None


def _scalar(value):
    scalar_tree(value)
    if len(encoded(value)) > CONTEXT_BYTES:
        raise ValueError
    return value


def _question(value):
    text(value, 4000, 16000)
    if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError
    return value


def validate_payload(method, value):
    def check():
        if method not in ('plan', 'start', 'status', 'cancel', 'read', 'download', 'history'):
            raise ValueError
        if method == 'plan':
            keys = ('schema_version', 'turn_id', 'report_id', 'report_plan_sha256',
                    'question', 'output_language', 'expected_history_sha256')
        elif method == 'history':
            keys = ('schema_version', 'report_id', 'report_plan_sha256', 'before_ordinal')
        else:
            keys = ('schema_version', 'turn_id', 'plan_sha256') + (('kind',) if method == 'download' else ())
        exact(value, keys); _scalar(value)
        integer(value['schema_version'], 1, 1)
        if method in ('plan', 'history'):
            uuid_string(value['report_id']); sha(value['report_plan_sha256'])
        if method != 'history':
            uuid_string(value['turn_id'])
        if method == 'plan':
            _question(value['question'])
            if value['output_language'] not in ('en', 'zh', 'ms'):
                raise ValueError
            if value['expected_history_sha256'] is not None:
                sha(value['expected_history_sha256'])
        elif method == 'history':
            if value['before_ordinal'] is not None:
                integer(value['before_ordinal'], 1, 1000)
        else:
            sha(value['plan_sha256'])
            if method == 'download' and (type(value['kind']) is not str or value['kind'] not in KINDS):
                raise ValueError
        if len(encoded(value)) > REQUEST_BYTES:
            raise ValueError
        return json.loads(encoded(value))
    return _fail('invalid_request', check)


def empty_head(report_id, report_plan_sha256):
    uuid_string(report_id); sha(report_plan_sha256)
    return digest(dict(schema_version=1, report_id=report_id,
                       report_plan_sha256=report_plan_sha256, turns=[]))


def next_head(report_id, report_plan_sha256, predecessor, ordinal,
              turn_id, plan_sha256, question, answer_sha256, receipt_sha256):
    uuid_string(report_id); sha(report_plan_sha256); sha(predecessor)
    integer(ordinal, 1, 1000); uuid_string(turn_id); sha(plan_sha256)
    _question(question); sha(answer_sha256); sha(receipt_sha256)
    return digest(dict(schema_version=1, report_id=report_id,
        report_plan_sha256=report_plan_sha256, predecessor_head_sha256=predecessor,
        ordinal=ordinal, turn_id=turn_id, plan_sha256=plan_sha256,
        question_sha256=hashlib.sha256(question.encode('utf-8')).hexdigest(),
        answer_sha256=answer_sha256, receipt_sha256=receipt_sha256))


def validate_history(value, report_id, report_plan_sha256):
    exact(value, ('head_sha256', 'total_completed', 'window_start', 'pairs'))
    total = integer(value['total_completed'], 0, 1000)
    pairs = value['pairs']
    if type(pairs) is not list or len(pairs) != min(5, total) or value['window_start'] != (total-len(pairs)+1):
        raise ValueError
    head = empty_head(report_id, report_plan_sha256) if total == 0 else None
    for index, pair in enumerate(pairs):
        exact(pair, ('turn_id', 'plan_sha256', 'ordinal', 'question', 'answer_sha256',
            'answer_prefix', 'answer_prefix_sha256', 'answer_characters',
            'admitted_characters', 'truncated', 'receipt_sha256',
            'predecessor_head_sha256', 'published_head_sha256'))
        uuid_string(pair['turn_id']); sha(pair['plan_sha256']); sha(pair['answer_sha256'])
        sha(pair['receipt_sha256']); sha(pair['predecessor_head_sha256']); sha(pair['published_head_sha256'])
        if pair['ordinal'] != value['window_start'] + index:
            raise ValueError
        _question(pair['question'])
        prefix = pair['answer_prefix']
        if type(prefix) is not str or len(prefix) > 4000 or not prefix.strip():
            raise ValueError
        characters = integer(pair['answer_characters'], 1, 16384)
        if (pair['admitted_characters'] != min(4000, characters) or len(prefix) != pair['admitted_characters']
                or type(pair['truncated']) is not bool or pair['truncated'] != (characters > 4000)
                or hashlib.sha256(prefix.encode('utf-8')).hexdigest() != pair['answer_prefix_sha256']):
            raise ValueError
        if not pair['truncated'] and hashlib.sha256(prefix.encode('utf-8')).hexdigest() != pair['answer_sha256']:
            raise ValueError
        expected = next_head(report_id, report_plan_sha256, pair['predecessor_head_sha256'],
            pair['ordinal'], pair['turn_id'], pair['plan_sha256'], pair['question'],
            pair['answer_sha256'], pair['receipt_sha256'])
        if pair['published_head_sha256'] != expected or head is not None and pair['predecessor_head_sha256'] != head:
            raise ValueError
        head = expected
    sha(value['head_sha256'])
    if head is not None and head != value['head_sha256']:
        raise ValueError
    return value


def validate_report_context(value):
    exact(value, ('file_sha256', 'prefix_sha256', 'prefix_characters', 'total_characters', 'truncated'))
    sha(value['file_sha256']); sha(value['prefix_sha256'])
    total = integer(value['total_characters'], 1, FILE_BYTES)
    if (value['prefix_characters'] != min(15000, total) or type(value['truncated']) is not bool
            or value['truncated'] != (total > 15000)):
        raise ValueError
    return value


def validate_identity(value):
    def check():
        exact(value, IDENTITY); integer(value['schema_version'], 1, 1)
        uuid_string(value['turn_id'])
        binding = exact(value['binding'], ('display_graph_id', 'principal', 'scope', 'report', 'native_binding'))
        validate_binding(binding['native_binding'])
        native = binding['native_binding']
        if any(binding[key] != native[key] for key in ('display_graph_id', 'principal', 'scope')):
            raise ValueError
        report = exact(binding['report'], ('report_id', 'plan_sha256', 'receipt_sha256',
                                          'manifest_sha256', 'full_report_sha256'))
        uuid_string(report['report_id'])
        for key in ('plan_sha256', 'receipt_sha256', 'manifest_sha256', 'full_report_sha256'):
            sha(report[key])
        options = exact(value['options'], ('question', 'output_language', 'expected_history_sha256'))
        _question(options['question'])
        if options['output_language'] not in ('en', 'zh', 'ms'):
            raise ValueError
        if options['expected_history_sha256'] is not None:
            sha(options['expected_history_sha256'])
        validate_history(value['history'], report['report_id'], report['plan_sha256'])
        if options['expected_history_sha256'] is not None and options['expected_history_sha256'] != value['history']['head_sha256']:
            raise ValueError
        validate_report_context(value['report_context'])
        sha(value['context_sha256']); sha(value['source_projection_sha256'])
        text(value['model_label'], 200); validate_limits(value['limits'])
        if value['ceiling_microusd'] is not None:
            integer(value['ceiling_microusd'], 1, 2**63-1)
        return value
    return _fail('invalid_reply', check)


def validate_manifest(value):
    exact(value, ('schema_version', 'files')); integer(value['schema_version'], 1, 1)
    if type(value['files']) is not list or len(value['files']) != len(FILE_NAMES):
        raise ValueError
    for file, name in zip(value['files'], FILE_NAMES):
        exact(file, ('name', 'size', 'sha256'))
        if file['name'] != name:
            raise ValueError
        integer(file['size'], 1, FILE_BYTES); sha(file['sha256'])
    if sum(file['size'] for file in value['files']) > TOTAL_BYTES:
        raise ValueError
    return value


def validate_receipt(value, identity, manifest):
    exact(value, ('schema_version', 'turn_id', 'plan_sha256', 'parent_report_id',
        'parent_report_plan_sha256', 'parent_report_receipt_sha256', 'context_sha256',
        'history_head_sha256', 'ordinal', 'manifest_sha256', 'output_language',
        'reference_integrity', 'semantic_support_status'))
    expected = dict(schema_version=1, turn_id=identity['turn_id'],
        plan_sha256=digest(identity), parent_report_id=identity['binding']['report']['report_id'],
        parent_report_plan_sha256=identity['binding']['report']['plan_sha256'],
        parent_report_receipt_sha256=identity['binding']['report']['receipt_sha256'],
        context_sha256=identity['context_sha256'], history_head_sha256=identity['history']['head_sha256'],
        ordinal=identity['history']['total_completed']+1, manifest_sha256=digest(manifest),
        output_language=identity['options']['output_language'],
        reference_integrity='validated', semantic_support_status='not_reviewed')
    if value != expected:
        raise ValueError
    return value


def validate_answer_references(answer, identity):
    """Pure final reference-integrity gate; semantic support remains separate."""
    if type(answer) is not str or not answer.strip() or len(answer.encode('utf-8')) > 16384:
        raise FollowupError('followup_failed')
    pattern = r'(?<!\[)\[\[([^\[\]]+)\]\](?!\])'
    markers = re.findall(pattern, answer)
    allowed = set(identity['binding']['native_binding']['reference_keys'])
    if not markers or any(marker not in allowed for marker in markers):
        raise FollowupError('followup_failed')
    remainder = re.sub(pattern, '', answer)
    if '[[' in remainder or ']]' in remainder:
        raise FollowupError('followup_failed')
    for marker in markers:
        try:
            reference_key(marker)
        except (ValueError, TypeError):
            raise FollowupError('followup_failed') from None
    return markers


def validate_conversation(value, identity, answer):
    """Current pair has external proof; every earlier pair proves its chain."""
    try:
        exact(value, ('schema_version', 'report_id', 'report_plan_sha256', 'total_completed', 'pairs'))
        integer(value['schema_version'], 1, 1)
        report = identity['binding']['report']
        ordinal = identity['history']['total_completed'] + 1
        if (value['report_id'] != report['report_id']
                or value['report_plan_sha256'] != report['plan_sha256']
                or value['total_completed'] != ordinal or type(value['pairs']) is not list
                or len(value['pairs']) != ordinal or len(encoded(value)) > FILE_BYTES):
            raise ValueError
        previous = empty_head(report['report_id'], report['plan_sha256'])
        for index, pair in enumerate(value['pairs'], 1):
            exact(pair, ('turn_id', 'plan_sha256', 'ordinal', 'question', 'answer',
                'answer_sha256', 'predecessor_head_sha256', 'receipt_sha256',
                'published_head_sha256'))
            uuid_string(pair['turn_id']); sha(pair['plan_sha256']); _question(pair['question'])
            sha(pair['answer_sha256']); sha(pair['predecessor_head_sha256'])
            if (pair['ordinal'] != index or type(pair['answer']) is not str
                    or not pair['answer'].strip() or len(pair['answer'].encode('utf-8')) > 16384
                    or hashlib.sha256(pair['answer'].encode('utf-8')).hexdigest() != pair['answer_sha256']
                    or pair['predecessor_head_sha256'] != previous):
                raise ValueError
            if index < ordinal:
                sha(pair['receipt_sha256']); sha(pair['published_head_sha256'])
                expected = next_head(report['report_id'], report['plan_sha256'], previous,
                    index, pair['turn_id'], pair['plan_sha256'], pair['question'],
                    pair['answer_sha256'], pair['receipt_sha256'])
                if pair['published_head_sha256'] != expected:
                    raise ValueError
                previous = expected
            else:
                if (pair['receipt_sha256'] is not None or pair['published_head_sha256'] is not None
                        or pair['turn_id'] != identity['turn_id'] or pair['plan_sha256'] != digest(identity)
                        or pair['question'] != identity['options']['question'] or pair['answer'] != answer
                        or previous != identity['history']['head_sha256']):
                    raise ValueError
        return value
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise FollowupError('conflict') from None


def validate_result(value, graph_id, scope, payload, method, principal=None, known=None):
    def check():
        _scalar(value); exact(value, COMMON)
        if len(encoded(value)) > RESULT_BYTES:
            raise FollowupError('result_too_large')
        identity = validate_identity({k: value[k] for k in IDENTITY})
        binding = identity['binding']
        if (binding['display_graph_id'] != graph_id or binding['scope'] != scope
                or principal is not None and binding['principal'] != principal
                or value['turn_id'] != payload['turn_id']
                or sha(value['plan_sha256']) != digest(identity)):
            raise ValueError
        if method == 'plan':
            if (binding['report']['report_id'] != payload['report_id']
                    or binding['report']['plan_sha256'] != payload['report_plan_sha256']
                    or identity['options'] != {k:payload[k] for k in ('question', 'output_language', 'expected_history_sha256')}):
                raise ValueError
        elif value['plan_sha256'] != payload['plan_sha256']:
            raise ValueError
        if known is not None and any(value[k] != known[k] for k in IDENTITY):
            raise ValueError
        authorization = exact(value['authorization'], ('model_calls_enabled', 'budget_configured'))
        if any(type(v) is not bool for v in authorization.values()) or authorization['model_calls_enabled'] and not authorization['budget_configured']:
            raise ValueError
        if authorization['budget_configured'] and identity['ceiling_microusd'] is None:
            raise ValueError
        if value['state'] not in STATES or type(value['cancel_requested']) is not bool:
            raise ValueError
        progress = exact(value['progress'], ('stage', 'percent', 'completed_sections', 'total_sections'))
        if progress['stage'] not in STATES | {'planning', 'researching', 'writing', 'publishing'}:
            raise ValueError
        integer(progress['percent'], 0, 100)
        total = integer(progress['total_sections'], 0, 8)
        integer(progress['completed_sections'], 0, total)
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
        if value['error_code'] is not None and value['error_code'] not in CODES:
            raise ValueError
        if value['state'] in ('planned', 'queued', 'generating', 'completed') and value['error_code'] is not None:
            raise ValueError
        if value['state'] in ('failed', 'uncertain') and value['error_code'] is None:
            raise ValueError
        if value['state'] in ('planned', 'queued', 'completed', 'failed', 'cancelled', 'uncertain') and progress['stage'] != value['state']:
            raise ValueError
        if value['state'] == 'completed':
            manifest = validate_manifest(value['manifest'])
            validate_receipt(value['receipt'], identity, manifest)
            if (value['receipt_sha256'] != digest(value['receipt'])
                    or value['published_history_head_sha256'] != next_head(
                        binding['report']['report_id'], binding['report']['plan_sha256'],
                        identity['history']['head_sha256'], value['receipt']['ordinal'],
                        identity['turn_id'], value['plan_sha256'], identity['options']['question'],
                        next(f['sha256'] for f in manifest['files'] if f['name']=='answer.md'),
                        value['receipt_sha256'])
                    or cleanup != dict(known=True, pending=False, owner_thread_alive=False)
                    or progress['percent'] != 100):
                raise ValueError
        elif any(value[k] is not None for k in ('receipt', 'receipt_sha256', 'manifest', 'published_history_head_sha256')):
            raise ValueError
        return json.loads(encoded(value))
    return _fail('invalid_reply', check)


def validate_read(value, graph_id, scope, payload, principal=None, known=None):
    def check():
        exact(value, ('schema_version', 'turn', 'content'))
        integer(value['schema_version'], 1, 1)
        turn = validate_result(value['turn'], graph_id, scope, payload, 'read', principal, known)
        if turn['state'] != 'completed' or type(value['content']) is not str or len(encoded(value)) > CONTENT_BYTES:
            raise ValueError
        raw = value['content'].encode('utf-8')
        file = turn['manifest']['files'][1]
        if len(raw) != file['size'] or hashlib.sha256(raw).hexdigest() != file['sha256']:
            raise ValueError
        return json.loads(encoded(value))
    return _fail('invalid_reply', check)


def validate_download(value, payload, known):
    def check():
        exact(value, ('schema_version', 'turn_id', 'plan_sha256', 'receipt_sha256', 'artifact'))
        integer(value['schema_version'], 1, 1)
        if known['state'] != 'completed' or any(value[k] != known[k] for k in ('turn_id', 'plan_sha256', 'receipt_sha256')):
            raise ValueError
        artifact = exact(value['artifact'], ('name', 'mime', 'size', 'sha256', 'content_base64'))
        file = next(f for f in known['manifest']['files'] if f['name'] == KINDS[payload['kind']])
        if any(artifact[k] != file[k] for k in ('name', 'size', 'sha256')):
            raise ValueError
        if artifact['mime'] != ('text/markdown' if file['name'].endswith('.md') else 'application/json'):
            raise ValueError
        raw = base64.b64decode(artifact['content_base64'], validate=True)
        if (base64.b64encode(raw).decode('ascii') != artifact['content_base64']
                or len(raw) != file['size'] or hashlib.sha256(raw).hexdigest() != file['sha256']
                or len(encoded(value)) > CONTENT_BYTES):
            raise ValueError
        raw.decode('utf-8', errors='strict')
        return json.loads(encoded(value))
    return _fail('invalid_reply', check)


def validate_history_page(value, payload):
    def check():
        exact(value, ('schema_version', 'report_id', 'report_plan_sha256', 'binding',
                      'head_sha256', 'total_completed', 'pairs', 'before_ordinal'))
        integer(value['schema_version'], 1, 1)
        if value['report_id'] != payload['report_id'] or value['report_plan_sha256'] != payload['report_plan_sha256']:
            raise ValueError
        binding = exact(value['binding'], ('display_graph_id', 'principal', 'scope', 'report', 'native_binding'))
        validate_binding(binding['native_binding'])
        report = exact(binding['report'], ('report_id', 'plan_sha256', 'receipt_sha256',
                                          'manifest_sha256', 'full_report_sha256'))
        uuid_string(report['report_id'])
        for key in ('plan_sha256', 'receipt_sha256', 'manifest_sha256', 'full_report_sha256'):
            sha(report[key])
        if (binding['report']['report_id'] != payload['report_id']
                or binding['report']['plan_sha256'] != payload['report_plan_sha256']
                or any(binding[k] != binding['native_binding'][k] for k in ('display_graph_id', 'principal', 'scope'))):
            raise ValueError
        sha(value['head_sha256']); total = integer(value['total_completed'], 0, 1000)
        before = value['before_ordinal']
        if before != payload['before_ordinal'] or before is not None and before > total + 1:
            raise ValueError
        end = total if before is None else before - 1
        pairs = value['pairs']
        if type(pairs) is not list or len(pairs) != min(5, end):
            raise ValueError
        first = max(1, end - 4)
        previous = empty_head(value['report_id'], value['report_plan_sha256']) if first == 1 else None
        for ordinal, pair in enumerate(pairs, first):
            if pair['ordinal'] != ordinal:
                raise ValueError
            # Reuse the complete five-pair validator's strict field checks via
            # the single-pair primitive below; prior pages need no current head.
            _validate_completed_pair(pair, value['report_id'], value['report_plan_sha256'])
            if previous is not None and pair['predecessor_head_sha256'] != previous:
                raise ValueError
            previous = pair['published_head_sha256']
        if before is None and previous != value['head_sha256']:
            raise ValueError
        if total == 0 and value['head_sha256'] != empty_head(value['report_id'], value['report_plan_sha256']):
            raise ValueError
        return json.loads(encoded(value))
    return _fail('invalid_reply', check)


def _validate_completed_pair(pair, report_id, report_plan_sha256):
    exact(pair, ('turn_id', 'plan_sha256', 'ordinal', 'question', 'answer_sha256',
        'answer_prefix', 'answer_prefix_sha256', 'answer_characters',
        'admitted_characters', 'truncated', 'receipt_sha256',
        'predecessor_head_sha256', 'published_head_sha256'))
    uuid_string(pair['turn_id']); sha(pair['plan_sha256']); sha(pair['answer_sha256'])
    sha(pair['answer_prefix_sha256']); sha(pair['receipt_sha256'])
    sha(pair['predecessor_head_sha256']); sha(pair['published_head_sha256'])
    integer(pair['ordinal'], 1, 1000); _question(pair['question'])
    prefix = pair['answer_prefix']
    if type(prefix) is not str or not prefix.strip() or len(prefix) > 4000:
        raise ValueError
    characters = integer(pair['answer_characters'], 1, 16384)
    if (len(prefix) != pair['admitted_characters'] or pair['admitted_characters'] != min(4000, characters)
            or type(pair['truncated']) is not bool or pair['truncated'] != (characters > 4000)
            or hashlib.sha256(prefix.encode('utf-8')).hexdigest() != pair['answer_prefix_sha256']):
        raise ValueError
    if not pair['truncated'] and pair['answer_prefix_sha256'] != pair['answer_sha256']:
        raise ValueError
    if next_head(report_id, report_plan_sha256, pair['predecessor_head_sha256'],
            pair['ordinal'], pair['turn_id'], pair['plan_sha256'], pair['question'],
            pair['answer_sha256'], pair['receipt_sha256']) != pair['published_head_sha256']:
        raise ValueError
    return pair


def dispatch(value):
    exact(value, ('schema_version', 'turn_id', 'plan_sha256', 'attempt_id'))
    integer(value['schema_version'], 1, 1)
    uuid_string(value['turn_id']); sha(value['plan_sha256']); uuid_string(value['attempt_id'])
    return value


def workflow_id(value):
    dispatch(value)
    return 'mf-followup-v1-' + UUID(value['turn_id']).hex + '-' + value['plan_sha256']


def budget_fingerprint(plan_sha256):
    return hashlib.sha256(('connected_followup_budget_v1:' + sha(plan_sha256)).encode('ascii')).hexdigest()


def budget_episode(scope_group_id, turn_id):
    if type(scope_group_id) is not str or not scope_group_id.startswith('mf1_') or len(scope_group_id) != 68:
        raise ValueError
    sha(scope_group_id[4:])
    operation = turn_id if type(turn_id) is UUID else UUID(uuid_string(turn_id))
    return uuid5(NAMESPACE_URL, 'mirofish:connected-followup-budget:v1:' + scope_group_id + ':' + str(operation))


@dataclass(frozen=True)
class FollowupBudgetReceipt:
    operation_id: UUID
    attempt_id: UUID
    fingerprint: str
    plan_sha256: str
    _proof: bytes
    followup_receipt_sha256: str
    kind: str = 'connected_followup_budget_v1'

    @classmethod
    def from_wire(cls, value):
        try:
            exact(value, ('kind', 'operation_id', 'attempt_id', 'fingerprint', 'plan_sha256',
                          'followup_receipt', 'followup_receipt_sha256'))
            if value['kind'] != 'connected_followup_budget_v1' or len(encoded(value)) > 4096:
                raise ValueError
            operation = UUID(uuid_string(value['operation_id']))
            attempt = UUID(uuid_string(value['attempt_id']))
            plan = sha(value['plan_sha256'])
            fingerprint = sha(value['fingerprint'])
            receipt = value['followup_receipt']
            exact(receipt, ('schema_version', 'turn_id', 'plan_sha256', 'parent_report_id',
                'parent_report_plan_sha256', 'parent_report_receipt_sha256', 'context_sha256',
                'history_head_sha256', 'ordinal', 'manifest_sha256', 'output_language',
                'reference_integrity', 'semantic_support_status'))
            integer(receipt['schema_version'], 1, 1)
            if (receipt['turn_id'] != str(operation) or receipt['plan_sha256'] != plan
                    or fingerprint != budget_fingerprint(plan)
                    or sha(value['followup_receipt_sha256']) != digest(receipt)):
                raise ValueError
            for key in ('parent_report_plan_sha256', 'parent_report_receipt_sha256',
                        'context_sha256', 'history_head_sha256', 'manifest_sha256'):
                sha(receipt[key])
            uuid_string(receipt['parent_report_id'])
            integer(receipt['ordinal'], 1, 1000)
            if receipt['output_language'] not in ('en', 'zh', 'ms') or receipt['reference_integrity'] != 'validated' or receipt['semantic_support_status'] != 'not_reviewed':
                raise ValueError
            return cls(operation, attempt, fingerprint, plan,
                canonical(receipt), value['followup_receipt_sha256'])
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise FollowupError('followup_uncertain') from None

    def json_value(self):
        return dict(kind=self.kind, operation_id=str(self.operation_id), attempt_id=str(self.attempt_id),
            fingerprint=self.fingerprint, plan_sha256=self.plan_sha256,
            followup_receipt=self.followup_receipt,
            followup_receipt_sha256=self.followup_receipt_sha256)

    @property
    def followup_receipt(self):
        return json.loads(self._proof)

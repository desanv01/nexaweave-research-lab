"""SDK-free strict native launch wire boundary; no native/model imports."""
from __future__ import annotations

import json
import re
from uuid import UUID
from .preparation_client import canonical, digest, encoded, integer, sha, text, uuid_string

REQUEST_BYTES, RESULT_BYTES, ENVELOPE_OVERHEAD = 4096, 65536, 128
CODES = frozenset(('invalid_request not_found unauthorized origin_denied conflict busy tombstoned '
    'result_too_large model_calls_disabled budget_denied native_launch_unavailable '
    'native_launch_uncertain native_launch_failed invalid_reply internal_error').split())
STATES = frozenset('planned queued starting running completed failed cancelled uncertain'.split())
IDENTITY = ('schema_version', 'display_graph_id', 'scope', 'preparation', 'request',
            'limits', 'ceiling_microusd', 'model_label')
IDENTITY_V2 = (*IDENTITY, 'checkpoint')
KEYS = set(IDENTITY) | {'launch_sha256', 'state', 'error_code', 'authorization',
                       'workflow', 'receipt', 'cancel_requested', 'cleanup'}
_KEYS_V2 = KEYS | {'checkpoint'}
_NATIVE_REQUEST_KEYS = {'schema_version', 'principal', 'project_id', 'project_revision',
                        'simulation_id', 'run_id', 'artifact_sha256', 'runtime_sha256',
                        'platforms', 'seed', 'max_rounds'}
_CONTINUATION_KEYS = {'source_run_id', 'checkpoint_sha256'}


class NativeLaunchError(RuntimeError):
    def __init__(self, code='invalid_request'):
        self.code = code if code in CODES else 'internal_error'
        super().__init__(self.code)


def _checkpoint(value, launch_id):
    if type(value) is not dict or type(value.get('mode')) is not str:
        raise ValueError
    if value['mode'] == 'capture' and set(value) == {'mode'}:
        return None
    if value['mode'] != 'continue' or set(value) != ({'mode'} | _CONTINUATION_KEYS):
        raise ValueError
    source_run_id = uuid_string(value['source_run_id'])
    if source_run_id == launch_id:
        raise ValueError
    return source_run_id, sha(value['checkpoint_sha256'])


def validate_payload(method, value):
    try:
        if method not in {'plan', 'start', 'status', 'cancel'} or type(value) is not dict:
            raise ValueError
        version = integer(value['schema_version'], 1, 2)
        expected = {'schema_version', 'launch_id', 'preparation' if method == 'plan' else 'launch_sha256'}
        if version == 2 and method == 'plan':
            expected.add('checkpoint')
        if set(value) != expected:
            raise ValueError
        launch_id = uuid_string(value['launch_id'])
        if method == 'plan':
            prep = value['preparation']
            if type(prep) is not dict or set(prep) != {'operation_id', 'plan_sha256'}:
                raise ValueError
            uuid_string(prep['operation_id']); sha(prep['plan_sha256'])
            if version == 2:
                _checkpoint(value['checkpoint'], launch_id)
        else:
            sha(value['launch_sha256'])
        if len(encoded(value)) > REQUEST_BYTES:
            raise ValueError
        return json.loads(encoded(value))
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise NativeLaunchError('invalid_request') from None


def validate_limits(value):
    if type(value) is not dict or set(value) != {'max_calls', 'max_input_bytes', 'max_output_tokens', 'max_run_seconds'}:
        raise ValueError
    for name, high in [('max_calls', 10000), ('max_input_bytes', 2097152),
                       ('max_output_tokens', 4096), ('max_run_seconds', 600)]:
        integer(value[name], 1, high)
    return value


def validate_result(value, graph_id, scope, payload, method):
    try:
        if type(value) is not dict:
            raise ValueError
        version = integer(value['schema_version'], 1, 2)
        expected = KEYS if version == 1 else _KEYS_V2
        if (version != integer(payload['schema_version'], 1, 2) or set(value) != expected
                or len(encoded(value)) > RESULT_BYTES):
            raise ValueError
        if value['display_graph_id'] != graph_id or value['scope'] != scope:
            raise ValueError
        if (type(scope) is not dict or set(scope) != {'schema_version', 'workspace_id', 'project_id', 'graph_id', 'layer', 'run_id', 'branch_id'}
                or scope['layer'] != 'source' or scope['run_id'] is not None or scope['branch_id'] is not None):
            raise ValueError
        integer(scope['schema_version'], 1, 1)
        uuid_string(scope['workspace_id']); uuid_string(scope['project_id']); uuid_string(scope['graph_id'])
        prep = value['preparation']
        if type(prep) is not dict or set(prep) != {'operation_id', 'plan_sha256', 'simulation_id', 'artifact_sha256'}:
            raise ValueError
        uuid_string(prep['operation_id']); sha(prep['plan_sha256']); sha(prep['artifact_sha256'])
        if prep['simulation_id'] != 'sim_' + UUID(prep['operation_id']).hex:
            raise ValueError
        checkpoint = _checkpoint(value['checkpoint'], payload['launch_id']) if version == 2 else None
        request_version = 1 if checkpoint is None else 2
        request_keys = (_NATIVE_REQUEST_KEYS if request_version == 1
                        else _NATIVE_REQUEST_KEYS | _CONTINUATION_KEYS)
        req = value['request']
        if type(req) is not dict or set(req) != request_keys:
            raise ValueError
        integer(req['schema_version'], request_version, request_version)
        integer(req['project_revision'], 1, 2147483647)
        uuid_string(req['run_id']); uuid_string(req['project_id'])
        sha(req['artifact_sha256']); sha(req['runtime_sha256'])
        if (type(req['principal']) is not str or not re.fullmatch(r'[\x20-\x7e]{1,128}', req['principal'])
                or not req['principal'].strip() or req['project_id'] != scope['project_id']
                or req['run_id'] != payload['launch_id'] or req['simulation_id'] != prep['simulation_id']
                or req['artifact_sha256'] != prep['artifact_sha256']
                or type(req['platforms']) is not list
                or req['platforms'] not in [['twitter'], ['reddit'], ['twitter', 'reddit']]):
            raise ValueError
        integer(req['seed'], 0, 4294967295); integer(req['max_rounds'], 1, 24)
        if version == 2 and (req['platforms'] != ['twitter'] or req['max_rounds'] != 1):
            raise ValueError
        if checkpoint is not None and checkpoint != (
                uuid_string(req['source_run_id']), sha(req['checkpoint_sha256'])):
            raise ValueError
        validate_limits(value['limits']); text(value['model_label'], 128)
        ceiling = value['ceiling_microusd']
        if ceiling is not None and (type(ceiling) is not str or not re.fullmatch(r'[1-9][0-9]{0,18}', ceiling)
                                    or int(ceiling) > 9223372036854775807):
            raise ValueError
        identity = IDENTITY if version == 1 else IDENTITY_V2
        if sha(value['launch_sha256']) != digest({k: value[k] for k in identity}):
            raise ValueError
        if method == 'plan':
            if {k: prep[k] for k in ('operation_id', 'plan_sha256')} != payload['preparation']:
                raise ValueError
            if version == 2 and value['checkpoint'] != payload['checkpoint']:
                raise ValueError
        elif value['launch_sha256'] != payload['launch_sha256']:
            raise ValueError
        if value['state'] not in STATES or value['error_code'] is not None and value['error_code'] not in CODES:
            raise ValueError
        auth = value['authorization']
        if type(auth) is not dict or set(auth) != {'model_calls_enabled'} or type(auth['model_calls_enabled']) is not bool:
            raise ValueError
        if auth['model_calls_enabled'] and ceiling is None:
            raise ValueError
        if type(value['cancel_requested']) is not bool:
            raise ValueError
        cleanup = value['cleanup']
        if type(cleanup) is not dict or set(cleanup) != {'known', 'pending', 'owner_thread_alive'} or type(cleanup['known']) is not bool:
            raise ValueError
        if cleanup['known']:
            if any(type(cleanup[k]) is not bool for k in ('pending', 'owner_thread_alive')):
                raise ValueError
        elif cleanup['pending'] is not None or cleanup['owner_thread_alive'] is not None:
            raise ValueError
        workflow = value['workflow']
        if workflow is not None:
            if type(workflow) is not dict or set(workflow) != {'workflow_id', 'temporal_run_id', 'native_run_id'}:
                raise ValueError
            uuid_string(workflow['temporal_run_id'])
            if (workflow['native_run_id'] != req['run_id'] or workflow['workflow_id'] !=
                    'mf-native-v1-' + UUID(req['run_id']).hex + '-' + digest(req)):
                raise ValueError
        receipt = value['receipt']
        if receipt is not None:
            if type(receipt) is not dict or set(receipt) != {'run_id', 'attempt_id', 'instance_id', 'request_fingerprint', 'outcome', 'evidence_sha256'}:
                raise ValueError
            for k in ('run_id', 'attempt_id', 'instance_id'):
                uuid_string(receipt[k])
            sha(receipt['evidence_sha256'])
            if (receipt['run_id'] != req['run_id'] or receipt['request_fingerprint'] != digest(req)
                    or receipt['outcome'] not in {'completed', 'failed', 'cancelled'}
                    or receipt['outcome'] != value['state']):
                raise ValueError
        if value['state'] == 'completed' and receipt is None:
            raise ValueError
        return json.loads(encoded(value))
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise NativeLaunchError('invalid_reply') from None

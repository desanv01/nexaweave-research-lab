"""Strict, SDK-free version-one preparation boundary."""
from __future__ import annotations

import hashlib
import json
import re
from uuid import UUID

REQUEST_BYTES = 65536
RESULT_BYTES = 262144
ENVELOPE_OVERHEAD = 128
CODES = frozenset(('invalid_request not_found unauthorized origin_denied conflict busy '
    'tombstoned empty_selection result_too_large preparation_unavailable model_calls_disabled '
    'budget_denied preparation_failed preparation_cancelled preparation_uncertain timeout '
    'transport_failure invalid_reply internal_error').split())
STATES = frozenset('planned queued preparing ready failed cancelled uncertain'.split())
STAGES = STATES | {'reading', 'generating_profiles', 'generating_config', 'publishing'}
_TYPE = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,63}\Z')
_HEX = re.compile(r'[0-9a-f]{64}\Z')


class PreparationError(RuntimeError):
    def __init__(self, code='invalid_request'):
        self.code = code if code in CODES else 'internal_error'
        super().__init__(self.code)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def canonical(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(',', ':')).encode('ascii')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def uuid_string(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError
    return value


def sha(value):
    if type(value) is not str or not _HEX.fullmatch(value):
        raise ValueError
    return value


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError
    return value


def text(value, maximum, byte_maximum=None):
    if (type(value) is not str or not value.strip() or len(value) > maximum
            or '\x00' in value or len(value.encode('utf-8')) > (byte_maximum or maximum * 4)):
        raise ValueError
    return value


def labels(value):
    if (type(value) is not list or len(value) > 50
            or any(type(v) is not str or not _TYPE.fullmatch(v) or v in {'Entity', 'Node'} for v in value)
            or len(set(value)) != len(value)):
        raise ValueError
    return value


def options(value):
    if type(value) is not dict or set(value) != {'types', 'max_agents', 'seed', 'platforms', 'max_rounds', 'simulation_requirement'}:
        raise ValueError
    if value['types'] is not None:
        labels(value['types'])
    integer(value['max_agents'], 1, 100)
    integer(value['seed'], 0, 4294967295)
    integer(value['max_rounds'], 1, 24)
    if type(value['platforms']) is not list or value['platforms'] not in [['twitter'], ['reddit'], ['twitter', 'reddit']]:
        raise ValueError
    text(value['simulation_requirement'], 8192, 32768)
    return value


def validate_payload(method, value):
    try:
        keys = {'schema_version', 'operation_id'} | ({'source_revision', 'options'} if method == 'plan' else {'plan_sha256'})
        if method not in {'plan', 'start', 'status'} or type(value) is not dict or set(value) != keys:
            raise ValueError
        integer(value['schema_version'], 1, 1)
        uuid_string(value['operation_id'])
        if method == 'plan':
            uuid_string(value['source_revision'])
            options(value['options'])
        else:
            sha(value['plan_sha256'])
        if len(encoded(value)) > REQUEST_BYTES:
            raise ValueError
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise PreparationError('invalid_request') from None
    return json.loads(encoded(value))


def file_names(platforms):
    return ['state.json', 'simulation_config.json', 'source_grounding.json'] + [
        f'{p}_profiles.' + ('csv' if p == 'twitter' else 'json') for p in platforms]


def validate_receipt(value, operation_id, platforms):
    if type(value) is not dict or set(value) != {'simulation_id', 'artifact_sha256', 'files'}:
        raise ValueError
    if value['simulation_id'] != 'sim_' + UUID(operation_id).hex:
        raise ValueError
    sha(value['artifact_sha256'])
    files = value['files']
    if type(files) is not list or len(files) != len(file_names(platforms)):
        raise ValueError
    for item, name in zip(files, file_names(platforms)):
        if type(item) is not dict or set(item) != {'name', 'sha256', 'size'} or item['name'] != name:
            raise ValueError
        sha(item['sha256'])
        integer(item['size'], 1, 2097152)
    if digest({'schema_version': 1, 'files': files}) != value['artifact_sha256']:
        raise ValueError


def validate_result(value, graph_id, scope, payload, method):
    try:
        keys = {'schema_version', 'display_graph_id', 'scope', 'project_revision', 'operation_id',
                'source', 'options', 'actors', 'projection_sha256', 'plan_sha256', 'state',
                'progress', 'error_code', 'authorization', 'receipt', 'graph_snapshot_atomic',
                'model_calls_started', 'simulation_executed'}
        if type(value) is not dict or set(value) != keys:
            raise ValueError
        integer(value['schema_version'], 1, 1)
        integer(value['project_revision'], 1, 9007199254740991)
        if value['display_graph_id'] != graph_id or canonical(value['scope']) != canonical(scope) or value['operation_id'] != payload['operation_id']:
            raise ValueError
        if scope.get('layer') != 'source' or scope.get('run_id') is not None or scope.get('branch_id') is not None:
            raise ValueError
        source = value['source']
        if type(source) is not dict or set(source) != {'source_revision', 'source_name', 'source_sha256'}:
            raise ValueError
        uuid_string(source['source_revision']); text(source['source_name'], 256); sha(source['source_sha256'])
        options(value['options'])
        if method == 'plan':
            if source['source_revision'] != payload['source_revision'] or value['options'] != payload['options']:
                raise ValueError
        elif value['plan_sha256'] != payload['plan_sha256']:
            raise ValueError
        actors = value['actors']
        if type(actors) is not list or not 1 <= len(actors) <= value['options']['max_agents']:
            raise ValueError
        ids = []
        for actor in actors:
            if type(actor) is not dict or set(actor) != {'source_entity_uuid', 'name', 'labels'}:
                raise ValueError
            ids.append(uuid_string(actor['source_entity_uuid']))
            text(actor['name'], 1024); labels(actor['labels'])
            if not actor['labels'] or (value['options']['types'] and not set(actor['labels']).intersection(value['options']['types'])):
                raise ValueError
        if ids != sorted(set(ids)):
            raise ValueError
        sha(value['projection_sha256']); sha(value['plan_sha256'])
        identity = {key: value[key] for key in ('schema_version', 'display_graph_id', 'scope',
                    'project_revision', 'operation_id', 'source', 'options', 'actors', 'projection_sha256')}
        if digest(identity) != value['plan_sha256']:
            raise ValueError
        state, progress = value['state'], value['progress']
        if (type(state) is not str or state not in STATES or type(progress) is not dict
                or set(progress) != {'stage', 'completed', 'total'} or progress['stage'] not in STAGES):
            raise ValueError
        integer(progress['completed'], 0, 100); integer(progress['total'], 100, 100)
        if state != 'preparing' and progress['stage'] != state:
            raise ValueError
        error = value['error_code']
        if error is not None and (type(error) is not str or error not in CODES):
            raise ValueError
        auth = value['authorization']
        if type(auth) is not dict or set(auth) != {'model_calls_enabled', 'ceiling_microusd'} or type(auth['model_calls_enabled']) is not bool:
            raise ValueError
        ceiling = auth['ceiling_microusd']
        if ceiling is not None and (type(ceiling) is not str or not re.fullmatch(r'[1-9][0-9]{0,18}', ceiling) or int(ceiling) > 2**63-1):
            raise ValueError
        if auth['model_calls_enabled'] and ceiling is None:
            raise ValueError
        if value['graph_snapshot_atomic'] is not False or value['simulation_executed'] is not False or type(value['model_calls_started']) is not bool:
            raise ValueError
        if state == 'ready':
            validate_receipt(value['receipt'], value['operation_id'], value['options']['platforms'])
            if error is not None or progress['completed'] != 100 or not value['model_calls_started']:
                raise ValueError
        elif value['receipt'] is not None:
            raise ValueError
        if state == 'planned' and (value['model_calls_started'] or progress['completed'] != 0 or error is not None):
            raise ValueError
        if state == 'queued' and (value['model_calls_started'] or progress['completed'] != 0 or error is not None):
            raise ValueError
        if state == 'preparing' and (progress['stage'] not in {'reading', 'generating_profiles', 'generating_config', 'publishing'} or error is not None):
            raise ValueError
        if state in {'failed', 'cancelled', 'uncertain'} and error is None:
            raise ValueError
        if len(encoded(value)) > RESULT_BYTES:
            raise PreparationError('result_too_large')
    except PreparationError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise PreparationError('invalid_reply') from None
    return json.loads(encoded(value))

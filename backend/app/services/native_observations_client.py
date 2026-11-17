"""SDK-free, finite completed-observations wire contract."""
import hashlib
import json
from .preparation_client import encoded, digest, integer, sha, uuid_string
from .native_launch_client import validate_result as validate_launch, NativeLaunchError

REQUEST_BYTES, RESULT_BYTES, ENVELOPE_OVERHEAD = 4096, 262144, 128
LOG_BYTES, LINE_BYTES, RECORDS, DEPTH = 8388608, 8192, 10000, 8
OUTPUT_BYTES, AGGREGATE_BYTES = 67108864, 268435456
CODES = frozenset(('invalid_request not_found unauthorized origin_denied conflict tombstoned busy '
                  'result_too_large observations_unavailable evidence_invalid invalid_reply internal_error').split())
COUNT_KEYS = {'event_records', 'action_records', 'successful_action_records', 'failed_action_records'}


class NativeObservationsError(RuntimeError):
    def __init__(self, code='invalid_request'):
        self.code = code if code in CODES else 'internal_error'
        super().__init__(self.code)


def validate_payload(value):
    try:
        if type(value) is not dict or set(value) != {'schema_version', 'launch_id', 'launch_sha256', 'platform', 'offset', 'limit'}:
            raise ValueError
        version = integer(value['schema_version'], 1, 2)
        uuid_string(value['launch_id']); sha(value['launch_sha256'])
        if (type(value['platform']) is not str or value['platform'] not in {'twitter', 'reddit'}
                or (version == 2 and value['platform'] != 'twitter')):
            raise ValueError
        integer(value['offset'], 0, RECORDS); integer(value['limit'], 1, 20)
        if len(encoded(value)) > REQUEST_BYTES:
            raise ValueError
        return json.loads(encoded(value))
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise NativeObservationsError('invalid_request') from None


def launch_reference(payload):
    return {k: payload[k] for k in ('schema_version', 'launch_id', 'launch_sha256')}


def action_log_name(platform, *, restored=False):
    return 'actions.jsonl' if restored else platform + '/actions.jsonl'


def output_names(platforms, *, restored=False):
    if type(platforms) is not list or platforms not in [['twitter'], ['reddit'], ['twitter', 'reddit']]:
        raise ValueError
    if restored:
        if platforms != ['twitter']:
            raise ValueError
        # Keep the exact native restored receipt manifest order.
        return ['native.sqlite3', action_log_name('twitter', restored=True)]
    return [name for platform in platforms for name in (platform + '_simulation.db', action_log_name(platform))]


def validate_result(value, graph_id, scope, payload, principal):
    """Validate injected replies too; never allow facade injection to bypass wire limits."""
    try:
        if type(value) is not dict or set(value) != {'schema_version', 'launch', 'manifest', 'platform', 'offset', 'limit',
                'total_records', 'next_offset', 'counts', 'records'}:
            raise ValueError
        if len(encoded(value)) > RESULT_BYTES:
            raise NativeObservationsError('result_too_large')
        version = integer(value['schema_version'], 1, 2)
        if version != integer(payload['schema_version'], 1, 2):
            raise ValueError
        launch = validate_launch(value['launch'], graph_id, scope, launch_reference(payload), 'status')
        if launch['state'] != 'completed' or launch['receipt'] is None or launch['request']['principal'] != principal:
            raise ValueError
        platforms = launch['request']['platforms']
        restored = version == 2
        if (payload['platform'] not in platforms
                or (restored and (launch['checkpoint']['mode'] != 'continue' or launch['cancel_requested']))):
            raise ValueError
        manifest = value['manifest']
        if type(manifest) is not dict or set(manifest) != {'schema_version', 'files'}:
            raise ValueError
        integer(manifest['schema_version'], 1, 1)
        names = output_names(platforms, restored=restored)
        logs = {action_log_name(platform, restored=restored) for platform in platforms}
        if type(manifest['files']) is not list or len(manifest['files']) != len(names):
            raise ValueError
        for file, name in zip(manifest['files'], names):
            if type(file) is not dict or set(file) != {'name', 'sha256', 'size'} or file['name'] != name:
                raise ValueError
            sha(file['sha256'])
            integer(file['size'], 1 if restored else 0, LOG_BYTES if name in logs else OUTPUT_BYTES)
        if sum(file['size'] for file in manifest['files']) > AGGREGATE_BYTES or digest(manifest) != launch['receipt']['evidence_sha256']:
            raise ValueError
        for key in ('platform', 'offset', 'limit'):
            if type(value[key]) is not type(payload[key]) or value[key] != payload[key]:
                raise ValueError
        total = integer(value['total_records'], 0, RECORDS)
        offset, limit = payload['offset'], payload['limit']
        if offset > total or type(value['records']) is not list or len(value['records']) != min(limit, total - offset):
            raise ValueError
        next_offset = offset + len(value['records'])
        if next_offset < total:
            integer(value['next_offset'], 0, RECORDS)
            if value['next_offset'] != next_offset:
                raise ValueError
        elif value['next_offset'] is not None:
            raise ValueError
        counts = value['counts']
        if type(counts) is not dict or set(counts) != COUNT_KEYS:
            raise ValueError
        for count in counts.values():
            integer(count, 0, total)
        if counts['successful_action_records'] + counts['failed_action_records'] > counts['action_records']:
            raise ValueError
        from .native_observation_reader import parse_record
        page_counts = dict.fromkeys(COUNT_KEYS, 0)
        for index, record in enumerate(value['records'], offset):
            if type(record) is not dict or set(record) != {'index', 'record_sha256', 'raw_json'}:
                raise ValueError
            integer(record['index'], offset, RECORDS)
            if (record['index'] != index or type(record['raw_json']) is not str
                    or '\n' in record['raw_json'] or '\r' in record['raw_json']):
                raise ValueError
            raw = record['raw_json'].encode('utf-8')
            if len(raw) > LINE_BYTES or sha(record['record_sha256']) != hashlib.sha256(raw).hexdigest():
                raise ValueError
            _, observed = parse_record(raw)
            for key in COUNT_KEYS:
                page_counts[key] += observed[key]
        selected_log = action_log_name(payload['platform'], restored=restored)
        selected_file = next(file for file in manifest['files'] if file['name'] == selected_log)
        minimum = (3 * total - 1 if total else 0) + sum(len(record['raw_json'].encode('utf-8')) - 2 for record in value['records'])
        if selected_file['size'] < minimum or total == 0 and selected_file['size'] != 0:
            raise ValueError
        if any(page_counts[k] > counts[k] for k in COUNT_KEYS):
            raise ValueError
        unseen_records = total - len(value['records'])
        if any(counts[k] - page_counts[k] > unseen_records for k in COUNT_KEYS):
            raise ValueError
        if offset == 0 and len(value['records']) == total and page_counts != counts:
            raise ValueError
        return json.loads(encoded(value))
    except NativeObservationsError as error:
        if error.code == 'result_too_large':
            raise
        raise NativeObservationsError('invalid_reply') from None
    except (NativeLaunchError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise NativeObservationsError('invalid_reply') from None

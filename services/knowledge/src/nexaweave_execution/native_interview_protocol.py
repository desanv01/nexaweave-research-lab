"""Private, finite framing for one retained native Twitter interview."""
from __future__ import annotations

import json
import math
import re
from uuid import UUID

FRAME_BYTES = 4096
OWNER_WINDOW_SECONDS = 20
CALL_SECONDS = 8
MAX_ANSWER_BYTES = 4096
COMMAND_KEYS = frozenset({'schema_version', 'launch_id', 'launch_sha256', 'command_id',
                         'platform', 'agent_id', 'prompt'})


class InterviewUnavailable(RuntimeError):
    code = 'native_launch_unavailable'

    def __init__(self):
        super().__init__(self.code)


def uuid_text(value):
    if type(value) is not str or len(value) != 36 or str(UUID(value)) != value:
        raise ValueError
    return value


def private_text(value, chars, size):
    if (type(value) is not str or not 1 <= len(value) <= chars
            or not value.strip() or '\x00' in value or len(value.encode('utf-8')) > size):
        raise ValueError
    return value


def command(value):
    if (type(value) is not dict or set(value) != COMMAND_KEYS
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['platform'] != 'twitter' or type(value['agent_id']) is not int
            or not 0 <= value['agent_id'] < 500):
        raise ValueError
    uuid_text(value['launch_id'])
    uuid_text(value['command_id'])
    if type(value['launch_sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', value['launch_sha256']):
        raise ValueError
    prompt = private_text(value['prompt'], 400, 1600)
    # Leave room for the exact command/owner identity and finite timestamp.
    if len(json.dumps(prompt, ensure_ascii=True)) > FRAME_BYTES - 640:
        raise ValueError
    return dict(value)


def timestamp(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError
    return value


def base_fields(request, attempt, instance):
    return {'run_id': str(request.run_id), 'attempt_id': str(attempt),
            'instance_id': str(instance), 'request_fingerprint': request.fingerprint}


def validate_frame(value, kind, base, fields):
    if (type(value) is not dict or set(value) != set(base) | {'kind'} | set(fields)
            or value['kind'] != kind or any(value[key] != item for key, item in base.items())):
        raise ValueError
    return value


def frame(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                     separators=(',', ':')).encode('ascii')
    if not 0 < len(raw) <= FRAME_BYTES:
        raise ValueError
    return raw


def unframe(raw):
    if not 0 < len(raw) <= FRAME_BYTES:
        raise ValueError

    def unique(pairs):
        items = {}
        for key, value in pairs:
            if key in items:
                raise ValueError
            items[key] = value
        return items

    return json.loads(raw.decode('ascii'), object_pairs_hook=unique)

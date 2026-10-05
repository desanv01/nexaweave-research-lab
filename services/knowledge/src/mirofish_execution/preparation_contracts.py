"""Identifier-only preparation dispatch and distinct budget proof; no SDK imports."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from uuid import UUID


class PreparationAuthorityError(RuntimeError):
    def __init__(self, code='conflict'):
        self.code = code
        super().__init__(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def identifier(value):
    if type(value) is UUID:
        return value
    if type(value) is str and str(UUID(value)) == value:
        return UUID(value)
    raise ValueError


def sha(value):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError
    return value


@dataclass(frozen=True)
class PreparationDispatch:
    operation_id: UUID
    attempt_id: UUID
    plan_sha256: str
    schema_version: int = 1

    @classmethod
    def from_wire(cls, value):
        try:
            if type(value) is cls:
                value = value.to_wire()
            if type(value) is not dict or set(value) != {'schema_version', 'operation_id', 'attempt_id', 'plan_sha256'} or type(value['schema_version']) is not int or value['schema_version'] != 1:
                raise ValueError
            return cls(identifier(value['operation_id']), identifier(value['attempt_id']), sha(value['plan_sha256']))
        except (ValueError, TypeError, KeyError):
            raise PreparationAuthorityError('invalid_request') from None

    def to_wire(self):
        return {'schema_version': 1, 'operation_id': str(self.operation_id),
                'attempt_id': str(self.attempt_id), 'plan_sha256': self.plan_sha256}

    @property
    def workflow_id(self):
        return 'mf-preparation-v1-' + self.operation_id.hex


@dataclass(frozen=True)
class PreparedBudgetReceipt:
    operation_id: UUID
    attempt_id: UUID
    fingerprint: str
    artifact_sha256: str
    kind: str = 'prepared_budget_v1'

    @classmethod
    def from_wire(cls, value):
        try:
            if type(value) is cls:
                value = value.json_value()
            if type(value) is not dict or set(value) != {'kind', 'operation_id', 'attempt_id', 'fingerprint', 'artifact_sha256'} or value['kind'] != 'prepared_budget_v1':
                raise ValueError
            return cls(identifier(value['operation_id']), identifier(value['attempt_id']), sha(value['fingerprint']), sha(value['artifact_sha256']))
        except (ValueError, TypeError, KeyError):
            raise PreparationAuthorityError('preparation_uncertain') from None

    def json_value(self):
        value = asdict(self)
        value['operation_id'], value['attempt_id'] = str(self.operation_id), str(self.attempt_id)
        return value

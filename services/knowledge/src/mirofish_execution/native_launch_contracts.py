"""Frozen connected launch identity and distinct native budget proof."""
from dataclasses import dataclass
import json
from uuid import uuid5, NAMESPACE_URL
from .preparation_contracts import canonical, digest, identifier, sha
from .native_run_contracts import NativeRunRequest, NativeRunReceipt, InvalidNativeRun

IDENTITY = ('schema_version', 'display_graph_id', 'scope', 'preparation', 'request',
            'limits', 'ceiling_microusd', 'model_label')


class LaunchAuthorityError(RuntimeError):
    def __init__(self, code='conflict'):
        self.code = code
        super().__init__(code)


def native_budget_fingerprint(launch_sha256):
    return digest({'kind': 'native_run_budget_v1', 'launch_sha256': sha(launch_sha256)})


def native_budget_episode(scope_group_id, run_id):
    # Existing scope IDs/catalog and the ingestion/preparation episode mapping
    # are unchanged. Only native reservations get this third-purpose UUID.
    return uuid5(NAMESPACE_URL,'mirofish:native-budget:v1:'+scope_group_id+':'+str(identifier(run_id)))


def validate_declaration(value):
    try:
        if type(value) is not dict or set(value)!={'schema_version','launch_id','preparation'}:
            raise ValueError
        if type(value['schema_version']) is not int or value['schema_version']!=1 or len(canonical(value))>4096:
            raise ValueError
        identifier(value['launch_id'])
        prep=value['preparation']
        if type(prep) is not dict or set(prep)!={'operation_id','plan_sha256'}:
            raise ValueError
        identifier(prep['operation_id']);sha(prep['plan_sha256'])
        return json.loads(canonical(value))
    except (ValueError,TypeError,KeyError,UnicodeError):
        raise LaunchAuthorityError('invalid_request') from None


def validate_identity(value):
    from mirofish_knowledge.contracts import KnowledgeScope
    try:
        if type(value) is not dict or set(value) != set(IDENTITY) or len(canonical(value)) > 65536:
            raise ValueError
        value = json.loads(canonical(value))
        if type(value['schema_version']) is not int or value['schema_version'] != 1:
            raise ValueError
        scope = KnowledgeScope.model_validate_json(json.dumps(value['scope']))
        if (type(value['scope']) is not dict or set(value['scope'])!={'schema_version','workspace_id','project_id','graph_id','run_id','branch_id','layer'}
                or type(value['scope']['schema_version']) is not int or value['scope']['schema_version']!=1
                or scope.model_dump(mode='json') != value['scope'] or scope.layer.value != 'source' or scope.run_id is not None or scope.branch_id is not None):
            raise ValueError
        label = value['model_label']
        graph = value['display_graph_id']
        for item, maximum in ((label, 128), (graph, 256)):
            if type(item) is not str or not item.strip() or len(item) > maximum or '\x00' in item:
                raise ValueError
            item.encode('utf-8')
        prep = value['preparation']
        if type(prep) is not dict or set(prep) != {'operation_id', 'plan_sha256', 'simulation_id', 'artifact_sha256'}:
            raise ValueError
        operation = identifier(prep['operation_id']); sha(prep['plan_sha256']); sha(prep['artifact_sha256'])
        req = NativeRunRequest.from_wire(value['request'])
        if (value['request'] != req.to_wire() or req.project_id != scope.project_id
                or req.simulation_id != 'sim_' + operation.hex or prep['simulation_id'] != req.simulation_id
                or prep['artifact_sha256'] != req.artifact_sha256 or not 0 <= req.seed <= 4294967295):
            raise ValueError
        limits = value['limits']
        bounds = {'max_calls': 10000, 'max_input_bytes': 2097152, 'max_output_tokens': 4096, 'max_run_seconds': 600}
        if type(limits) is not dict or set(limits) != set(bounds):
            raise ValueError
        if any(type(limits[k]) is not int or not 1 <= limits[k] <= hi for k, hi in bounds.items()):
            raise ValueError
        ceiling = value['ceiling_microusd']
        if ceiling is not None:
            if type(ceiling) is not str or not ceiling.isascii() or not ceiling.isdecimal() or str(int(ceiling)) != ceiling or not 1 <= int(ceiling) <= 2**63-1:
                raise ValueError
        return value
    except (ValueError, TypeError, KeyError, UnicodeError, InvalidNativeRun):
        raise LaunchAuthorityError('invalid_request') from None


@dataclass(frozen=True)
class NativeBudgetReceipt:
    operation_id: object
    attempt_id: object
    fingerprint: str
    launch_sha256: str
    native_receipt: NativeRunReceipt
    kind: str = 'native_run_budget_v1'

    @classmethod
    def from_wire(cls, value):
        try:
            if type(value) is cls:
                value = value.json_value()
            if type(value) is not dict or set(value) != {'kind', 'operation_id', 'attempt_id', 'fingerprint', 'launch_sha256', 'native_receipt'} or value['kind'] != 'native_run_budget_v1':
                raise ValueError
            operation, attempt = identifier(value['operation_id']), identifier(value['attempt_id'])
            launch = sha(value['launch_sha256'])
            receipt = NativeRunReceipt.from_wire(value['native_receipt'])
            if receipt.run_id != operation or value['fingerprint'] != native_budget_fingerprint(launch):
                raise ValueError
            return cls(operation, attempt, value['fingerprint'], launch, receipt)
        except (ValueError, TypeError, KeyError, InvalidNativeRun):
            raise LaunchAuthorityError('native_launch_uncertain') from None

    def json_value(self):
        return {'kind': self.kind, 'operation_id': str(self.operation_id), 'attempt_id': str(self.attempt_id),
                'fingerprint': self.fingerprint, 'launch_sha256': self.launch_sha256,
                'native_receipt': self.native_receipt.to_wire()}

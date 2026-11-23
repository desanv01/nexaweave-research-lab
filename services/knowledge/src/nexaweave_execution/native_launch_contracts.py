"""Frozen connected launch identity and distinct native budget proof."""
from dataclasses import dataclass
import hashlib
import json
import math
import struct
from uuid import uuid5, NAMESPACE_URL
from .preparation_contracts import canonical, digest, identifier, sha
from .native_run_contracts import NativeRunRequest, NativeRunReceipt, InvalidNativeRun, SwapActiveActorV1

IDENTITY = ('schema_version', 'display_graph_id', 'scope', 'preparation', 'request',
            'limits', 'ceiling_microusd', 'model_label')
_DECLARATION_KEYS = {'schema_version', 'launch_id', 'preparation'}
_ARCHIVE_LIMIT = 7200
_RUNNER_BYTES = 2564
_RUNNER_HEADER = struct.Struct('>4sB5I32s')
_RUNNER_TRAILER = struct.Struct('>HBd')
_RUNNER_KEYS = ('completed_round', 'horizon', 'minutes_per_round', 'simulated_minutes', 'total_actions')
_MANIFEST_KEYS = {'anchors', 'round', 'prior_cursor', 'prior_post_id', 'cursor', 'database_sha256',
    'database_bytes', 'log_sha256', 'log_bytes', 'post_count', 'manifest', 'full_checkpoint',
    'continuation_supported', 'memory', 'topology', 'runner_state', 'engine_clock',
    'recommendation_state', 'version', 'platform', 'source_journal_mode'}


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


def _version(value, keys):
    if type(value) is not dict:
        raise ValueError
    version = value.get('schema_version')
    if type(version) is not int or version not in (1, 2, 3, 4, 5):
        raise ValueError
    expected = keys if version == 1 else keys | {'checkpoint'}
    if set(value) != expected:
        raise ValueError
    return version


def _checkpoint(value, run_id, version):
    if type(value) is not dict or type(value.get('mode')) is not str:
        raise ValueError
    if version in (2, 4, 5) and value['mode'] == 'capture' and set(value) == {'mode'}:
        return None
    keys = {'mode', 'source_run_id', 'checkpoint_sha256'}
    mode = 'continue'
    if version == 3:
        keys.add('intervention')
        mode = 'fork'
    if value['mode'] != mode or set(value) != keys:
        raise ValueError
    if type(value['source_run_id']) is not str:
        raise ValueError
    source = identifier(value['source_run_id'])
    if source == run_id:
        raise ValueError
    intervention = SwapActiveActorV1.from_wire(value['intervention']) if version == 3 else None
    return source, sha(value['checkpoint_sha256']), intervention


def validate_declaration(value):
    try:
        version = _version(value, _DECLARATION_KEYS)
        raw = canonical(value)
        if len(raw) > 4096:
            raise ValueError
        launch_id = identifier(value['launch_id'])
        prep=value['preparation']
        if type(prep) is not dict or set(prep)!={'operation_id','plan_sha256'}:
            raise ValueError
        identifier(prep['operation_id']);sha(prep['plan_sha256'])
        if version >= 2:
            _checkpoint(value['checkpoint'], launch_id, version)
        return json.loads(raw)
    except (ValueError,TypeError,KeyError,UnicodeError,InvalidNativeRun):
        raise LaunchAuthorityError('invalid_request') from None


def validate_identity(value):
    from nexaweave_knowledge.contracts import KnowledgeScope
    try:
        version = _version(value, set(IDENTITY))
        raw = canonical(value)
        if len(raw) > 65536:
            raise ValueError
        value = json.loads(raw)
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
        checkpoint = _checkpoint(value['checkpoint'], req.run_id, version) if version >= 2 else None
        if version == 5:
            profile_matches = req.platforms == ('twitter', 'reddit') and req.max_rounds >= 2
        else:
            platform = 'reddit' if version == 4 else 'twitter'
            profile_matches = req.platforms == (platform,) and req.max_rounds == 1
        if (req.schema_version != (1 if checkpoint is None else version)
                or (checkpoint is not None and checkpoint !=
                    (req.source_run_id, req.checkpoint_sha256, req.intervention))
                or (version >= 2 and not profile_matches)
                or value['request'] != req.to_wire()
                or req.project_id != scope.project_id
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
class NativeForkArchive:
    """Private byte witnesses authenticated by the terminal capture receipt."""

    lineage: bytes
    manifest: bytes
    runner_state: bytes


def _archive_object(raw, expected_digest):
    if (type(raw) is not bytes or not 0 < len(raw) <= _ARCHIVE_LIMIT
            or hashlib.sha256(raw).hexdigest() != expected_digest):
        raise ValueError
    value = json.loads(raw.decode('ascii'))
    if type(value) is not dict or canonical(value) != raw:
        raise ValueError
    return value


def verify_fork_archive(archive, request, display_graph_id, producer_pid):
    """Prove the intervention names the exact captured scheduler bytes."""
    try:
        if type(archive) is not NativeForkArchive:
            raise ValueError
        intervention = request.intervention
        lineage = _archive_object(archive.lineage, request.checkpoint_sha256)
        manifest = _archive_object(archive.manifest, intervention.manifest_sha256)
        runner = archive.runner_state
        if (type(runner) is not bytes or len(runner) != _RUNNER_BYTES
                or hashlib.sha256(runner).hexdigest() != intervention.runner_state_sha256):
            raise ValueError
        magic, version, completed, horizon, interval, offset, actions, config_digest = _RUNNER_HEADER.unpack_from(runner)
        index, cache_tag, cache = _RUNNER_TRAILER.unpack_from(runner, _RUNNER_BYTES - _RUNNER_TRAILER.size)
        anchors = {'graph_id': display_graph_id, 'simulation_id': request.simulation_id,
                   'run_id': str(request.source_run_id), 'project_id': str(request.project_id),
                   'project_revision': request.project_revision,
                   'branch_id': str(uuid5(NAMESPACE_URL, 'mirofish:native-checkpoint:v1:' + str(request.source_run_id)))}
        owners = lineage['owners']
        prepared = lineage['prepared']
        topology = manifest['topology']
        if (set(lineage) != {'version', 'anchors', 'producer_pid', 'manifest_sha256', 'prepared', 'owners', 'runner'}
                or type(lineage['version']) is not int or lineage['version'] != 1
                or type(lineage['producer_pid']) is not int or lineage['producer_pid'] != producer_pid
                or lineage['manifest_sha256'] != intervention.manifest_sha256
                or canonical(lineage['anchors']) != canonical(anchors)
                or canonical(manifest['anchors']) != canonical(anchors)
                or type(prepared) is not dict
                or set(prepared) != {'artifact_sha256', 'config_sha256', 'source_grounding_sha256', 'profile_sha256'}
                or prepared['artifact_sha256'] != request.artifact_sha256
                or prepared['config_sha256'] != config_digest.hex()
                or type(owners) is not list or len(owners) != 2
                or any(type(owner) is not dict or set(owner) != {'platform_id', 'name', 'camel_id', 'token_limit'}
                       or type(owner['platform_id']) is not int or owner['platform_id'] != actor_id
                       for actor_id, owner in enumerate(owners))
                or set(manifest) != _MANIFEST_KEYS or manifest['manifest'] != 'manifest.json'
                or manifest['platform'] != 'twitter' or type(manifest['version']) is not int or manifest['version'] != 1
                or type(manifest['round']) is not int or manifest['round'] != 1
                or type(manifest['post_count']) is not int or manifest['post_count'] != 1
                or manifest['full_checkpoint'] is not False or manifest['continuation_supported'] is not False
                or type(manifest['memory']['agent_count']) is not int or manifest['memory']['agent_count'] != 2
                or topology['directed'] is not True or type(topology['vertex_count']) is not int
                or topology['vertex_count'] != 2 or type(topology['edge_count']) is not int or topology['edge_count'] != 0
                or canonical(manifest['runner_state']) != canonical({'version': 1, 'file': 'native-runner.bin',
                       'bytes': _RUNNER_BYTES, 'sha256': intervention.runner_state_sha256})
                or magic != b'NWR1' or version != 3 or completed != 1 or offset != 0
                or not 2 <= horizon <= 10080 or not 1 <= interval <= 1440 or horizon * interval > 10080
                or index > 624 or cache_tag not in (0, 1)
                or (not cache_tag and not runner.endswith(b'\x00' * 8)) or not math.isfinite(cache)
                or not actions or canonical(lineage['runner']) != canonical(dict(zip(
                       _RUNNER_KEYS, (completed, horizon, interval, offset, actions), strict=True)))):
            raise ValueError
        sha(prepared['source_grounding_sha256']); sha(prepared['profile_sha256'])
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
        raise LaunchAuthorityError('conflict') from None


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

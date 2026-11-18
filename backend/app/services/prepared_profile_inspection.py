"""Receipt-bound inspection of a single actor in a trusted READY preparation."""
import csv
import hashlib
import io
import json
import time
from types import SimpleNamespace
from uuid import UUID

if __package__:
    from .knowledge_transport import _json_object
    from .preparation_client import (PreparationError, RESULT_BYTES, encoded, file_names,
                                     sha, uuid_string, validate_payload, validate_receipt)
else:
    from knowledge_transport import _json_object
    from preparation_client import (PreparationError, RESULT_BYTES, encoded, file_names,
                                    sha, uuid_string, validate_payload, validate_receipt)

_SELECTOR = 'source_entity_uuid'
_TWITTER_FIELDS = ('user_id', 'name', 'username', 'user_char', 'description')
_MAX_ARTIFACT = 2097152
_PLATFORM_ORDERS = (['twitter'], ['reddit'], ['twitter', 'reddit'])
_SUPPORT_FIELDS = {_SELECTOR, 'labels', 'summary', 'attributes', 'episode_ids', 'evidence_ids', 'facts'}
PROFILE_ERRORS = frozenset(('invalid_request', 'not_found', 'unauthorized', 'conflict',
                            'tombstoned', 'result_too_large', 'preparation_unavailable', 'timeout'))


def profile_request(raw, scope):
    request = _json_object(raw)
    if (set(request) != {'version', 'request_id', 'method', 'scope', 'payload'}
            or type(request['version']) is not int or request['version'] != 1
            or request['method'] != 'profiles' or request['scope'] != scope
            or type(request['scope']['schema_version']) is not int):
        raise PreparationError('invalid_request')
    uuid_string(request['request_id'])
    request['payload'] = validate_profile_selector(request['payload'])
    return request


class PreparationReadAuthority:
    """Common read checks for the durable host and the isolated profile reader."""

    _missing_binding_unavailable = False

    def _owned(self, source_revision, *, expected_revision=None):
        from nexaweave_knowledge.operations import Tombstoned, NotFound, StorageError
        from nexaweave_storage.store import NotFound as AppNotFound, StorageError as AppError
        try:
            try:
                binding = self.bindings.resolve(self.principal, self.display_graph_id)
            except (Tombstoned, NotFound):
                if self._missing_binding_unavailable:
                    raise PreparationError('preparation_unavailable') from None
                raise
            scope = binding.scope
            if scope.model_dump(mode='json') != self.scope_dto or binding.principal != self.principal or binding.display_graph_id != self.display_graph_id:
                raise PreparationError('conflict')
            project = self.projects.get(self.principal, scope.project_id)
            if project.workspace_id != scope.workspace_id or project.principal != self.principal or project.project_id != scope.project_id:
                raise PreparationError('unauthorized')
            if expected_revision is not None and project.revision != expected_revision:
                raise PreparationError('conflict')
            retained = self.sources.get_source(self.principal, scope.project_id, UUID(source_revision))
            if retained.project_id != scope.project_id or str(retained.source_revision) != source_revision or not retained.passages or retained.codepoint_length > 32768:
                raise PreparationError('invalid_request')
            return scope, project, retained
        except Tombstoned:
            raise PreparationError('tombstoned') from None
        except (NotFound, AppNotFound):
            raise PreparationError('not_found') from None
        except (StorageError, AppError):
            raise PreparationError('preparation_unavailable') from None

    @staticmethod
    def _source_check(deadline=None):
        until = time.monotonic() + 15
        if deadline is not None:
            until = min(until, deadline)
        def check():
            if time.monotonic() >= until:
                raise PreparationError('timeout')
        return check

    @staticmethod
    def _source_snapshot(retained, check):
        check()
        if len(retained.text) > 32768 or not 1 <= len(retained.passages) <= 100:
            raise PreparationError('result_too_large')
        passages = []
        for p in retained.passages:
            check()
            passages.append(dict(evidence_id=str(p.evidence_id), start=p.start, end=p.end,
                page=p.page, excerpt=p.excerpt, excerpt_sha256=p.excerpt_sha256))
        snapshot = dict(source_revision=str(retained.source_revision), source_name=retained.name,
            source_sha256=retained.text_sha256, text=retained.text, passages=passages)
        check()
        return json.loads(encoded(snapshot))

    def _owned_row(self, row, *, expected_revision=None, deadline=None, check=None):
        """Reauthorize all frozen documents; status may use current project revision."""
        from nexaweave_execution.preparation_contracts import validate_preparation_sources

        if row.frozen['public']['schema_version'] == 1:
            return self._owned(row.frozen['public']['source']['source_revision'], expected_revision=expected_revision)
        check = check or self._source_check(deadline)
        try:
            validate_preparation_sources(row.frozen, check)
            initial_scope = initial_project = anchor = None
            for source in row.frozen['sources']:
                check()
                scope, project, retained = self._owned(source['source_revision'], expected_revision=expected_revision)
                check()
                if (scope.model_dump(mode='json') != row.frozen['public']['scope']
                        or str(project.project_id) != row.frozen['public']['scope']['project_id']
                        or self._source_snapshot(retained, check) != source):
                    raise PreparationError('conflict')
                if initial_scope is None:
                    initial_scope, initial_project, anchor = scope, project, retained
                    current_revision = project.revision
                elif scope != initial_scope or project.revision != current_revision or project != initial_project:
                    raise PreparationError('conflict')
            check()
            return initial_scope, initial_project, anchor
        except (ValueError, TypeError, KeyError, UnicodeError, AttributeError):
            raise PreparationError('conflict') from None

    def _row(self, payload):
        row = self.store.get(self.principal, payload['operation_id'], payload.get('plan_sha256'))
        if row.display_graph_id != self.display_graph_id or row.frozen['public']['scope'] != self.scope_dto:
            raise PreparationError('not_found')
        return row


def validate_profile_selector(value):
    if type(value) is not dict or _SELECTOR not in value:
        raise PreparationError('invalid_request')
    reference = validate_payload('status', {k: v for k, v in value.items() if k != _SELECTOR})
    try:
        return {**reference, _SELECTOR: uuid_string(value[_SELECTOR])}
    except ValueError:
        raise PreparationError('invalid_request') from None


def validate_profile_result(value, graph_id, scope, payload):
    try:
        keys = set(payload) | {'display_graph_id', 'scope', 'project_revision', 'artifact_sha256',
                               'actor', 'generated_profiles', 'frozen_support'}
        if (type(value) is not dict or set(value) != keys or value['display_graph_id'] != graph_id
                or value['scope'] != scope or any(type(value[k]) is not type(v) or value[k] != v
                                                   for k, v in payload.items())
                or type(value['project_revision']) is not int or value['project_revision'] < 1):
            raise ValueError
        sha(value['artifact_sha256'])
        actor, profiles, support = (value[key] for key in ('actor', 'generated_profiles', 'frozen_support'))
        if (type(actor) is not dict or set(actor) != {'ordinal', 'name', 'labels'}
                or type(actor['ordinal']) is not int or actor['ordinal'] < 0
                or type(actor['name']) is not str or type(actor['labels']) is not list
                or type(support) is not dict or set(support) != _SUPPORT_FIELDS
                or support[_SELECTOR] != payload[_SELECTOR] or type(profiles) is not list):
            raise ValueError
        for profile in profiles:
            if (type(profile) is not dict or set(profile) != {'platform', 'handle', 'description', 'persona'}
                    or any(type(field) is not str or not field.strip() for field in profile.values())):
                raise ValueError
        if [profile['platform'] for profile in profiles] not in _PLATFORM_ORDERS:
            raise ValueError
        if len(encoded(value)) > RESULT_BYTES:
            raise PreparationError('result_too_large')
    except (KeyError, ValueError, TypeError, RecursionError):
        raise PreparationError('invalid_reply') from None
    return value


def _reauthorize(host, row, check):
    check()
    scope, project, retained = host._owned_row(row, expected_revision=row.project_revision, check=check)
    source = row.frozen['public']['source']
    if (row.principal != host.principal or row.project_id != scope.project_id
            or row.project_revision != project.revision
            or row.project_revision != row.frozen['public']['project_revision']
            or retained.text_sha256 != source['source_sha256'] or retained.name != source['source_name']):
        raise PreparationError('conflict')
    check()


def _selected_support(artifact, frozen, actor):
    uid = actor[_SELECTOR]
    if set(artifact) != {item[_SELECTOR] for item in frozen['public']['actors']}:
        raise ValueError
    graph = frozen['graph']
    node, = (node for node in graph['nodes'] if node['uuid'] == uid)
    if node['name'] != actor['name']:
        raise ValueError
    expected = {
        _SELECTOR: uid, 'labels': list(node['labels']), 'summary': node['summary'],
        'attributes': node['attributes'], 'episode_ids': list(node['episodes']),
        'evidence_ids': list(node['evidence_ids']),
        'facts': [{'edge_uuid': edge['uuid'], 'fact': edge['fact'],
                   'episode_ids': list(edge['episodes']), 'evidence_ids': list(edge['evidence_ids'])}
                  for edge in graph['edges']
                  if uid in (edge['source_node_uuid'], edge['target_node_uuid'])],
    }
    if encoded(artifact[uid]) != encoded(expected):
        raise ValueError
    return expected


def _profile_rows(raw, platform, actors):
    if platform == 'twitter':
        reader = csv.reader(io.StringIO(raw.decode('utf-8'), newline=''), strict=True)
        if next(reader, None) != list(_TWITTER_FIELDS):
            raise ValueError
        rows = []
        for fields in reader:
            if len(fields) != len(_TWITTER_FIELDS) or len(rows) >= len(actors):
                raise ValueError
            rows.append(dict(zip(_TWITTER_FIELDS, fields)))
    else:
        # Reuse the strict JSON parser by wrapping the top-level list.
        rows = _json_object(b'{"profiles":' + raw + b'}')['profiles']
    if type(rows) is not list or len(rows) != len(actors):
        raise ValueError
    handles = set()
    for ordinal, (row, actor) in enumerate(zip(rows, actors)):
        expected_id = str(ordinal) if platform == 'twitter' else ordinal
        if (type(row) is not dict or type(row.get('user_id')) is not type(expected_id)
                or row['user_id'] != expected_id or row.get('name') != actor['name']):
            raise ValueError
        handle = row.get('username')
        if type(handle) is not str or not handle.strip() or handle in handles:
            raise ValueError
        handles.add(handle)
        for key in ('description', 'user_char') if platform == 'twitter' else ('bio', 'persona'):
            if type(row.get(key)) is not str or not row[key].strip():
                raise ValueError
    return rows


def _root_identity(root):
    from nexaweave_execution.native_owned_binding import _safe_ancestors

    try:
        if not root.is_absolute() or not _safe_ancestors(root) or not root.is_dir() or root.resolve(strict=True) != root:
            raise ValueError
        details = root.stat()
        return details.st_dev, details.st_ino
    except (ValueError, OSError, RuntimeError):
        raise PreparationError('preparation_unavailable') from None


def inspect_prepared_profile(host, payload, *, deadline=None):
    from nexaweave_execution.native_owned_binding import _regular_bound_file

    check = host._source_check(deadline)
    identity = _root_identity(host.root)
    reference = {key: payload[key] for key in ('schema_version', 'operation_id', 'plan_sha256')}
    check()
    row = host._row(reference)
    if row.state != 'ready' or row.receipt is None:
        raise PreparationError('conflict')
    _reauthorize(host, row, check)

    public = row.frozen['public']
    actors = public['actors']
    selected = [(ordinal, actor) for ordinal, actor in enumerate(actors)
                if actor[_SELECTOR] == payload[_SELECTOR]]
    if len(selected) != 1:
        raise PreparationError('not_found')
    ordinal, actor = selected[0]
    platforms = public['options']['platforms']
    if platforms not in _PLATFORM_ORDERS:
        raise PreparationError('conflict')
    root = host.root / ('sim_' + row.operation_id.hex)
    try:
        validate_receipt(row.receipt, str(row.operation_id), platforms)
        blobs = {}
        for pin in row.receipt['files'][2:]:
            check()
            raw = _regular_bound_file(root, pin['name'], min(pin['size'], _MAX_ARTIFACT))
            if len(raw) != pin['size'] or hashlib.sha256(raw).hexdigest() != pin['sha256']:
                raise ValueError
            blobs[pin['name']] = raw
        support = _selected_support(_json_object(blobs['source_grounding.json']), row.frozen, actor)
        rows = {platform: _profile_rows(blobs[name], platform, actors)
                for platform, name in zip(platforms, file_names(platforms)[3:])}
        if set(rows) == {'twitter', 'reddit'}:
            if __package__:
                from .profile_loader_row import twitter_loader_row
            else:
                from profile_loader_row import twitter_loader_row

            reddit = rows['reddit'][ordinal]
            twitter = rows['twitter'][ordinal]
            profile = SimpleNamespace(name=reddit['name'], user_name=reddit['username'],
                                      bio=reddit['bio'], persona=reddit['persona'])
            expected = twitter_loader_row(profile, ordinal)
            if [twitter[key] for key in _TWITTER_FIELDS] != [str(value) for value in expected]:
                raise ValueError
    except (KeyError, ValueError, TypeError, OSError, RecursionError, csv.Error):
        raise PreparationError('conflict') from None

    profiles = []
    for platform in platforms:
        entry = rows[platform][ordinal]
        description, persona = (('description', 'user_char') if platform == 'twitter'
                                else ('bio', 'persona'))
        profiles.append({'platform': platform, 'handle': entry['username'],
                         'description': entry[description], 'persona': entry[persona]})
    result = {**payload, 'display_graph_id': host.display_graph_id, 'scope': public['scope'],
              'project_revision': row.project_revision, 'artifact_sha256': row.receipt['artifact_sha256'],
              'actor': {'ordinal': ordinal, 'name': actor['name'], 'labels': actor['labels']},
              'generated_profiles': profiles, 'frozen_support': support}
    check()
    current = host._row(reference)
    if current != row:
        raise PreparationError('conflict')
    _reauthorize(host, current, check)
    if _root_identity(host.root) != identity:
        raise PreparationError('conflict')
    check()
    return result

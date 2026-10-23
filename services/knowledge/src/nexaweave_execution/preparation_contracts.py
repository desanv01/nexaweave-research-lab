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


def source_revisions(values, anchor=None):
    if type(values) is not list or not 1 <= len(values) <= 100:
        raise ValueError
    for value in values:
        if type(value) is not str:
            raise ValueError
        identifier(value)
    if len(set(values)) != len(values) or anchor is not None and values[0] != anchor:
        raise ValueError
    return values


def source_metadata(value):
    if type(value) is not dict or set(value) != {'source_revision', 'source_name', 'source_sha256'}:
        raise ValueError
    if type(value['source_revision']) is not str:
        raise ValueError
    identifier(value['source_revision']); sha(value['source_sha256'])
    name = value['source_name']
    if type(name) is not str or not name.strip() or '\x00' in name or len(name) > 256:
        raise ValueError
    name.encode('utf-8')
    return value


def validate_source_snapshots(sources, check=None):
    check = check or (lambda: None)
    if type(sources) is not list or not 1 <= len(sources) <= 100:
        raise ValueError
    revisions, evidence = set(), set()
    characters = count = 0
    for source in sources:
        check()
        if type(source) is not dict or set(source) != {'source_revision', 'source_name', 'source_sha256', 'text', 'passages'}:
            raise ValueError
        source_metadata({k: source[k] for k in ('source_revision', 'source_name', 'source_sha256')})
        if source['source_revision'] in revisions:
            raise ValueError
        revisions.add(source['source_revision'])
        content, passages = source['text'], source['passages']
        if type(content) is not str or not content.strip() or '\x00' in content or len(content) > 32768:
            raise ValueError
        if hashlib.sha256(content.encode('utf-8')).hexdigest() != source['source_sha256']:
            raise ValueError
        if type(passages) is not list or not 1 <= len(passages) <= 100:
            raise ValueError
        characters += len(content); count += len(passages)
        if characters > 32768 or count > 100:
            raise ValueError
        for passage in passages:
            check()
            if type(passage) is not dict or set(passage) != {'evidence_id', 'start', 'end', 'page', 'excerpt', 'excerpt_sha256'}:
                raise ValueError
            key = passage['evidence_id']
            if type(key) is not str:
                raise ValueError
            identifier(key)
            if key in evidence:
                raise ValueError
            evidence.add(key)
            start, end, page = passage['start'], passage['end'], passage['page']
            if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(content)
                    or page is not None and (type(page) is not int or not 1 <= page <= 2147483647)
                    or passage['excerpt'] != content[start:end]):
                raise ValueError
            raw = passage['excerpt'].encode('utf-8')
            if not 1 <= len(raw) <= 32768 or sha(passage['excerpt_sha256']) != hashlib.sha256(raw).hexdigest():
                raise ValueError
    check()
    return sources


def document_envelope(sources):
    result = json.dumps({'schema_version': 1, 'sources': [
        {k: source[k] for k in ('source_revision', 'source_name', 'source_sha256', 'text')}
        for source in sources]}, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    if len(result) > 32768:
        raise ValueError
    return result


def validate_selected_source_links(sources, graph, actors, check=None):
    check = check or (lambda: None)
    if type(actors) is not list or not 1 <= len(actors) <= 100:
        raise ValueError
    selected = {}
    for actor in actors:
        check()
        if type(actor) is not dict or set(actor) != {'source_entity_uuid', 'name', 'labels'}:
            raise ValueError
        key = actor['source_entity_uuid']
        if type(key) is not str:
            raise ValueError
        identifier(key)
        if key in selected:
            raise ValueError
        if (type(actor['name']) is not str or not actor['name'].strip() or '\x00' in actor['name']
                or len(actor['name']) > 1024 or type(actor['labels']) is not list or not 1 <= len(actor['labels']) <= 50
                or any(type(label) is not str or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}', label)
                       or label in {'Entity', 'Node'} for label in actor['labels'])
                or len(set(actor['labels'])) != len(actor['labels'])):
            raise ValueError
        actor['name'].encode('utf-8')
        selected[key] = actor
    if (type(graph) is not dict or type(graph.get('nodes')) is not list or len(graph['nodes']) > 10000
            or type(graph.get('edges')) is not list or len(graph['edges']) > 20000):
        raise ValueError
    wanted = {p['evidence_id'] for s in sources for p in s['passages']}
    linked, found = set(), set()
    for node in graph['nodes']:
        check()
        if node['uuid'] not in selected:
            continue
        found.add(node['uuid'])
        if selected[node['uuid']] != dict(source_entity_uuid=node['uuid'], name=node['name'],
                labels=[label for label in node['labels'] if label not in {'Entity', 'Node'}]):
            raise ValueError
        ids = node['evidence_ids']
        if type(ids) is not list or len(ids) > 100:
            raise ValueError
        for key in ids:
            check()
            if key in wanted:
                linked.add(key)
    if found != set(selected):
        raise ValueError
    for edge in graph['edges']:
        check()
        if edge['source_node_uuid'] not in selected and edge['target_node_uuid'] not in selected:
            continue
        ids = edge['evidence_ids']
        if type(ids) is not list or len(ids) > 100:
            raise ValueError
        for key in ids:
            check()
            if key in wanted:
                linked.add(key)
    for source in sources:
        check()
        if not any(p['evidence_id'] in linked for p in source['passages']):
            raise ValueError
    check()


def validate_preparation_sources(frozen, check=None):
    """Pure persisted V2 snapshot/envelope proof; no backend/store imports."""
    check = check or (lambda: None)
    check()
    if type(frozen) is not dict or set(frozen) != {'public', 'plan_identity', 'request', 'source_text', 'graph', 'sources'}:
        raise ValueError
    public, request, sources = frozen['public'], frozen['request'], frozen['sources']
    if (type(public) is not dict or set(public) != {'schema_version', 'display_graph_id', 'scope',
            'project_revision', 'operation_id', 'source', 'sources', 'options', 'actors', 'projection_sha256', 'plan_sha256'}
            or frozen['graph']['graph_id'] != public['display_graph_id']):
        raise ValueError
    if type(public['schema_version']) is not int or public['schema_version'] != 2 or type(request['schema_version']) is not int or request['schema_version'] != 2:
        raise ValueError
    validate_source_snapshots(sources, check)
    metadata = [{k: source[k] for k in ('source_revision', 'source_name', 'source_sha256')} for source in sources]
    requested = source_revisions(request['source_revisions'], request['source_revision'])
    maximum = public['options']['max_agents']
    if type(maximum) is not int or not 1 <= maximum <= 100 or len(public['actors']) > maximum:
        raise ValueError
    if (public['sources'] != metadata or public['source'] != metadata[0]
            or requested != [s['source_revision'] for s in sources]
            or request['operation_id'] != public['operation_id'] or request['options'] != public['options']
            or set(request) != {'schema_version', 'operation_id', 'source_revision', 'source_revisions', 'options'}
            or frozen['source_text'] != document_envelope(sources)):
        raise ValueError
    validate_selected_source_links(sources, frozen['graph'], public['actors'], check)
    check()


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

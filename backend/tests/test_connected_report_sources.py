"""Explicit document-set context contracts; authored statically, Main executes."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.services import connected_report_context as module
from app.services.connected_report_client import ReportError, digest, encoded
from test_connected_report_client import fixture_context, declaration


def uid(number):
    return str(UUID(int=number))


def source_record(revision, name, text, evidence):
    return dict(source_revision=revision, source_name=name,
        source_sha256=hashlib.sha256(text.encode()).hexdigest(), text=text,
        passages=[dict(evidence_id=evidence, start=0, end=len(text), page=2,
            excerpt=text, excerpt_sha256=hashlib.sha256(text.encode()).hexdigest())])


def selected_context():
    original = fixture_context()
    first = dict(original['binding']['source'], text=original['source_text'], passages=original['passages'])
    second = source_record(uid(31), 'DOCX 雪 <literal>', 'Second café 雪 🐟', uid(32))
    original['graph']['nodes'][0]['evidence_ids'].append(uid(32))
    original['binding']['reference_keys'].insert(1, 'source:' + uid(32))
    original['binding']['source_set'] = dict(schema_version=1,
        source_revisions=[first['source_revision'], second['source_revision']])
    return dict(schema_version=2, binding=original['binding'], graph=original['graph'],
        sources=[first, second], native_records=original['native_records'],
        native_manifest=original['native_manifest'])


def reset_references(context):
    context['binding']['source_set'] = dict(schema_version=1,
        source_revisions=[source['source_revision'] for source in context['sources']])
    context['binding']['reference_keys'] = [
        'source:' + p['evidence_id'] for source in context['sources'] for p in source['passages']
    ] + ['native:%s:%d:%s' % (platform, record['index'], record['record_sha256'])
        for platform in context['binding']['native']['platforms'] for record in context['native_records'][platform]]


def test_v1_context_remains_byte_shape_compatible_and_helper_detaches():
    original = fixture_context()
    assert encoded(module.validate_context(original)) == encoded(original)
    passages = module.admitted_source_passages(original)
    assert passages == [dict(original['passages'][0],
        source_revision=original['binding']['source']['source_revision'],
        source_name=original['binding']['source']['source_name'],
        reference='[[source:%s]]' % original['passages'][0]['evidence_id'])]
    assert passages[0]['source_revision'] == original['binding']['source']['source_revision']
    assert passages[0]['source_name'] == original['binding']['source']['source_name']
    assert passages[0]['reference'] == '[[source:%s]]' % original['passages'][0]['evidence_id']
    passages[0]['excerpt'] = 'changed'
    assert original['passages'][0]['excerpt'] != 'changed'


def test_v2_cross_document_offsets_hashes_provenance_and_reference_order():
    context = selected_context()
    admitted = module.validate_context(context)
    assert encoded(admitted) == encoded(context)
    assert admitted is not context and admitted['sources'][1] is not context['sources'][1]
    flattened = module.admitted_source_passages(admitted)
    assert [p['source_revision'] for p in flattened] == [s['source_revision'] for s in context['sources']]
    assert [p['source_name'] for p in flattened] == [s['source_name'] for s in context['sources']]
    assert [p['reference'] for p in flattened] == ['[[source:%s]]' % uid(i) for i in (8, 32)]
    assert flattened[1]['excerpt'] == context['sources'][1]['text']
    assert flattened[1]['end'] == len(context['sources'][1]['text'])
    assert context['binding']['reference_keys'][:2] == ['source:' + uid(8), 'source:' + uid(32)]
    changed = deepcopy(context)
    changed['sources'][1]['source_name'] += ' changed'
    assert digest(changed) != digest(context)


@pytest.mark.parametrize('change', [
    lambda c: c.update(schema_version=True), lambda c: c.update(source_text='extra'),
    lambda c: c.update(sources=[]), lambda c: c.update(sources=c['sources'] * 51),
    lambda c: c['sources'][1].update(extra=True),
    lambda c: c['sources'][1].update(source_revision=c['sources'][0]['source_revision']),
    lambda c: c['sources'][1].update(source_revision='NOT-UUID'),
    lambda c: c['sources'][1].update(source_name=''),
    lambda c: c['sources'][1].update(source_sha256='0' * 64),
    lambda c: c['sources'][1].update(text='substituted'),
    lambda c: c['sources'][1]['passages'][0].update(evidence_id=uid(8)),
    lambda c: c['sources'][1]['passages'][0].update(start=1),
    lambda c: c['sources'][1]['passages'][0].update(end=True),
    lambda c: c['sources'][1]['passages'][0].update(page=0),
    lambda c: c['sources'][1]['passages'][0].update(excerpt='foreign'),
    lambda c: c['sources'][1]['passages'][0].update(excerpt_sha256='0' * 64),
    lambda c: c['sources'][1]['passages'][0].update(excerpt='\ud800'),
    lambda c: c['sources'].reverse(),
    lambda c: c['binding']['reference_keys'].reverse(),
    lambda c: c['graph']['nodes'][0].update(evidence_ids=[uid(8)]),
    lambda c: c['binding'].pop('source_set'),
    lambda c: c['binding']['source_set'].update(extra=True),
    lambda c: c['binding']['source_set'].update(schema_version=2),
    lambda c: c['binding']['source_set'].update(schema_version=True),
    lambda c: c['binding']['source_set'].update(source_revisions=[]),
    lambda c: c['binding']['source_set'].update(source_revisions='not-a-list'),
    lambda c: c['binding']['source_set'].update(source_revisions=[uid(9), uid(9)]),
    lambda c: c['binding']['source_set']['source_revisions'].reverse(),
    lambda c: c['binding']['source_set']['source_revisions'].pop(),
    lambda c: c['binding']['source_set']['source_revisions'].__setitem__(1, uid(999)),
])
def test_v2_rejects_malformed_unlinked_or_relabelled_document_data(change):
    context = selected_context()
    change(context)
    with pytest.raises(ReportError):
        module.validate_context(context)


def test_v1_context_rejects_source_set_even_when_it_names_only_the_anchor():
    context = fixture_context()
    context['binding']['source_set'] = dict(schema_version=1,
        source_revisions=[context['binding']['source']['source_revision']])
    with pytest.raises(ReportError) as caught:
        module.validate_context(context)
    assert caught.value.code == 'conflict'


def test_v2_context_rejects_inconsistent_binding_order_with_unchanged_anchor():
    context = selected_context()
    third = source_record(uid(41), 'third', 'third source', uid(42))
    context['sources'].append(third)
    context['graph']['nodes'][0]['evidence_ids'].append(uid(42))
    reset_references(context)
    assert module.validate_context(context) == context
    context['binding']['source_set']['source_revisions'][1:] = [uid(41), uid(31)]
    with pytest.raises(ReportError) as caught:
        module.validate_context(context)
    assert caught.value.code == 'conflict'


def test_v2_each_source_needs_one_link_not_every_passage_and_edges_can_link():
    context = selected_context()
    second = context['sources'][1]
    second['passages'].append(dict(second['passages'][0], evidence_id=uid(33)))
    context['graph']['nodes'][0]['evidence_ids'] = [uid(8)]
    context['graph']['edges'] = [dict(uuid=uid(40), source_node_uuid=uid(7), target_node_uuid=uid(7),
        fact='second document', episodes=[], evidence_ids=[uid(32)])]
    context['graph']['edge_count'] = 1
    reset_references(context)
    assert module.validate_context(context) == context


def test_v2_retained_text_limit_is_aggregate_not_per_document():
    context = selected_context()
    context['sources'] = [source_record(uid(9), 'source', 'a' * 16384, uid(8)),
                          source_record(uid(31), 'second', 'b' * 16384, uid(32))]
    context['binding']['source'] = {key: context['sources'][0][key] for key in context['binding']['source']}
    assert module.validate_context(context) == context
    context['sources'][1] = source_record(uid(31), 'second', 'b' * 16385, uid(32))
    with pytest.raises(ReportError) as caught:
        module.validate_context(context)
    assert caught.value.code == 'result_too_large'


def test_v2_passage_count_is_globally_bounded_and_exact_100_remains_valid():
    context = selected_context()
    for index, source in enumerate(context['sources']):
        source['passages'] = [dict(source['passages'][0], evidence_id=uid(100 + 50 * index + i))
                              for i in range(50)]
    context['graph']['nodes'][0]['evidence_ids'] = [uid(100), uid(150)]
    reset_references(context)
    assert module.validate_context(context) == context
    context['sources'][1]['passages'].append(dict(context['sources'][1]['passages'][0], evidence_id=uid(250)))
    reset_references(context)
    with pytest.raises(ReportError) as caught:
        module.validate_context(context)
    assert caught.value.code == 'result_too_large'


def test_v2_exact_100_distinct_linked_sources_and_declared_order_are_retained():
    context = selected_context()
    context['sources'] = [source_record(uid(9), 'source', 'a', uid(8))] + [
        source_record(uid(300 + index), 'document %d' % index, '雪', uid(500 + index))
        for index in range(99)]
    context['binding']['source'] = {key: context['sources'][0][key] for key in context['binding']['source']}
    context['graph']['nodes'][0]['evidence_ids'] = [s['passages'][0]['evidence_id'] for s in context['sources']]
    reset_references(context)
    assert module.validate_context(context) == context
    initial_digest = digest(context)
    context['sources'][1], context['sources'][2] = context['sources'][2], context['sources'][1]
    reset_references(context)
    admitted = module.validate_context(context)
    assert admitted == context and digest(admitted) != initial_digest
    assert [p['source_revision'] for p in module.admitted_source_passages(admitted)] == [
        source['source_revision'] for source in context['sources']]


def test_v2_context_bytes_remain_bounded():
    context = selected_context()
    context['sources'][1]['source_name'] = 'x' * module.CONTEXT_BYTES
    with pytest.raises(ReportError) as caught:
        module.validate_context(context)
    assert caught.value.code == 'result_too_large'


def freezer_fixture(tmp_path, monkeypatch):
    context = selected_context()
    # Equality describes persisted scope identity.
    class Scope:
        def model_dump(self, **kwargs):
            return deepcopy(context['binding']['scope'])
        def __eq__(self, other):
            return isinstance(other, Scope) and self.model_dump() == other.model_dump()
    scope = Scope()
    project = SimpleNamespace(project_id=UUID(context['binding']['scope']['project_id']), revision=1)
    rows = {}
    for source in context['sources']:
        rows[source['source_revision']] = SimpleNamespace(project_id=project.project_id,
            source_revision=UUID(source['source_revision']), name=source['source_name'], text=source['text'],
            text_sha256=source['source_sha256'], passages=tuple(SimpleNamespace(
                **dict(p, evidence_id=UUID(p['evidence_id']))) for p in source['passages']))
    calls = []
    def owned(revision, *, expected_revision):
        assert expected_revision == project.revision
        calls.append(revision)
        return scope, project, rows[revision]
    preparation = SimpleNamespace(root=tmp_path, _owned=owned)
    prep = SimpleNamespace(operation_id=UUID(int=10), project_revision=1, receipt={'prepared': True},
        frozen={'public': {'source': deepcopy(context['binding']['source'])}})
    native_binding = context['binding']['native']
    row = SimpleNamespace(request=SimpleNamespace(platforms=native_binding['platforms'],
        fingerprint=native_binding['request_fingerprint']), run_id=UUID(native_binding['run_id']),
        launch_sha256=native_binding['launch_sha256'], receipt={'evidence_sha256': native_binding['evidence_sha256']})
    launch_data = {'preparation': context['binding']['preparation']}
    effects = []
    @contextmanager
    def native_context(*args):
        effects.append('native-enter')
        try:
            yield dict(records=context['native_records'], coverage=context['binding']['coverage'], manifest=context['native_manifest'])
        finally:
            effects.append('native-exit')
    observations = SimpleNamespace(principal='owner', display_graph_id='display-1',
        launch=SimpleNamespace(preparation=preparation, _row=lambda _: row),
        _authority=lambda _: (row, prep, 'native', launch_data),
        _artifacts=lambda *_: prep.receipt, reader=SimpleNamespace(context=native_context))
    reader = SimpleNamespace(graph_data=lambda _: deepcopy(context['graph']))
    payload = dict(declaration(), schema_version=2, source_revisions=list(rows))
    return SimpleNamespace(context=context, scope=scope, project=project, rows=rows, calls=calls,
        observations=observations, reader=reader, payload=payload, effects=effects, owned=owned,
        prep=prep, row=row, launch_data=launch_data)


def test_freezer_rechecks_all_selected_sources_before_yield_and_retains_native_lease(tmp_path, monkeypatch):
    fixture = freezer_fixture(tmp_path, monkeypatch)
    with module.freeze_context(fixture.observations, fixture.reader, fixture.payload) as context:
        assert context == fixture.context
        assert context['binding']['source_set'] == dict(schema_version=1,
            source_revisions=fixture.payload['source_revisions'])
        assert context['binding']['source_set']['source_revisions'] is not fixture.payload['source_revisions']
        assert fixture.calls == fixture.payload['source_revisions'] * 2
        assert fixture.effects == ['native-enter']
    assert fixture.effects == ['native-enter', 'native-exit']


def test_freezer_v1_keeps_single_source_wire_and_does_not_read_second_document(tmp_path, monkeypatch):
    fixture = freezer_fixture(tmp_path, monkeypatch)
    payload = declaration()
    with module.freeze_context(fixture.observations, fixture.reader, payload) as context:
        assert context['schema_version'] == 1
        assert 'source_set' not in context['binding']
        assert set(context) == set(fixture_context())
        assert context['source_text'] == fixture.context['sources'][0]['text']
        assert context['passages'] == fixture.context['sources'][0]['passages']
        assert 'source:' + uid(32) not in context['binding']['reference_keys']
        assert fixture.calls == [fixture.payload['source_revisions'][0]] * 2
    assert fixture.effects == ['native-enter', 'native-exit']


@pytest.mark.parametrize('kind', ['v1', 'subset', 'extra', 'reorder'])
def test_v2_preparation_requires_report_exact_selected_set_before_native_evidence_access(tmp_path, monkeypatch, kind):
    fixture = freezer_fixture(tmp_path, monkeypatch)
    fixture.prep.frozen['public'].update(schema_version=2,
        sources=[{key: source[key] for key in ('source_revision', 'source_name', 'source_sha256')}
                 for source in fixture.context['sources']])
    if kind == 'v1':
        fixture.payload = declaration()
    elif kind == 'subset':
        fixture.payload['source_revisions'].pop()
    elif kind == 'extra':
        fixture.payload['source_revisions'].append(uid(41))
    else:
        fixture.payload['source_revisions'].reverse()
    fixture.reader.graph_data = lambda _: pytest.fail('mismatched preparation selection reached projection')
    with pytest.raises(ReportError) as caught:
        with module.freeze_context(fixture.observations, fixture.reader, fixture.payload):
            pytest.fail('mismatched preparation selection yielded')
    assert caught.value.code == 'conflict'
    assert fixture.calls == [] and fixture.effects == []


def test_v2_preparation_accepts_report_identical_selection_and_preserves_anchor(tmp_path, monkeypatch):
    fixture = freezer_fixture(tmp_path, monkeypatch)
    fixture.prep.frozen['public'].update(schema_version=2,
        sources=[{key: source[key] for key in ('source_revision', 'source_name', 'source_sha256')}
                 for source in fixture.context['sources']])
    with module.freeze_context(fixture.observations, fixture.reader, fixture.payload) as context:
        assert context['binding']['source_set']['source_revisions'] == fixture.payload['source_revisions']
        assert context['binding']['source'] == fixture.context['binding']['source']


@pytest.mark.parametrize('kind', ['anchor', 'duplicate', 'missing', 'revoked', 'foreign-scope', 'foreign-project'])
def test_freezer_refuses_invalid_or_unauthorized_selection_before_publication(tmp_path, monkeypatch, kind):
    fixture = freezer_fixture(tmp_path, monkeypatch)
    second = fixture.payload['source_revisions'][1]
    if kind == 'anchor':
        fixture.payload['source_revisions'].reverse()
    elif kind == 'duplicate':
        fixture.payload['source_revisions'][1] = fixture.payload['source_revisions'][0]
    else:
        def owned(revision, **kwargs):
            if revision != second:
                return fixture.owned(revision, **kwargs)
            if kind in ('missing', 'revoked'):
                raise ReportError('not_found' if kind == 'missing' else 'unauthorized')
            scope, project, record = fixture.owned(revision, **kwargs)
            if kind == 'foreign-scope':
                scope = object()
            else:
                project = SimpleNamespace(project_id=UUID(int=999), revision=1)
            return scope, project, record
        fixture.observations.launch.preparation._owned = owned
    with pytest.raises(ReportError):
        with module.freeze_context(fixture.observations, fixture.reader, fixture.payload):
            pytest.fail('invalid selection yielded a context')
    assert fixture.effects == []


@pytest.mark.parametrize('kind', ['text', 'passage', 'metadata', 'scope', 'project', 'revoked', 'missing'])
def test_freezer_rechecks_every_full_source_record_and_authority_after_freeze(tmp_path, monkeypatch, kind):
    fixture = freezer_fixture(tmp_path, monkeypatch)
    second = fixture.payload['source_revisions'][1]
    counts = {}
    def owned(revision, **kwargs):
        counts[revision] = counts.get(revision, 0) + 1
        if revision == second and counts[revision] == 2:
            if kind in ('revoked', 'missing'):
                raise ReportError('unauthorized' if kind == 'revoked' else 'not_found')
            record = fixture.rows[revision]
            # Mutate in place: comparison must use a detached initial snapshot.
            if kind == 'text': record.text += ' changed'
            elif kind == 'passage': record.passages[0].excerpt = 'changed'
            elif kind == 'metadata': record.name += ' changed'
            elif kind == 'scope': return object(), fixture.project, record
            elif kind == 'project': return fixture.scope, SimpleNamespace(revision=2), record
        return fixture.owned(revision, **kwargs)
    fixture.observations.launch.preparation._owned = owned
    with pytest.raises(ReportError):
        with module.freeze_context(fixture.observations, fixture.reader, fixture.payload):
            pytest.fail('changed source yielded a context')
    assert fixture.effects == ['native-enter', 'native-exit']


def test_freezer_aggregate_refusal_stops_before_more_reads_or_native_lease(tmp_path, monkeypatch):
    fixture = freezer_fixture(tmp_path, monkeypatch)
    fixture.rows[uid(31)].text = 'x' * 32768
    fixture.payload['source_revisions'].append(uid(41))
    with pytest.raises(ReportError) as caught:
        with module.freeze_context(fixture.observations, fixture.reader, fixture.payload):
            pytest.fail('aggregate overflow yielded')
    assert caught.value.code == 'result_too_large'
    assert fixture.calls == fixture.payload['source_revisions'][:2]
    assert fixture.effects == []


@pytest.mark.parametrize('kind', ['unlinked', 'text-hash', 'anchor-name'])
def test_freezer_rejects_initial_source_or_link_corruption_before_yield(tmp_path, monkeypatch, kind):
    fixture = freezer_fixture(tmp_path, monkeypatch)
    if kind == 'unlinked':
        fixture.context['graph']['nodes'][0]['evidence_ids'] = [uid(8)]
    elif kind == 'text-hash':
        fixture.rows[uid(31)].text_sha256 = '0' * 64
    else:
        fixture.rows[fixture.payload['source_revisions'][0]].name = 'changed anchor'
    with pytest.raises(ReportError):
        with module.freeze_context(fixture.observations, fixture.reader, fixture.payload):
            pytest.fail('corrupt initial source yielded')
    assert fixture.effects == ['native-enter', 'native-exit']


@pytest.mark.parametrize('kind', ['projection', 'artifacts', 'native-authority'])
def test_v2_preserves_final_projection_artifact_and_native_authority_checks(tmp_path, monkeypatch, kind):
    fixture = freezer_fixture(tmp_path, monkeypatch)
    calls = []
    if kind == 'projection':
        def read(_):
            calls.append(True)
            graph = deepcopy(fixture.context['graph'])
            if len(calls) == 2:
                graph['nodes'][0]['summary'] = 'changed projection'
            return graph
        fixture.reader.graph_data = read
    elif kind == 'artifacts':
        def artifacts(*_):
            calls.append(True)
            return fixture.prep.receipt if len(calls) == 1 else {'changed': True}
        fixture.observations._artifacts = artifacts
    else:
        def authority(_):
            calls.append(True)
            return (fixture.row, fixture.prep, 'native' if len(calls) == 1 else 'changed', fixture.launch_data)
        fixture.observations._authority = authority
    with pytest.raises(ReportError) as caught:
        with module.freeze_context(fixture.observations, fixture.reader, fixture.payload):
            pytest.fail('changed final authority yielded')
    assert caught.value.code == 'conflict'
    assert len(calls) == 2
    assert fixture.effects == ['native-enter', 'native-exit']


@pytest.mark.parametrize('caller_deadline', [None, 103.0])
def test_freezer_shared_clock_refuses_slow_source_access_without_extending_tighter_deadline(tmp_path, monkeypatch, caller_deadline):
    fixture = freezer_fixture(tmp_path, monkeypatch)
    clock = [100.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    second = fixture.payload['source_revisions'][1]
    def owned(revision, **kwargs):
        result = fixture.owned(revision, **kwargs)
        if revision == second:
            clock[0] += 4 if caller_deadline is not None else 16
        return result
    fixture.observations.launch.preparation._owned = owned
    with pytest.raises(ReportError) as caught:
        with module.freeze_context(fixture.observations, fixture.reader, fixture.payload, deadline=caller_deadline):
            pytest.fail('expired source access yielded')
    assert caught.value.code == 'timeout'
    assert fixture.effects == []


def test_v2_context_validation_checks_clock_while_scanning_selected_graph_membership(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    calls = []
    def tick():
        calls.append(True)
        if len(calls) == 9:
            clock[0] += 16
    with pytest.raises(ReportError) as caught:
        module.validate_context(selected_context(), tick=tick)
    assert caught.value.code == 'timeout'
    assert len(calls) == 9

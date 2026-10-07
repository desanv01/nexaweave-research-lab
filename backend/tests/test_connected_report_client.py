"""Pure cold contract negatives, no optional PG/Temporal/engine imports."""
from copy import deepcopy
import base64
import hashlib
from uuid import UUID
import pytest


def fixture_context():
    from app.services.connected_report_client import digest
    uid = lambda i: str(UUID(int=i))
    source = 'café 雨 🐟 <script>literal</script>'
    passage = dict(evidence_id=uid(8), start=0, end=len(source), page=None, excerpt=source,
                   excerpt_sha256=hashlib.sha256(source.encode()).hexdigest())
    scope = dict(schema_version=1, workspace_id=uid(1), project_id=uid(2), graph_id=uid(3),
                 layer='source', run_id=None, branch_id=None)
    node = dict(uuid=uid(7), name='Rain', labels=['Entity', 'Person'], summary='Rain in the source',
                attributes={}, episodes=[uid(6)], evidence_ids=[uid(8)])
    graph = dict(graph_id='display-1', nodes=[node], edges=[], node_count=1, edge_count=0)
    raw = '{"event_type":"post","unknown":["雨",null],"id":1}'
    record = dict(index=0, raw_json=raw, record_sha256=hashlib.sha256(raw.encode()).hexdigest())
    records = dict(twitter=[deepcopy(record)], reddit=[deepcopy(record)])
    files = [dict(name=name, size=len(blob), sha256=hashlib.sha256(blob).hexdigest())
        for p in ('twitter', 'reddit') for name, blob in
        ((p + '_simulation.db', b'native-db'), (p + '/actions.jsonl', (raw + '\n').encode()))]
    manifest = dict(schema_version=1, files=files)
    binding = dict(display_graph_id='display-1', principal='owner', scope=scope, project_revision=1,
        source=dict(source_revision=uid(9), source_name='source', source_sha256=hashlib.sha256(source.encode()).hexdigest()),
        preparation=dict(operation_id=uid(10), plan_sha256='a'*64, simulation_id='sim_' + UUID(int=10).hex, artifact_sha256='b'*64),
        native=dict(run_id=uid(11), launch_sha256='c'*64, request_fingerprint='d'*64,
            evidence_sha256=digest(manifest), platforms=['twitter', 'reddit']),
        coverage=[dict(platform=p, total_records=1, selected_records=1, complete=True,
                       windows=[dict(offset=0, count=1)]) for p in ('twitter', 'reddit')],
        reference_keys=['source:' + uid(8)] + ['native:%s:0:%s' % (p, record['record_sha256']) for p in ('twitter', 'reddit')])
    return dict(schema_version=1, binding=binding, graph=graph, source_text=source, passages=[passage],
                native_records=records, native_manifest=manifest)


def declaration():
    context = fixture_context()
    return dict(schema_version=1, report_id=str(UUID(int=12)), launch_id=context['binding']['native']['run_id'],
        launch_sha256=context['binding']['native']['launch_sha256'], requirement='Explain recorded behavior',
        output_language='en', native_windows=None)


def public_result(state='planned'):
    from app.services.connected_report_client import DEFAULT_LIMITS, BASE_NAMES, digest
    context, payload = fixture_context(), declaration()
    identity = dict(schema_version=1, report_id=payload['report_id'], binding=context['binding'],
        options={k: payload[k] for k in ('requirement', 'output_language', 'native_windows')},
        context_sha256=digest(context), source_projection_sha256=digest(context['graph']),
        model_label='scripted-local', limits=dict(DEFAULT_LIMITS), ceiling_microusd=4)
    result = dict(identity, plan_sha256=digest(identity), authorization=dict(model_calls_enabled=False, budget_configured=True),
        state=state, progress=dict(stage=state, percent=0, completed_sections=0, total_sections=0), workflow=None,
        receipt=None, receipt_sha256=None, manifest=None, cleanup=dict(known=True, pending=False, owner_thread_alive=False),
        cancel_requested=False, error_code=None)
    content = 'Observed rain [[' + context['binding']['reference_keys'][-1] + ']]'
    if state == 'completed':
        manifest = dict(schema_version=1, files=[dict(name=name, size=len(content.encode()), sha256=hashlib.sha256(content.encode()).hexdigest())
            for name in BASE_NAMES + ['section_01.md']])
        receipt = dict(schema_version=1, report_id=identity['report_id'], plan_sha256=result['plan_sha256'],
            context_sha256=identity['context_sha256'], manifest_sha256=digest(manifest), output_language='en',
            reference_integrity='validated', semantic_support_status='not_reviewed')
        result.update(manifest=manifest, receipt=receipt, receipt_sha256=digest(receipt),
            progress=dict(stage='completed', percent=100, completed_sections=1, total_sections=1))
    return result, content


def reference(result):
    return {k: result[k] for k in ('schema_version', 'report_id', 'plan_sha256')}


def report_budget_wire():
    from nexaweave_execution.report_contracts import budget_fingerprint, digest
    result, _ = public_result('completed')
    return dict(kind='connected_report_budget_v1', operation_id=result['report_id'], attempt_id=str(UUID(int=99)),
        fingerprint=budget_fingerprint(result['plan_sha256']), plan_sha256=result['plan_sha256'],
        report_receipt=deepcopy(result['receipt']), report_receipt_sha256=digest(result['receipt']))


def test_report_budget_receipt_roundtrip_has_no_mutable_alias_and_distinct_episode():
    from dataclasses import FrozenInstanceError
    from uuid import uuid5, NAMESPACE_URL
    from nexaweave_execution.report_contracts import ReportBudgetReceipt, budget_episode
    wire = report_budget_wire(); receipt = ReportBudgetReceipt.from_wire(wire)
    assert receipt.json_value() == wire
    assert receipt.operation_id == UUID(wire['operation_id']) and receipt.attempt_id == UUID(wire['attempt_id'])
    assert receipt.report_receipt_sha256 == wire['report_receipt_sha256']
    original = deepcopy(wire)
    wire['report_receipt']['context_sha256'] = 'f'*64
    detached = receipt.report_receipt; detached['manifest_sha256'] = 'e'*64
    exported = receipt.json_value(); exported['report_receipt']['output_language'] = 'ms'
    assert receipt.json_value() == original
    with pytest.raises(FrozenInstanceError):
        receipt.plan_sha256 = 'a'*64
    group, operation = 'mf1_' + 'a'*64, receipt.operation_id
    expected = uuid5(NAMESPACE_URL, 'mirofish:connected-report-budget:v1:' + group + ':' + str(operation))
    assert budget_episode(group, operation) == budget_episode(group, str(operation)) == expected
    assert expected != uuid5(NAMESPACE_URL, 'mirofish:episode:v1:' + group + ':' + str(operation))
    assert expected != uuid5(NAMESPACE_URL, 'mirofish:native-budget:v1:' + group + ':' + str(operation))


@pytest.mark.parametrize('change', [lambda w: w.update(extra=True), lambda w: w.update(kind='prepared_budget_v1'),
    lambda w: w.update(operation_id=True), lambda w: w.update(operation_id=str(UUID(int=13))),
    lambda w: w.update(attempt_id='BAD'), lambda w: w.update(fingerprint='f'*64),
    lambda w: w.update(plan_sha256='e'*64), lambda w: w.update(report_receipt_sha256='0'*64),
    lambda w: w['report_receipt'].update(schema_version=True), lambda w: w['report_receipt'].update(extra=True),
    lambda w: w['report_receipt'].update(report_id=str(UUID(int=14))),
    lambda w: w['report_receipt'].update(context_sha256='F'*64), lambda w: w['report_receipt'].update(manifest_sha256=True),
    lambda w: w['report_receipt'].update(output_language='fr'),
    lambda w: w['report_receipt'].update(reference_integrity='unvalidated'),
    lambda w: w['report_receipt'].update(semantic_support_status='reviewed')],
    ids=['extra', 'foreign-kind', 'bool-id', 'foreign-id', 'attempt', 'fingerprint', 'plan', 'digest',
         'bool-version', 'proof-extra', 'proof-report', 'context', 'manifest', 'language', 'integrity', 'semantic'])
def test_report_budget_proof_refuses_mutations_even_with_rehashed_nested_proof(change):
    from nexaweave_execution.report_contracts import ReportBudgetReceipt, ReportError, digest
    wire = report_budget_wire(); change(wire)
    if wire['report_receipt_sha256'] != '0'*64:
        wire['report_receipt_sha256'] = digest(wire['report_receipt'])
    with pytest.raises(ReportError) as error:
        ReportBudgetReceipt.from_wire(wire)
    assert error.value.code == 'report_uncertain'


@pytest.mark.parametrize('change', [lambda p: p.update(schema_version=True), lambda p: p.update(report_id='REPORT'),
    lambda p: p.update(launch_sha256='C'*64), lambda p: p.update(output_language='fr'),
    lambda p: p.update(requirement=''), lambda p: p.update(requirement='\ud800'),
    lambda p: p.update(principal='owner'), lambda p: p.update(native_windows=[]),
    lambda p: p.update(native_windows=[dict(platform='twitter', offset=True, count=1)]),
    lambda p: p.update(native_windows=[dict(platform='twitter', offset=0, count=0)]),
    lambda p: p.update(native_windows=[dict(platform='twitter', offset=0, count=1)]*2)])
def test_plan_rejects_untrusted_or_noncanonical_fields(change):
    from app.services.connected_report_client import ReportError, validate_payload
    payload = declaration(); change(payload)
    with pytest.raises(ReportError) as error:
        validate_payload('plan', payload)
    assert error.value.code == 'invalid_request'


def test_duplicate_depth_nonfinite_and_invalid_utf8_json():
    from app.services.connected_report_client import strict_json
    for raw in (b'{"x":1,"x":2}', b'{"x":1e999}', b'{"x":"\xff"}', ('['*17 + '0' + ']'*17).encode()):
        with pytest.raises((ValueError, UnicodeError)):
            strict_json(raw)


def test_dynamic_auth_never_changes_identity_but_stale_immutable_reply_refused():
    from app.services.connected_report_client import ReportError, validate_result, digest, IDENTITY
    result, _ = public_result()
    known = deepcopy(result)
    result['authorization']['model_calls_enabled'] = True
    assert validate_result(result, 'display-1', result['binding']['scope'], declaration(), 'plan', 'owner', known)['plan_sha256'] == known['plan_sha256']
    result['model_label'] = 'changed'
    result['plan_sha256'] = digest({k: result[k] for k in IDENTITY})
    with pytest.raises(ReportError):
        validate_result(result, 'display-1', result['binding']['scope'], declaration(), 'plan', 'owner', known)


@pytest.mark.parametrize('change', [lambda r: r['cleanup'].update(known=False, pending=None, owner_thread_alive=None),
    lambda r: r['receipt'].update(semantic_support_status='verified'), lambda r: r['manifest']['files'].reverse(),
    lambda r: r['manifest']['files'][0].update(size=True), lambda r: r['progress'].update(completed_sections=0),
    lambda r: r.update(receipt_sha256='f'*64)])
def test_completed_requires_real_integrity_and_cleanup(change):
    from app.services.connected_report_client import ReportError, validate_result
    result, _ = public_result('completed'); payload = reference(result); change(result)
    with pytest.raises(ReportError):
        validate_result(result, 'display-1', result['binding']['scope'], payload, 'status', 'owner')


def test_read_and_download_verify_actual_content_not_only_metadata():
    from app.services.connected_report_client import ReportError, validate_read, validate_download
    result, content = public_result('completed'); payload = reference(result)
    read = dict(schema_version=1, report=result, content=content)
    assert validate_read(read, 'display-1', result['binding']['scope'], payload)['content'] == content
    read['content'] += 'corrupt'
    with pytest.raises(ReportError):
        validate_read(read, 'display-1', result['binding']['scope'], payload)
    payload.update(kind='report', section_index=None)
    file = result['manifest']['files'][2]
    download = dict(schema_version=1, report_id=result['report_id'], plan_sha256=result['plan_sha256'],
        receipt_sha256=result['receipt_sha256'], artifact=dict(file, mime='text/markdown', content_base64=base64.b64encode(content.encode()).decode()))
    assert validate_download(download, payload, result)['artifact']['name'] == 'full_report.md'
    download['artifact']['content_base64'] += '\n'
    with pytest.raises(ReportError):
        validate_download(download, payload, result)

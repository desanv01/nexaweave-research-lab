"""Main actual inherited preparation/native/report journey; offline envelopes."""
import asyncio
import base64
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.postgres, pytest.mark.connected_report_temporal]


_fixture_phase_sink = None
_actual_graph_journey = False


class FixturePhases:
    """Bounded diagnostics only; no lifecycle or qualification authority."""
    def __init__(self, suite):
        self.suite = suite
        self.count = 0
        self.started = {}

    def emit(self, phase, event, started, *, cleanup=False):
        active_error = sys.exception() if cleanup else None
        try:
            if self.count >= 128:
                return
            now = time.monotonic()
            elapsed = max(0.0, now - started)
            if not math.isfinite(now) or not math.isfinite(elapsed) or now < 0:
                return
            self.count += 1
            record = dict(
                schema_version=1, suite=self.suite, phase=phase, event=event,
                monotonic_seconds=now, elapsed_seconds=elapsed)
            if _fixture_phase_sink is not None:
                _fixture_phase_sink(record)
            else:
                print('NEXAWEAVE_FIXTURE_PHASE ' + json.dumps(record, allow_nan=False), flush=True)
        except (KeyboardInterrupt, SystemExit):
            # Called only after all original cleanup assertions have succeeded.
            # An existing work/cleanup exception must retain its exact identity.
            if not cleanup or active_error is None:
                raise
        except Exception:
            pass

    def start(self, phase):
        try:
            started = time.monotonic()
            self.started[phase] = started
            self.emit(phase, 'start', started)
        except Exception:
            pass

    def end(self, phase):
        try:
            started = self.started.pop(phase, None)
            if started is not None:
                self.emit(phase, 'end', started)
        except Exception:
            pass

    def observed_closed(self):
        active_error = sys.exception()
        try:
            self.emit('cleanup', 'observed_closed', time.monotonic(), cleanup=True)
        except (KeyboardInterrupt, SystemExit):
            if active_error is None:
                raise
        except Exception:
            pass


@dataclass(frozen=True)
class ScriptedReportTransportFactory:
    log_path: str

    def __call__(self, *, max_retries, timeout):
        assert type(max_retries) is int and max_retries == 0 and 0 < timeout <= 15
        return ScriptedReportTransport(self.log_path)


class ScriptedReportTransport:
    """Actual inherited ReportAgent consumes these raw cooperative SDK replies."""
    max_retries = 0

    def __init__(self, log_path):
        self.path, self.count, self.prose_count = Path(log_path), 0, 0
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        assert kwargs['stream'] is False and 0 < kwargs['timeout'] <= 15
        tokens = kwargs.get('max_tokens', kwargs.get('max_completion_tokens'))
        assert type(tokens) is int and 1 <= tokens <= 4096
        self.count += 1
        assert self.count <= 64
        all_text = '\n'.join(message['content'] for message in kwargs['messages'])
        refs = re.findall(r'source:[0-9a-f-]{36}|native:(?:twitter|reddit):[0-9]+:[0-9a-f]{64}', all_text)
        sources = [value for value in refs if value.startswith('source:')]
        natives = [value for value in refs if value.startswith('native:')]
        assert sources and natives, 'Connected prompts must expose only actual admitted reference keys'
        if kwargs.get('response_format') is not None:
            content = json.dumps(dict(title='Recorded evidence report', summary='Source and native evidence are distinct.',
                sections=[dict(title='Source evidence'), dict(title='Observed native records')]))
        else:
            slot = self.prose_count % 4
            self.prose_count += 1
            if slot == 0:
                content = '<tool_call>{"name":"quick_search","parameters":{"query":"source"}}</tool_call>'
            elif slot == 1:
                content = '<tool_call>{"name":"panorama_search","parameters":{"query":"source"}}</tool_call>'
            elif slot == 2:
                match = re.fullmatch(r'native:([^:]+):([0-9]+):([0-9a-f]{64})', natives[0])
                content = '<tool_call>' + json.dumps(dict(name='recorded_native_events',
                    parameters=dict(platform=match[1], offset=int(match[2]), limit=1))) + '</tool_call>'
            else:
                content = ('Final Answer: Retained source assertions remain source evidence [[' + sources[0]
                    + ']]. The selected native log record is observed execution evidence [[' + natives[0]
                    + ']]. This narrative is interpretation, not a real-world prediction or billed-cost statement. '
                    'Literal hostile text <img src=x onerror=alert(1)> <script>inert</script> 猫 😀 stays quoted.')
        with self.path.open('a', encoding='utf-8', newline='\n') as output:
            output.write(json.dumps(dict(call=self.count, response_format=kwargs.get('response_format'),
                timeout=kwargs['timeout'], max_tokens=tokens, stream=False, reference_keys=refs), ensure_ascii=True)+'\n')
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason='stop')],
                               usage=SimpleNamespace(completion_tokens=128, total_tokens=256))

    def close(self):
        with self.path.open('a', encoding='utf-8', newline='\n') as output:
            output.write(json.dumps(dict(closed=True, calls=self.count))+'\n')


def _retain_four_formats(factory, directory, monkeypatch, prep, scope):
    """Actual public parsing/retention for the explicitly selected real-graph journey."""

    from nexaweave_storage import SourceStore
    from nexaweave_storage.pdf import extract_pdf
    from test_pdf_source import pdf_bytes
    from test_document_source_integration import _package, _paragraph
    from test_source_library_http_integration import _host, _exchange, _payload

    # Separate Main-selected locked runtimes: never replace KG with backend Python.
    assert _actual_graph_journey and os.environ.get('KNOWLEDGE_INTEGRATION') == '1'
    python = Path(os.environ['KNOWLEDGE_PYTHON'])
    backend = Path(os.environ['NEXAWEAVE_WORKBENCH_BACKEND_PYTHON'])
    bootstrap = Path(os.environ['KNOWLEDGE_BOOTSTRAP_SCRIPT'])
    assert python.is_absolute() and python.is_file()
    assert backend.is_absolute() and backend.is_file() and backend == Path(sys.executable).absolute()
    assert python != backend, 'separate locked KG/backend executable paths required'
    assert not bootstrap.resolve().is_relative_to(Path(sys.prefix).resolve())
    assert bootstrap.is_absolute() and bootstrap.is_file() and 'site-packages' in bootstrap.parts
    assert bootstrap.name == 'read_bootstrap.py' and bootstrap.parent.name == 'nexaweave_knowledge'
    assert all(bootstrap.with_name(name).is_file() for name in
               ('source_bootstrap.py', 'source_ingestion_bootstrap.py', 'read_session_bootstrap.py'))
    pdf = pdf_bytes(['PDF assertions 猫', 'Second PDF page 雪'])
    markdown = '# Markdown assertions\n\nRetained **Markdown** evidence 猫.\n'
    plain = 'Plain text assertions 雪 😀\n'
    docx = _package(_paragraph('Office assertions 猫') +
                    '<w:tbl><w:tr><w:tc>' + _paragraph('Table evidence 雪') + '</w:tc></w:tr></w:tbl>')
    documents = [('original.pdf', 'pdf', pdf, extract_pdf(pdf).text),
                 ('notes.md', 'text', markdown, markdown),
                 ('notes.txt', 'text', plain, plain),
                 ('office.docx', 'docx', docx, 'Office assertions 猫\n\nTable evidence 雪')]
    upload_root = directory / 'uploaded-documents'
    upload_root.mkdir()
    http_root = directory / 'source-http'
    http_root.mkdir()
    records, snapshots, receipts = [], [], []
    store = SourceStore(factory)
    with _host(http_root, scope, prep.display_graph_id) as (port, token, _):
        for name, kind, content, expected in documents:
            binary = content.encode('utf-8') if isinstance(content, str) else content
            (upload_root / name).write_bytes(binary)
            revision = uuid4()
            payload = _payload(revision, name, content, kind)
            route = '/api/source/retain/' + prep.display_graph_id
            if kind == 'pdf':
                payload['schema_version'] = 2
                route = '/api/source/retain-original/' + prep.display_graph_id
            status, headers, reply = _exchange(port, 'POST', route, payload, token=token)
            assert status == 200 and reply['success'] is True
            assert headers['Cache-Control'] == 'no-store' and headers['X-Content-Type-Options'] == 'nosniff'
            receipt = reply['data']
            assert receipt['extraction']['format'] == kind
            assert receipt['extraction']['input_hash_verified'] is True
            assert receipt['extraction']['input_sha256'] == hashlib.sha256(binary).hexdigest()
            assert receipt['graph_ingestion_executed'] is False
            record = store.get_source('owner', scope.project_id, revision)
            assert record.name == name and record.text == expected and record.passages
            assert record.text_sha256 == hashlib.sha256(expected.encode('utf-8')).hexdigest()
            if kind == 'pdf':
                assert receipt['schema_version'] == 2 and receipt['binary_retained'] is True
                assert receipt['binary']['sha256'] == hashlib.sha256(pdf).hexdigest()
                original = store.get_original('owner', scope.project_id, revision)
                assert original.content == pdf and original.source == record
                assert [p.page for p in record.passages] == [1, 2]
            else:
                assert receipt['binary_retained'] is False
            if kind == 'docx':
                assert [p.excerpt for p in record.passages] == ['Office assertions 猫', 'Table evidence 雪']
            passages = []
            for p in record.passages:
                assert expected[p.start:p.end] == p.excerpt
                assert hashlib.sha256(p.excerpt.encode('utf-8')).hexdigest() == p.excerpt_sha256
                passages.append(dict(evidence_id=str(p.evidence_id), start=p.start, end=p.end, page=p.page,
                                     excerpt=p.excerpt, excerpt_sha256=p.excerpt_sha256))
            snapshots.append(dict(source_revision=str(revision), source_name=name,
                                  source_sha256=record.text_sha256, text=expected, passages=passages))
            records.append(record)
            receipts.append(receipt)
    return SimpleNamespace(records=records, snapshots=snapshots, receipts=receipts, pdf=pdf,
                           revisions=[str(record.source_revision) for record in records], upload_root=upload_root)


def _assert_four_format_restart(factory, directory, prep, scope, selected):
    from nexaweave_storage import SourceStore, NotFound
    from test_source_library_http_integration import _host, _exchange
    restart = directory / 'source-http-restarted'
    restart.mkdir()
    with _host(restart, scope, prep.display_graph_id) as (port, token, _):
        for record, snapshot in zip(selected.records, selected.snapshots, strict=True):
            status, _, reply = _exchange(port, 'GET',
                f'/api/source/item/{prep.display_graph_id}/{record.source_revision}', token=token)
            assert status == 200 and reply['data']['text'] == snapshot['text']
            assert reply['data']['passages'] == [
                {key: p[key] for key in ('evidence_id', 'start', 'end', 'page', 'excerpt_sha256')}
                for p in snapshot['passages']]
            assert SourceStore(factory).get_source('owner', scope.project_id, record.source_revision) == record
        revision = selected.revisions[0]
        route = f'/api/source/original/{prep.display_graph_id}/{revision}'
        status, headers, reply = _exchange(port, 'GET', route, token=token)
        assert status == 200 and headers['Cache-Control'] == 'no-store'
        assert headers['X-Content-Type-Options'] == 'nosniff'
        assert base64.b64decode(reply['data']['content_base64'], validate=True) == selected.pdf
        assert reply['data']['binary'] == selected.receipts[0]['binary']
        assert reply['data']['binary']['sha256'] == hashlib.sha256(selected.pdf).hexdigest()
        assert _exchange(port, 'GET', route)[0] == 401
        assert _exchange(port, 'GET', route, token='foreign-token')[0] == 401
        assert _exchange(port, 'GET', route, token=token, origin='http://denied.example')[0] == 403
        assert _exchange(port, 'GET', f'/api/source/original/foreign_graph/{revision}', token=token)[0] == 404
        assert _exchange(port, 'GET', f'/api/source/original/{prep.display_graph_id}/{uuid4()}', token=token)[0] == 404
    with pytest.raises(NotFound):
        SourceStore(factory).get_original('other', scope.project_id, selected.records[0].source_revision)
    for record, receipt in zip(selected.records, selected.receipts, strict=True):
        binary = (selected.upload_root / record.name).read_bytes()
        assert hashlib.sha256(binary).hexdigest() == receipt['extraction']['input_sha256']


def _assert_accounted(prep, expected):
    status = prep.budget.status('owner', prep.account_id)
    assert status.cap_microusd == 12 and status.accounted_ceiling_microusd == expected
    assert status.reserved_microusd == status.uncertain_microusd == 0
    assert status.remaining_microusd == 12 - expected and status.actual_usage_microusd is None


@contextmanager
def _actual_graph_sources(factory, directory, monkeypatch, prep, scope):
    """Actual installed Graphiti ingestion/read; only SDK model replies are canned."""
    from psycopg.conninfo import conninfo_to_dict
    from nexaweave_knowledge.contracts import OntologySpec
    from nexaweave_knowledge.operations import Ledger, CompletionReceipt
    from nexaweave_execution.budget import ReservationState
    from test_source_ingestion_http_integration import host, model_server, neo_scope
    from test_source_library_http_integration import _exchange
    from app.services.knowledge_read_facade import KnowledgeReadFacade
    selected = _retain_four_formats(factory, directory, monkeypatch, prep, scope)
    ontology = OntologySpec(revision=uuid4(), entity_types=(
        {'name': 'Person', 'description': 'Named human'},
        {'name': 'Organization', 'description': 'Named organization'}), edge_types=(
        {'name': 'WORKS_FOR', 'description': 'Employment relationship',
         'source_targets': ({'source': 'Person', 'target': 'Organization'},)},))
    _assert_accounted(prep, 0)
    with neo_scope(scope) as driver, model_server() as (model_port, records, errors, _):
        selected.ingestion = []
        with host(directory / 'actual-source-ingestion', scope, prep.display_graph_id,
                  prep.account_id, model_port, ceiling=1) as (port, token, _):
            for index, record in enumerate(selected.records):
                operation = uuid4()
                payload = dict(schema_version=1, source_revision=str(record.source_revision),
                               operation_id=str(operation), ontology=ontology.model_dump(mode='json'))
                route = '/api/source/ingestion/execute/' + prep.display_graph_id
                status, _, result = _exchange(port, 'POST', route, payload, token=token)
                assert status == 200, result
                data = result['data']
                assert data['state'] == 'completed' and data['budget_state'] == 'settled'
                assert data['scope'] == scope.model_dump(mode='json')
                assert data['operation_id'] == str(operation) and data['ceiling_microusd'] == 1
                assert data['episode_id'] == str(scope.episode_uuid(operation))
                assert data['evidence_ids'] == [str(p.evidence_id) for p in record.passages]
                assert data['graph_ingestion_executed'] is True and data['actual_usage_microusd'] is None
                saved = Ledger(factory).get(scope, operation)
                with factory() as conn:
                    capacity = prep.budget._row(conn, prep.account_id, operation)
                assert capacity.state == ReservationState.settled and capacity.ceiling_microusd == 1
                assert type(capacity.receipt) is CompletionReceipt and capacity.receipt == saved.receipt
                assert saved.receipt.json_value() == data['receipt']
                assert capacity.fingerprint == saved.fingerprint == data['fingerprint']
                assert capacity.scope_group_id == scope.group_id
                assert capacity.episode_id == saved.receipt.episode_id == scope.episode_uuid(operation)
                assert capacity.evidence_ids == tuple(p.evidence_id for p in record.passages)
                rows, _, _ = driver.execute_query(
                    'MATCH (e:Episodic {uuid:$episode}), (m:MiroFishIngest {uuid:$episode}) '
                    'RETURN e.group_id AS group_id,e.content AS content,m.status AS state,'
                    'm.fingerprint AS fingerprint,m.evidence_ids AS evidence_ids',
                    episode=data['episode_id'], routing_='r')
                assert len(rows) == 1 and rows[0]['group_id'] == scope.group_id
                assert rows[0]['content'] == record.text and rows[0]['state'] == 'complete'
                assert hashlib.sha256(rows[0]['content'].encode()).hexdigest() == record.text_sha256
                assert rows[0]['fingerprint'] == data['fingerprint']
                assert rows[0]['evidence_ids'] == data['evidence_ids']
                before = len(records)
                assert _exchange(port, 'POST', route, payload, token=token)[2]['data'] == data
                assert len(records) == before and errors == []
                _assert_accounted(prep, index + 1)
                selected.ingestion.append(data)
        assert len({data['operation_id'] for data in selected.ingestion}) == 4
        assert len({data['episode_id'] for data in selected.ingestion}) == 4
        assert records and errors == []
        assert {row['model'] for row in records} == {'fixture-generation', 'fixture-embedding'}
        pg = conninfo_to_dict(os.environ['PROJECT_STORE_POSTGRES_TEST_DSN'])
        child = dict(KNOWLEDGE_PRINCIPAL='owner', KNOWLEDGE_DISPLAY_GRAPH_ID=prep.display_graph_id,
            KNOWLEDGE_BOUND_SCOPE_JSON=scope.model_dump_json(), KNOWLEDGE_PG_HOST=pg['host'],
            KNOWLEDGE_PG_PORT=pg['port'], KNOWLEDGE_PG_DATABASE=pg['dbname'],
            KNOWLEDGE_PG_USER=pg['user'], KNOWLEDGE_PG_PASSWORD=pg['password'],
            KNOWLEDGE_NEO4J_URI='bolt://127.0.0.1:17687', KNOWLEDGE_NEO4J_USER='neo4j',
            KNOWLEDGE_NEO4J_PASSWORD=os.environ['KNOWLEDGE_TEST_PASSWORD'])
        settings = replace(prep.settings, python=os.environ['KNOWLEDGE_PYTHON'],
            bootstrap=os.environ['KNOWLEDGE_BOOTSTRAP_SCRIPT'], child_environment=child)
        reader = KnowledgeReadFacade(settings)
        before_reads = len(records)
        graph = reader.graph_data(prep.display_graph_id)
        assert graph['graph_id'] == prep.display_graph_id and graph['nodes'] and graph['edges']
        expected = {p['evidence_id'] for source in selected.snapshots for p in source['passages']}
        linked = {uid for fact in graph['nodes'] + graph['edges'] for uid in fact['evidence_ids']}
        assert linked == expected
        assert all({p['evidence_id'] for p in source['passages']} & linked for source in selected.snapshots)
        assert len(records) == before_reads and errors == []
        _assert_accounted(prep, 4)
        prep.settings, prep.reader = settings, reader
        selected.graph = graph
        selected.model_records, selected.model_errors = records, errors
        selected.ingestion_calls = before_reads
        yield selected
        # All later graph reads/reauthorization/recovery are read-only and never reingest.
        assert len(records) == before_reads and errors == []

@pytest.fixture(scope='module')
def factory():
    from test_native_launch_store import factory as actual_factory
    return actual_factory.__wrapped__()


@pytest.mark.asyncio
@pytest.mark.parametrize('slow_authorization', [False, True], ids=['ordinary', 'cooperative-heartbeat'])
async def test_actual_native_receipt_to_inherited_report_preserves_inputs_and_disabled_reads(factory, tmp_path, monkeypatch, slow_authorization):
    from test_connected_preparation_native_launch import RichConnectedChat
    from test_preparation_store import real_host, request, reference as prep_reference
    from test_native_launch_store import migrate_all, launch_host, declaration
    from test_native_launch_api import reference as native_reference
    from test_temporal_connected_launch import loopback_client, temporal_bridge
    from nexaweave_execution.temporal_preparation_host import TemporalPreparationHost
    from nexaweave_execution.temporal_native_host import TemporalNativeHost, NativeWorkflowRef
    from nexaweave_execution.temporal_report import TemporalReportHost
    from nexaweave_execution.report_store import migrate as migrate_reports
    from app.services.durable_report_host import DurableReportHost
    from app.services.report_models import BoundedReportModelFactory
    from app.services.connected_report_client import digest, validate_read, validate_download, ReportError

    assert not (_actual_graph_journey and slow_authorization)
    seed_types = ['Person', 'Organization']
    class SeedChat(RichConnectedChat):
        def create(self, **kwargs):
            response = super().create(**kwargs)
            if 'hot_topics' in kwargs['messages'][-1]['content']:
                response.choices[0].message.content = json.dumps(dict(hot_topics=['recorded evidence'],
                    narrative_direction='Offline native-to-report binding', initial_posts=[
                        dict(poster_type=seed_types[0], content='Repeated seed 猫 😀'),
                        dict(poster_type=seed_types[1], content='Organisation B'),
                        dict(poster_type=seed_types[0], content='Repeated seed 猫 😀'),
                        dict(poster_type=seed_types[1], content='Organisation D'),
                        dict(poster_type=seed_types[0], content='Final same actor E')]), ensure_ascii=False)
            return response

    phases = FixturePhases('report-cooperative-heartbeat' if slow_authorization else 'report-ordinary')
    phases.start('setup')
    monkeypatch.chdir(tmp_path)
    migrate_all(factory)
    with factory() as conn:
        migrate_reports(conn)
    temporal = await loopback_client()
    input_artifacts = tmp_path/'native-inputs'
    input_artifacts.mkdir()
    prep, source_scope, retained, chat, _, projection_transport = real_host(
        factory, input_artifacts, cap=12, chat=SeedChat(), ceiling=2 if _actual_graph_journey else 4)
    with ExitStack() as actual_graph_lifecycle:
        selected = None
        if _actual_graph_journey:
            selected = await asyncio.to_thread(actual_graph_lifecycle.enter_context,
                _actual_graph_sources(factory, tmp_path, monkeypatch, prep, source_scope))
        loop = asyncio.get_running_loop()
        preparation = TemporalPreparationHost(client=temporal, task_queue='mf-report-prep-'+uuid4().hex,
            trusted_host=prep, allow_dispatch=lambda: True)
        prep.scheduler = preparation.scheduler_for(loop)
        native_host = launch_host(prep, factory, ceiling=2 if _actual_graph_journey else 4)
        native_owners = []
        def construct(native_request):
            owner = native_host.supervisor_factory(native_request)
            native_owners.append(owner)
            return owner
        native = TemporalNativeHost(client=temporal, task_queue='mf-report-native-'+uuid4().hex,
            trusted_principal='owner', supervisor_factory=construct, allow_dispatch=lambda: True)
        native_host.attach_temporal(native, temporal_bridge(native, loop))
        native_row = None
        report_enabled = [True]
        report_log = tmp_path/'actual-report-sdk-requests.jsonl'
        limits = dict(max_calls=64, max_input_bytes=262144, max_output_tokens=4096, max_run_seconds=120)
        report_reader = prep.reader
        reader_wait_log = tmp_path/'owned-reader-waits.jsonl'
        slow_counter = tmp_path/'first-authorization-reader-count'
        if slow_authorization:
            # Main fixture replaces only the existing synthetic projection byte
            # transport with actual owned children. It does not claim graph quality.
            from dataclasses import replace
            import sys
            from app.services.knowledge_read_facade import KnowledgeReadFacade
            facts = {}
            for kind in ('node', 'edge'):
                wire = json.dumps(dict(payload=dict(kind=kind), request_id=str(uuid4()))).encode()
                facts[kind] = json.loads(projection_transport.call(wire))['result']['facts']
            bootstrap = tmp_path/'slow-projection-child.py'
            bootstrap.write_text(
                'import json,sys,time\nfrom pathlib import Path\n'
                f'facts={facts!r}\ncounter=Path({str(slow_counter)!r})\nlog=Path({str(reader_wait_log)!r})\n'
                'header=sys.stdin.buffer.read(4)\nrequest=json.loads(sys.stdin.buffer.read(int.from_bytes(header,"big")))\n'
                'remaining=int(counter.read_text()) if counter.exists() else 0\n'
                'if remaining:\n counter.write_text(str(remaining-1))\n start=time.monotonic()\n time.sleep(4.25)\n'
                ' with log.open("a",encoding="utf-8") as stream: stream.write(json.dumps(dict(elapsed=time.monotonic()-start))+"\\n")\n'
                'reply=json.dumps(dict(version=1,request_id=request["request_id"],ok=True,result=dict(schema_version=1,facts=facts[request["payload"]["kind"]],next_cursor=None)),ensure_ascii=False).encode()\n'
                'sys.stdout.buffer.write(len(reply).to_bytes(4,"big")+reply)\nsys.stdout.buffer.flush()\n', encoding='utf-8')
            settings = replace(prep.settings, python=str(Path(sys.executable).resolve()),
                               bootstrap=str(bootstrap), child_environment={})
            report_reader = KnowledgeReadFacade(settings)
        reports = DurableReportHost(settings=prep.settings, connection_factory=factory, read_facade=report_reader,
            native_launch_host=native_host, artifact_root=tmp_path/'reports', account_id=prep.account_id,
            ceiling_microusd=4, authorize=lambda: report_enabled[0], model_label='scripted offline inherited report',
            limits=limits, model_factory=BoundedReportModelFactory(ScriptedReportTransportFactory(str(report_log)),
                'scripted-report', limits), scheduler=lambda _: None)
        report_temporal = TemporalReportHost(client=temporal, task_queue='mf-report-body-'+uuid4().hex,
            trusted_host=reports, allow_dispatch=lambda: report_enabled[0])
        if slow_authorization:
            original_generate = reports.generate
            def generate_with_slow_first_authorization(wire, *, heartbeat=None, cancelled=None):
                slow_counter.write_text('4', encoding='ascii')
                return original_generate(wire, heartbeat=heartbeat, cancelled=cancelled)
            reports.generate = generate_with_slow_first_authorization
        reports.scheduler = report_temporal.scheduler_for(loop)
        try:
            phases.end('setup')
            async with preparation.worker(), native.worker(), report_temporal.worker():
                phases.start('preparation-plan-start')
                payload = request(retained)
                if selected is not None:
                    payload = dict(request(selected.records[0]), schema_version=2, source_revisions=selected.revisions[:])
                payload['options'].update(types=['Person','Organization'], max_agents=2, max_rounds=1)
                planned = await asyncio.to_thread(prep.plan, payload)
                if selected is not None:
                    assert len(planned['actors']) == 2
                    seed_types[:] = [actor['labels'][0] for actor in planned['actors']]
                await asyncio.to_thread(prep.start, prep_reference(planned))
                preparation_row = prep.store.get('owner', planned['operation_id'])
                if selected is not None:
                    assert planned['schema_version'] == 2
                    assert planned['source'] == planned['sources'][0]
                    assert planned['sources'] == [{key: source[key] for key in
                        ('source_revision', 'source_name', 'source_sha256')} for source in selected.snapshots]
                    assert preparation_row.frozen['sources'] == selected.snapshots
                    assert preparation_row.frozen['request']['source_revisions'] == selected.revisions
                phases.end('preparation-plan-start')
                phases.start('preparation-result')
                await asyncio.wait_for(temporal.get_workflow_handle(preparation_row.dispatch.workflow_id).result(), 60)
                ready = await asyncio.to_thread(prep.status, prep_reference(planned))
                assert ready['state']=='ready' and not ready['simulation_executed']
                phases.end('preparation-result')
                input_root = prep.root/ready['receipt']['simulation_id']
                original_inputs = {f['name']:(input_root/f['name']).read_bytes() for f in ready['receipt']['files']}
                if selected is not None:
                    assert prep.store.get('owner', planned['operation_id']).frozen['sources'] == selected.snapshots
                    grounding = json.loads(original_inputs['source_grounding.json'])
                    expected_evidence = {p['evidence_id'] for source in selected.snapshots for p in source['passages']}
                    projected = {node['uuid']: node for node in preparation_row.frozen['graph']['nodes']}
                    assert set(grounding) == {actor['source_entity_uuid'] for actor in planned['actors']}
                    for uid, actor in grounding.items():
                        assert actor['source_entity_uuid'] == uid
                        assert actor['evidence_ids'] == projected[uid]['evidence_ids']
                        assert set(actor['evidence_ids']) <= expected_evidence
                    linked = {uid for fact in preparation_row.frozen['graph']['nodes'] +
                              preparation_row.frozen['graph']['edges'] for uid in fact['evidence_ids']}
                    assert linked == expected_evidence
                    from nexaweave_execution.preparation_contracts import PreparedBudgetReceipt
                    from uuid import UUID
                    with factory() as conn:
                        capacity = prep.budget._row(conn, prep.account_id, UUID(planned['operation_id']))
                    assert capacity.ceiling_microusd == 2 and type(capacity.receipt) is PreparedBudgetReceipt
                    assert capacity.state.value == 'settled'
                    assert capacity.receipt.operation_id == UUID(planned['operation_id'])
                    assert capacity.receipt.attempt_id == capacity.attempt_id
                    assert capacity.receipt.fingerprint == capacity.fingerprint
                    assert capacity.receipt.artifact_sha256 == ready['receipt']['artifact_sha256']
                    _assert_accounted(prep, 6)
                phases.start('native-plan-start')
                launch = await asyncio.to_thread(native_host.plan, declaration(planned))
                await asyncio.to_thread(native_host.start, native_reference(launch))
                native_row = native_host.store.get('owner', launch['request']['run_id'])
                if selected is not None:
                    assert native_row.frozen['identity']['preparation'] == launch['preparation']
                    assert launch['preparation']['operation_id'] == planned['operation_id']
                    assert launch['preparation']['plan_sha256'] == planned['plan_sha256']
                    assert native_row.request.artifact_sha256 == ready['receipt']['artifact_sha256']
                    assert native_row.request.simulation_id == ready['receipt']['simulation_id']
                phases.end('native-plan-start')
                phases.start('native-result')
                native_receipt = await asyncio.wait_for(native.result(native_row.request, NativeWorkflowRef(**native_row.workflow)), 180)
                assert native_receipt.outcome=='completed' and len(native_owners)==1
                launch = await asyncio.to_thread(native_host.status, native_reference(launch))
                assert launch['state']=='completed' and launch['receipt']==native_receipt.to_wire()
                if selected is not None:
                    from nexaweave_execution.native_launch_contracts import NativeBudgetReceipt
                    with factory() as conn:
                        capacity = prep.budget._row(conn, prep.account_id, native_row.run_id)
                    assert capacity.ceiling_microusd == 2 and type(capacity.receipt) is NativeBudgetReceipt
                    assert capacity.state.value == 'settled' and capacity.receipt.native_receipt == native_receipt
                    assert capacity.receipt.operation_id == native_row.run_id
                    assert capacity.receipt.attempt_id == capacity.attempt_id
                    assert capacity.receipt.launch_sha256 == launch['launch_sha256']
                    assert capacity.receipt.fingerprint == capacity.fingerprint
                    _assert_accounted(prep, 8)
                phases.end('native-result')
                original_outputs = {name:(input_root/name).read_bytes() for platform in ('twitter','reddit')
                    for name in (platform+'_simulation.db', platform+'/actions.jsonl')}
                phases.start('report-plan-start')
                declared = dict(schema_version=1, report_id=str(uuid4()), launch_id=str(native_row.run_id),
                    launch_sha256=launch['launch_sha256'], requirement='Distinguish retained source and actual observed records.',
                    output_language='en', native_windows=None)
                if selected is not None:
                    declared.update(schema_version=2, source_revisions=selected.revisions[:])
                reviewed = await asyncio.to_thread(reports.plan, declared)
                assert reviewed['state']=='planned' and not report_log.exists()
                reference = dict(schema_version=1, report_id=reviewed['report_id'], plan_sha256=reviewed['plan_sha256'])
                await asyncio.to_thread(reports.start, reference)
                row = reports.store.get('owner', reference['report_id'], reference['plan_sha256'])
                if selected is not None:
                    assert row.frozen['context']['schema_version'] == 2
                    assert row.frozen['context']['sources'] == selected.snapshots
                    assert row.frozen['identity']['binding']['source_set'] == dict(
                        schema_version=1, source_revisions=selected.revisions)
                    assert row.frozen['identity']['binding']['preparation'] == launch['preparation']
                    assert row.frozen['identity']['binding']['native']['run_id'] == str(native_row.run_id)
                    assert row.frozen['identity']['context_sha256'] == digest(row.frozen['context'])
                phases.end('report-plan-start')
                phases.start('report-result-proof')
                await asyncio.wait_for(temporal.get_workflow_handle(row.workflow['workflow_id']).result(), 180)
                completed = await asyncio.to_thread(reports.status, reference)
                assert completed['state']=='completed' and completed['cleanup']==dict(known=True,pending=False,owner_thread_alive=False)
                assert completed['receipt']['manifest_sha256']==digest(completed['manifest'])
                assert completed['receipt_sha256']==digest(completed['receipt'])
                assert completed['receipt']['semantic_support_status']=='not_reviewed'
                if slow_authorization:
                    waits = [json.loads(line)['elapsed'] for line in reader_wait_log.read_text().splitlines()]
                    assert len(waits) == 4 and sum(waits) > 15
                    assert all(4.25 <= seconds < 15 for seconds in waits)
                    assert slow_counter.read_text() == '0'
                    # Real Temporal server accepted the unchanged 15-second
                    # heartbeat workflow while initial full authorization exceeded it.
                    from temporalio.api.enums.v1 import EventType
                    history = await temporal.get_workflow_handle(row.workflow['workflow_id']).fetch_history()
                    assert not any(event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT
                                   for event in history.events)
                from nexaweave_execution.report_contracts import ReportBudgetReceipt, budget_episode, budget_fingerprint
                from nexaweave_execution.budget import ReservationState
                with factory() as conn:
                    report_capacity = native_host.budget._row(conn, prep.account_id, row.report_id)
                assert report_capacity.state == ReservationState.settled and type(report_capacity.receipt) is ReportBudgetReceipt
                assert report_capacity.attempt_id == row.attempt_id and report_capacity.receipt.operation_id == row.report_id
                assert report_capacity.receipt.attempt_id == row.attempt_id and report_capacity.receipt.plan_sha256 == completed['plan_sha256']
                assert report_capacity.fingerprint == report_capacity.receipt.fingerprint == budget_fingerprint(completed['plan_sha256'])
                assert report_capacity.episode_id == budget_episode(report_capacity.scope_group_id, row.report_id)
                assert report_capacity.evidence_ids == () and report_capacity.ceiling_microusd == 4
                assert report_capacity.receipt.report_receipt == completed['receipt']
                assert report_capacity.receipt.report_receipt_sha256 == completed['receipt_sha256']
                assert ReportBudgetReceipt.from_wire(report_capacity.receipt.json_value()) == report_capacity.receipt
                if selected is not None:
                    _assert_accounted(prep, 12)
                calls = [json.loads(line) for line in report_log.read_text(encoding='utf-8').splitlines()]
                assert calls[-1]['closed'] and 1<=calls[-1]['calls']<=64 and any(c.get('response_format') for c in calls)
                phases.end('report-result-proof')
                phases.start('report-protected-reads-exports-recovery')
                report_enabled[0]=False
                before_calls=report_log.read_bytes()
                before_budget=native_host.budget.status('owner',prep.account_id)
                assert before_budget.accounted_ceiling_microusd==12
                assert before_budget.remaining_microusd == 0 and before_budget.actual_usage_microusd is None
                before_journal=reports.store.get('owner',reference['report_id'],reference['plan_sha256'])
                read=await asyncio.to_thread(reports.read,reference)
                validate_read(read,prep.display_graph_id,reports.scope_dto,reference,'owner')
                assert read['report']['receipt']==completed['receipt'] and '[[source:' in read['content'] and '[[native:' in read['content']
                assert not read['report']['authorization']['model_calls_enabled']
                for kind in ('report','outline','evidence','native_evidence','metadata','section'):
                    body=dict(reference,kind=kind,section_index=1 if kind=='section' else None)
                    downloaded=await asyncio.to_thread(reports.download,body)
                    validate_download(downloaded,body,completed)
                    artifact=downloaded['artifact'];raw=base64.b64decode(artifact['content_base64'],validate=True)
                    assert len(raw)==artifact['size'] and hashlib.sha256(raw).hexdigest()==artifact['sha256']
                    if selected is not None and kind == 'native_evidence':
                        evidence = json.loads(raw)
                        assert evidence['schema_version'] == 2
                        assert evidence['source_set'] == dict(schema_version=1, source_revisions=selected.revisions)
                        assert evidence['sources'] == [{key: source[key] for key in
                            ('source_revision', 'source_name', 'source_sha256', 'passages')} for source in selected.snapshots]
                        assert evidence['context_sha256'] == digest(row.frozen['context'])
                        assert evidence['native'] == row.frozen['identity']['binding']['native']
                        assert {key[7:] for key in evidence['reference_keys'] if key.startswith('source:')} == expected_evidence
                assert reports.store.get('owner',reference['report_id'],reference['plan_sha256'])==before_journal
                assert native_host.budget.status('owner',prep.account_id)==before_budget and report_log.read_bytes()==before_calls
                assert all((input_root/name).read_bytes()==raw for name,raw in {**original_inputs,**original_outputs}.items())
                recovered=await asyncio.to_thread(reports.start,reference)
                assert recovered['receipt']==completed['receipt'] and report_log.read_bytes()==before_calls
                if selected is not None:
                    assert reports.store.get('owner', reference['report_id']).frozen['context']['sources'] == selected.snapshots
                    assert prep.store.get('owner', planned['operation_id']).frozen['sources'] == selected.snapshots
                    await asyncio.to_thread(_assert_four_format_restart, factory, tmp_path, prep, source_scope, selected)
                    assert native_host.budget.status('owner', prep.account_id) == before_budget
                    assert report_log.read_bytes() == before_calls
                phases.end('report-protected-reads-exports-recovery')
                phases.start('foreign-report-read-refusal')
                foreign=dict(reference,report_id=str(uuid4()))
                with pytest.raises(ReportError):
                    await asyncio.to_thread(reports.read,foreign)
                assert report_log.read_bytes()==before_calls
                phases.end('foreign-report-read-refusal')
        finally:
            for owner in native_owners:
                assert await asyncio.to_thread(owner.close,20)
            if native_row is not None:
                local=await native.retry_cleanup(native_row.request)
                assert not local.cleanup_pending and not local.owner_thread_alive
            phases.observed_closed()

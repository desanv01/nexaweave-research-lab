"""Pure authority/order/read-only boundaries; actual PG lives in service tests."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID
import hashlib
import pytest
from test_connected_report_client import public_result, reference, declaration, fixture_context


def file_host(tmp_path):
    from app.services.durable_report_host import DurableReportHost
    from app.services.connected_report_client import encoded, digest
    dto, text = public_result('completed')
    root = tmp_path / UUID(dto['report_id']).hex / 'output' / dto['report_id']
    root.mkdir(parents=True)
    for file in dto['manifest']['files']:
        raw = text.encode() if file['name'].endswith('.md') else encoded(dict(schema_version=1))
        (root / file['name']).write_bytes(raw)
        file.update(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    dto['receipt']['manifest_sha256'] = digest(dto['manifest']); dto['receipt_sha256'] = digest(dto['receipt'])
    row = SimpleNamespace(report_id=UUID(dto['report_id']), plan_sha256=dto['plan_sha256'], state='completed', manifest=dto['manifest'], receipt=dto['receipt'],
        frozen=dict(identity={k: dto[k] for k in ('schema_version', 'report_id', 'binding', 'options', 'context_sha256',
            'source_projection_sha256', 'model_label', 'limits', 'ceiling_microusd')}, context=fixture_context()),
        public=lambda auth=None: deepcopy(dto))
    host = object.__new__(DurableReportHost)
    host.root, host.principal, host.display_graph_id, host.scope_dto = tmp_path, 'owner', 'display-1', dto['binding']['scope']
    host.store = SimpleNamespace(get=lambda *args: row)
    host.authorization = lambda r=None: dict(model_calls_enabled=False, budget_configured=True)
    host._reauthorize = lambda r: r
    def forbidden(*args, **kwargs):
        pytest.fail('read/download touched a model, poll, scheduler or budget mutation')
    host.model_factory, host.scheduler, host.authorize = forbidden, forbidden, forbidden
    host.store.recover_expired = host.store.queue = host.store.finish = host.store.cancel = forbidden
    return host, row, dto, root


def test_disabled_read_download_no_recovery_mutations_and_exact_hashes(tmp_path):
    from app.services.connected_report_client import validate_download
    host, row, dto, root = file_host(tmp_path)
    payload = reference(dto)
    result = host.read(payload)
    assert result['content'].encode() == (root / 'full_report.md').read_bytes()
    assert result['report']['authorization']['model_calls_enabled'] is False
    for kind in ('report', 'outline', 'evidence', 'native_evidence', 'metadata', 'section'):
        request = dict(payload, kind=kind, section_index=1 if kind == 'section' else None)
        download = host.download(request)
        assert validate_download(download, request, dto) == download


def test_changed_output_or_manifest_never_returns_content(tmp_path):
    from app.services.connected_report_client import ReportError
    host, row, dto, root = file_host(tmp_path)
    (root / 'section_01.md').write_bytes(b'changed')
    # Even a metadata download verifies every declared output, not only selection.
    with pytest.raises(ReportError):
        host.download(dict(reference(dto), kind='metadata', section_index=None))
    with pytest.raises(ReportError):
        host.read(reference(dto))


@pytest.mark.parametrize('state', ['planned', 'queued', 'generating', 'failed', 'cancelled', 'uncertain'])
def test_noncompleted_refused_before_report_file_access(tmp_path, state):
    from app.services.connected_report_client import ReportError
    host, row, dto, root = file_host(tmp_path); row.state = state
    with pytest.raises(ReportError) as error:
        host.read(reference(dto))
    assert error.value.code == 'conflict'


def test_start_lost_scheduler_reply_keeps_same_id_fenced(tmp_path):
    from app.services.durable_report_host import DurableReportHost
    from app.services.connected_report_client import ReportError, digest
    dto, _ = public_result()
    row = SimpleNamespace(report_id=UUID(dto['report_id']), plan_sha256=dto['plan_sha256'], state='planned',
        attempt_id=UUID(int=99), frozen=dict(identity=dto), public=lambda auth=None: deepcopy(dto))
    host = object.__new__(DurableReportHost)
    host.principal, host.display_graph_id, host.scope_dto, host.account_id = 'owner', 'display-1', dto['binding']['scope'], UUID(int=20)
    host._row = lambda payload: row
    host._reauthorize = lambda value: value
    host.authorization = lambda value=None: dict(model_calls_enabled=True, budget_configured=True)
    calls = []
    def queue(*args):
        row.state = 'queued'; calls.append('admission'); return row, True
    def schedule(wire):
        calls.append('scheduler'); raise TimeoutError('lost reply, secret')
    def finish(*args, **kwargs):
        row.state = 'uncertain'; dto.update(state='uncertain', error_code='report_uncertain')
        calls.append(('retained', kwargs['cleanup'])); return row
    host.store = SimpleNamespace(queue=queue, finish=finish)
    host.scheduler = schedule
    with pytest.raises(ReportError) as error:
        host.start(reference(dto))
    assert error.value.code == 'report_uncertain'
    assert host.start(reference(dto))['state'] == 'uncertain'
    assert calls == ['admission', 'scheduler', ('retained', dict(known=False, pending=None, owner_thread_alive=None))]


def test_fresh_report_reauthorization_ticks_and_still_refuses_changed_context(monkeypatch):
    from contextlib import contextmanager
    from app.services import durable_report_host as module
    from app.services.connected_report_client import ReportError
    dto, _ = public_result()
    row = SimpleNamespace(frozen=dict(identity={key: dto[key] for key in (
        'schema_version', 'report_id', 'binding', 'options', 'context_sha256',
        'source_projection_sha256', 'model_label', 'limits', 'ceiling_microusd')},
        context=fixture_context(), declaration=declaration()))
    host = object.__new__(module.DurableReportHost)
    host.observations, host.reader = object(), object()
    ticks, seen = [], []
    @contextmanager
    def frozen(*args, **kwargs):
        assert callable(kwargs['tick'])
        seen.append(kwargs['deadline'])
        kwargs['tick']()
        changed = deepcopy(row.frozen['context'])
        changed['graph']['nodes'][0]['name'] = 'changed'
        yield changed
    monkeypatch.setattr(module, 'freeze_context', frozen)
    with pytest.raises(ReportError) as caught:
        host._reauthorize(row, deadline=123, tick=lambda: ticks.append(True))
    assert caught.value.code == 'conflict' and seen == [123] and len(ticks) >= 2


def test_report_context_translates_private_reader_abort_after_cooperative_tick(monkeypatch):
    import time
    from app.services import connected_report_context as context_module
    from app.services import knowledge_transport
    from app.services.knowledge_read_facade import KnowledgeReadFacade
    from app.services.connected_report_client import ReportError
    context = fixture_context()
    prep = SimpleNamespace(frozen=dict(public=dict(source=context['binding']['source'])), project_revision=1)
    row = SimpleNamespace(request=SimpleNamespace(platforms=['twitter', 'reddit']))
    preparation = SimpleNamespace(_owned=lambda *args, **kwargs: (None, None, None))
    launch = SimpleNamespace(_row=lambda reference: row, preparation=preparation)
    observations = SimpleNamespace(launch=launch, display_graph_id='display-1',
        _authority=lambda reference: (row, prep, None, None))
    read_facade = object.__new__(KnowledgeReadFacade)
    read_facade._client_factory = None
    read_facade._settings = SimpleNamespace(python='unused', bootstrap='unused',
        child_environment={}, scope=context['binding']['scope'], display_graph_id='display-1')
    armed, ticks = [], []
    class AbortClient:
        def __init__(self, *args, **kwargs):
            self.tick = kwargs['cooperative_tick']
        def call(self, raw):
            armed.append(True)
            self.tick()
            pytest.fail('cooperative tick must abort before reader reply')
    monkeypatch.setattr(knowledge_transport, 'KnowledgeProcessClient', AbortClient)
    def tick():
        ticks.append(True)
        if armed:
            raise ReportError('report_cancelled')
    with pytest.raises(ReportError) as caught:
        with context_module.freeze_context(observations, read_facade, declaration(),
                deadline=time.monotonic() + 20, tick=tick):
            pytest.fail('cancelled projection cannot publish context')
    assert caught.value.code == 'report_cancelled' and armed == [True] and len(ticks) >= 4


def test_generation_ticks_initial_go_first_and_both_publication_authorizations(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from app.services import report_process as process_module
    from app.services.durable_report_host import DurableReportHost
    dto, prose = public_result('completed')
    row = SimpleNamespace(report_id=UUID(dto['report_id']), plan_sha256=dto['plan_sha256'],
        cancel_requested=False, frozen=dict(identity={key: dto[key] for key in (
            'schema_version', 'report_id', 'binding', 'options', 'context_sha256',
            'source_projection_sha256', 'model_label', 'limits', 'ceiling_microusd')},
            context=fixture_context()), public=lambda authorization=None: deepcopy(dto))
    host = object.__new__(DurableReportHost)
    host.principal, host.root, host.model_factory = 'owner', tmp_path, None
    host._row = lambda wire: row
    host.authorization = lambda current: dict(model_calls_enabled=True, budget_configured=True)
    authorizations, heartbeats, first = [], [], []
    def reauthorize(current, *, deadline, tick):
        assert current is row and deadline is not None and callable(tick)
        authorizations.append(len(authorizations))
        tick()
        return current
    host._reauthorize = reauthorize
    def finish(*args, **kwargs):
        assert kwargs['state'] == 'completed'
        kwargs['verify_output']()
        return row
    host.store = SimpleNamespace(claim=lambda *args: row, first_request=lambda *args: first.append(True), finish=finish)
    class Process:
        def __init__(self, **kwargs):
            assert kwargs['frozen'] is row.frozen
        def run(self, **kwargs):
            kwargs['reauthorize']()  # READY/GO
            kwargs['reauthorize']()  # FIRST/ADMITTED
            kwargs['first_call']()
            return process_module.ProcessOutcome('completed',
                dict(known=True, pending=False, owner_thread_alive=False), None, dto['manifest'])
    @contextmanager
    def files(root, manifest):
        assert manifest == dto['manifest']
        yield {'full_report.md': prose.encode(), 'section_01.md': prose.encode()}
    monkeypatch.setattr(process_module, 'ReportProcess', Process)
    monkeypatch.setattr(process_module, 'report_files', files)
    wire = dict(schema_version=1, report_id=dto['report_id'], plan_sha256=dto['plan_sha256'],
        attempt_id=str(UUID(int=99)))
    result = host.generate(wire, heartbeat=lambda: heartbeats.append(True), cancelled=lambda: False)
    assert result['state'] == 'completed' and authorizations == list(range(5))
    assert len(heartbeats) >= 5 and first == [True]


def test_context_reader_preserves_repeats_lexemes_crlf_and_rechecks_full_manifest(tmp_path):
    from app.services.native_observation_reader import NativeObservationReader
    from app.services.native_observations_client import NativeObservationsError
    from app.services.connected_report_client import digest
    root = tmp_path / 'native'; root.mkdir()
    raws = ['{"event_type":"post","unknown":1.00}', '{"unknown":null,"event_type":"post"}', '{"event_type":"post","unknown":1.00}']
    files = []
    for p in ('twitter', 'reddit'):
        (root / p).mkdir()
        for name, raw in ((p + '_simulation.db', b'db'), (p + '/actions.jsonl', ('\r\n'.join(raws)+'\r\n').encode())):
            (root / name).write_bytes(raw)
            files.append(dict(name=name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
    manifest = dict(schema_version=1, files=files); reader = NativeObservationReader()
    with reader.context(root, ('twitter', 'reddit'), None, digest(manifest)) as complete:
        assert [r['raw_json'] for r in complete['records']['twitter']] == raws
        assert [r['index'] for r in complete['records']['twitter']] == [0, 1, 2]
        assert complete['records']['twitter'][0]['record_sha256'] == complete['records']['twitter'][2]['record_sha256']
        assert all(c['complete'] for c in complete['coverage'])
    with reader.context(root, ('twitter', 'reddit'), [dict(platform='reddit', offset=1, count=1)], digest(manifest)) as partial:
        assert partial['records']['twitter'] == [] and partial['records']['reddit'][0]['index'] == 1
    with pytest.raises(NativeObservationsError):
        with reader.context(root, ('twitter', 'reddit'), None, digest(manifest)):
            (root / 'twitter_simulation.db').write_bytes(b'changed native')

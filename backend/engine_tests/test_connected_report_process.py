"""Actual spawned inherited-agent bodies, scripted cooperative SDK only.

Main selects this module with backend/tests also on its explicit fixture path.
No execution, SDK import, skip or provider construction occurs at collection.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
from uuid import uuid4
import pytest


@dataclass(frozen=True)
class ScriptedReportTransportFactory:
    mode: str = 'ok'
    sections: int = 2
    trace_path: str | None = None

    def __call__(self, *, max_retries, timeout):
        assert max_retries == 0 and 0 < timeout <= 15
        return ScriptedReportTransport(self.mode, self.sections, self.trace_path)


class ScriptedReportTransport:
    max_retries = 0

    def __init__(self, mode, sections, trace_path):
        self.mode, self.sections, self.trace_path = mode, sections, trace_path
        self.calls, self.section_call = 0, 0
        self.chat = SimpleNamespace(completions=self)

    def _trace(self, event, **fields):
        if self.trace_path is not None:
            with open(self.trace_path, 'a', encoding='utf-8') as handle:
                handle.write(json.dumps(dict(event=event, pid=os.getpid(), cwd=os.getcwd(), **fields)) + '\n')

    def create(self, **kwargs):
        self.calls += 1
        assert kwargs['stream'] is False and 0 < kwargs['timeout'] <= 15
        assert kwargs.get('max_tokens', kwargs.get('max_completion_tokens')) <= 4096
        system = kwargs['messages'][0]['content']
        self._trace('request', ordinal=self.calls, native_channel='recorded_native_events' in system,
                    language=next((lang for lang in ('English', 'Chinese', 'Malay') if 'write in ' + lang in system), None))
        if self.mode == 'transport_failure':
            error = RuntimeError('private provider error')
            error.status_code = 400; error.body = dict(error=dict(param='response_format'))
            raise error
        if kwargs.get('response_format'):
            content = json.dumps(dict(title='Recorded observations', summary='Local scripted research',
                sections=[dict(title='Observations %d' % i) for i in range(1, self.sections+1)]))
        else:
            self.section_call += 1
            turn = (self.section_call - 1) % 4
            native = re.search(r'native:(twitter|reddit):(\d+):[0-9a-f]{64}', system)
            source = re.search(r'source:[0-9a-f-]{36}', system)
            assert native is not None and source is not None
            if turn == 0:
                content = '<tool_call>' + json.dumps(dict(name='recorded_native_events', parameters=dict(
                    platform=native.group(1), offset=int(native.group(2)), limit=1))) + '</tool_call>'
            elif turn in (1, 2):
                content = '<tool_call>' + json.dumps(dict(name='quick_search', parameters=dict(query='rain observations', limit=2))) + '</tool_call>'
            elif self.mode == 'invalid_reference':
                content = 'Final Answer: Invented [[native:reddit:0:' + 'f'*64 + ']]'
            elif self.mode == 'failed_section':
                content = ''
            else:
                content = ('Final Answer: Source evidence [[' + source.group(0) + ']]. '
                    'Recorded native observation [[' + native.group(0) + ']]. Interpretation remains unreviewed.')
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason='stop')])

    def close(self):
        self._trace('close', calls=self.calls)


@dataclass(frozen=True)
class AdmissionTraceFactory(ScriptedReportTransportFactory):
    def __call__(self, *, max_retries, timeout):
        transport = super().__call__(max_retries=max_retries, timeout=timeout)
        transport._trace('construct')
        return transport


def admission_fixture(tmp_path, *, run_seconds=120):
    import hashlib
    import pickle
    from app.services.report_models import BoundedReportModelFactory
    frozen, original = frozen_report()
    limits = dict(original.limits, max_run_seconds=run_seconds)
    frozen['identity']['limits'] = limits
    root = tmp_path / 'admission'; root.mkdir()
    trace = root / 'work' / 'admission_trace.jsonl'
    factory = BoundedReportModelFactory(AdmissionTraceFactory(trace_path=str(trace)), original.model_name, limits)
    frozen['configuration']['factory_sha256'] = hashlib.sha256(pickle.dumps(factory)).hexdigest()
    return frozen, factory, root, trace


def admission_trace(trace):
    return [json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines()] if trace.exists() else []


def frozen_report(*, language='en', report_id=None):
    from test_connected_report_client import public_result, fixture_context, declaration
    from app.services.connected_report_client import IDENTITY, digest
    from app.services.report_models import BoundedReportModelFactory
    import hashlib
    import pickle
    dto, _ = public_result()
    identity = {k: dto[k] for k in IDENTITY}
    identity['report_id'] = report_id or str(uuid4())
    identity['options']['output_language'] = language
    factory = BoundedReportModelFactory(ScriptedReportTransportFactory(), 'scripted-local', identity['limits'])
    payload = declaration(); payload.update(report_id=identity['report_id'], output_language=language)
    return dict(identity=identity, declaration=payload, context=fixture_context(),
        configuration=dict(account_id=str(uuid4()), factory_sha256=hashlib.sha256(pickle.dumps(factory)).hexdigest())), factory


@pytest.mark.parametrize('language', ['en', 'zh', 'ms'])
def test_actual_inherited_child_react_assembly_native_refs_locale_and_isolation(tmp_path, language):
    from app.services.report_process import ReportProcess, report_files
    from app.services.report_agent import ReportManager
    from app.utils.locale import get_locale
    from app.services.connected_report_client import digest
    frozen, factory = frozen_report(language=language)
    root = tmp_path / 'op'; root.mkdir()
    before_cwd, before_root, before_locale = os.getcwd(), ReportManager.REPORTS_DIR, get_locale()
    first, progress = [], []
    outcome = ReportProcess(frozen=frozen, model_factory=factory, operation_root=root).run(
        checkpoint=lambda: None, first_call=lambda: first.append(True), progress=progress.append, cancelled=lambda: False)
    assert outcome.state == 'completed' and outcome.error_code is None
    assert outcome.cleanup == dict(known=True, pending=False, owner_thread_alive=False)
    assert first == [True] and progress
    assert (os.getcwd(), ReportManager.REPORTS_DIR, get_locale()) == (before_cwd, before_root, before_locale)
    with report_files(root/'output'/frozen['identity']['report_id'], outcome.manifest) as content:
        assert len(outcome.manifest['files']) == 7
        assert b'[[native:' in content['full_report.md'] and b'[[source:' in content['full_report.md']
        assert json.loads(content['native_evidence.json'])['semantic_support_status'] == 'not_reviewed'
        assert json.loads(content['native_evidence.json'])['context_sha256'] == digest(frozen['context'])
        assert json.loads(content['retrieval_evidence.json'])['schema_version'] == 1
        assert (root/'output'/frozen['identity']['report_id']/'agent_log.jsonl').is_file()
        assert b'Final Answer' not in content['full_report.md']


@pytest.mark.parametrize('mode', ['invalid_reference', 'failed_section', 'transport_failure'])
def test_actual_failed_report_never_publishes_and_keeps_partial_artifacts(tmp_path, mode):
    from app.services.report_process import ReportProcess
    from app.services.report_models import BoundedReportModelFactory
    import hashlib
    import pickle
    frozen, original = frozen_report()
    trace = tmp_path/'trace.jsonl'
    factory = BoundedReportModelFactory(ScriptedReportTransportFactory(mode=mode, trace_path=str(trace)), original.model_name, original.limits)
    frozen['configuration']['factory_sha256'] = hashlib.sha256(pickle.dumps(factory)).hexdigest()
    root = tmp_path/'op'; root.mkdir(); first=[]
    outcome = ReportProcess(frozen=frozen, model_factory=factory, operation_root=root).run(
        checkpoint=lambda: None, first_call=lambda: first.append(True), progress=lambda value: None, cancelled=lambda: False)
    assert outcome.state in ('failed', 'uncertain') and outcome.manifest is None and first == [True]
    assert outcome.cleanup == dict(known=True, pending=False, owner_thread_alive=False)
    assert (root/'output'/frozen['identity']['report_id']/'meta.json').is_file()
    requests = [json.loads(line) for line in trace.read_text().splitlines() if json.loads(line)['event']=='request']
    if mode == 'transport_failure':
        assert len(requests) == 1


def test_actual_late_context_corruption_is_sdk_cold_and_owned_cleanup(tmp_path):
    from app.services.report_process import ReportProcess
    frozen, factory = frozen_report()
    frozen['context']['native_records']['reddit'][-1]['raw_json'] = '{"bad":true}'
    root=tmp_path/'op';root.mkdir();first=[]
    outcome=ReportProcess(frozen=frozen,model_factory=factory,operation_root=root).run(
        checkpoint=lambda:None,first_call=lambda:first.append(True),progress=lambda value:None,cancelled=lambda:False)
    assert not first and outcome.manifest is None and outcome.state=='failed'
    assert outcome.cleanup == dict(known=True,pending=False,owner_thread_alive=False)
    assert not (root/'output').exists()


def test_actual_cancellation_after_first_possible_request_keeps_owned_cleanup(tmp_path):
    from app.services.report_process import ReportProcess
    frozen,factory=frozen_report();root=tmp_path/'op';root.mkdir();first=[]
    outcome=ReportProcess(frozen=frozen,model_factory=factory,operation_root=root).run(
        checkpoint=lambda:None,first_call=lambda:first.append(True),progress=lambda value:None,
        cancelled=lambda:bool(first))
    assert first==[True] and outcome.state=='cancelled' and outcome.manifest is None
    assert outcome.cleanup==dict(known=True,pending=False,owner_thread_alive=False)


def test_actual_separate_operations_never_share_manager_outputs(tmp_path):
    from app.services.report_process import ReportProcess
    outputs=[]
    for index,language in enumerate(('en','ms')):
        frozen,factory=frozen_report(language=language);root=tmp_path/('op%d'%index);root.mkdir()
        outcome=ReportProcess(frozen=frozen,model_factory=factory,operation_root=root).run(
            checkpoint=lambda:None,first_call=lambda:None,progress=lambda value:None,cancelled=lambda:False)
        assert outcome.state=='completed' and not outcome.cleanup['pending']
        outputs.append((root/'output'/frozen['identity']['report_id'],frozen['identity']['report_id']))
    assert outputs[0][1]!=outputs[1][1]
    assert not (outputs[0][0].parent/outputs[1][1]).exists()
    assert not (outputs[1][0].parent/outputs[0][1]).exists()


def test_actual_concurrent_report_children_keep_outputs_sdk_logs_and_parent_globals_isolated(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from copy import deepcopy
    import hashlib
    import pickle
    import threading
    import time
    from app.services.report_process import ReportProcess, report_files, output_manifest
    from app.services.report_models import BoundedReportModelFactory
    from app.services.report_agent import ReportManager
    from app.services.connected_report_context import validate_context, validate_references
    from app.services.connected_report_client import digest, encoded, BASE_NAMES
    from app.utils.locale import get_locale
    before_parent = (os.getcwd(), ReportManager.REPORTS_DIR, get_locale())
    operations = []
    for index, language in enumerate(('en', 'ms')):
        frozen, original_factory = frozen_report(language=language, report_id=str(uuid4()))
        root = tmp_path / ('concurrent%d' % index); root.mkdir()
        # Distinct admitted native hashes make a swapped context/output fail,
        # rather than relying only on identical synthetic source facts.
        context = frozen['context']
        for platform in context['binding']['native']['platforms']:
            raw = json.dumps(dict(event_type='post', operation=frozen['identity']['report_id'], unknown=None), separators=(',', ':'))
            record = context['native_records'][platform][0]
            record.update(raw_json=raw, record_sha256=hashlib.sha256(raw.encode()).hexdigest())
            file = next(f for f in context['native_manifest']['files'] if f['name'] == platform + '/actions.jsonl')
            file.update(size=len((raw+'\n').encode()), sha256=hashlib.sha256((raw+'\n').encode()).hexdigest())
        context['binding']['native']['evidence_sha256'] = digest(context['native_manifest'])
        context['binding']['reference_keys'] = ['source:' + p['evidence_id'] for p in context['passages']] + [
            'native:%s:%d:%s' % (platform, record['index'], record['record_sha256'])
            for platform in context['binding']['native']['platforms'] for record in context['native_records'][platform]]
        assert validate_context(context) == context
        frozen['identity']['binding'] = deepcopy(context['binding'])
        frozen['identity']['context_sha256'] = digest(context)
        trace = root / 'work' / 'transport_trace.jsonl'
        factory = BoundedReportModelFactory(ScriptedReportTransportFactory(trace_path=str(trace)),
            original_factory.model_name, original_factory.limits)
        frozen['configuration']['factory_sha256'] = hashlib.sha256(pickle.dumps(factory)).hexdigest()
        operations.append(dict(frozen=frozen, factory=factory, root=root, trace=trace, first=[], progress=[], before=encoded(frozen)))
    assert operations[0]['frozen']['identity']['report_id'] != operations[1]['frozen']['identity']['report_id']
    assert operations[0]['frozen']['identity']['context_sha256'] != operations[1]['frozen']['identity']['context_sha256']
    first_gate = threading.Barrier(2)
    def run(operation):
        def first():
            operation['first'].append(True)
            # Both actual children reach their first-request handshake before
            # either transport is admitted; the existing handshake is 10s.
            first_gate.wait(timeout=10)
        return ReportProcess(frozen=operation['frozen'], model_factory=operation['factory'], operation_root=operation['root']).run(
            checkpoint=lambda: None, first_call=first, progress=operation['progress'].append, cancelled=lambda: False)
    # One joint bound uses the unchanged whole-run ceiling plus existing20s
    # cleanup; sequential result retrieval does not restart either wait clock.
    deadline = time.monotonic() + max(op['frozen']['identity']['limits']['max_run_seconds'] for op in operations) + 20
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, operation) for operation in operations]
        outcomes = [future.result(timeout=max(0, deadline-time.monotonic())) for future in futures]
    assert (os.getcwd(), ReportManager.REPORTS_DIR, get_locale()) == before_parent
    pids, reports = [], []
    for index, (operation, outcome) in enumerate(zip(operations, outcomes)):
        frozen, root = operation['frozen'], operation['root']
        report_id = frozen['identity']['report_id']
        other_id = operations[1-index]['frozen']['identity']['report_id']
        report_root = root / 'output' / report_id
        assert outcome.state == 'completed' and outcome.error_code is None
        assert outcome.cleanup == dict(known=True, pending=False, owner_thread_alive=False)
        assert operation['first'] == [True] and operation['progress']
        assert encoded(frozen) == operation['before']
        assert list((root/'output').iterdir()) == [report_root]
        assert not (root/'output'/other_id).exists()
        assert outcome.manifest == output_manifest(report_root, 2)
        assert [f['name'] for f in outcome.manifest['files']] == BASE_NAMES + ['section_01.md', 'section_02.md']
        with report_files(report_root, outcome.manifest) as content:
            assert json.loads(content['meta.json'])['report_id'] == report_id
            native = json.loads(content['native_evidence.json'])
            assert native['context_sha256'] == frozen['identity']['context_sha256']
            assert native['native'] == frozen['context']['binding']['native']
            assert native['reference_keys'] == frozen['context']['binding']['reference_keys']
            assert native['records'] == frozen['context']['native_records']
            prose = content['full_report.md'].decode('utf-8')
            assert validate_references(prose, frozen['context'])
            assert all(key not in prose for key in operations[1-index]['frozen']['context']['binding']['reference_keys'] if key.startswith('native:'))
            reports.append(prose)
        logs = [json.loads(line) for line in (report_root/'agent_log.jsonl').read_text(encoding='utf-8').splitlines()]
        assert logs and all(entry['report_id'] == report_id for entry in logs)
        assert other_id not in (report_root/'console_log.txt').read_text(encoding='utf-8')
        trace = [json.loads(line) for line in operation['trace'].read_text(encoding='utf-8').splitlines()]
        requests = [entry for entry in trace if entry['event'] == 'request']
        closes = [entry for entry in trace if entry['event'] == 'close']
        assert [entry['ordinal'] for entry in requests] == list(range(1, 10))
        assert len(closes) == 1 and closes[0]['calls'] == 9
        assert all(entry['native_channel'] and entry['language'] == ('English' if index == 0 else 'Malay') for entry in requests)
        assert all(Path(entry['cwd']) == root/'work' for entry in trace)
        assert len({entry['pid'] for entry in trace}) == 1
        pids.append(trace[0]['pid'])
    assert pids[0] != pids[1] and all(pid != os.getpid() for pid in pids)
    assert reports[0] != reports[1]


@pytest.mark.parametrize('delay', [0, 11], ids=['control0', 'slow11'])
def test_actual_two_phase_admission_keeps_cold_and_request_gates_during_full_authorization(tmp_path, delay):
    import time
    from app.services.report_process import ReportProcess, report_files
    from app.services.connected_report_context import validate_references
    frozen, factory, root, trace = admission_fixture(tmp_path)
    first, stages, progress = [], [], []
    def reauthorize():
        stage = len(stages)
        before = admission_trace(trace)
        assert not any(entry['event'] == 'request' for entry in before)
        assert sum(entry['event'] == 'construct' for entry in before) == stage
        started = time.monotonic()
        time.sleep(delay)
        after = admission_trace(trace)
        assert after == before and not first
        stages.append((stage, time.monotonic()-started))
    outcome = ReportProcess(frozen=frozen, model_factory=factory, operation_root=root).run(
        checkpoint=lambda: None, first_call=lambda: first.append(True), progress=progress.append,
        cancelled=lambda: False, reauthorize=reauthorize)
    assert outcome.state == 'completed' and outcome.error_code is None
    assert outcome.cleanup == dict(known=True, pending=False, owner_thread_alive=False)
    assert first == [True] and [stage for stage, _ in stages] == [0, 1] and progress
    if delay:
        assert all(duration > 10 for _, duration in stages)
    assert frozen['identity']['limits']['max_run_seconds'] == 120
    events = admission_trace(trace)
    assert sum(event['event'] == 'construct' for event in events) == 1
    assert [event['ordinal'] for event in events if event['event'] == 'request'] == list(range(1, 10))
    with report_files(root/'output'/frozen['identity']['report_id'], outcome.manifest) as files:
        assert validate_references(files['full_report.md'].decode('utf-8'), frozen['context'])


@pytest.mark.parametrize('stage', [0, 1], ids=['before-go', 'before-admitted'])
@pytest.mark.parametrize('code', ['conflict', 'unauthorized'], ids=['changed-context', 'denied-authority'])
def test_actual_denied_grant_never_constructs_or_requests_early(tmp_path, stage, code):
    from app.services.report_process import ReportProcess
    from app.services.connected_report_client import ReportError
    frozen, factory, root, trace = admission_fixture(tmp_path)
    calls, first = [], []
    def reauthorize():
        current = len(calls); calls.append(current)
        assert not any(event['event'] == 'request' for event in admission_trace(trace))
        if current == stage:
            raise ReportError(code)
    outcome = ReportProcess(frozen=frozen, model_factory=factory, operation_root=root).run(
        checkpoint=lambda: None, first_call=lambda: first.append(True), progress=lambda value: None,
        cancelled=lambda: False, reauthorize=reauthorize)
    assert outcome.state != 'completed' and outcome.manifest is None and first == []
    assert outcome.error_code == code
    assert outcome.cleanup == dict(known=True, pending=False, owner_thread_alive=False)
    events = admission_trace(trace)
    assert not any(event['event'] == 'request' for event in events)
    assert sum(event['event'] == 'construct' for event in events) == stage
    assert not (root/'output'/frozen['identity']['report_id']/'full_report.md').exists()


class AdmissionParentPipe:
    """Test-owned private frame corruption; actual child and cleanup stay real."""
    def __init__(self, connection, *, stage, fault):
        self.connection, self.stage, self.fault = connection, stage, fault
    def __getattr__(self, name):
        return getattr(self.connection, name)
    def send(self, frame):
        if frame == ('checking', self.stage):
            if self.fault == 'wrong-action':
                frame = ('checking', 'admitted' if self.stage == 'go' else 'go')
            elif self.fault == 'grant-first':
                frame = (self.stage,)
            elif self.fault == 'closed':
                self.connection.close(); return
            elif self.fault == 'missing-checking':
                return
        elif frame == (self.stage,) and self.fault == 'duplicate-checking':
            frame = ('checking', self.stage)
        return self.connection.send(frame)


def controlled_admission_context(monkeypatch, *, stage=None, fault=None):
    import multiprocessing
    original_context = multiprocessing.get_context
    actual = original_context('spawn')
    events = []
    def pipe(*args, **kwargs):
        parent, child = actual.Pipe(*args, **kwargs)
        if fault is not None:
            parent = AdmissionParentPipe(parent, stage=stage, fault=fault)
        return parent, child
    def event():
        value = actual.Event(); events.append(value); return value
    class ControlledContext:
        Pipe = staticmethod(pipe)
        Event = staticmethod(event)
        def __getattr__(self, name):
            return getattr(actual, name)
    controlled = ControlledContext()
    monkeypatch.setattr(multiprocessing, 'get_context',
        lambda method=None: controlled if method in (None, 'spawn') else original_context(method))
    return events


@pytest.mark.parametrize('stage', ['go', 'admitted'], ids=['go', 'admitted'])
@pytest.mark.parametrize('fault', ['wrong-action', 'grant-first', 'duplicate-checking', 'closed'],
                         ids=['wrong-action', 'out-of-order', 'duplicate', 'closed'])
def test_actual_private_invalid_or_closed_control_refuses_sdk_requests(tmp_path, monkeypatch, stage, fault):
    from app.services.report_process import ReportProcess
    frozen, factory, root, trace = admission_fixture(tmp_path)
    controlled_admission_context(monkeypatch, stage=stage, fault=fault)
    first = []
    outcome = ReportProcess(frozen=frozen, model_factory=factory, operation_root=root).run(
        checkpoint=lambda: None, first_call=lambda: first.append(True), progress=lambda value: None,
        cancelled=lambda: False, reauthorize=lambda: None)
    assert outcome.state != 'completed' and outcome.manifest is None
    assert outcome.cleanup == dict(known=True, pending=False, owner_thread_alive=False)
    events = admission_trace(trace)
    assert not any(event['event'] == 'request' for event in events)
    assert sum(event['event'] == 'construct' for event in events) == (0 if stage == 'go' else 1)
    assert len(first) <= 1
    if stage == 'go':
        assert first == []


def test_actual_missing_checking_does_not_turn_control_ten_seconds_into_authorization_wait(tmp_path, monkeypatch):
    import time
    from app.services.report_process import ReportProcess
    frozen, factory, root, trace = admission_fixture(tmp_path)
    controlled_admission_context(monkeypatch, stage='go', fault='missing-checking')
    first = []
    outcome = ReportProcess(frozen=frozen, model_factory=factory, operation_root=root).run(
        checkpoint=lambda: None, first_call=lambda: first.append(True), progress=lambda value: None,
        cancelled=lambda: False, reauthorize=lambda: time.sleep(11))
    assert outcome.state == 'uncertain' and outcome.error_code in ('timeout', 'report_uncertain') and outcome.manifest is None
    assert outcome.cleanup == dict(known=True, pending=False, owner_thread_alive=False)
    assert first == [] and not admission_trace(trace)


@pytest.mark.parametrize('stage', [0, 1], ids=['go', 'admitted'])
def test_actual_cancel_event_while_awaiting_authorization_refuses_grant(tmp_path, monkeypatch, stage):
    from app.services.report_process import ReportProcess
    frozen, factory, root, trace = admission_fixture(tmp_path)
    events = controlled_admission_context(monkeypatch)
    calls, first = [], []
    def reauthorize():
        current = len(calls); calls.append(current)
        if current == stage:
            events[0].set()
    outcome = ReportProcess(frozen=frozen, model_factory=factory, operation_root=root).run(
        checkpoint=lambda: None, first_call=lambda: first.append(True), progress=lambda value: None,
        cancelled=lambda: bool(events and events[0].is_set()), reauthorize=reauthorize)
    assert outcome.state == 'cancelled' and outcome.error_code == 'report_cancelled' and outcome.manifest is None
    assert outcome.cleanup == dict(known=True, pending=False, owner_thread_alive=False)
    assert first == [] and not any(event['event'] == 'request' for event in admission_trace(trace))
    assert sum(event['event'] == 'construct' for event in admission_trace(trace)) == stage


def test_actual_absolute_deadline_while_awaiting_grant_has_no_clock_restart(tmp_path):
    import time
    from app.services.report_process import ReportProcess
    frozen, factory, root, trace = admission_fixture(tmp_path, run_seconds=2)
    deadline = time.monotonic()+2
    first, checks = [], []
    def reauthorize():
        checks.append(True)
        while time.monotonic() < deadline:
            time.sleep(min(0.05, max(0, deadline-time.monotonic())))
    outcome = ReportProcess(frozen=frozen, model_factory=factory, operation_root=root).run(
        checkpoint=lambda: None, first_call=lambda: first.append(True), progress=lambda value: None,
        cancelled=lambda: False, reauthorize=reauthorize, deadline=deadline)
    assert outcome.state == 'uncertain' and outcome.error_code == 'timeout' and outcome.manifest is None
    assert outcome.cleanup == dict(known=True, pending=False, owner_thread_alive=False)
    assert first == [] and checks == [True] and not admission_trace(trace)
    assert frozen['identity']['limits']['max_run_seconds'] == 2

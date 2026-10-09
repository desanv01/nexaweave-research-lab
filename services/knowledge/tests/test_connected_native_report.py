"""Main actual inherited preparation/native/report journey; offline envelopes."""
import asyncio
import base64
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.postgres, pytest.mark.connected_report_temporal]


_fixture_phase_sink = None


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

    class SeedChat(RichConnectedChat):
        def create(self, **kwargs):
            response = super().create(**kwargs)
            if 'hot_topics' in kwargs['messages'][-1]['content']:
                response.choices[0].message.content = json.dumps(dict(hot_topics=['recorded evidence'],
                    narrative_direction='Offline native-to-report binding', initial_posts=[
                        dict(poster_type='Person', content='Repeated seed 猫 😀'),
                        dict(poster_type='Organization', content='Organisation B'),
                        dict(poster_type='Person', content='Repeated seed 猫 😀'),
                        dict(poster_type='Organization', content='Organisation D'),
                        dict(poster_type='Person', content='Final same actor E')]), ensure_ascii=False)
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
    prep, _, retained, chat, _, projection_transport = real_host(factory, input_artifacts, cap=12, chat=SeedChat())
    loop = asyncio.get_running_loop()
    preparation = TemporalPreparationHost(client=temporal, task_queue='mf-report-prep-'+uuid4().hex,
        trusted_host=prep, allow_dispatch=lambda: True)
    prep.scheduler = preparation.scheduler_for(loop)
    native_host = launch_host(prep, factory)
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
            payload['options'].update(types=['Person','Organization'], max_agents=2, max_rounds=1)
            planned = await asyncio.to_thread(prep.plan, payload)
            await asyncio.to_thread(prep.start, prep_reference(planned))
            preparation_row = prep.store.get('owner', planned['operation_id'])
            phases.end('preparation-plan-start')
            phases.start('preparation-result')
            await asyncio.wait_for(temporal.get_workflow_handle(preparation_row.dispatch.workflow_id).result(), 60)
            ready = await asyncio.to_thread(prep.status, prep_reference(planned))
            assert ready['state']=='ready' and not ready['simulation_executed']
            phases.end('preparation-result')
            input_root = prep.root/ready['receipt']['simulation_id']
            original_inputs = {f['name']:(input_root/f['name']).read_bytes() for f in ready['receipt']['files']}
            phases.start('native-plan-start')
            launch = await asyncio.to_thread(native_host.plan, declaration(planned))
            await asyncio.to_thread(native_host.start, native_reference(launch))
            native_row = native_host.store.get('owner', launch['request']['run_id'])
            phases.end('native-plan-start')
            phases.start('native-result')
            native_receipt = await asyncio.wait_for(native.result(native_row.request, NativeWorkflowRef(**native_row.workflow)), 180)
            assert native_receipt.outcome=='completed' and len(native_owners)==1
            launch = await asyncio.to_thread(native_host.status, native_reference(launch))
            assert launch['state']=='completed' and launch['receipt']==native_receipt.to_wire()
            phases.end('native-result')
            original_outputs = {name:(input_root/name).read_bytes() for platform in ('twitter','reddit')
                for name in (platform+'_simulation.db', platform+'/actions.jsonl')}
            phases.start('report-plan-start')
            declared = dict(schema_version=1, report_id=str(uuid4()), launch_id=str(native_row.run_id),
                launch_sha256=launch['launch_sha256'], requirement='Distinguish retained source and actual observed records.',
                output_language='en', native_windows=None)
            reviewed = await asyncio.to_thread(reports.plan, declared)
            assert reviewed['state']=='planned' and not report_log.exists()
            reference = dict(schema_version=1, report_id=reviewed['report_id'], plan_sha256=reviewed['plan_sha256'])
            await asyncio.to_thread(reports.start, reference)
            row = reports.store.get('owner', reference['report_id'], reference['plan_sha256'])
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
            assert reports.store.get('owner',reference['report_id'],reference['plan_sha256'])==before_journal
            assert native_host.budget.status('owner',prep.account_id)==before_budget and report_log.read_bytes()==before_calls
            assert all((input_root/name).read_bytes()==raw for name,raw in {**original_inputs,**original_outputs}.items())
            recovered=await asyncio.to_thread(reports.start,reference)
            assert recovered['receipt']==completed['receipt'] and report_log.read_bytes()==before_calls
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

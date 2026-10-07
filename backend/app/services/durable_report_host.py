"""Trusted completed native receipt → frozen narrative → immutable artifacts."""
import base64
import hashlib
import json
from pathlib import Path
import pickle
import time
from uuid import UUID
from .connected_report_client import (ReportError, DEFAULT_LIMITS, IDENTITY, encoded, digest,
    validate_limits, validate_payload, validate_result, validate_read, validate_download, artifact_name, text)
from .connected_report_context import freeze_context, validate_context, validate_references
from .report_models import BoundedReportModelFactory


class DurableReportHost:
    def __init__(self, settings, connection_factory, read_facade, native_launch_host, artifact_root,
                 account_id=None, ceiling_microusd=None, authorize=None, model_factory=None,
                 model_label=None, limits=None, scheduler=None):
        from nexaweave_execution.report_store import ReportStore
        from .native_observations_host import NativeObservationsHost
        if not callable(connection_factory) or not Path(artifact_root).is_absolute():
            raise ReportError('report_unavailable')
        self.settings, self.principal, self.display_graph_id = settings, settings.principal, settings.display_graph_id
        self.scope_dto = json.loads(encoded(settings.scope))
        self.observations = NativeObservationsHost(launch_host=native_launch_host)
        if (self.observations.principal != self.principal or self.observations.display_graph_id != self.display_graph_id
                or self.observations.scope_dto != self.scope_dto or read_facade._settings.principal != self.principal
                or read_facade._settings.scope != self.scope_dto or read_facade._settings.display_graph_id != self.display_graph_id):
            raise ReportError('unauthorized')
        self.reader, self.root, self.store = read_facade, Path(artifact_root), ReportStore(connection_factory)
        self.account_id, self.ceiling, self.authorize, self.scheduler = account_id, ceiling_microusd, authorize, scheduler
        self.limits = dict(validate_limits(limits if limits is not None else DEFAULT_LIMITS))
        self.model_label = text(model_label if model_label is not None else 'unconfigured', 200)
        if model_factory is not None and type(model_factory) is not BoundedReportModelFactory:
            raise ReportError('report_unavailable')
        self.model_factory = model_factory

    def _configuration(self):
        raw = pickle.dumps(self.model_factory)
        if len(raw) > 65536:
            raise ReportError('report_unavailable')
        return dict(account_id=str(self.account_id) if self.account_id is not None else None,
                    factory_sha256=hashlib.sha256(raw).hexdigest())

    def authorization(self, row=None):
        try:
            budget = (type(self.account_id) is UUID and self.account_id == self.observations.launch.preparation.account_id
                      and type(self.ceiling) is int and 1 <= self.ceiling <= 2**63 - 1)
            enabled = (budget and callable(self.authorize) and self.authorize() is True
                and self.model_factory is not None and self.model_factory.configured()
                and self.model_factory.limits == self.limits and callable(self.scheduler))
            if row is not None:
                identity = row.frozen['identity']
                enabled = enabled and (identity['limits'] == self.limits and identity['model_label'] == self.model_label
                    and identity['ceiling_microusd'] == self.ceiling and row.frozen['configuration'] == self._configuration())
                budget = budget and identity['ceiling_microusd'] == self.ceiling and row.frozen['configuration']['account_id'] == str(self.account_id)
            return dict(model_calls_enabled=bool(enabled), budget_configured=bool(budget))
        except Exception:
            return dict(model_calls_enabled=False, budget_configured=False)

    def _dto(self, row, payload, method):
        return validate_result(row.public(self.authorization(row)), self.display_graph_id, self.scope_dto, payload, method, self.principal)

    def _row(self, payload):
        row = self.store.get(self.principal, payload['report_id'], payload.get('plan_sha256'))
        binding = row.frozen['identity']['binding']
        if binding['display_graph_id'] != self.display_graph_id or binding['scope'] != self.scope_dto or binding['principal'] != self.principal:
            raise ReportError('not_found')
        return row

    def _reauthorize(self, row, *, deadline=None, tick=None):
        if tick is not None:
            tick()
        validate_context(row.frozen['context'], row.frozen['identity']['binding'])
        with freeze_context(self.observations, self.reader, row.frozen['declaration'], deadline=deadline, tick=tick) as current:
            if current != row.frozen['context'] or digest(current) != row.frozen['identity']['context_sha256']:
                raise ReportError('conflict')
        if tick is not None:
            tick()
        return row

    def plan(self, payload):
        payload = validate_payload('plan', payload)
        try:
            prior = self.store.get(self.principal, payload['report_id'])
        except ReportError as error:
            if error.code != 'not_found':
                raise
        else:
            if prior.declaration_sha256 != digest(payload):
                raise ReportError('conflict')
            self._reauthorize(prior)
            return self._dto(prior, payload, 'plan')
        with freeze_context(self.observations, self.reader, payload) as context:
            identity = dict(schema_version=1, report_id=payload['report_id'], binding=context['binding'],
                options={k: payload[k] for k in ('requirement', 'output_language', 'native_windows')},
                context_sha256=digest(context), source_projection_sha256=digest(context['graph']),
                model_label=self.model_label, limits=dict(self.limits),
                ceiling_microusd=self.ceiling if type(self.ceiling) is int and 1 <= self.ceiling <= 2**63 - 1 else None)
            row = self.store.put(self.principal, payload, identity, context, self._configuration())
        return self._dto(row, payload, 'plan')

    def start(self, payload):
        payload = validate_payload('start', payload)
        row = self._reauthorize(self._row(payload))
        if row.state != 'planned':
            return self._dto(row, payload, 'start')
        if not self.authorization(row)['model_calls_enabled']:
            raise ReportError('model_calls_disabled')
        from nexaweave_knowledge.contracts import KnowledgeScope
        row, claimed = self.store.queue(self.principal, row.report_id, row.plan_sha256,
            KnowledgeScope.model_validate_json(json.dumps(self.scope_dto)), self.account_id)
        if claimed:
            wire = dict(schema_version=1, report_id=str(row.report_id), plan_sha256=row.plan_sha256, attempt_id=str(row.attempt_id))
            try:
                workflow = self.scheduler(wire)
                row = self.store.workflow(self.principal, row.report_id, workflow)
            except Exception:
                # An unacknowledged schedule may already own the child. Never
                # release capacity or schedule this report a second time.
                try:
                    self.store.finish(self.principal, wire, state='uncertain', error_code='report_uncertain',
                        cleanup=dict(known=False, pending=None, owner_thread_alive=None))
                except Exception:
                    pass
                raise ReportError('report_uncertain') from None
        return self._dto(row, payload, 'start')

    def status(self, payload):
        payload = validate_payload('status', payload)
        row = self._reauthorize(self._row(payload))
        row = self.store.recover_expired(self.principal, row.report_id, row.plan_sha256)
        return self._dto(row, payload, 'status')

    def cancel(self, payload):
        payload = validate_payload('cancel', payload)
        self._reauthorize(self._row(payload))
        row = self.store.cancel(self.principal, payload['report_id'], payload['plan_sha256'])
        return self._dto(row, payload, 'cancel')

    def _output_root(self, row):
        return self.root / row.report_id.hex / 'output' / str(row.report_id)

    def generate(self, wire, *, heartbeat=None, cancelled=None):
        """Trusted Temporal activity: claim once, never resume a partial child."""
        from nexaweave_execution.report_contracts import dispatch
        from .report_process import ReportProcess, report_files
        from ..utils.safe_paths import ensure_directory
        dispatch(wire)
        row = self.store.claim(self.principal, wire)
        deadline = time.monotonic() + row.frozen['identity']['limits']['max_run_seconds']
        try:
            def checkpoint():
                if time.monotonic() >= deadline:
                    raise ReportError('timeout')
                current = self._row(wire)
                if current.cancel_requested or callable(cancelled) and cancelled():
                    raise ReportError('report_cancelled')
                if not self.authorization(current)['model_calls_enabled']:
                    raise ReportError('model_calls_disabled')
                if time.monotonic() >= deadline:
                    raise ReportError('timeout')
            def tick():
                if time.monotonic() >= deadline:
                    raise ReportError('timeout')
                if heartbeat is not None:
                    try:
                        heartbeat()
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except BaseException:
                        raise ReportError('report_uncertain') from None
                checkpoint()
            checkpoint()
            self._reauthorize(row, deadline=deadline, tick=tick)
            operation = Path(ensure_directory(str(self.root), row.report_id.hex))
            outcome = ReportProcess(frozen=row.frozen, model_factory=self.model_factory, operation_root=operation).run(
                checkpoint=checkpoint, first_call=lambda: self.store.first_request(self.principal, wire),
                progress=lambda value: self.store.progress(self.principal, wire, value),
                cancelled=lambda: self._row(wire).cancel_requested or bool(callable(cancelled) and cancelled()), heartbeat=heartbeat,
                reauthorize=lambda: self._reauthorize(self._row(wire), deadline=deadline, tick=tick), deadline=deadline)
            if outcome.state != 'completed':
                return self.store.finish(self.principal, wire, state=outcome.state, cleanup=outcome.cleanup,
                    error_code=outcome.error_code).public(self.authorization(row))
            checkpoint()
            self._reauthorize(self._row(wire), deadline=deadline, tick=tick)
            with report_files(self._output_root(row), outcome.manifest) as content:
                validate_references(content['full_report.md'].decode('utf-8'), row.frozen['context'])
                for name in content:
                    if name.startswith('section_'):
                        validate_references(content[name].decode('utf-8'), row.frozen['context'], require_native=False)
                checkpoint()
                receipt = dict(schema_version=1, report_id=str(row.report_id), plan_sha256=row.plan_sha256,
                    context_sha256=row.frozen['identity']['context_sha256'], manifest_sha256=digest(outcome.manifest),
                    output_language=row.frozen['identity']['options']['output_language'],
                    reference_integrity='validated', semantic_support_status='not_reviewed')
            # The lease's final descriptor/path checks must succeed before a
            # completed journal transition can commit. A changed output cannot
            # raise after completion has already been persisted.
            checkpoint()
            self._reauthorize(self._row(wire), deadline=deadline, tick=tick)
            def verify_output():
                if time.monotonic() >= deadline:
                    raise ReportError('timeout')
                with report_files(self._output_root(row), outcome.manifest) as final_content:
                    validate_references(final_content['full_report.md'].decode('utf-8'), row.frozen['context'])
                    for name in final_content:
                        if name.startswith('section_'):
                            validate_references(final_content[name].decode('utf-8'), row.frozen['context'], require_native=False)
                if time.monotonic() >= deadline:
                    raise ReportError('timeout')
            completed = self.store.finish(self.principal, wire, state='completed', cleanup=outcome.cleanup,
                receipt=receipt, manifest=outcome.manifest, verify_output=verify_output)
            return completed.public(self.authorization(completed))
        except Exception as error:
            code = getattr(error, 'code', 'report_uncertain')
            state = 'cancelled' if code == 'report_cancelled' else 'uncertain'
            # Before any ReportProcess exists, no child/request can exist. Once
            # it has returned, its durable local outcome supplies cleanup proof.
            known = 'outcome' in locals()
            no_process = 'operation' not in locals()
            cleanup = outcome.cleanup if known else (dict(known=True, pending=False, owner_thread_alive=False) if no_process else dict(known=False, pending=None, owner_thread_alive=None))
            if cleanup['known'] and not cleanup['pending'] and code not in ('report_uncertain', 'timeout'):
                state = 'cancelled' if code == 'report_cancelled' else 'failed'
            final = self.store.finish(self.principal, wire, state=state, cleanup=cleanup,
                error_code=code if code in ('report_cancelled', 'conflict', 'model_calls_disabled', 'unauthorized', 'result_too_large', 'timeout') else 'report_uncertain')
            return final.public(self.authorization(final))

    def read(self, payload):
        payload = validate_payload('read', payload)
        row = self._reauthorize(self._row(payload))
        if row.state != 'completed':
            raise ReportError('conflict')
        from .report_process import report_files
        with report_files(self._output_root(row), row.manifest) as content:
            self._reauthorize(row)
            if self._row(payload) != row:
                raise ReportError('conflict')
            result = validate_read(dict(schema_version=1, report=self._dto(row, payload, 'read'),
                content=content['full_report.md'].decode('utf-8')), self.display_graph_id, self.scope_dto, payload, self.principal)
        return result

    def download(self, payload):
        payload = validate_payload('download', payload)
        row = self._reauthorize(self._row(payload))
        if row.state != 'completed':
            raise ReportError('conflict')
        from .report_process import report_files
        name = artifact_name(payload['kind'], payload['section_index'])
        with report_files(self._output_root(row), row.manifest) as content:
            if name not in content:
                raise ReportError('invalid_request')
            self._reauthorize(row)
            if self._row(payload) != row:
                raise ReportError('conflict')
            file = next(f for f in row.manifest['files'] if f['name'] == name)
            result = dict(schema_version=1, report_id=str(row.report_id), plan_sha256=row.plan_sha256,
                receipt_sha256=digest(row.receipt), artifact=dict(file, mime='text/markdown' if name.endswith('.md') else 'application/json',
                content_base64=base64.b64encode(content[name]).decode('ascii')))
            result = validate_download(result, payload, self._dto(row, payload, 'download'))
        return result

"""One durable inherited chat turn from an accepted connected report."""
import base64
import hashlib
import json
from pathlib import Path
import pickle
import time
from uuid import UUID

from nexaweave_execution.followup_contracts import (FollowupError, DEFAULT_LIMITS,
    FILE_BYTES, KINDS, budget_fingerprint, digest, encoded, validate_payload,
    validate_limits, validate_result, validate_read, validate_download,
    validate_history_page, validate_manifest, validate_receipt)
from nexaweave_execution.followup_contracts import validate_conversation
from .connected_followup_context import freeze_parent, verify_frozen_parent
from .followup_process import (FollowupProcess, followup_files, prior_conversation,
    reserve_conversation, conversation_document)
from .report_models import BoundedReportModelFactory
from .connected_report_context import validate_references


class DurableFollowupHost:
    def __init__(self, settings, connection_factory, parent_host, artifact_root,
                 account_id=None, ceiling_microusd=None, authorize=None,
                 model_factory=None, model_label=None, limits=None, scheduler=None):
        from nexaweave_execution.followup_store import FollowupStore
        if (not callable(connection_factory) or not Path(artifact_root).is_absolute()
                or getattr(parent_host,'principal',None)!=settings.principal
                or getattr(parent_host,'display_graph_id',None)!=settings.display_graph_id
                or getattr(parent_host,'scope_dto',None)!=settings.scope):
            raise FollowupError('followup_unavailable')
        self.settings,self.principal,self.display_graph_id=settings,settings.principal,settings.display_graph_id
        self.scope_dto=json.loads(encoded(settings.scope))
        self.parent,self.root,self.store=parent_host,Path(artifact_root),FollowupStore(connection_factory)
        self.account_id,self.ceiling,self.authorize,self.scheduler=account_id,ceiling_microusd,authorize,scheduler
        self.limits=dict(validate_limits(limits if limits is not None else DEFAULT_LIMITS))
        if type(model_label) is not str or not model_label.strip() or len(model_label)>200:
            model_label='unconfigured'
        self.model_label=model_label
        if model_factory is not None and type(model_factory) is not BoundedReportModelFactory:
            raise FollowupError('followup_unavailable')
        self.model_factory=model_factory

    def _configuration(self):
        raw=pickle.dumps(self.model_factory)
        if len(raw)>65536:
            raise FollowupError('followup_unavailable')
        return dict(account_id=str(self.account_id) if self.account_id is not None else None,
                    factory_sha256=hashlib.sha256(raw).hexdigest())

    def authorization(self,row=None):
        try:
            budget=(type(self.account_id) is UUID
                and self.account_id==self.parent.observations.launch.preparation.account_id
                and type(self.ceiling) is int and 1<=self.ceiling<=2**63-1)
            enabled=(budget and callable(self.authorize) and self.authorize() is True
                and self.model_factory is not None and self.model_factory.configured()
                and self.model_factory.limits==self.limits and callable(self.scheduler))
            if row is not None:
                identity=row.frozen['identity']
                enabled=enabled and (identity['limits']==self.limits and identity['model_label']==self.model_label
                    and identity['ceiling_microusd']==self.ceiling
                    and row.frozen['configuration']==self._configuration())
                budget=budget and identity['ceiling_microusd']==self.ceiling
            return dict(model_calls_enabled=bool(enabled),budget_configured=bool(budget))
        except Exception:
            return dict(model_calls_enabled=False,budget_configured=False)

    def _dto(self,row,payload,method):
        return validate_result(row.public(self.authorization(row)),self.display_graph_id,
            self.scope_dto,payload,method,self.principal)

    def _row(self,payload):
        row=self.store.get(self.principal,payload['turn_id'],payload.get('plan_sha256'))
        binding=row.frozen['identity']['binding']
        if (binding['display_graph_id']!=self.display_graph_id or binding['principal']!=self.principal
                or binding['scope']!=self.scope_dto):
            raise FollowupError('not_found')
        return row

    def _verified_parent(self,row,*,deadline=None,tick=None):
        if tick is not None: tick()
        verified=verify_frozen_parent(self.parent,row.frozen,deadline=deadline,tick=tick)
        if tick is not None: tick()
        return verified

    def _reauthorize(self,row,*,deadline=None,tick=None):
        self._verified_parent(row,deadline=deadline,tick=tick)
        return row

    def plan(self,payload):
        payload=validate_payload('plan',payload)
        try:
            prior=self.store.get(self.principal,payload['turn_id'])
        except FollowupError as error:
            if error.code!='not_found': raise
        else:
            options={k:payload[k] for k in ('question','output_language','expected_history_sha256')}
            if (prior.frozen['identity']['binding']['report']['report_id']!=payload['report_id']
                    or prior.frozen['identity']['binding']['report']['plan_sha256']!=payload['report_plan_sha256']
                    or prior.frozen['identity']['options']!=options):
                raise FollowupError('turn_conflict')
            self._reauthorize(prior)
            return self._dto(prior,payload,'plan')
        parent,binding,context,provenance,_=freeze_parent(self.parent,payload['report_id'],payload['report_plan_sha256'])
        history=self.store.latest(self.principal,payload['report_id'],payload['report_plan_sha256'])
        if payload['expected_history_sha256'] is not None and payload['expected_history_sha256']!=history['head_sha256']:
            raise FollowupError('history_changed')
        identity=dict(schema_version=1,turn_id=payload['turn_id'],binding=binding,
            options={k:payload[k] for k in ('question','output_language','expected_history_sha256')},
            history=history,report_context=provenance,context_sha256=digest(context),
            source_projection_sha256=digest(context['graph']),model_label=self.model_label,
            limits=dict(self.limits),ceiling_microusd=self.ceiling if type(self.ceiling) is int and 1<=self.ceiling<=2**63-1 else None)
        row=self.store.put(self.principal,identity,context,self._configuration())
        return self._dto(row,payload,'plan')

    def start(self,payload):
        payload=validate_payload('start',payload)
        row=self._reauthorize(self._row(payload))
        if row.state!='planned':
            return self._dto(row,payload,'start')
        if not self.authorization(row)['model_calls_enabled']:
            raise FollowupError('model_calls_disabled')
        report=row.frozen['identity']['binding']['report']
        prior=prior_conversation(self.store,self.root,self.principal,report['report_id'],report['plan_sha256'])
        reserve_conversation(row.frozen['identity'],prior)
        from nexaweave_knowledge.contracts import KnowledgeScope
        row,claimed=self.store.queue(self.principal,row.turn_id,row.plan_sha256,
            KnowledgeScope.model_validate_json(json.dumps(self.scope_dto)),self.account_id)
        if claimed:
            wire=dict(schema_version=1,turn_id=str(row.turn_id),plan_sha256=row.plan_sha256,
                      attempt_id=str(row.attempt_id))
            try:
                workflow=self.scheduler(wire)
                row=self.store.workflow(self.principal,row.turn_id,workflow)
            except Exception:
                try:
                    self.store.finish(self.principal,wire,state='uncertain',error_code='followup_uncertain',
                        cleanup=dict(known=False,pending=None,owner_thread_alive=None))
                except Exception: pass
                raise FollowupError('followup_uncertain') from None
        return self._dto(row,payload,'start')

    def status(self,payload):
        payload=validate_payload('status',payload)
        row=self._reauthorize(self._row(payload))
        row=self.store.recover_expired(self.principal,row.turn_id,row.plan_sha256)
        return self._dto(row,payload,'status')

    def cancel(self,payload):
        payload=validate_payload('cancel',payload)
        self._reauthorize(self._row(payload))
        row=self.store.cancel(self.principal,payload['turn_id'],payload['plan_sha256'])
        return self._dto(row,payload,'cancel')

    def history(self,payload):
        payload=validate_payload('history',payload)
        freeze_parent(self.parent,payload['report_id'],payload['report_plan_sha256'])
        result=self.store.history(self.principal,payload['report_id'],payload['report_plan_sha256'],payload['before_ordinal'])
        if (result['binding']['display_graph_id']!=self.display_graph_id
                or result['binding']['principal']!=self.principal or result['binding']['scope']!=self.scope_dto):
            raise FollowupError('not_found')
        return validate_history_page(result,payload)

    def _output_root(self,row):
        return self.root/row.turn_id.hex/'output'

    def generate(self,wire,*,heartbeat=None,cancelled=None):
        from nexaweave_execution.followup_contracts import dispatch
        from ..utils.safe_paths import ensure_directory
        dispatch(wire)
        row=self.store.claim(self.principal,wire)
        deadline=time.monotonic()+row.frozen['identity']['limits']['max_run_seconds']
        try:
            def checkpoint():
                if time.monotonic()>=deadline: raise FollowupError('timeout')
                current=self._row(wire)
                if current.cancel_requested or callable(cancelled) and cancelled():
                    raise FollowupError('followup_cancelled')
                if not self.authorization(current)['model_calls_enabled']:
                    raise FollowupError('model_calls_disabled')
                if time.monotonic()>=deadline: raise FollowupError('timeout')
            def tick():
                if time.monotonic()>=deadline: raise FollowupError('timeout')
                if heartbeat is not None:
                    try: heartbeat()
                    except (KeyboardInterrupt,SystemExit): raise
                    except BaseException: raise FollowupError('followup_uncertain') from None
                checkpoint()
            checkpoint()
            parent,binding,context,provenance,bundle=self._verified_parent(row,deadline=deadline,tick=tick)
            report=row.frozen['identity']['binding']['report']
            prior=prior_conversation(self.store,self.root,self.principal,report['report_id'],report['plan_sha256'])
            reserve_conversation(row.frozen['identity'],prior)
            operation=Path(ensure_directory(str(self.root),row.turn_id.hex))
            ephemeral=dict(row.frozen,parent_manifest=parent.manifest,
                parent_requirement=parent.frozen['identity']['options']['requirement'])
            outcome=FollowupProcess(frozen=ephemeral,model_factory=self.model_factory,
                operation_root=operation,parent_bundle=bundle,prior=prior).run(
                checkpoint=checkpoint,first_call=lambda:self.store.first_request(self.principal,wire),
                cancelled=lambda:self._row(wire).cancel_requested or bool(callable(cancelled) and cancelled()),
                heartbeat=heartbeat,reauthorize=lambda:self._reauthorize(self._row(wire),deadline=deadline,tick=tick),
                deadline=deadline)
            if outcome.state!='completed':
                return self.store.finish(self.principal,wire,state=outcome.state,
                    cleanup=outcome.cleanup,error_code=outcome.error_code).public(self.authorization(row))
            checkpoint(); self._reauthorize(self._row(wire),deadline=deadline,tick=tick)
            with followup_files(self._output_root(row),outcome.manifest) as content:
                answer=content['answer.md'].decode('utf-8')
                validate_references(answer,context,require_native=False)
                conversation=json.loads(content['conversation.json'])
                validate_conversation(conversation,row.frozen['identity'],answer)
                if conversation!=conversation_document(row.frozen['identity'],prior,answer,
                        hashlib.sha256(content['answer.md']).hexdigest()):
                    raise FollowupError('conflict')
                turn=json.loads(content['turn.json'])
                if (turn.get('identity')!=row.frozen['identity']
                        or turn.get('publication_proof_scope')!='external_current_receipt_and_head'
                        or any(key in turn for key in ('receipt','manifest','published_history_head_sha256'))):
                    raise FollowupError('conflict')
                if json.loads(content['native_evidence.json'])!=json.loads(bundle['native_evidence.json']):
                    raise FollowupError('conflict')
                before_evidence=json.loads(bundle['retrieval_evidence.json'])
                after_evidence=json.loads(content['retrieval_evidence.json'])
                if (type(after_evidence) is not dict or set(after_evidence)!=set(before_evidence)
                        or any(after_evidence[key]!=before_evidence[key] for key in before_evidence if key!='retrievals')
                        or type(after_evidence.get('retrievals')) is not list
                        or len(after_evidence['retrievals'])>1000):
                    raise FollowupError('conflict')
                trace=json.loads(content['tool_trace.json'])
                if (type(trace) is not dict or set(trace)!={'schema_version','executed'}
                        or type(trace['schema_version']) is not int or trace['schema_version']!=1
                        or type(trace['executed']) is not list or len(trace['executed'])>2):
                    raise FollowupError('conflict')
                for entry in trace['executed']:
                    if (type(entry) is not dict or set(entry)!={'tool','parameters','observation'}
                            or entry['tool'] not in ('recorded_native_events','quick_search','panorama_search','insight_forge')
                            or type(entry['parameters']) is not dict or type(entry['observation']) is not str
                            or len(entry['observation'])>1500):
                        raise FollowupError('conflict')
                receipt=dict(schema_version=1,turn_id=str(row.turn_id),plan_sha256=row.plan_sha256,
                    parent_report_id=report['report_id'],parent_report_plan_sha256=report['plan_sha256'],
                    parent_report_receipt_sha256=report['receipt_sha256'],
                    context_sha256=row.frozen['identity']['context_sha256'],
                    history_head_sha256=row.frozen['identity']['history']['head_sha256'],
                    ordinal=row.frozen['identity']['history']['total_completed']+1,
                    manifest_sha256=digest(outcome.manifest),
                    output_language=row.frozen['identity']['options']['output_language'],
                    reference_integrity='validated',semantic_support_status='not_reviewed')
                validate_receipt(receipt,row.frozen['identity'],outcome.manifest)
            checkpoint(); self._reauthorize(self._row(wire),deadline=deadline,tick=tick)
            def verify_output():
                if time.monotonic()>=deadline: raise FollowupError('timeout')
                with followup_files(self._output_root(row),outcome.manifest) as final:
                    if final['answer.md']!=answer.encode('utf-8'):
                        raise FollowupError('conflict')
                    validate_references(answer,context,require_native=False)
                if time.monotonic()>=deadline: raise FollowupError('timeout')
            completed=self.store.finish(self.principal,wire,state='completed',cleanup=outcome.cleanup,
                receipt=receipt,manifest=outcome.manifest,answer=answer,verify_output=verify_output)
            return completed.public(self.authorization(completed))
        except Exception as error:
            code=getattr(error,'code','followup_uncertain')
            state='cancelled' if code=='followup_cancelled' else 'uncertain'
            known='outcome' in locals()
            no_process='operation' not in locals()
            cleanup=(outcome.cleanup if known else dict(known=True,pending=False,owner_thread_alive=False)
                     if no_process else dict(known=False,pending=None,owner_thread_alive=None))
            if cleanup['known'] and not cleanup['pending'] and code not in ('followup_uncertain','timeout'):
                state='cancelled' if code=='followup_cancelled' else 'failed'
            final=self.store.finish(self.principal,wire,state=state,cleanup=cleanup,
                error_code=code if code in ('followup_cancelled','conflict','model_calls_disabled',
                    'unauthorized','result_too_large','timeout') else 'followup_uncertain')
            return final.public(self.authorization(final))

    def read(self,payload):
        payload=validate_payload('read',payload)
        row=self._reauthorize(self._row(payload))
        if row.state!='completed': raise FollowupError('conflict')
        with followup_files(self._output_root(row),row.manifest) as content:
            validate_conversation(json.loads(content['conversation.json']),row.frozen['identity'],
                                  content['answer.md'].decode('utf-8'))
            self._reauthorize(row)
            if self._row(payload)!=row: raise FollowupError('conflict')
            return validate_read(dict(schema_version=1,turn=self._dto(row,payload,'read'),
                content=content['answer.md'].decode('utf-8')),
                self.display_graph_id,self.scope_dto,payload,self.principal)

    def download(self,payload):
        payload=validate_payload('download',payload)
        row=self._reauthorize(self._row(payload))
        if row.state!='completed': raise FollowupError('conflict')
        name=KINDS[payload['kind']]
        with followup_files(self._output_root(row),row.manifest) as content:
            validate_conversation(json.loads(content['conversation.json']),row.frozen['identity'],
                                  content['answer.md'].decode('utf-8'))
            self._reauthorize(row)
            if self._row(payload)!=row: raise FollowupError('conflict')
            file=next(f for f in row.manifest['files'] if f['name']==name)
            result=dict(schema_version=1,turn_id=str(row.turn_id),plan_sha256=row.plan_sha256,
                receipt_sha256=digest(row.receipt),artifact=dict(file,
                    mime='text/markdown' if name.endswith('.md') else 'application/json',
                    content_base64=base64.b64encode(content[name]).decode('ascii')))
            return validate_download(result,payload,self._dto(row,payload,'download'))

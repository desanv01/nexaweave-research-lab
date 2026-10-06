"""Trusted READY→frozen PG launch→existing Temporal/owned native composition.

The optional synchronous Temporal bridge is operator-owned: it invokes the
injected host on its existing loop with a finite timeout. A lost reply never
causes redispatch. No clients, threads or loops are constructed here.
"""
from dataclasses import asdict, replace
import json
import hashlib
import pickle
from uuid import UUID
from .native_launch_client import (NativeLaunchError, IDENTITY, digest, encoded,
    validate_payload, validate_result, validate_limits, text)
from .native_launch_models import BoundedNativeModelFactory


class DurableNativeLaunchHost:
    def __init__(self, *, preparation_host, connection_factory, runtime_sha256,
                 model_label='unconfigured', limits=None, model_factory=None,
                 account_id=None, ceiling_microusd=None, authorize=None,
                 temporal_host=None, temporal_call=None):
        from mirofish_execution.native_launch_store import NativeLaunchStore
        from mirofish_execution.native_run_store import NativeRunStore
        from mirofish_execution.budget import BudgetLedger
        from mirofish_execution.native_run_contracts import sha256
        from .durable_preparation_host import DurablePreparationHost
        if not isinstance(preparation_host,DurablePreparationHost) or not callable(connection_factory):
            raise NativeLaunchError('native_launch_unavailable')
        self.preparation = preparation_host
        self.principal, self.display_graph_id = preparation_host.principal, preparation_host.display_graph_id
        self.scope_dto = json.loads(encoded(preparation_host.scope_dto))
        self.runtime_sha256 = sha256(runtime_sha256)
        self.model_label = text(model_label,128)
        self.limits = dict(validate_limits(limits or {'max_calls':100,'max_input_bytes':262144,
                                                     'max_output_tokens':4096,'max_run_seconds':120}))
        if model_factory is not None and type(model_factory) is not BoundedNativeModelFactory:
            raise NativeLaunchError('native_launch_unavailable')
        self.model_factory = model_factory
        self.account_id,self.ceiling,self.authorize = account_id,ceiling_microusd,authorize
        self.connect = connection_factory
        self.store,self.native,self.budget = NativeLaunchStore(connection_factory),NativeRunStore(connection_factory),BudgetLedger(connection_factory)
        self.temporal,self.temporal_call = None,None
        if temporal_host is not None:
            self.attach_temporal(temporal_host,temporal_call)

    def attach_temporal(self, host, call):
        from mirofish_execution.temporal_native_host import TemporalNativeHost
        if not isinstance(host,TemporalNativeHost) or host._principal != self.principal or not callable(call):
            raise NativeLaunchError('native_launch_unavailable')
        if self.temporal is not None:
            raise NativeLaunchError('conflict')
        self.temporal,self.temporal_call = host,call

    def authorization(self, row=None):
        try:
            enabled = (callable(self.authorize) and self.authorize() is True
                and self.account_id is not None and self.account_id == self.preparation.account_id
                and type(self.ceiling) is int and 1 <= self.ceiling <= 2**63-1
                and self.model_factory is not None and self.model_factory.configured()
                and self.model_factory.limits == self.limits
                and self.temporal is not None and callable(self.temporal_call))
            if row is not None:
                identity = row.frozen['identity']
                enabled = enabled and (identity['limits']==self.limits and identity['model_label']==self.model_label
                    and identity['ceiling_microusd']==str(self.ceiling)
                    and row.request.runtime_sha256==self.runtime_sha256
                    and row.request.platforms==self.model_factory.platforms
                    and row.frozen['configuration']==self._configuration())
            return {'model_calls_enabled':bool(enabled)}
        except Exception:
            return {'model_calls_enabled':False}

    def _configuration(self):
        raw = pickle.dumps(self.model_factory)
        if len(raw)>65536:
            raise NativeLaunchError('native_launch_unavailable')
        return {'account_id':str(self.account_id) if self.account_id is not None else None,
                'factory_sha256':hashlib.sha256(raw).hexdigest()}

    def _preparation(self, descriptor):
        from .preparation_client import validate_receipt
        ref = {'schema_version':1,'operation_id':descriptor['operation_id'],'plan_sha256':descriptor['plan_sha256']}
        row = self.preparation._row(ref)
        self.preparation._owned(row.frozen['public']['source']['source_revision'],expected_revision=row.project_revision)
        if row.state != 'ready' or row.receipt is None:
            raise NativeLaunchError('conflict')
        validate_receipt(row.receipt,str(row.operation_id),row.frozen['public']['options']['platforms'])
        return row,ref

    def _row(self,payload):
        row = self.store.get(self.principal,payload['launch_id'],payload.get('launch_sha256'))
        if row.frozen['identity']['scope']!=self.scope_dto or row.frozen['identity']['display_graph_id']!=self.display_graph_id:
            raise NativeLaunchError('not_found')
        return row

    def _current(self,row):
        prep,ref = self._preparation(row.frozen['identity']['preparation'])
        request = row.request
        opts = prep.frozen['public']['options']
        if (request.project_revision!=prep.project_revision or request.project_id!=prep.project_id
                or request.simulation_id!=prep.receipt['simulation_id'] or request.artifact_sha256!=prep.receipt['artifact_sha256']
                or request.platforms!=tuple(opts['platforms']) or request.seed!=opts['seed'] or request.max_rounds!=opts['max_rounds']):
            raise NativeLaunchError('conflict')
        return prep,ref

    def _dto(self,row,method,payload):
        cleanup = {'known':False,'pending':None,'owner_thread_alive':None}
        if self.temporal is not None and self.temporal_call is not None and row.state!='planned':
            try:
                local = self.temporal_call('local_status',row.request,None)
                # Only the actual retained local registry can qualify cleanup.
                from mirofish_execution.temporal_native_activities import LocalNativeStatus
                if isinstance(local,LocalNativeStatus) and local.run_id==row.run_id:
                    cleanup = {'known':True,'pending':local.cleanup_pending,'owner_thread_alive':local.owner_thread_alive}
            except Exception:
                pass
        data = json.loads(encoded(row.frozen['identity']))
        data.update(launch_sha256=row.launch_sha256,state=row.state,error_code=row.error_code,
                    authorization=self.authorization(row),workflow=row.workflow,receipt=row.receipt,
                    cancel_requested=row.cancel_requested,cleanup=cleanup)
        return validate_result(data,self.display_graph_id,self.scope_dto,payload,method)

    def plan(self,payload):
        from mirofish_execution.native_launch_contracts import LaunchAuthorityError
        payload = validate_payload('plan',payload)
        prep,ref = self._preparation(payload['preparation'])
        try:
            prior = self.store.get(self.principal,payload['launch_id'])
        except LaunchAuthorityError as error:
            if error.code!='not_found':
                raise
        else:
            if prior.request_sha256!=digest(payload):
                raise NativeLaunchError('conflict')
            return self._dto(prior,'plan',payload)
        opts = prep.frozen['public']['options']
        model = self.model_factory or BoundedNativeModelFactory(tuple(opts['platforms']),self.limits,None)
        request,_ = self.preparation.bind_native(ref,run_id=UUID(payload['launch_id']),
                runtime_sha256=self.runtime_sha256,model_factory=model)
        identity = {'schema_version':1,'display_graph_id':self.display_graph_id,'scope':self.scope_dto,
            'preparation':{'operation_id':str(prep.operation_id),'plan_sha256':prep.plan_sha256,
                           'simulation_id':request.simulation_id,'artifact_sha256':request.artifact_sha256},
            'request':request.to_wire(),'limits':dict(self.limits),
            'ceiling_microusd':str(self.ceiling) if type(self.ceiling) is int and 1<=self.ceiling<=2**63-1 else None,
            'model_label':self.model_label}
        return self._dto(self.store.put(self.principal,payload,identity,configuration=self._configuration()),'plan',payload)

    def start(self,payload):
        from mirofish_knowledge.contracts import KnowledgeScope
        from mirofish_execution.budget import ReservationState
        payload = validate_payload('start',payload)
        row = self._row(payload)
        self._current(row)
        if row.state!='planned':
            return self._dto(self._recover(row),'start',payload)
        if not self.authorization(row)['model_calls_enabled']:
            raise NativeLaunchError('model_calls_disabled')
        scope = KnowledgeScope.model_validate_json(json.dumps(self.scope_dto))
        reservation = self.budget.reserve_native(self.principal,self.account_id,scope,row.request,
                                                 row.launch_sha256,int(row.frozen['identity']['ceiling_microusd']))
        if reservation.state!=ReservationState.reserved:
            raise NativeLaunchError('conflict')
        try:
            row,queued = self.store.queue(self.principal,row.run_id,row.launch_sha256,reservation.attempt_id)
        except Exception:
            # Queue acknowledgement can be lost after commit. The row-lock
            # close serializes with every same-ID queue and permanently closes
            # an unclaimed review BEFORE any shared reservation is released.
            try:
                _,closed = self.store.close_undispatched(self.principal,row.run_id,row.launch_sha256)
                if closed:
                    self.budget.release_undispatched(self.principal,self.account_id,row.run_id,reservation.attempt_id)
            except Exception:
                pass
            raise
        if not queued:
            row,closed = self.store.close_undispatched(self.principal,row.run_id,row.launch_sha256)
            if closed:
                self.budget.release_undispatched(self.principal,self.account_id,row.run_id,reservation.attempt_id)
            return self._dto(self._recover(row),'start',payload)
        try:
            # Account conservatively before the scheduler may dispatch. Even
            # an unacknowledged scheduler call keeps the full ceiling held.
            self.budget.start(self.principal,self.account_id,row.run_id,reservation.attempt_id)
            ref = self.temporal_call('start',row.request,None)
            from mirofish_execution.temporal_native_host import NativeWorkflowRef
            if not isinstance(ref,NativeWorkflowRef):
                raise NativeLaunchError('native_launch_uncertain')
            row = self.store.workflow(self.principal,row.run_id,row.launch_sha256,asdict(ref))
        except Exception:
            try:
                self.budget.mark_uncertain(self.principal,self.account_id,row.run_id,reservation.attempt_id,'dispatch_uncertain')
            except Exception:
                pass
            try:
                self.store.scheduling_uncertain(self.principal,row.run_id,row.launch_sha256)
            except Exception:
                pass
            raise NativeLaunchError('native_launch_uncertain') from None
        return self._dto(self._recover(row),'start',payload)

    def _recover(self,row):
        from mirofish_execution.native_run_contracts import NativeRunDenied
        from mirofish_knowledge.contracts import KnowledgeScope
        if row.state in {'planned','cancelled'} and row.budget_attempt_id is None:
            return row
        try:
            native = self.native.reconcile_expired(self.principal,row.run_id)
        except NativeRunDenied:
            # Absence does not prove an unacknowledged scheduler did not run.
            return row
        row = self.store.observe(self.principal,row.run_id,row.launch_sha256,native)
        if native.receipt is not None:
            self.budget.settle_native(self.principal,UUID(row.frozen['configuration']['account_id']),
                KnowledgeScope.model_validate_json(json.dumps(self.scope_dto)),row.request,
                row.budget_attempt_id,row.launch_sha256,native.receipt)
        return row

    def status(self,payload):
        payload = validate_payload('status',payload)
        row = self._row(payload)
        self._current(row)
        return self._dto(self._recover(row),'status',payload)

    def cancel(self,payload):
        from mirofish_execution.native_run_contracts import NativeRunDenied
        payload = validate_payload('cancel',payload)
        row = self._row(payload)
        self._current(row)
        row = self.store.cancel(self.principal,row.run_id,row.launch_sha256)
        if row.budget_attempt_id is not None:
            try:
                self.native.request_cancel(self.principal,row.run_id)
            except NativeRunDenied:
                pass
            if row.workflow is not None and self.temporal_call is not None:
                try:
                    from mirofish_execution.temporal_native_host import NativeWorkflowRef
                    self.temporal_call('cancel',row.request,NativeWorkflowRef(**row.workflow))
                except Exception:
                    # Persisted intent survives unavailable remote owner.
                    pass
        return self._dto(self._recover(row),'cancel',payload)

    def supervisor_factory(self,request):
        """Trusted Temporal callback: authorize frozen PG row before files."""
        from mirofish_execution.native_run_contracts import NativeRunRequest
        from mirofish_execution.budgeted_native_supervisor import BudgetedNativeSupervisor
        from mirofish_knowledge.contracts import KnowledgeScope
        from .native_prepared_host import NativePreparedHost
        request = NativeRunRequest.from_wire(request)
        row = self.store.get(self.principal,request.run_id)
        if row.request!=request or row.state not in {'queued','uncertain'} or row.budget_attempt_id is None:
            raise NativeLaunchError('conflict')
        _,ref = self._current(row)
        if row.cancel_requested or not self.authorization(row)['model_calls_enabled']:
            raise NativeLaunchError('model_calls_disabled')
        reservation = self.budget.native_reservation(self.principal,self.account_id,request,row.launch_sha256)
        from mirofish_execution.budget import ReservationState
        from mirofish_execution.native_launch_contracts import native_budget_fingerprint
        if (reservation is None or reservation.attempt_id!=row.budget_attempt_id
                or reservation.fingerprint!=native_budget_fingerprint(row.launch_sha256)
                or reservation.state not in {ReservationState.started,ReservationState.uncertain}):
            raise NativeLaunchError('budget_denied')
        bound,factory = self.preparation.bind_native(ref,run_id=request.run_id,
                runtime_sha256=request.runtime_sha256,model_factory=self.model_factory)
        if bound!=request:
            raise NativeLaunchError('conflict')
        factory = replace(factory,timeout_seconds=row.frozen['identity']['limits']['max_run_seconds'])
        host = NativePreparedHost(principal=self.principal,request=request,session_factory=factory,
            connection_factory=self.connect,dispatch_allowed=lambda req:self._dispatch_allowed(row,req))
        # Use the existing driver/coordinator; replace only the unstarted
        # supervisor with its permitted ledger-settling subclass.
        return BudgetedNativeSupervisor(request,host.supervisor.coordinator,ledger=self.budget,
            account_id=self.account_id,scope=KnowledgeScope.model_validate_json(json.dumps(self.scope_dto)),
            budget_attempt_id=row.budget_attempt_id,launch_sha256=row.launch_sha256,
            poll_seconds=host.supervisor.poll_seconds,call_budget_seconds=host.supervisor.call_budget_seconds)

    def _dispatch_allowed(self,row,request):
        if request!=row.request or not self.authorization(row)['model_calls_enabled']:
            return False
        current=self.store.get(self.principal,request.run_id,row.launch_sha256)
        self._current(current)
        return current.dispatch_claimed and not current.cancel_requested

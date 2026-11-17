"""Trusted READY→frozen PG launch→existing Temporal/owned native composition.

The optional synchronous Temporal bridge is operator-owned: it invokes the
injected host on its existing loop with a finite timeout. A lost reply never
causes redispatch. No clients, threads or loops are constructed here.
"""
from dataclasses import asdict, replace
import json
import hashlib
import math
import pickle
from uuid import UUID, NAMESPACE_URL, uuid5
from .native_launch_client import (NativeLaunchError, IDENTITY, digest, encoded,
    validate_payload, validate_result, validate_limits, text)
from .native_launch_models import BoundedNativeModelFactory


def _validate_checkpoint_config(prep, root):
    from nexaweave_execution.native_owned_binding import _regular_bound_file
    from nexaweave_execution.native_seed_contracts import validate_native_seed_plan
    from .native_recording_contracts import MAX_JSON, strict_json
    from .native_simulation import parse_execution_controls

    config_name = 'simulation_config.json'
    descriptor = next(item for item in prep.receipt['files'] if item['name'] == config_name)
    raw = _regular_bound_file(root, config_name, MAX_JSON)
    if len(raw) != descriptor['size'] or hashlib.sha256(raw).hexdigest() != descriptor['sha256']:
        raise ValueError
    config = strict_json(raw)
    if len(validate_native_seed_plan(config)) not in (1, 2):
        raise ValueError
    timing = config.get('time_config')
    if type(timing) is not dict:
        raise ValueError
    hours, interval = timing.get('total_simulation_hours'), timing.get('minutes_per_round')
    if (type(hours) is not int or not 1 <= hours <= 168
            or type(interval) is not int or not 1 <= interval <= 1440
            or hours * 60 // interval < 2):
        raise ValueError
    lower, upper = timing.get('agents_per_hour_min', 5), timing.get('agents_per_hour_max', 20)
    if type(lower) is not int or type(upper) is not int or not 0 <= lower <= upper:
        raise ValueError
    peak = timing.get('peak_hours', [9, 10, 11, 14, 15, 20, 21, 22])
    off_peak = timing.get('off_peak_hours', [0, 1, 2, 3, 4, 5])
    if any(not _valid_checkpoint_hours(values) for values in (peak, off_peak)):
        raise ValueError
    multipliers = (timing.get('peak_activity_multiplier', 1.5),
                   timing.get('off_peak_activity_multiplier', 0.3))
    if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
           for value in multipliers):
        raise ValueError
    active_hours = (0, interval // 60 % 24)
    for hour in active_hours:
        multiplier = multipliers[0] if hour in peak else multipliers[1] if hour in off_peak else 1.0
        if not 1 <= lower * multiplier <= upper * multiplier < 2:
            raise ValueError

    agents = config.get('agent_configs')
    if type(agents) is not list or len(agents) != 2:
        raise ValueError
    for platform_id, agent in enumerate(agents):
        if (type(agent) is not dict or type(agent.get('agent_id')) is not int
                or agent['agent_id'] != platform_id):
            raise ValueError
        hours_for_agent = agent.get('active_hours', list(range(8, 23)))
        activity = agent.get('activity_level', 0.5)
        if (not _valid_checkpoint_hours(hours_for_agent)
                or not all(hour in hours_for_agent for hour in active_hours)
                or type(activity) not in (int, float) or activity != 1):
            raise ValueError
    if parse_execution_controls(config, ('twitter',), dict(enumerate(agents))) is not None:
        raise ValueError


def _valid_checkpoint_hours(values):
    return (type(values) is list and len(values) <= 24
            and all(type(value) is int and 0 <= value <= 23 for value in values))


class DurableNativeLaunchHost:
    def __init__(self, *, preparation_host, connection_factory, runtime_sha256,
                 model_label='unconfigured', limits=None, model_factory=None,
                 account_id=None, ceiling_microusd=None, authorize=None,
                 temporal_host=None, temporal_call=None):
        from nexaweave_execution.native_launch_store import NativeLaunchStore
        from nexaweave_execution.native_run_store import NativeRunStore
        from nexaweave_execution.budget import BudgetLedger
        from nexaweave_execution.native_run_contracts import sha256
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
        from nexaweave_execution.temporal_native_host import TemporalNativeHost
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
        self.preparation._owned_row(row,expected_revision=row.project_revision)
        if row.state != 'ready' or row.receipt is None:
            raise NativeLaunchError('conflict')
        validate_receipt(row.receipt,str(row.operation_id),row.frozen['public']['options']['platforms'])
        return row,ref

    def _row(self,payload):
        row = self.store.get(self.principal,payload['launch_id'],payload.get('launch_sha256'))
        if row.frozen['identity']['scope']!=self.scope_dto or row.frozen['identity']['display_graph_id']!=self.display_graph_id:
            raise NativeLaunchError('not_found')
        if row.frozen['identity']['schema_version'] != payload['schema_version']:
            raise NativeLaunchError('conflict')
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

    @staticmethod
    def _preparation_descriptor(prep):
        return {'operation_id': str(prep.operation_id), 'plan_sha256': prep.plan_sha256,
                'simulation_id': prep.receipt['simulation_id'],
                'artifact_sha256': prep.receipt['artifact_sha256']}

    def _checkpoint_request(self, prep, launch_id, checkpoint):
        from nexaweave_execution.native_run_contracts import NativeRunRequest

        opts = prep.frozen['public']['options']
        if opts['platforms'] != ['twitter'] or opts['max_rounds'] != 1:
            raise NativeLaunchError('conflict')
        wire = {'schema_version': 1, 'principal': self.principal, 'project_id': str(prep.project_id),
                'project_revision': prep.project_revision, 'simulation_id': prep.receipt['simulation_id'],
                'run_id': launch_id, 'artifact_sha256': prep.receipt['artifact_sha256'],
                'runtime_sha256': self.runtime_sha256, 'platforms': opts['platforms'],
                'seed': opts['seed'], 'max_rounds': opts['max_rounds']}
        if checkpoint['mode'] == 'continue':
            wire.update(schema_version=2, source_run_id=checkpoint['source_run_id'],
                        checkpoint_sha256=checkpoint['checkpoint_sha256'])
        return NativeRunRequest.from_wire(wire)

    def _checkpoint_producer(self, request, preparation):
        from nexaweave_execution.native_launch_contracts import LaunchAuthorityError
        from nexaweave_execution.native_run_contracts import NativeRunDenied, NativeRunReceipt, RunState

        original = replace(request, schema_version=1, run_id=request.source_run_id,
                           source_run_id=None, checkpoint_sha256=None)
        try:
            producer = self.store.get(self.principal, request.source_run_id)
        except LaunchAuthorityError as error:
            if error.code != 'not_found':
                raise
            raise NativeLaunchError('conflict') from None
        identity = producer.frozen['identity']
        if (producer.state != 'completed' or not producer.dispatch_claimed or producer.receipt is None
                or identity['schema_version'] != 2 or identity.get('checkpoint') != {'mode': 'capture'}
                or identity['display_graph_id'] != self.display_graph_id or identity['scope'] != self.scope_dto
                or identity['preparation'] != preparation or producer.request != original):
            raise NativeLaunchError('conflict')
        receipt = NativeRunReceipt.from_wire(producer.receipt)
        if receipt.evidence_sha256 != request.checkpoint_sha256:
            raise NativeLaunchError('conflict')
        try:
            native = self.native.get(self.principal, request.source_run_id)
        except NativeRunDenied:
            raise NativeLaunchError('conflict') from None
        if (native.state != RunState.completed or native.request != original
                or native.fingerprint != original.fingerprint or native.receipt != receipt
                or native.child is None or native.child.instance_id != receipt.instance_id):
            raise NativeLaunchError('conflict')
        return native.child

    def _checkpoint_factory(self, prep, ref, request, checkpoint, *, preflight=False):
        from nexaweave_execution.native_twitter_checkpoint_binding import (
            NativeRestoredTwitterFactory, NativeTwitterCaptureFactory, _directory, _require_disjoint,
        )
        from .native_recording_contracts import RecordingAnchors

        if request != self._checkpoint_request(prep, str(request.run_id), checkpoint):
            raise NativeLaunchError('conflict')
        public = prep.frozen['public']
        if len(public['actors']) != 2 or len({actor['name'] for actor in public['actors']}) != 2:
            raise NativeLaunchError('conflict')
        model = self.model_factory
        if (model is None or model.platforms != ('twitter',) or model.limits != self.limits
                or not model.configured()):
            raise NativeLaunchError('model_calls_disabled')
        producer_child = None
        if request.source_run_id is not None:
            producer_child = self._checkpoint_producer(request, self._preparation_descriptor(prep))
        bound, prepared = self.preparation.bind_native(
            ref, run_id=request.run_id, runtime_sha256=request.runtime_sha256, model_factory=model,
        )
        if bound != replace(request, schema_version=1, source_run_id=None, checkpoint_sha256=None):
            raise NativeLaunchError('conflict')
        prepared = replace(prepared, timeout_seconds=self.limits['max_run_seconds'])
        try:
            root = _directory(self.preparation.root)
            prepared_root = _directory(prepared.simulation_dir)
            if prepared_root != root / request.simulation_id:
                raise ValueError
            _validate_checkpoint_config(prep, prepared_root)
            producer_id = request.source_run_id or request.run_id
            checkpoint_dir = _directory(root / ('native_checkpoint_' + producer_id.hex),
                                        output=producer_child is None)
            lineage_dir = _directory(root / ('native_lineage_' + producer_id.hex),
                                     output=producer_child is None)
            if producer_child is None:
                _require_disjoint(prepared_root, checkpoint_dir, lineage_dir)
                branch_id = uuid5(NAMESPACE_URL, 'mirofish:native-checkpoint:v1:' + str(producer_id))
                anchors = RecordingAnchors(
                    prepared.graph_id, request.simulation_id, str(producer_id), str(branch_id),
                    str(request.project_id), request.project_revision,
                )
                return NativeTwitterCaptureFactory(prepared, str(checkpoint_dir), str(lineage_dir),
                                                   anchors.wire())
            child_dir = _directory(root / ('native_child_' + request.run_id.hex), output=True)
            _require_disjoint(prepared_root, checkpoint_dir, lineage_dir, child_dir)
            restoration = NativeRestoredTwitterFactory(
                prepared, str(checkpoint_dir), str(lineage_dir), str(child_dir),
                request.checkpoint_sha256, producer_child.process_id, str(producer_id), None,
            )
            if preflight:
                restoration._checked_restore(request)
            return restoration
        except (ValueError, TypeError, KeyError, OSError, OverflowError):
            raise NativeLaunchError('conflict') from None

    def _dto(self,row,method,payload):
        cleanup = {'known':False,'pending':None,'owner_thread_alive':None}
        if self.temporal is not None and self.temporal_call is not None and row.state!='planned':
            try:
                local = self.temporal_call('local_status',row.request,None)
                # Only the actual retained local registry can qualify cleanup.
                from nexaweave_execution.temporal_native_activities import LocalNativeStatus
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
        from nexaweave_execution.native_launch_contracts import LaunchAuthorityError
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
        checkpoint = payload.get('checkpoint')
        if checkpoint is None:
            model = self.model_factory or BoundedNativeModelFactory(tuple(opts['platforms']),self.limits,None)
            request,_ = self.preparation.bind_native(ref,run_id=UUID(payload['launch_id']),
                    runtime_sha256=self.runtime_sha256,model_factory=model)
        else:
            request = self._checkpoint_request(prep, payload['launch_id'], checkpoint)
            self._checkpoint_factory(prep, ref, request, checkpoint)
        identity = {'schema_version':payload['schema_version'],'display_graph_id':self.display_graph_id,'scope':self.scope_dto,
            'preparation':self._preparation_descriptor(prep),
            'request':request.to_wire(),'limits':dict(self.limits),
            'ceiling_microusd':str(self.ceiling) if type(self.ceiling) is int and 1<=self.ceiling<=2**63-1 else None,
            'model_label':self.model_label}
        if checkpoint is not None:
            identity['checkpoint'] = checkpoint
        return self._dto(self.store.put(self.principal,payload,identity,configuration=self._configuration()),'plan',payload)

    def start(self,payload):
        from nexaweave_knowledge.contracts import KnowledgeScope
        from nexaweave_execution.budget import ReservationState
        payload = validate_payload('start',payload)
        row = self._row(payload)
        prep, ref = self._current(row)
        if row.state!='planned':
            return self._dto(self._recover(row),'start',payload)
        if not self.authorization(row)['model_calls_enabled']:
            raise NativeLaunchError('model_calls_disabled')
        checkpoint = row.frozen['identity'].get('checkpoint')
        if checkpoint is not None:
            self._checkpoint_factory(prep, ref, row.request, checkpoint, preflight=True)
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
            from nexaweave_execution.temporal_native_host import NativeWorkflowRef
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
        from nexaweave_execution.native_run_contracts import NativeRunDenied
        from nexaweave_knowledge.contracts import KnowledgeScope
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
        from nexaweave_execution.native_run_contracts import NativeRunDenied
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
                    from nexaweave_execution.temporal_native_host import NativeWorkflowRef
                    self.temporal_call('cancel',row.request,NativeWorkflowRef(**row.workflow))
                except Exception:
                    # Persisted intent survives unavailable remote owner.
                    pass
        return self._dto(self._recover(row),'cancel',payload)

    def supervisor_factory(self,request):
        """Trusted Temporal callback: authorize frozen PG row before files."""
        from nexaweave_execution.native_run_contracts import NativeRunRequest
        from nexaweave_execution.budgeted_native_supervisor import BudgetedNativeSupervisor
        from nexaweave_knowledge.contracts import KnowledgeScope
        from .native_prepared_host import NativePreparedHost
        request = NativeRunRequest.from_wire(request)
        row = self.store.get(self.principal,request.run_id)
        if row.request!=request or row.state not in {'queued','uncertain'} or row.budget_attempt_id is None:
            raise NativeLaunchError('conflict')
        prep,ref = self._current(row)
        if row.cancel_requested or not self.authorization(row)['model_calls_enabled']:
            raise NativeLaunchError('model_calls_disabled')
        reservation = self.budget.native_reservation(self.principal,self.account_id,request,row.launch_sha256)
        from nexaweave_execution.budget import ReservationState
        from nexaweave_execution.native_launch_contracts import native_budget_fingerprint
        if (reservation is None or reservation.attempt_id!=row.budget_attempt_id
                or reservation.fingerprint!=native_budget_fingerprint(row.launch_sha256)
                or reservation.state not in {ReservationState.started,ReservationState.uncertain}):
            raise NativeLaunchError('budget_denied')
        checkpoint = row.frozen['identity'].get('checkpoint')
        if checkpoint is None:
            bound,factory = self.preparation.bind_native(ref,run_id=request.run_id,
                    runtime_sha256=request.runtime_sha256,model_factory=self.model_factory)
            if bound!=request:
                raise NativeLaunchError('conflict')
            factory = replace(factory,timeout_seconds=row.frozen['identity']['limits']['max_run_seconds'])
        else:
            factory = self._checkpoint_factory(prep, ref, request, checkpoint)
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
        if request.source_run_id is not None:
            self._checkpoint_producer(request, current.frozen['identity']['preparation'])
        return current.dispatch_claimed and not current.cancel_requested

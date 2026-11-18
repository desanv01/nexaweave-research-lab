"""Receipt-bound observation reads and explicit pending event admission."""
import hashlib
import json
from uuid import uuid5
from .native_observations_client import (NativeObservationsError, validate_payload, validate_result,
    launch_reference, encoded, stage_operation_id, stage_scope, validate_stage_payload,
    validate_dispatch_payload)
from .native_observation_reader import NativeObservationReader
from .preparation_client import canonical


class NativeObservationsHost:
    def __init__(self, *, launch_host, event_writer=None, event_verifier=None):
        from .durable_native_launch_host import DurableNativeLaunchHost
        if not isinstance(launch_host, DurableNativeLaunchHost):
            raise NativeObservationsError('observations_unavailable')
        self.launch = launch_host
        self.principal, self.display_graph_id = launch_host.principal, launch_host.display_graph_id
        self.scope_dto = json.loads(encoded(launch_host.scope_dto))
        self.reader = NativeObservationReader()
        self.event_writer = event_writer
        self.event_verifier = event_verifier

    def _authority(self, payload):
        row = self.launch._row(launch_reference(payload))
        prep, _ = self.launch._current(row)
        _, _, retained = self.launch.preparation._owned_row(prep, expected_revision=prep.project_revision)
        if retained.text_sha256 != prep.frozen['public']['source']['source_sha256']:
            raise NativeObservationsError('conflict')
        if (row.state != 'completed' or row.receipt is None or not row.dispatch_claimed
                or row.budget_attempt_id is None or row.workflow is None):
            raise NativeObservationsError('conflict')
        native = self.launch.native.get(self.principal, row.run_id)
        if (native.request != row.request or native.fingerprint != row.request.fingerprint
                or native.state.value != 'completed' or native.receipt is None
                or native.receipt.to_wire() != row.receipt or native.owner_id is None
                or native.attempt_id != native.receipt.attempt_id or native.child is None
                or native.child.instance_id != native.receipt.instance_id):
            raise NativeObservationsError('conflict')
        if payload['platform'] not in row.request.platforms:
            raise NativeObservationsError('conflict')
        if self.event_writer is not None and row.request.runtime_sha256 != self.launch.runtime_sha256:
            raise NativeObservationsError('conflict')
        # Existing authorization inspects frozen configuration only; it never constructs
        # providers. Cleanup remains unknown because runtime polling is not a read here.
        dto = json.loads(encoded(row.frozen['identity']))
        dto.update(launch_sha256=row.launch_sha256, state=row.state, error_code=row.error_code,
                   authorization=self.launch.authorization(row), workflow=row.workflow, receipt=row.receipt,
                   cancel_requested=row.cancel_requested,
                   cleanup={'known': False, 'pending': None, 'owner_thread_alive': None})
        from .native_launch_client import validate_result as validate_launch
        dto = validate_launch(dto, self.display_graph_id, self.scope_dto, launch_reference(payload), 'status')
        if dto['request']['principal'] != self.principal:
            raise NativeObservationsError('unauthorized')
        if dto['schema_version'] == 2:
            if dto['checkpoint']['mode'] != 'continue' or row.cancel_requested or native.cancel_requested:
                raise NativeObservationsError('conflict')
            self.launch._checkpoint_producer(row.request, row.frozen['identity']['preparation'])
        if prep.frozen['public']['schema_version'] == 2:
            self.launch.preparation._owned_row(prep, expected_revision=prep.project_revision)
        return row, prep, native, dto

    def page(self, payload):
        result, _, _, _ = self._read_page(validate_payload(payload))
        return result

    def _read_page(self, payload):
        row, prep, native, launch = self._authority(payload)  # All authority failures before file access.
        base = self.launch.preparation.root
        prepared_root = base / ('sim_' + prep.operation_id.hex)
        if self._artifacts(prep, prepared_root) != prep.receipt:
            raise NativeObservationsError('conflict')
        restored = launch['schema_version'] == 2
        output_root = base / ('native_child_' + row.run_id.hex) if restored else prepared_root
        reader_options = {"restored": True} if restored else {}
        with self.reader.page(output_root, row.request.platforms, payload['platform'], payload['offset'],
                              payload['limit'], row.receipt['evidence_sha256'], **reader_options) as observed:
            after, current, native_after, final_launch = self._authority(payload)
            if (after != row or current != prep or native_after != native or final_launch != launch
                    or self._artifacts(current, prepared_root) != current.receipt):
                raise NativeObservationsError('conflict')
            result = validate_result(dict(observed, schema_version=launch['schema_version'], launch=launch),
                                     self.display_graph_id, self.scope_dto, payload, self.principal)
        return result, row, prep, native

    def _event(self, payload):
        from nexaweave_execution.native_launch_store import _verify_checkpoint_source, _verify_current_preparation
        from nexaweave_knowledge.bindings import ScopeBindingStore
        from nexaweave_knowledge.contracts import KnowledgeScope, SourceEnvelope
        from nexaweave_knowledge.native_observation_event_bootstrap import event_ontology
        from nexaweave_knowledge.operations import request_fingerprint

        index = payload['record_index']
        page, run, prep, native = self._read_page(dict(
            launch_reference(payload), platform=payload['platform'], offset=index - 1, limit=3,
        ))
        # The restored writer appends one action bracketed by its round markers.
        # Earlier successful actions belong to the copied checkpoint prefix.
        if page['total_records'] != index + 2 or len(page['records']) != 3:
            raise NativeObservationsError('conflict')
        first, selected, last = page['records']
        if selected['index'] != index or selected['record_sha256'] != payload['record_sha256']:
            raise NativeObservationsError('conflict')
        start, action, end = (json.loads(item['raw_json']) for item in (first, selected, last))
        args = action.get('action_args')
        round_number = action.get('round')
        if (type(round_number) is not int or round_number < 1
                or start.get('event_type') != 'round_start' or end.get('event_type') != 'round_end'
                or any(type(marker.get('round')) is not int or marker['round'] != round_number
                       for marker in (start, end))
                or type(end.get('actions_count')) is not int or end['actions_count'] != 1
                or action.get('action_type') != 'CREATE_POST' or action.get('success') is not True
                or type(args) is not dict or type(args.get('content')) is not str or not args['content']
                or type(args.get('post_id')) is not int or args['post_id'] < 1):
            raise NativeObservationsError('conflict')

        source_scope = KnowledgeScope.model_validate_json(encoded(self.scope_dto))
        scope = KnowledgeScope.model_validate_json(encoded(stage_scope(self.scope_dto, payload['launch_id'])))
        operation = stage_operation_id(payload['launch_id'], index)
        ontology = event_ontology()
        content = encoded({
            'launch_sha256': run.launch_sha256, 'evidence_sha256': native.receipt.evidence_sha256,
            'platform': 'twitter', 'record_index': index, 'record_sha256': selected['record_sha256'],
            'raw_json': selected['raw_json'],
        })
        source = SourceEnvelope(
            source_revision=operation, source_sha256=hashlib.sha256(content).hexdigest(),
            ontology_revision=ontology.revision, operation_id=operation, source_kind='event',
            content=content.decode('utf-8'), source_name='Restored Twitter action',
            recorded_at=native.updated_at,
            evidence_ids=(uuid5(operation, 'mirofish:restored-twitter-action-evidence:v1'),),
        )
        fingerprint = request_fingerprint(scope, source, ontology)

        def authorize_admission(conn):
            ScopeBindingStore(self.launch.connect).resolve_for_write(
                conn, self.principal, self.display_graph_id, source_scope)
            # Follow the native launch queue's lock order. Keep all locks through
            # the pending operation insert on this same transaction.
            current = self.launch.store._get(conn, self.principal, run.run_id, lock=True)
            if current != run:
                raise NativeObservationsError('conflict')
            identity = run.frozen['identity']
            _verify_current_preparation(conn, run.request, identity)
            if self.launch.preparation.store._get(conn, self.principal, prep.operation_id) != prep:
                raise NativeObservationsError('conflict')
            _verify_checkpoint_source(conn, run.request, identity)
            if self.launch.native._owned(self.principal, run.run_id, lock=True, conn=conn) != native:
                raise NativeObservationsError('conflict')

        return scope, operation, fingerprint, authorize_admission, action, source

    def stage_event(self, payload):
        from nexaweave_knowledge.operations import Conflict, Ledger, NotFound, OperationState, StorageError, Tombstoned

        payload = validate_stage_payload(payload)
        scope, operation, fingerprint, authorize_admission, _, _ = self._event(payload)
        try:
            admitted = Ledger(self.launch.connect).admit(
                scope, operation, fingerprint, authorize_admission=authorize_admission,
            )
        except Tombstoned:
            raise NativeObservationsError('tombstoned') from None
        except (Conflict, NotFound):
            raise NativeObservationsError('conflict') from None
        except StorageError:
            raise NativeObservationsError('observations_unavailable') from None
        if admitted.state != OperationState.pending:
            raise NativeObservationsError('conflict')
        return dict(payload, scope=scope.model_dump(mode='json'), operation_id=str(operation),
                    fingerprint=fingerprint, state='pending', completion_barrier_open=False,
                    graph_ingestion_executed=False)

    def event_status(self, payload):
        from nexaweave_knowledge.operations import (
            CompletionReceipt, Conflict, InvalidTransition, Ledger, NotFound,
            OperationState, StaleAttempt, StorageError, Tombstoned,
        )

        payload = validate_dispatch_payload(payload)
        locator = {key: value for key, value in payload.items() if key != 'expected_fingerprint'}
        scope, operation, fingerprint, authorize, _, source = self._event(locator)
        if fingerprint != payload['expected_fingerprint']:
            raise NativeObservationsError('conflict')
        try:
            row = Ledger(self.launch.connect).read_staged_event(
                scope, operation, fingerprint, authorize_admission=authorize,
            )
        except Tombstoned:
            raise NativeObservationsError('tombstoned') from None
        except NotFound:
            raise NativeObservationsError('not_found') from None
        except (Conflict, InvalidTransition, StaleAttempt):
            raise NativeObservationsError('conflict') from None
        except StorageError:
            raise NativeObservationsError('observations_unavailable') from None
        completed = row.state == OperationState.completed
        expected = CompletionReceipt(scope.group_id, scope.episode_uuid(operation), fingerprint, source.evidence_ids)
        if completed and row.receipt != expected:
            raise NativeObservationsError('conflict')
        return dict(payload, scope=scope.model_dump(mode='json'), operation_id=str(operation),
                    state=row.state.value, attempt_id=str(row.current_attempt) if row.current_attempt else None,
                    completion_barrier_open=completed, receipt=expected.json_value() if completed else None)

    def dispatch_event(self, payload):
        from nexaweave_knowledge.operations import (
            Busy, Claim, CompletionReceipt, Conflict, InvalidTransition, Ledger, NotFound,
            OperationState, StaleAttempt, StorageError, Tombstoned,
        )
        from nexaweave_knowledge.native_observation_event_bootstrap import WIRE_BYTES, event_projection

        payload = validate_dispatch_payload(payload)
        if self.event_writer is None:
            raise NativeObservationsError('observations_unavailable')
        locator = {key: value for key, value in payload.items() if key != 'expected_fingerprint'}
        scope, operation, fingerprint, authorize, action, source = self._event(locator)
        if fingerprint != payload['expected_fingerprint']:
            raise NativeObservationsError('conflict')
        actor = action.get('agent_id')
        if type(actor) is not int or not 0 <= actor <= 2**53 - 1:
            raise NativeObservationsError('conflict')
        episode = scope.episode_uuid(operation)
        receipt = CompletionReceipt(scope.group_id, episode, fingerprint, source.evidence_ids)
        frame = {'version': 1, 'scope': scope.model_dump(mode='json'), 'operation_id': str(operation),
                 'attempt_id': str(operation), 'fingerprint': fingerprint, 'source': source.model_dump(mode='json')}
        try:
            if max(len(encoded(frame)), len(encoded(event_projection(frame)))) > WIRE_BYTES:
                raise NativeObservationsError('result_too_large')
        except (KeyError, ValueError, TypeError, OverflowError):
            raise NativeObservationsError('conflict') from None
        ledger = Ledger(self.launch.connect)
        try:
            claimed = ledger.claim_staged(scope, operation, fingerprint, authorize_admission=authorize,
                                         recover_event_ack=self.event_verifier is not None)
        except Tombstoned:
            raise NativeObservationsError('tombstoned') from None
        except Busy:
            raise NativeObservationsError('busy') from None
        except (Conflict, InvalidTransition, NotFound, StaleAttempt):
            raise NativeObservationsError('conflict') from None
        except StorageError:
            raise NativeObservationsError('observations_unavailable') from None

        if isinstance(claimed, Claim):
            def uncertain():
                try:
                    ledger.mark_uncertain(scope, operation, claimed.attempt_id, 'event_ack_uncertain')
                except Exception:
                    # Even if classification fails, the existing running admission fences replay.
                    pass

            try:
                frame['attempt_id'] = str(claimed.attempt_id)
                if canonical(self.event_writer(frame)) != canonical(event_projection(frame)):
                    raise ValueError
            except Exception:
                uncertain()
                raise NativeObservationsError('observations_unavailable') from None
            try:
                claimed = ledger.complete(scope, operation, claimed.attempt_id, receipt)
            except Exception:
                try:
                    observed = ledger.get(scope, operation)
                    if (observed.state != OperationState.completed or observed.current_attempt != claimed.attempt_id
                            or observed.receipt != receipt):
                        raise ValueError
                    claimed = observed
                except Exception:
                    uncertain()
                    raise NativeObservationsError('observations_unavailable') from None
        elif claimed.state == OperationState.uncertain:
            try:
                frame['attempt_id'] = str(claimed.current_attempt)
                if canonical(self.event_verifier(frame)) != canonical(event_projection(frame)):
                    raise ValueError
                claimed = ledger.complete_event_ack(scope, operation, claimed.current_attempt, receipt,
                                                   authorize_admission=authorize)
            except Exception:
                raise NativeObservationsError('observations_unavailable') from None
        if claimed.state != OperationState.completed or claimed.receipt != receipt or claimed.current_attempt is None:
            raise NativeObservationsError('conflict')
        return dict(payload, scope=scope.model_dump(mode='json'), operation_id=str(operation),
                    state='completed', receipt=receipt.json_value())

    def _artifacts(self, prep, root):
        try:
            return self.launch.preparation._artifacts(prep, root)
        except (OSError, ValueError, TypeError, UnicodeError):
            raise NativeObservationsError('evidence_invalid') from None

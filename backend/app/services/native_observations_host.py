"""Trusted authority reads only. No recovery, runtime polling, model or budget calls."""
import json
from .native_observations_client import (NativeObservationsError, validate_payload, validate_result,
    launch_reference, encoded)
from .native_observation_reader import NativeObservationReader


class NativeObservationsHost:
    def __init__(self, *, launch_host):
        from .durable_native_launch_host import DurableNativeLaunchHost
        if not isinstance(launch_host, DurableNativeLaunchHost):
            raise NativeObservationsError('observations_unavailable')
        self.launch = launch_host
        self.principal, self.display_graph_id = launch_host.principal, launch_host.display_graph_id
        self.scope_dto = json.loads(encoded(launch_host.scope_dto))
        self.reader = NativeObservationReader()

    def _authority(self, payload):
        row = self.launch._row(launch_reference(payload))
        prep, _ = self.launch._current(row)
        _, _, retained = self.launch.preparation._owned(prep.frozen['public']['source']['source_revision'],
                                                      expected_revision=prep.project_revision)
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
        return row, prep, native, dto

    def page(self, payload):
        payload = validate_payload(payload)
        row, prep, native, launch = self._authority(payload)  # All authority failures before file access.
        root = self.launch.preparation.root / ('sim_' + prep.operation_id.hex)
        if self._artifacts(prep, root) != prep.receipt:
            raise NativeObservationsError('conflict')
        with self.reader.page(root, row.request.platforms, payload['platform'], payload['offset'],
                              payload['limit'], row.receipt['evidence_sha256']) as observed:
            after, current, native_after, final_launch = self._authority(payload)
            if (after != row or current != prep or native_after != native or final_launch != launch
                    or self._artifacts(current, root) != current.receipt):
                raise NativeObservationsError('conflict')
            result = validate_result(dict(observed, schema_version=1, launch=launch), self.display_graph_id,
                                     self.scope_dto, payload, self.principal)
        return result

    def _artifacts(self, prep, root):
        try:
            return self.launch.preparation._artifacts(prep, root)
        except (OSError, ValueError, TypeError, UnicodeError):
            raise NativeObservationsError('evidence_invalid') from None

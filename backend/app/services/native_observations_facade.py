"""Cold optional trusted composition and four-call admission, without SDK imports."""
import threading
from .native_observations_client import (NativeObservationsError, CODES, validate_payload,
                                         validate_stage_payload, validate_dispatch_payload)

TRANSLATIONS = {'native_run_denied': 'unauthorized', 'native_run_conflict': 'conflict',
    'native_run_busy': 'busy', 'native_run_uncertain': 'conflict',
    'native_run_unavailable': 'observations_unavailable', 'native_run_migration_mismatch': 'observations_unavailable',
    'native_launch_unavailable': 'observations_unavailable', 'preparation_unavailable': 'observations_unavailable',
    'preparation_failed': 'evidence_invalid', 'preparation_uncertain': 'conflict'}


class NativeObservationsFacade:
    def __init__(self, settings, *, host=None, event_runtime=None):
        self.settings, self.host = settings, host
        self._event_runtime = event_runtime
        self._configuration = threading.Lock()
        self._calls = threading.BoundedSemaphore(4)

    def execute(self, graph_id, payload):
        return self._execute(graph_id, validate_payload(payload))

    def stage_event(self, graph_id, payload):
        return self._execute(graph_id, validate_stage_payload(payload), operation='stage_event')

    def dispatch_event(self, graph_id, payload):
        return self._execute(graph_id, validate_dispatch_payload(payload), operation='dispatch_event')

    def _execute(self, graph_id, payload, *, operation='page'):
        if graph_id != self.settings.display_graph_id:
            raise NativeObservationsError('not_found')
        if not self._calls.acquire(blocking=False):
            raise NativeObservationsError('busy')
        try:
            if self.host is None and self._event_runtime is not None:
                with self._configuration:
                    if self.host is None:
                        try:
                            self.host = self._event_runtime.host(self.settings)
                        except (ImportError, OSError, ValueError):
                            raise NativeObservationsError('observations_unavailable') from None
            if self.host is None or (self.host.principal != self.settings.principal
                    or self.host.display_graph_id != graph_id or self.host.scope_dto != self.settings.scope):
                raise NativeObservationsError('observations_unavailable')
            return getattr(self.host, operation)(payload)
        except NativeObservationsError:
            raise
        except Exception as error:
            code = getattr(error, 'code', None)
            raise NativeObservationsError(code if code in CODES else TRANSLATIONS.get(code, 'internal_error')) from None
        finally:
            self._calls.release()

"""Cold optional trusted composition and four-call admission, without SDK imports."""
import threading
from .native_observations_client import NativeObservationsError, CODES, validate_payload

TRANSLATIONS = {'native_run_denied': 'unauthorized', 'native_run_conflict': 'conflict',
    'native_run_busy': 'busy', 'native_run_uncertain': 'conflict',
    'native_run_unavailable': 'observations_unavailable', 'native_run_migration_mismatch': 'observations_unavailable',
    'native_launch_unavailable': 'observations_unavailable', 'preparation_unavailable': 'observations_unavailable',
    'preparation_failed': 'evidence_invalid', 'preparation_uncertain': 'conflict'}


class NativeObservationsFacade:
    def __init__(self, settings, *, host=None):
        self.settings, self.host = settings, host
        self._calls = threading.BoundedSemaphore(4)

    def execute(self, graph_id, payload):
        payload = validate_payload(payload)
        if graph_id != self.settings.display_graph_id:
            raise NativeObservationsError('not_found')
        if self.host is None or (self.host.principal != self.settings.principal
                or self.host.display_graph_id != graph_id or self.host.scope_dto != self.settings.scope):
            raise NativeObservationsError('observations_unavailable')
        if not self._calls.acquire(blocking=False):
            raise NativeObservationsError('busy')
        try:
            return self.host.page(payload)
        except NativeObservationsError:
            raise
        except Exception as error:
            code = getattr(error, 'code', None)
            raise NativeObservationsError(code if code in CODES else TRANSLATIONS.get(code, 'internal_error')) from None
        finally:
            self._calls.release()

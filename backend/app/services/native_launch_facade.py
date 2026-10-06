"""Lazy trusted composition only; cold HTTP has no native capability."""
import threading
from .native_launch_client import NativeLaunchError, CODES, validate_payload

TRANSLATIONS = {
    'preparation_unavailable': 'native_launch_unavailable', 'preparation_uncertain': 'native_launch_uncertain',
    'native_run_denied': 'unauthorized', 'native_run_conflict': 'conflict', 'native_run_busy': 'busy',
    'native_run_uncertain': 'native_launch_uncertain', 'native_run_unavailable': 'native_launch_unavailable',
    'native_run_migration_mismatch': 'native_launch_unavailable', 'budget_busy': 'conflict',
    'budget_conflict': 'conflict', 'budget_uncertain': 'native_launch_uncertain',
    'budget_store_unavailable': 'native_launch_unavailable', 'budget_migration_mismatch': 'native_launch_unavailable'}


class NativeLaunchFacade:
    def __init__(self, settings, *, host=None):
        self.settings, self.host = settings, host
        self._calls = threading.BoundedSemaphore(4)

    def execute(self, method, graph_id, payload):
        payload = validate_payload(method, payload)
        if graph_id != self.settings.display_graph_id:
            raise NativeLaunchError('not_found')
        if self.host is None or (self.host.principal != self.settings.principal
                or self.host.display_graph_id != graph_id or self.host.scope_dto != self.settings.scope):
            raise NativeLaunchError('native_launch_unavailable')
        if not self._calls.acquire(blocking=False):
            raise NativeLaunchError('busy')
        try:
            return getattr(self.host, method)(payload)
        except NativeLaunchError:
            raise
        except Exception as error:
            code = getattr(error, 'code', None)
            raise NativeLaunchError(code if code in CODES else TRANSLATIONS.get(code, 'internal_error')) from None
        finally:
            self._calls.release()

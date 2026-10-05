"""Lazy opt-in facade. Ordinary HTTP imports create no model/Temporal clients."""
from .preparation_client import PreparationError, validate_payload
import threading


class KnowledgePreparationFacade:
    def __init__(self, settings, *, host=None):
        self.settings = settings
        self.host = host
        self._calls = threading.BoundedSemaphore(4)

    def execute(self, method, graph_id, payload):
        payload = validate_payload(method, payload)
        if graph_id != self.settings.display_graph_id:
            raise PreparationError('not_found')
        if self.host is None:
            raise PreparationError('preparation_unavailable')
        if (self.host.principal != self.settings.principal
                or self.host.display_graph_id != graph_id
                or self.host.scope_dto != self.settings.scope):
            raise PreparationError('preparation_unavailable')
        if not self._calls.acquire(blocking=False):
            raise PreparationError('busy')
        try:
            return getattr(self.host, method)(payload)
        except PreparationError:
            raise
        except Exception as error:
            from .preparation_client import CODES
            code = getattr(error, 'code', None)
            if code in CODES:
                raise PreparationError(code) from None
            if code in {'budget_busy', 'budget_conflict'}:
                raise PreparationError('conflict') from None
            if code in {'budget_uncertain'}:
                raise PreparationError('preparation_uncertain') from None
            if code in {'budget_store_unavailable', 'budget_migration_mismatch'}:
                raise PreparationError('preparation_unavailable') from None
            raise PreparationError('internal_error') from None
        finally:
            self._calls.release()

"""Cold optional host, fixed failures, bounded concurrent HTTP admission."""
import threading
from .connected_report_client import ReportError, CODES, validate_payload

TRANSLATIONS = {'native_run_denied': 'unauthorized', 'native_run_conflict': 'conflict',
    'native_run_busy': 'busy', 'native_run_uncertain': 'conflict', 'evidence_invalid': 'conflict',
    'budget_conflict': 'conflict', 'budget_busy': 'busy', 'budget_uncertain': 'report_uncertain',
    'budget_store_unavailable': 'report_unavailable', 'budget_migration_mismatch': 'report_unavailable',
    'native_launch_unavailable': 'report_unavailable', 'preparation_unavailable': 'report_unavailable'}


class ConnectedReportFacade:
    def __init__(self, settings, *, host=None):
        self.settings, self.host = settings, host
        self.calls = threading.BoundedSemaphore(4)

    def execute(self, method, graph_id, payload):
        payload = validate_payload(method, payload)
        if graph_id != self.settings.display_graph_id:
            raise ReportError('not_found')
        if (self.host is None or self.host.principal != self.settings.principal
                or self.host.display_graph_id != graph_id or self.host.scope_dto != self.settings.scope):
            raise ReportError('report_unavailable')
        if not self.calls.acquire(blocking=False):
            raise ReportError('busy')
        try:
            return getattr(self.host, method)(payload)
        except ReportError:
            raise
        except Exception as error:
            code = getattr(error, 'code', None)
            raise ReportError(code if code in CODES else TRANSLATIONS.get(code, 'internal_error')) from None
        finally:
            self.calls.release()

    def known_report(self, graph_id, payload):
        """Durable metadata read only, independent of runtime status recovery."""
        validate_payload('download', payload)
        if graph_id != self.settings.display_graph_id or self.host is None:
            raise ReportError('report_unavailable')
        row = self.host._reauthorize(self.host._row(payload))
        return self.host._dto(row, payload, 'download')

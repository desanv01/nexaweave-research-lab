"""Cold optional follow-up host, bounded concurrent HTTP admission."""
import threading
from .connected_followup_client import FollowupError, CODES, validate_payload

TRANSLATIONS={'native_run_denied':'unauthorized','native_run_conflict':'conflict',
    'native_run_busy':'busy','native_run_uncertain':'conflict',
    'budget_conflict':'turn_conflict','budget_busy':'busy',
    'budget_uncertain':'followup_uncertain','budget_store_unavailable':'followup_unavailable',
    'budget_migration_mismatch':'followup_unavailable','report_unavailable':'followup_unavailable',
    'report_uncertain':'followup_uncertain','preparation_unavailable':'followup_unavailable'}


class ConnectedFollowupFacade:
    def __init__(self,settings,*,host=None):
        self.settings,self.host=settings,host
        self.calls=threading.BoundedSemaphore(4)

    def execute(self,method,graph_id,payload):
        payload=validate_payload(method,payload)
        if graph_id!=self.settings.display_graph_id:
            raise FollowupError('not_found')
        if (self.host is None or self.host.principal!=self.settings.principal
                or self.host.display_graph_id!=graph_id or self.host.scope_dto!=self.settings.scope):
            raise FollowupError('followup_unavailable')
        if not self.calls.acquire(blocking=False):
            raise FollowupError('busy')
        try:
            return getattr(self.host,method)(payload)
        except FollowupError:
            raise
        except Exception as error:
            code=getattr(error,'code',None)
            raise FollowupError(code if code in CODES else TRANSLATIONS.get(code,'internal_error')) from None
        finally:
            self.calls.release()

    def known_turn(self,graph_id,payload):
        validate_payload('download',payload)
        if graph_id!=self.settings.display_graph_id or self.host is None:
            raise FollowupError('followup_unavailable')
        row=self.host._reauthorize(self.host._row(payload))
        return self.host._dto(row,payload,'download')

"""Fixed read-only evidence commands; request scope never confers authority."""
import json
from uuid import UUID

from .stdio import _object, PipeProtocolError
from .read_runtime import _scope_json
from .research_cli import parse_request as parse_research
from .dossier_cli import parse_request as parse_dossier
from .research_contracts import ResearchRequest, ResearchResult, TwoHopRequest, TwoHopResult
from .report_contracts import EvidenceDossier
from .evidence_research import EvidenceResearchService, ResearchFailure
from .evidence_dossier import EvidenceDossierService, DossierFailure

REQUEST_LIMITS = {"research": 16384, "dossier": 32768, "two_hop": 16384}
RESULT_LIMITS = {"research": 2 * 1024 * 1024, "dossier": 4 * 1024 * 1024,
    "two_hop": 2 * 1024 * 1024}
ENVELOPE_OVERHEAD = 1024
ERROR_CODES = frozenset({"invalid_request", "invalid_configuration", "research_unavailable",
    "research_deadline", "research_busy", "dossier_unavailable", "dossier_deadline",
    "dossier_busy", "result_too_large", "invalid_reply"})


def _encode(value, maximum):
    body = bytearray()
    for piece in json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":")).iterencode(value):
        chunk = piece.encode("utf-8")
        if len(body) + len(chunk) > maximum:
            raise ValueError("bounded output exceeded")
        body.extend(chunk)
    return bytes(body)


class EvidenceCommandDispatcher:
    def __init__(self, settings, *, research=None, dossier=None):
        self.settings = settings
        self.research = research or EvidenceResearchService(settings.principal, settings.connection,
            settings.driver, trusted_scope=settings.scope)
        self.dossier = dossier or EvidenceDossierService(settings.principal, settings.connection,
            settings.driver, trusted_scope=settings.scope)

    async def dispatch(self, raw, *, principal):
        request_id = None
        method = None
        try:
            if type(raw) is not bytes or not 0 < len(raw) <= 32768 + ENVELOPE_OVERHEAD:
                raise ValueError
            value = _object(raw)
            if (set(value) != {"version", "request_id", "method", "scope", "payload"}
                    or type(value["version"]) is not int or value["version"] != 1):
                raise ValueError
            identity = value["request_id"]
            if type(identity) is not str or str(UUID(identity)) != identity:
                raise ValueError
            request_id = identity
            method = value["method"]
            if type(method) is not str or method not in REQUEST_LIMITS:
                raise ValueError
            if len(raw) > REQUEST_LIMITS[method] + ENVELOPE_OVERHEAD:
                raise ValueError
            if type(value["scope"]) is not dict:
                raise ValueError
            scope = _scope_json(json.dumps(value["scope"], allow_nan=False, separators=(",", ":")))
            if principal != self.settings.principal or scope != self.settings.scope:
                raise ValueError
            payload = _encode(value["payload"], REQUEST_LIMITS[method])
            if method == "two_hop":
                request = TwoHopRequest.model_validate_json(payload)
                if request.display_graph_id != self.settings.display_graph_id:
                    raise ValueError
            else:
                request = (parse_research if method == "research" else parse_dossier)(payload)
                if ((method == "research" and type(request) is not ResearchRequest)
                        or self.settings.display_graph_id not in request.display_graph_ids):
                    raise ValueError
        except (ValueError, TypeError, KeyError, OverflowError, PipeProtocolError, ResearchFailure, DossierFailure):
            return self._error(request_id, "invalid_request")
        fallback = "dossier_unavailable" if method == "dossier" else "research_unavailable"
        try:
            if method == "two_hop":
                result = await self.research.two_hop(request)
                if (type(result) is not TwoHopResult
                        or result.scope_coverage.display_graph_id != request.display_graph_id
                        or result.scope_coverage.scope != scope
                        or result.source_node_id != request.source_node_id
                        or result.target_node_id != request.target_node_id):
                    return self._error(request_id, "invalid_reply")
                model = TwoHopResult
            elif method == "research":
                result = await self.research.research(request)
                if type(result) is not ResearchResult:
                    raise ValueError
                if tuple(s.display_graph_id for s in result.scopes) != request.display_graph_ids:
                    raise ValueError
                model = ResearchResult
            else:
                result = await self.dossier.build(request)
                if type(result) is not EvidenceDossier:
                    raise ValueError
                if result.request != request:
                    raise ValueError
                model = EvidenceDossier
            data = result.model_dump(mode="json")
            try:
                encoded = _encode(data, RESULT_LIMITS[method])
                reply = _encode({"version": 1, "request_id": request_id, "ok": True, "result": data},
                    RESULT_LIMITS[method] + ENVELOPE_OVERHEAD)
            except ValueError:
                return self._error(request_id, "result_too_large")
            try:
                model.model_validate_json(encoded)
            except ValueError:
                return self._error(request_id, "invalid_reply" if method == "two_hop" else "result_too_large")
            return reply
        except (ResearchFailure, DossierFailure) as error:
            return self._error(request_id, error.code if error.code in ERROR_CODES else fallback)
        except Exception:
            return self._error(request_id, fallback)

    @staticmethod
    def _error(request_id, code):
        return _encode({"version": 1, "request_id": request_id, "ok": False, "error": {"code": code}}, 1024)

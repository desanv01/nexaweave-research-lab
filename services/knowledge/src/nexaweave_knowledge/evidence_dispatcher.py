"""Fixed read-only evidence commands; request scope never confers authority."""
import json
from uuid import UUID

from .stdio import _object, PipeProtocolError
from .read_runtime import _scope_json
from .research_cli import parse_request as parse_research
from .dossier_cli import parse_request as parse_dossier
from .research_contracts import ResearchResult
from .report_contracts import EvidenceDossier
from .evidence_research import EvidenceResearchService, ResearchFailure
from .evidence_dossier import EvidenceDossierService, DossierFailure

REQUEST_LIMITS = {"research": 16384, "dossier": 32768}
RESULT_LIMITS = {"research": 2 * 1024 * 1024, "dossier": 4 * 1024 * 1024}
ENVELOPE_OVERHEAD = 1024
ERROR_CODES = frozenset({"invalid_request", "invalid_configuration", "research_unavailable",
    "research_deadline", "research_busy", "dossier_unavailable", "dossier_deadline",
    "dossier_busy", "result_too_large"})


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
            scope = _scope_json(json.dumps(value["scope"], allow_nan=False, separators=(",", ":")))
            if principal != self.settings.principal or scope != self.settings.scope:
                raise ValueError
            payload = _encode(value["payload"], REQUEST_LIMITS[method])
            request = (parse_research if method == "research" else parse_dossier)(payload)
            if self.settings.display_graph_id not in request.display_graph_ids:
                raise ValueError
        except (ValueError, TypeError, KeyError, OverflowError, ResearchFailure, DossierFailure):
            return self._error(request_id, "invalid_request")
        try:
            if method == "research":
                result = await self.research.research(request)
                if type(result) is not ResearchResult:
                    raise ValueError
                if tuple(s.display_graph_id for s in result.scopes) != request.display_graph_ids:
                    raise ValueError
            else:
                result = await self.dossier.build(request)
                if type(result) is not EvidenceDossier:
                    raise ValueError
                if result.request != request:
                    raise ValueError
            data = result.model_dump(mode="json")
            try:
                encoded = _encode(data, RESULT_LIMITS[method])
                (ResearchResult if method == "research" else EvidenceDossier).model_validate_json(encoded)
                return _encode({"version": 1, "request_id": request_id, "ok": True, "result": data},
                    RESULT_LIMITS[method] + ENVELOPE_OVERHEAD)
            except ValueError:
                return self._error(request_id, "result_too_large")
        except (ResearchFailure, DossierFailure) as error:
            fallback = "research_unavailable" if method == "research" else "dossier_unavailable"
            return self._error(request_id, error.code if error.code in ERROR_CODES else fallback)
        except Exception:
            return self._error(request_id, "research_unavailable" if method == "research" else "dossier_unavailable")

    @staticmethod
    def _error(request_id, code):
        return _encode({"version": 1, "request_id": request_id, "ok": False, "error": {"code": code}}, 1024)

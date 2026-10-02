"""Protected source graph routes, present only in research_local."""
import threading

from flask import Response, request
from werkzeug.exceptions import BadRequest

from .services.knowledge_ingestion_client import (MAX_BYTES, ENVELOPE_OVERHEAD,
    encoded, validate_payload, validate_result)
from .services.knowledge_ingestion_facade import KnowledgeIngestionFacade, require_execution, execution_settings
from .services.knowledge_transport import KnowledgeTransportError, _json_object, _uuid
from .services.knowledge_reader import KnowledgeReadError

STATUS = {"invalid_request": 400, "not_found": 404, "source_denied": 403,
    "tombstoned": 410, "model_calls_disabled": 403, "budget_denied": 403,
    "conflict": 409, "cancelled": 409, "busy": 503, "uncertain": 503,
    "outcome_unknown": 503, "source_unavailable": 503, "internal_error": 500,
    "result_too_large": 413}


def register_ingestion_routes(app, settings, *, ingestion_facade=None):
    facade, lock = ingestion_facade, threading.Lock()

    def reader():
        nonlocal facade
        with lock:
            if facade is None:
                facade = KnowledgeIngestionFacade(settings)
            return facade

    @app.after_request
    def ingestion_headers(response):
        if request.path.startswith("/api/source/ingestion/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def failure(code):
        code = code if code in STATUS else "source_unavailable"
        return Response(encoded({"success": False, "error": {"code": code}}),
                        status=STATUS[code], content_type="application/json")

    def run(method, graph_id, operation_id=None):
        try:
            if graph_id != settings.display_graph_id:
                raise KnowledgeReadError("not_found")
            if (settings.scope["layer"] != "source" or settings.scope["run_id"] is not None
                    or settings.scope["branch_id"] is not None):
                raise KnowledgeReadError("source_denied")
            if request.args or request.headers.get("Transfer-Encoding") or request.headers.get("Content-Encoding"):
                raise KnowledgeReadError("invalid_request")
            if method == "status":
                if request.content_length not in (None, 0) or request.stream.read(1):
                    raise KnowledgeReadError("invalid_request")
                payload = {"operation_id": _uuid(operation_id)}
            else:
                if (request.mimetype != "application/json" or request.content_length is None
                        or not 0 < request.content_length <= MAX_BYTES):
                    raise KnowledgeReadError("invalid_request")
                raw = request.stream.read(MAX_BYTES + 1)
                if len(raw) != request.content_length or len(raw) > MAX_BYTES:
                    raise KnowledgeReadError("invalid_request")
                payload = _json_object(raw)
            validate_payload(method, payload)
            if method == "execute":
                # Injection never bypasses the operator's explicit authorization.
                require_execution(execution_settings())
            data = reader().execute(method, graph_id, payload)
            validate_result(method, data, settings.scope, payload)
            body = encoded({"success": True, "data": data})
            if len(body) > MAX_BYTES + ENVELOPE_OVERHEAD:
                raise KnowledgeReadError("result_too_large")
            return Response(body, content_type="application/json")
        except (BadRequest, ValueError, TypeError, UnicodeError, RecursionError):
            return failure("invalid_request")
        except KnowledgeReadError as error:
            return failure(error.code)
        except KnowledgeTransportError as error:
            return failure("outcome_unknown" if error.outcome_unknown else error.code)
        except Exception:
            return failure("internal_error")

    app.add_url_rule("/api/source/ingestion/plan/<graph_id>", "source_ingestion_plan",
                     lambda graph_id: run("plan", graph_id), methods=["POST"])
    app.add_url_rule("/api/source/ingestion/execute/<graph_id>", "source_ingestion_execute",
                     lambda graph_id: run("execute", graph_id), methods=["POST"])
    app.add_url_rule("/api/source/ingestion/operation/<graph_id>/<operation_id>", "source_ingestion_status",
                     lambda graph_id, operation_id: run("status", graph_id, operation_id), methods=["GET"])

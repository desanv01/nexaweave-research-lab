"""Protected text/DOCX/PDF retention through the fixed source facade and child."""
import threading

from flask import Response, request
from werkzeug.exceptions import BadRequest

from .services.knowledge_source_client import MAX_BYTES, ENVELOPE_OVERHEAD, encoded
from .services.knowledge_source_facade import KnowledgeSourceFacade, validate_upload, validate_public_result
from .services.knowledge_transport import KnowledgeTransportError, _json_object, _uuid
from .services.knowledge_reader import KnowledgeReadError

STATUS = {"invalid_request": 400, "unauthorized": 401, "origin_denied": 403,
    "source_denied": 403, "not_found": 404, "conflict": 409, "tombstoned": 410,
    "result_too_large": 413, "limit_exceeded": 413, "busy": 503, "timeout": 504,
    "source_unavailable": 503, "outcome_unknown": 503, "internal_error": 500}


def register_source_routes(app, settings, *, source_facade=None):
    lock = threading.Lock()
    facade = source_facade

    def reader():
        nonlocal facade
        with lock:
            if facade is None:
                facade = KnowledgeSourceFacade(settings)
            return facade

    @app.after_request
    def source_headers(response):
        if request.path.startswith("/api/source/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def failure(code):
        if code not in STATUS:
            code = "source_unavailable"
        return Response(encoded({"success": False, "error": {"code": code}}),
                        status=STATUS[code], content_type="application/json")

    def run(method, graph_id, revision=None):
        try:
            if graph_id != settings.display_graph_id:
                raise KnowledgeReadError("not_found")
            if request.args or request.headers.get("Transfer-Encoding") or request.headers.get("Content-Encoding"):
                raise KnowledgeReadError("invalid_request")
            if method == "retain":
                if (request.mimetype != "application/json" or request.content_length is None
                        or not 0 < request.content_length <= MAX_BYTES):
                    raise KnowledgeReadError("invalid_request")
                raw = request.stream.read(MAX_BYTES + 1)
                if len(raw) != request.content_length or len(raw) > MAX_BYTES:
                    raise KnowledgeReadError("invalid_request")
                payload = _json_object(raw)
                validate_upload(payload)
            else:
                if request.content_length not in (None, 0) or request.stream.read(1):
                    raise KnowledgeReadError("invalid_request")
                payload = {} if method == "list" else {"source_revision": _uuid(revision)}
            data = reader().execute(method, graph_id, payload)
            validate_public_result(method, data, settings.scope, payload)
            body = encoded({"success": True, "data": data})
            if len(body) > MAX_BYTES + ENVELOPE_OVERHEAD:
                raise KnowledgeReadError("result_too_large")
            return Response(body, content_type="application/json")
        except (BadRequest, ValueError, UnicodeError, TypeError, RecursionError):
            return failure("invalid_request")
        except KnowledgeReadError as error:
            return failure(error.code)
        except KnowledgeTransportError as error:
            return failure(error.code if not error.outcome_unknown else "outcome_unknown")
        except Exception:
            return failure("internal_error")

    app.add_url_rule("/api/source/library/<graph_id>", "source_library",
                     lambda graph_id: run("list", graph_id), methods=["GET"])
    app.add_url_rule("/api/source/item/<graph_id>/<source_revision>", "source_item",
                     lambda graph_id, source_revision: run("get", graph_id, source_revision), methods=["GET"])
    app.add_url_rule("/api/source/retain/<graph_id>", "source_retain",
                     lambda graph_id: run("retain", graph_id), methods=["POST"])

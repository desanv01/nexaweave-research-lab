"""Token-protected loopback Flask surface for bounded graph reads only."""

from __future__ import annotations

import hmac
import os

from flask import Flask, jsonify, request

from .config import Config
from .services.knowledge_read_facade import KnowledgeReadFacade, ReadHostSettings
from .services.knowledge_reader import KnowledgeReadError
from .services.knowledge_transport import KnowledgeTransportError
from .utils.browser_origins import parse_allowed_origins


_STATUS = {"invalid_request": 400, "invalid_reply": 502, "not_found": 404,
           "unauthorized": 401, "busy": 409, "tombstoned": 410,
           "conflict": 409, "timeout": 503, "transport_failure": 503,
           "result_too_large": 413, "limit_exceeded": 413,
           "inconsistent_graph": 409, "unsupported": 501,
           "internal_error": 500}
_HEADERS = frozenset({"authorization", "content-type"})


def _failure(code, status=None):
    return jsonify({"success": False, "error": {"code": code}}), status or _STATUS.get(code, 503)


def create_read_app(config_class, *, facade=None):
    app = Flask(__name__)
    app.config.from_object(config_class)
    if app.config.get("DEBUG") or os.environ.get("FLASK_HOST", "127.0.0.1") not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("invalid read-only server configuration")
    configured = app.config.get("MIROFISH_ALLOWED_ORIGINS")
    class_override = any("MIROFISH_ALLOWED_ORIGINS" in vars(candidate)
                         for candidate in getattr(config_class, "__mro__", ())
                         if candidate not in (Config, object))
    if not class_override and "MIROFISH_ALLOWED_ORIGINS" in os.environ:
        configured = os.environ["MIROFISH_ALLOWED_ORIGINS"]
    origins = frozenset(parse_allowed_origins(configured))
    settings = ReadHostSettings.from_config(config_class)
    reader = facade or KnowledgeReadFacade(settings)
    app.json.ensure_ascii = False

    @app.before_request
    def boundary():
        if not request.path.startswith("/api/"):
            return None
        origin_values = request.headers.getlist("Origin")
        if origin_values and (len(origin_values) != 1 or origin_values[0] not in origins):
            return _failure("origin_denied", 403)
        if request.method == "OPTIONS":
            method = request.headers.get("Access-Control-Request-Method")
            allowed = request.url_rule.methods if request.url_rule else set()
            requested_headers = request.headers.get("Access-Control-Request-Headers")
            names = [] if requested_headers is None else requested_headers.split(",")
            if (not origin_values or method != "GET" or method not in allowed
                    or any(not name.strip() or name.strip().lower() not in _HEADERS for name in names)):
                return _failure("origin_denied", 403)
            return "", 204
        authorization = request.headers.getlist("Authorization")
        supplied = authorization[0] if len(authorization) == 1 else ""
        if (not isinstance(supplied, str) or len(supplied) > 512 or not supplied.isascii()
                or not hmac.compare_digest(supplied, "Bearer " + settings.token)):
            return _failure("unauthorized", 401)
        return None

    @app.after_request
    def cors(response):
        if request.path.startswith("/api/"):
            response.vary.add("Origin")
            values = request.headers.getlist("Origin")
            if len(values) == 1 and values[0] in origins:
                response.headers["Access-Control-Allow-Origin"] = values[0]
                if request.method == "OPTIONS" and response.status_code == 204:
                    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
                    response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        return response

    def execute(call):
        try:
            return jsonify({"success": True, "data": call()})
        except KnowledgeReadError as error:
            return _failure(error.code)
        except KnowledgeTransportError:
            return _failure("transport_failure")
        except Exception:
            return _failure("internal_error")

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "mode": "graphiti_readonly",
                        "capabilities": ["graph_data", "entities", "entity_context"]})

    @app.get("/api/graph/data/<graph_id>")
    def graph_data(graph_id):
        if graph_id != settings.display_graph_id:
            return _failure("not_found")
        return execute(lambda: reader.graph_data(graph_id))

    @app.get("/api/graph/entities/<graph_id>")
    def entities(graph_id):
        if graph_id != settings.display_graph_id:
            return _failure("not_found")
        raw = request.args.get("types")
        if raw is not None and (len(raw) > 2048 or not raw):
            return _failure("invalid_request")
        types = raw.split(",") if raw is not None else None
        enrich = request.args.get("enrich", "true")
        if enrich not in {"true", "false"}:
            return _failure("invalid_request")
        return execute(lambda: reader.entities(graph_id, types, enrich == "true").to_dict())

    @app.get("/api/graph/entity/<graph_id>/<entity_uuid>")
    def entity(graph_id, entity_uuid):
        if graph_id != settings.display_graph_id:
            return _failure("not_found")
        def lookup():
            result = reader.entity(graph_id, entity_uuid)
            if result is None:
                raise KnowledgeReadError("not_found")
            return result.to_dict()
        return execute(lookup)

    @app.errorhandler(404)
    def missing(_error):
        return _failure("unsupported", 404)

    @app.errorhandler(405)
    def unsupported_method(_error):
        return _failure("unsupported", 405)

    return app

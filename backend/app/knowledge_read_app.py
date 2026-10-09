"""Token-protected loopback Flask surface for bounded graph reads only."""

from __future__ import annotations

import hmac
import json
import os
import re
import threading

from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import BadRequest

from .config import Config
from .services.knowledge_read_facade import KnowledgeReadFacade, ReadHostSettings
from .services.knowledge_reader import KnowledgeReadError
from .services.knowledge_transport import KnowledgeTransportError
from .utils.browser_origins import parse_allowed_origins
from .utils.branding import allowed_origins as configured_allowed_origins


_STATUS = {"invalid_request": 400, "invalid_reply": 502, "not_found": 404,
           "evidence_unavailable": 503,
           "unauthorized": 401, "busy": 409, "tombstoned": 410,
           "conflict": 409, "timeout": 503, "transport_failure": 503,
           "result_too_large": 413, "limit_exceeded": 413,
           "inconsistent_graph": 409, "unsupported": 501,
           "empty_selection": 422,
           "internal_error": 500}
_HEADERS = frozenset({"authorization", "content-type"})
_TYPE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_REQUEST_BYTES = 16 * 1024
_RESPONSE_BYTES = 2 * 1024 * 1024


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _population_options(export=False):
    if (request.mimetype != "application/json" or request.content_length is None
            or request.content_length > _REQUEST_BYTES):
        raise KnowledgeReadError("invalid_request")
    raw = request.get_data(cache=False)
    if len(raw) > _REQUEST_BYTES:
        raise KnowledgeReadError("invalid_request")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_pairs,
                           parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise KnowledgeReadError("invalid_request") from None
    allowed = {"types", "max_agents", "seed"} | ({"platform"} if export else set())
    if type(value) is not dict or not set(value) <= allowed:
        raise KnowledgeReadError("invalid_request")
    types = value.get("types")
    if "types" in value and (type(types) is not list or len(types) > 50
                             or any(type(item) is not str or not _TYPE.fullmatch(item)
                                    for item in types) or len(set(types)) != len(types)):
        raise KnowledgeReadError("invalid_request")
    maximum = value.get("max_agents", 10)
    seed = value.get("seed", 0)
    if (type(maximum) is not int or not 1 <= maximum <= 100
            or type(seed) is not int or not 0 <= seed <= 0xFFFFFFFF):
        raise KnowledgeReadError("invalid_request")
    options = {"types": types, "max_agents": maximum, "seed": seed}
    if export:
        platform = value.get("platform")
        if type(platform) is not str or platform not in {"twitter", "reddit"}:
            raise KnowledgeReadError("invalid_request")
        options["platform"] = platform
    return options


def _failure(code, status=None):
    return jsonify({"success": False, "error": {"code": code}}), status or _STATUS.get(code, 503)


def create_read_app(config_class, *, facade=None, evidence_facade=None, source_facade=None, ingestion_facade=None, experiment_facade=None,
                    preparation_facade=None, native_launch_facade=None, native_observations_facade=None, connected_report_facade=None, connected_followup_facade=None, mode="graphiti_readonly"):
    if mode not in {"graphiti_readonly", "research_local"}:
        raise ValueError("invalid application mode")
    app = Flask(__name__)
    app.config.from_object(config_class)
    app.config['NEXAWEAVE_APP_MODE'] = mode
    app.config['MIROFISH_APP_MODE'] = mode
    if app.config.get("DEBUG") or os.environ.get("FLASK_HOST", "127.0.0.1") not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("invalid read-only server configuration")
    origins = frozenset(parse_allowed_origins(configured_allowed_origins(config_class, Config)))
    app.config['NEXAWEAVE_ALLOWED_ORIGINS'] = tuple(sorted(origins))
    app.config['MIROFISH_ALLOWED_ORIGINS'] = tuple(sorted(origins))
    settings = ReadHostSettings.from_config(config_class)
    if mode == "research_local":
        from .source_library_api import register_source_routes
        register_source_routes(app, settings, source_facade=source_facade)
        from .source_ingestion_api import register_ingestion_routes
        register_ingestion_routes(app, settings, ingestion_facade=ingestion_facade)
        from .experiment_api import register_experiment_routes
        register_experiment_routes(app, settings, experiment_facade=experiment_facade)
        from .preparation_api import register_preparation_routes
        register_preparation_routes(app, settings, preparation_facade=preparation_facade)
        from .native_launch_api import register_native_launch_routes
        register_native_launch_routes(app, settings, native_launch_facade=native_launch_facade)
        from .native_observations_api import register_native_observations_routes
        register_native_observations_routes(app, settings, native_observations_facade=native_observations_facade)
        from .connected_report_api import register_connected_report_routes
        register_connected_report_routes(app, settings, connected_report_facade=connected_report_facade)
        from .connected_followup_api import register_connected_followup_routes
        register_connected_followup_routes(app, settings, followup_facade=connected_followup_facade)
    reader = facade or KnowledgeReadFacade(settings)
    evidence_lock = threading.Lock()
    evidence = evidence_facade

    def evidence_reader():
        nonlocal evidence
        with evidence_lock:
            if evidence is None:
                from .services.knowledge_evidence_facade import KnowledgeEvidenceFacade
                evidence = KnowledgeEvidenceFacade(settings)
            return evidence
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
            if (not origin_values or method not in {"GET", "POST"} or method not in allowed
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
                    allowed = request.url_rule.methods if request.url_rule else set()
                    response.headers["Access-Control-Allow-Methods"] = ", ".join(
                        method for method in ("GET", "POST", "OPTIONS") if method in allowed)
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
        return jsonify({"status": "ok", "mode": mode,
                        "capabilities": ["graph_data", "entities", "entity_context",
                                         "population_preview", "population_export",
                                         "evidence_research", "evidence_dossier"] +
                        (["source_library", "source_retention"] if mode == "research_local" else [])})

    def evidence_call(method, graph_id):
        if graph_id != settings.display_graph_id:
            return _failure("not_found")
        from .services.knowledge_evidence_client import REQUEST_LIMITS, RESULT_LIMITS, ENVELOPE_OVERHEAD, validate_payload
        from .services.knowledge_transport import _json_object
        try:
            maximum = REQUEST_LIMITS[method]
            if (request.args or request.mimetype != "application/json"
                    or request.headers.get("Transfer-Encoding") or request.headers.get("Content-Encoding")
                    or request.content_length is None or not 0 < request.content_length <= maximum):
                raise KnowledgeReadError("invalid_request")
            raw = request.stream.read(maximum + 1)
            if len(raw) != request.content_length or len(raw) > maximum:
                raise KnowledgeReadError("invalid_request")
            try:
                payload = _json_object(raw)
            except ValueError:
                raise KnowledgeReadError("invalid_request") from None
            validate_payload(method, payload, graph_id)
            data = evidence_reader().execute(method, graph_id, payload)
            # Compact JSON keeps the HTTP wrapper inside explicit envelope overhead.
            encoded_data = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
            if len(encoded_data) > RESULT_LIMITS[method]:
                raise KnowledgeReadError("result_too_large")
            body = b'{"success":true,"data":' + encoded_data + b'}'
            if len(body) > RESULT_LIMITS[method] + ENVELOPE_OVERHEAD:
                raise KnowledgeReadError("result_too_large")
            return Response(body, content_type="application/json", headers={"X-Content-Type-Options": "nosniff"})
        except BadRequest:
            return _failure("invalid_request")
        except KnowledgeReadError as error:
            return _failure(error.code)
        except KnowledgeTransportError as error:
            return _failure(error.code if error.code in {"busy", "invalid_request"} else "transport_failure")
        except Exception:
            return _failure("internal_error")

    @app.post("/api/graph/research/<graph_id>")
    def evidence_research(graph_id):
        return evidence_call("research", graph_id)

    @app.post("/api/graph/dossier/<graph_id>")
    def evidence_dossier(graph_id):
        return evidence_call("dossier", graph_id)

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

    @app.post("/api/graph/population/<graph_id>/preview")
    def population_preview(graph_id):
        if graph_id != settings.display_graph_id:
            return _failure("not_found")
        try:
            options = _population_options()
        except KnowledgeReadError as error:
            return _failure(error.code)
        try:
            response = jsonify({"success": True, "data": reader.population_preview(graph_id, **options)})
            if len(response.get_data()) > _RESPONSE_BYTES:
                raise KnowledgeReadError("result_too_large")
            return response
        except KnowledgeReadError as error:
            return _failure(error.code)
        except KnowledgeTransportError:
            return _failure("transport_failure")
        except Exception:
            return _failure("internal_error")

    @app.post("/api/graph/population/<graph_id>/export")
    def population_export(graph_id):
        if graph_id != settings.display_graph_id:
            return _failure("not_found")
        try:
            options = _population_options(export=True)
            body, content_type, filename = reader.population_export(graph_id, **options)
            if len(body) > _RESPONSE_BYTES:
                raise KnowledgeReadError("result_too_large")
            response = Response(body, content_type=content_type)
            response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        except KnowledgeReadError as error:
            return _failure(error.code)
        except KnowledgeTransportError:
            return _failure("transport_failure")
        except Exception:
            return _failure("internal_error")

    @app.errorhandler(404)
    def missing(_error):
        return _failure("unsupported", 404)

    @app.errorhandler(405)
    def unsupported_method(_error):
        return _failure("unsupported", 405)

    return app

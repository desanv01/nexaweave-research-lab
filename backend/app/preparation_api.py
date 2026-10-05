"""Protected bounded preparation routes registered only in research_local."""
import threading
from flask import Response, request
from werkzeug.exceptions import BadRequest
from .services.preparation_client import (PreparationError, REQUEST_BYTES, RESULT_BYTES,
    ENVELOPE_OVERHEAD, encoded, validate_payload, validate_result, CODES)
from .services.knowledge_preparation_facade import KnowledgePreparationFacade
from .services.knowledge_transport import _json_object

STATUS = {'invalid_request': 400, 'not_found': 404, 'unauthorized': 401,
          'origin_denied': 403, 'conflict': 409, 'busy': 503, 'tombstoned': 410,
          'empty_selection': 422, 'result_too_large': 413, 'model_calls_disabled': 409,
          'budget_denied': 409, 'invalid_reply': 502, 'internal_error': 500}


def register_preparation_routes(app, settings, *, preparation_facade=None):
    facade, lock = preparation_facade, threading.Lock()

    def reader():
        nonlocal facade
        with lock:
            if facade is None:
                facade = KnowledgePreparationFacade(settings)
            return facade

    @app.after_request
    def preparation_headers(response):
        if request.path.startswith('/api/preparation/'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def failure(code):
        code = code if code in CODES else 'internal_error'
        return Response(encoded({'success': False, 'error': {'code': code}}),
                        status=STATUS.get(code, 503), content_type='application/json')

    def run(method, graph_id):
        try:
            if graph_id != settings.display_graph_id:
                raise PreparationError('not_found')
            if (settings.scope['layer'] != 'source' or settings.scope['run_id'] is not None
                    or settings.scope['branch_id'] is not None):
                raise PreparationError('unauthorized')
            if (request.args or request.mimetype != 'application/json'
                    or request.headers.get('Transfer-Encoding') or request.headers.get('Content-Encoding')
                    or request.content_length is None or not 0 < request.content_length <= REQUEST_BYTES):
                raise PreparationError('invalid_request')
            raw = request.stream.read(REQUEST_BYTES + 1)
            if len(raw) != request.content_length or len(raw) > REQUEST_BYTES:
                raise PreparationError('invalid_request')
            payload = validate_payload(method, _json_object(raw))
            result = reader().execute(method, graph_id, payload)
            data = validate_result(result, graph_id, settings.scope, payload, method)
            body = encoded({'success': True, 'data': data})
            if len(body) > RESULT_BYTES + ENVELOPE_OVERHEAD:
                raise PreparationError('result_too_large')
            return Response(body, content_type='application/json')
        except PreparationError as error:
            return failure(error.code)
        except (BadRequest, ValueError, TypeError, UnicodeError, RecursionError):
            return failure('invalid_request')
        except Exception:
            return failure('internal_error')

    for method in ('plan', 'start', 'status'):
        app.add_url_rule('/api/preparation/<graph_id>/' + method,
                         'preparation_' + method,
                         lambda graph_id, method=method: run(method, graph_id), methods=['POST'])

"""Protected explicit completed-observations page POST, finite strict envelopes."""
from flask import Response, request
from werkzeug.exceptions import BadRequest
from .services.native_observations_client import (NativeObservationsError, CODES, REQUEST_BYTES,
    RESULT_BYTES, ENVELOPE_OVERHEAD, encoded, validate_payload, validate_result)
from .services.native_observations_facade import NativeObservationsFacade
from .services.knowledge_transport import _json_object

STATUS = dict(zip(('invalid_request not_found unauthorized origin_denied conflict tombstoned busy '
    'result_too_large observations_unavailable evidence_invalid invalid_reply internal_error').split(),
    (400, 404, 401, 403, 409, 410, 503, 413, 503, 502, 502, 500)))


def register_native_observations_routes(app, settings, *, native_observations_facade=None):
    facade = native_observations_facade or NativeObservationsFacade(settings)

    @app.after_request
    def observations_headers(response):
        if request.path.startswith('/api/native-observations/'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def failure(code):
        code = code if code in CODES else 'internal_error'
        return Response(encoded({'success': False, 'error': {'code': code}}), status=STATUS[code], content_type='application/json')

    @app.post('/api/native-observations/page/<graph_id>')
    def observations_page(graph_id):
        try:
            if graph_id != settings.display_graph_id:
                raise NativeObservationsError('not_found')
            if settings.scope['layer'] != 'source' or settings.scope['run_id'] is not None or settings.scope['branch_id'] is not None:
                raise NativeObservationsError('unauthorized')
            if (request.args or request.mimetype != 'application/json' or request.headers.get('Transfer-Encoding')
                    or request.headers.get('Content-Encoding') or request.content_length is None
                    or not 0 < request.content_length <= REQUEST_BYTES):
                raise NativeObservationsError('invalid_request')
            raw = request.stream.read(REQUEST_BYTES + 1)
            if len(raw) != request.content_length or len(raw) > REQUEST_BYTES:
                raise NativeObservationsError('invalid_request')
            payload = validate_payload(_json_object(raw))
            data = validate_result(facade.execute(graph_id, payload), graph_id, settings.scope, payload, settings.principal)
            body = encoded({'success': True, 'data': data})
            if len(body) > RESULT_BYTES + ENVELOPE_OVERHEAD:
                raise NativeObservationsError('result_too_large')
            return Response(body, content_type='application/json')
        except NativeObservationsError as error:
            return failure(error.code)
        except (BadRequest, ValueError, TypeError, UnicodeError, RecursionError):
            return failure('invalid_request')
        except Exception:
            return failure('internal_error')

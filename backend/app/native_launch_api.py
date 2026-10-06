"""Protected research_local native launch, finite strict POST envelopes."""
from flask import Response, request
from werkzeug.exceptions import BadRequest
from .services.native_launch_client import (NativeLaunchError, REQUEST_BYTES, RESULT_BYTES,
    ENVELOPE_OVERHEAD, encoded, validate_payload, validate_result, CODES)
from .services.native_launch_facade import NativeLaunchFacade
from .services.knowledge_transport import _json_object

STATUS = dict(zip(('invalid_request not_found unauthorized origin_denied conflict busy tombstoned '
    'result_too_large model_calls_disabled budget_denied native_launch_unavailable native_launch_uncertain '
    'native_launch_failed invalid_reply internal_error').split(),
    (400,404,401,403,409,503,410,413,409,409,503,503,503,502,500)))


def register_native_launch_routes(app, settings, *, native_launch_facade=None):
    facade = native_launch_facade or NativeLaunchFacade(settings)

    @app.after_request
    def native_launch_headers(response):
        if request.path.startswith('/api/native-launch/'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def failure(code):
        code = code if code in CODES else 'internal_error'
        return Response(encoded({'success': False, 'error': {'code': code}}),
                        status=STATUS[code], content_type='application/json')

    def run(method, graph_id):
        try:
            if graph_id != settings.display_graph_id:
                raise NativeLaunchError('not_found')
            if settings.scope['layer'] != 'source' or settings.scope['run_id'] is not None or settings.scope['branch_id'] is not None:
                raise NativeLaunchError('unauthorized')
            if (request.args or request.mimetype != 'application/json' or request.headers.get('Transfer-Encoding')
                    or request.headers.get('Content-Encoding') or request.content_length is None
                    or not 0 < request.content_length <= REQUEST_BYTES):
                raise NativeLaunchError('invalid_request')
            raw = request.stream.read(REQUEST_BYTES + 1)
            if len(raw) != request.content_length or len(raw) > REQUEST_BYTES:
                raise NativeLaunchError('invalid_request')
            payload = validate_payload(method, _json_object(raw))
            data = validate_result(facade.execute(method, graph_id, payload), graph_id, settings.scope, payload, method)
            if data['request']['principal'] != settings.principal:
                raise NativeLaunchError('invalid_reply')
            body = encoded({'success': True, 'data': data})
            if len(body) > RESULT_BYTES + ENVELOPE_OVERHEAD:
                raise NativeLaunchError('result_too_large')
            return Response(body, content_type='application/json')
        except NativeLaunchError as error:
            return failure(error.code)
        except (BadRequest, ValueError, TypeError, UnicodeError, RecursionError):
            return failure('invalid_request')
        except Exception:
            return failure('internal_error')

    for method in ('plan', 'start', 'status', 'cancel'):
        app.add_url_rule('/api/native-launch/' + method + '/<graph_id>', 'native_launch_' + method,
                        lambda graph_id, method=method: run(method, graph_id), methods=['POST'])

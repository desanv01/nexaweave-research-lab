"""Six explicit protected report POST routes; no optional model imports."""
import json
import threading
from flask import Response, request
from werkzeug.exceptions import BadRequest

STATUS = dict(zip(('invalid_request invalid_reply unauthorized origin_denied not_found conflict tombstoned '
    'busy result_too_large report_unavailable model_calls_disabled budget_denied report_failed report_cancelled '
    'report_uncertain timeout transport_failure internal_error').split(),
    (400, 502, 401, 403, 404, 409, 410, 409, 413, 503, 409, 409, 409, 409, 409, 503, 503, 500)))


def _encoded(value):
    """Fixed error envelopes stay available before the optional report codec."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def register_connected_report_routes(app, settings, connected_report_facade=None):
    facade = connected_report_facade
    facade_lock = threading.Lock()

    def trusted_facade():
        nonlocal facade
        if facade is None:
            with facade_lock:
                if facade is None:
                    from .services.connected_report_facade import ConnectedReportFacade
                    facade = ConnectedReportFacade(settings)
        return facade

    @app.after_request
    def connected_report_headers(response):
        if request.path.startswith('/api/connected-report/'):
            if response.status_code in (404, 405, 413):
                code = {404: 'not_found', 405: 'invalid_request', 413: 'result_too_large'}[response.status_code]
                response.set_data(_encoded(dict(success=False, error=dict(code=code))))
                response.status_code = STATUS[code]
                response.content_type = 'application/json'
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def failure(code):
        code = code if code in STATUS else 'internal_error'
        return Response(_encoded(dict(success=False, error=dict(code=code))), status=STATUS[code], content_type='application/json')

    def handle(method, graph_id):
        try:
            from .services.connected_report_client import (ReportError, REQUEST_BYTES, RESULT_BYTES,
                CONTENT_BYTES, encoded, strict_json, validate_payload, validate_result, validate_read, validate_download)
        except ImportError:
            return failure('report_unavailable')
        except Exception:
            return failure('internal_error')
        try:
            if graph_id != settings.display_graph_id:
                raise ReportError('not_found')
            if settings.scope['layer'] != 'source' or settings.scope['run_id'] is not None or settings.scope['branch_id'] is not None:
                raise ReportError('unauthorized')
            if (request.args or request.mimetype != 'application/json' or request.headers.get('Transfer-Encoding')
                    or request.headers.get('Content-Encoding') or request.content_length is None
                    or not 0 < request.content_length <= REQUEST_BYTES):
                raise ReportError('invalid_request')
            raw = request.stream.read(REQUEST_BYTES + 1)
            if len(raw) != request.content_length or len(raw) > REQUEST_BYTES:
                raise ReportError('invalid_request')
            payload = validate_payload(method, strict_json(raw))
            try:
                facade = trusted_facade()
            except ImportError:
                raise ReportError('report_unavailable') from None
            data = facade.execute(method, graph_id, payload)
            if method == 'read':
                data = validate_read(data, graph_id, settings.scope, payload, settings.principal)
            elif method == 'download':
                # Trusted facade must return a full known result separately for
                # allowlist/hash validation; no runtime/status polling required.
                if not callable(getattr(facade, 'known_report', None)):
                    raise ReportError('invalid_reply')
                known = validate_result(facade.known_report(graph_id, payload), graph_id, settings.scope,
                    payload, 'download', settings.principal)
                data = validate_download(data, payload, known)
            else:
                data = validate_result(data, graph_id, settings.scope, payload, method, settings.principal)
            body = encoded(dict(success=True, data=data))
            if len(body) > (CONTENT_BYTES if method in ('read', 'download') else RESULT_BYTES):
                raise ReportError('result_too_large')
            return Response(body, content_type='application/json')
        except ReportError as error:
            return failure(error.code)
        except (BadRequest, ValueError, TypeError, UnicodeError, RecursionError):
            return failure('invalid_request')
        except Exception:
            return failure('internal_error')

    for method in ('plan', 'start', 'status', 'cancel', 'read', 'download'):
        def route(graph_id, _method=method):
            return handle(_method, graph_id)
        app.add_url_rule('/api/connected-report/' + method + '/<graph_id>',
                         endpoint='connected_report_' + method, view_func=route, methods=['POST'])

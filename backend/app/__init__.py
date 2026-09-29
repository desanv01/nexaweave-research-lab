"""
MiroFish Backend - Flask应用工厂
"""

import os
import warnings

# 抑制 multiprocessing resource_tracker 的警告（来自第三方库如 transformers）
# 需要在所有其他导入之前设置
warnings.filterwarnings("ignore", message=".*resource_tracker.*")

from flask import Flask, jsonify, request

from .config import Config
from .utils.logger import setup_logger, get_logger
from .utils.browser_origins import parse_allowed_origins


_API_METHODS = ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS')
_CORS_HEADERS = frozenset({'content-type', 'authorization'})


def _origin_denied():
    return jsonify({'success': False, 'error': 'Origin not allowed'}), 403


def create_app(config_class=Config, *, read_facade=None):
    """Flask应用工厂函数"""
    mode = os.environ.get('MIROFISH_APP_MODE', getattr(config_class, 'MIROFISH_APP_MODE', 'legacy'))
    if mode == 'graphiti_readonly':
        from .knowledge_read_app import create_read_app
        return create_read_app(config_class, facade=read_facade)
    if mode != 'legacy':
        raise ValueError('invalid application mode')
    app = Flask(__name__)
    app.config.from_object(config_class)

    # A subclass can explicitly override the class default. Otherwise read
    # the environment at app startup, including an explicitly empty value.
    configured_origins = app.config.get('MIROFISH_ALLOWED_ORIGINS')
    # A subclass may inherit an explicit override from an intermediate base.
    # Stop before Config's default; the nearest defining subclass wins.
    class_override = any(
        'MIROFISH_ALLOWED_ORIGINS' in vars(candidate)
        for candidate in getattr(config_class, '__mro__', ())
        if candidate not in (Config, object)
    )
    if not class_override and 'MIROFISH_ALLOWED_ORIGINS' in os.environ:
        configured_origins = os.environ['MIROFISH_ALLOWED_ORIGINS']
    allowed_origins = frozenset(parse_allowed_origins(configured_origins))
    app.config['MIROFISH_ALLOWED_ORIGINS'] = tuple(sorted(allowed_origins))
    
    # 设置JSON编码：确保中文直接显示（而不是 \uXXXX 格式）
    # Flask >= 2.3 使用 app.json.ensure_ascii，旧版本使用 JSON_AS_ASCII 配置
    if hasattr(app, 'json') and hasattr(app.json, 'ensure_ascii'):
        app.json.ensure_ascii = False
    
    # 设置日志
    logger = setup_logger('mirofish')
    
    # 只在 reloader 子进程中打印启动信息（避免 debug 模式下打印两次）
    is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    debug_mode = app.config.get('DEBUG', False)
    should_log_startup = not debug_mode or is_reloader_process
    
    if should_log_startup:
        logger.info("=" * 50)
        logger.info("MiroFish Backend 启动中...")
        logger.info("=" * 50)
    
    @app.before_request
    def enforce_browser_origin():
        if not request.path.startswith('/api/'):
            return None
        origin_values = request.headers.getlist('Origin')
        if not origin_values:
            return None
        if len(origin_values) != 1 or origin_values[0] not in allowed_origins:
            return _origin_denied()
        if request.method not in _API_METHODS:
            return _origin_denied()
        if request.method != 'OPTIONS':
            return None

        requested_method = request.headers.get('Access-Control-Request-Method')
        route_methods = request.url_rule.methods if request.url_rule else set()
        if (requested_method not in _API_METHODS
                or requested_method not in route_methods):
            return _origin_denied()
        requested_headers = request.headers.get('Access-Control-Request-Headers')
        if requested_headers is not None:
            names = [name.strip().lower() for name in requested_headers.split(',')]
            if not names or any(name not in _CORS_HEADERS for name in names):
                return _origin_denied()
        # Returning here prevents a preflight from reaching a mutation handler.
        return '', 204

    @app.after_request
    def add_browser_origin_headers(response):
        if not request.path.startswith('/api/'):
            return response
        response.vary.add('Origin')
        origin = request.headers.get('Origin')
        if origin in allowed_origins and len(request.headers.getlist('Origin')) == 1:
            response.headers['Access-Control-Allow-Origin'] = origin
            if request.method == 'OPTIONS' and response.status_code == 204:
                route_methods = request.url_rule.methods if request.url_rule else set()
                methods = [method for method in _API_METHODS if method in route_methods]
                response.headers['Access-Control-Allow-Methods'] = ', '.join(methods)
                response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response
    
    # 注册模拟进程清理函数（确保服务器关闭时终止所有模拟进程）
    from .services.simulation_runner import SimulationRunner
    SimulationRunner.register_cleanup()
    if should_log_startup:
        logger.info("已注册模拟进程清理函数")
    
    # Log only registered endpoint identity, method and final status.
    @app.after_request
    def log_response(response):
        logger = get_logger('mirofish.request')
        endpoint = request.endpoint if request.url_rule is not None else 'unmatched'
        logger.debug('method=%s endpoint=%s status=%s',
                     request.method, endpoint, response.status_code)
        return response
    
    # 注册蓝图
    from .api import graph_bp, simulation_bp, report_bp
    app.register_blueprint(graph_bp, url_prefix='/api/graph')
    app.register_blueprint(simulation_bp, url_prefix='/api/simulation')
    app.register_blueprint(report_bp, url_prefix='/api/report')
    
    # 健康检查
    @app.route('/health')
    def health():
        return {'status': 'ok', 'service': 'MiroFish Backend'}
    
    if should_log_startup:
        logger.info("MiroFish Backend 启动完成")
    
    return app

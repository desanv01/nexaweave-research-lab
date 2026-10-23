"""Owned offline follow-up UI session; Main alone admits actual evaluation."""
from collections import deque
from dataclasses import replace
import asyncio
import base64
import hashlib
import hmac
import http.client
import json
import math
import os
from pathlib import Path
import queue
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from uuid import UUID

from app.utils.owned_process import OwnedProcess

ROOT = Path(__file__).resolve().parents[3]
ORIGIN = 'http://127.0.0.1:4317'
WIRE = 7200
EVENTS = 64
EVENT_BYTES = 2097152
COMMANDS = 64
STAGES = frozenset(('connect', 'followup-plan', 'followup-start', 'followup-ready',
    'followup-read', 'followup-export', 'followup-history', 'followup-recover', 'locale', 'close'))
ERRORS = frozenset(('followup_browser_failed', 'followup_browser_cleanup_failed',
    'followup_browser_protocol_refused', 'followup_browser_deadline'))


def require(value, code='followup_browser_refused'):
    if not value:
        raise RuntimeError(code)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, 'followup_browser_duplicate_key')
        result[key] = value
    return result


def strict(raw):
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def keys(value, names):
    require(type(value) is dict and set(value) == set(names))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def uuid(value):
    require(type(value) is str and str(UUID(value)) == value)


def digest(value):
    require(type(value) is str and len(value) == 64
            and all(char in '0123456789abcdef' for char in value))


class FollowupBrowserSession:
    """No host generation here: only real UI requests may dispatch the two turns."""
    def __init__(self, directory, prep, native_host, reports, followups,
                 report_reference, report_result, deadline):
        self.directory = Path(directory)
        self.prep, self.native_host = prep, native_host
        self.reports, self.followups = reports, followups
        self.report_reference, self.report_result = report_reference, report_result
        self.deadline = deadline
        self.sequence = 0
        self.active = None
        self.connected = self.closed = self.work_failed = False
        self.last_request, self.last_proof = None, {}
        self.server = self.server_thread = self.driver = self.vite = None
        self.chrome = None
        self.port = 0
        self.lock = threading.Lock()
        self.requests_condition = threading.Condition()
        self.requests = set()
        self.events = deque()
        self.event_count = 0
        self.event_bytes = 0
        self.failures = []
        self.children = []
        self.replies = queue.Queue(maxsize=2)
        self.vite_ready = threading.Event()
        self.turns = {}
        self.starts = set()
        self.ready = set()
        self.selected = None
        self.exports = {}
        self.locales = set()
        self.command_payload = None
        self.entered = False
        self.driver_closed = False

    def _remaining(self, maximum=15, *, cleanup=False):
        reserve = 0 if cleanup else 15
        value = min(maximum, self.deadline - time.monotonic() - reserve)
        require(value > 0, 'followup_browser_deadline')
        return value

    def _failure(self, code):
        with self.lock:
            if code not in self.failures and len(self.failures) < 16:
                self.failures.append(code)

    def _route(self, family, method):
        return '/api/connected-' + family + '/' + method + '/' + self.prep.display_graph_id

    def _allowed(self, stage, payload):
        graph = '/api/graph/data/' + self.prep.display_graph_id
        library = '/api/source/library/' + self.prep.display_graph_id
        status = self._route('report', 'status')
        history = self._route('followup', 'history')
        if stage == 'connect':
            return {('GET', graph), ('GET', library), ('POST', status)}
        method = {'followup-plan': 'plan', 'followup-start': 'start',
            'followup-ready': 'status', 'followup-read': 'read',
            'followup-export': 'download', 'followup-history': 'history',
            'followup-recover': 'status'}.get(stage)
        allowed = set() if method is None else {('POST', self._route('followup', method))}
        if stage == 'followup-plan':
            allowed.add(('POST', history))
        if stage == 'followup-recover' and payload.get('reload') is True:
            allowed.update({('GET', graph), ('GET', library), ('POST', status)})
        return allowed

    def _capture_payload(self, family, method, payload):
        # The real route has already consumed/validated its own stream. Capture
        # exactly the object it passes to the actual facade; never reread input.
        from flask import g, request
        require(request.method == 'POST' and request.path == self._route(family, method))
        raw = encoded(payload)
        require(0 < len(raw) <= WIRE and not hasattr(g, 'followup_browser_body'))
        g.followup_browser_body = payload
        g.followup_browser_request_sha256 = sha(raw)

    def _app(self):
        from app import Config, create_app
        from app.services.knowledge_source_facade import KnowledgeSourceFacade
        from app.services.knowledge_ingestion_facade import KnowledgeIngestionFacade
        from app.services.knowledge_preparation_facade import KnowledgePreparationFacade
        from app.services.native_launch_facade import NativeLaunchFacade
        from app.services.native_observations_facade import NativeObservationsFacade
        from app.services.native_observations_host import NativeObservationsHost
        from app.services.connected_report_facade import ConnectedReportFacade
        from app.services.connected_followup_facade import ConnectedFollowupFacade
        from flask import request, g

        session = self

        class ReportWitness(ConnectedReportFacade):
            def execute(self, method, graph_id, payload):
                session._capture_payload('report', method, payload)
                return super().execute(method, graph_id, payload)

        class FollowupWitness(ConnectedFollowupFacade):
            def execute(self, method, graph_id, payload):
                session._capture_payload('followup', method, payload)
                return super().execute(method, graph_id, payload)

        class BrowserConfig(Config):
            DEBUG = False
            TESTING = False
            NEXAWEAVE_APP_MODE = MIROFISH_APP_MODE = 'research_local'
            NEXAWEAVE_ALLOWED_ORIGINS = MIROFISH_ALLOWED_ORIGINS = (ORIGIN,)

        values = dict(self.settings.child_environment, KNOWLEDGE_PYTHON=self.settings.python,
            KNOWLEDGE_BOOTSTRAP_SCRIPT=self.settings.bootstrap, KNOWLEDGE_READ_TOKEN=self.token,
            FLASK_HOST='127.0.0.1', FLASK_DEBUG='0')
        previous = {key: os.environ.get(key) for key in values}
        try:
            os.environ.update(values)
            app = create_app(BrowserConfig, read_facade=self.prep.reader,
                source_facade=KnowledgeSourceFacade(self.settings),
                ingestion_facade=KnowledgeIngestionFacade(self.settings, environment={
                    'KNOWLEDGE_INGESTION_ENABLED': 'false', 'KNOWLEDGE_MODEL_CALLS_AUTHORIZED': 'false',
                    'KNOWLEDGE_INGESTION_ACCOUNT_ID': str(self.prep.account_id),
                    'KNOWLEDGE_INGESTION_CEILING_MICROUSD': '1'}),
                preparation_facade=KnowledgePreparationFacade(self.settings, host=self.prep),
                native_launch_facade=NativeLaunchFacade(self.settings, host=self.native_host),
                native_observations_facade=NativeObservationsFacade(self.settings,
                    host=NativeObservationsHost(launch_host=self.native_host)),
                connected_report_facade=ReportWitness(self.settings, host=self.reports),
                connected_followup_facade=FollowupWitness(self.settings, host=self.followups))
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        def entered():
            g.followup_browser_tracked = True
            with self.requests_condition:
                self.requests.add(threading.get_ident())
            with self.lock:
                g.followup_browser_stage = self.active
                g.followup_browser_sequence = self.sequence
                g.followup_browser_command = self.command_payload

        # Tracking precedes token/origin middleware, without changing its result.
        app.before_request_funcs.setdefault(None, []).insert(0, entered)

        @app.teardown_request
        def left(_error):
            if getattr(g, 'followup_browser_tracked', False):
                with self.requests_condition:
                    self.requests.discard(threading.get_ident())
                    self.requests_condition.notify_all()

        @app.after_request
        def observe(response):
            if not request.path.startswith('/api/'):
                return response
            try:
                stage = g.followup_browser_stage
                command = g.followup_browser_command
                require(stage in STAGES and type(command) is dict)
                require(g.followup_browser_sequence == self.sequence and stage == self.active)
                require(not request.query_string and request.headers.getlist('Origin') == [ORIGIN])
                require(not request.headers.get('Content-Encoding')
                        and not request.headers.get('Transfer-Encoding'))
                allowed = self._allowed(stage, command)
                method = request.method
                if method == 'OPTIONS':
                    requested = request.headers.get('Access-Control-Request-Method')
                    require((requested, request.path) in allowed and response.status_code == 204)
                    names = request.headers.get('Access-Control-Request-Headers', '').split(',')
                    require(all(name.strip().lower() in {'authorization', 'content-type'} for name in names))
                    raw = b''
                    body = data = None
                else:
                    require((method, request.path) in allowed)
                    supplied = request.headers.getlist('Authorization')
                    require(len(supplied) == 1
                            and hmac.compare_digest(supplied[0], 'Bearer ' + self.token))
                    require(response.status_code == 200 and response.mimetype == 'application/json'
                            and not response.headers.get('Content-Encoding'))
                    length = response.content_length
                    require(type(length) is int and 0 < length <= EVENT_BYTES)
                    raw = response.get_data()
                    require(len(raw) == length)
                    envelope = strict(raw)
                    keys(envelope, ('success', 'data'))
                    require(envelope['success'] is True and type(envelope['data']) is dict)
                    data = envelope['data']
                    body = getattr(g, 'followup_browser_body', None)
                    if method == 'POST':
                        require(request.mimetype == 'application/json'
                                and type(request.content_length) is int
                                and 0 < request.content_length <= WIRE and type(body) is dict)
                    else:
                        require(body is None and method == 'GET')
                metadata = dict(sequence=g.followup_browser_sequence, stage=stage,
                    method=method, path=request.path, origin=ORIGIN, status=response.status_code,
                    request=body, request_sha256=getattr(g, 'followup_browser_request_sha256', None),
                    response_sha256=sha(raw), response_bytes=len(raw))
                cost = len(raw) + len(encoded(metadata))
                with self.lock:
                    require(self.event_count < EVENTS and self.event_bytes + cost <= EVENT_BYTES,
                            'followup_browser_witness_bound')
                    self.event_count += 1
                    self.event_bytes += cost
                    if method == 'POST' and request.path in {
                            self._route('followup', 'plan'), self._route('followup', 'start')}:
                        require(not any(event['method'] == method and event['path'] == request.path
                                        for event in self.events), 'followup_browser_duplicate_mutation')
                    self.events.append(dict(metadata, data=data))
            except Exception:
                self._failure('followup_browser_witness_refused')
            return response
        return app

    def _start_http(self):
        from werkzeug.serving import make_server
        self.server = make_server('127.0.0.1', 0, self._app(), threaded=True)
        self.port = self.server.server_port
        self.server_thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=.05), name='followup-browser-http')
        self.server_thread.start()

    def _stop_http(self):
        if self.server is not None:
            if self.server_thread is not None and self.server_thread.is_alive():
                stop = threading.Thread(target=self.server.shutdown, name='followup-http-stop')
                stop.start()
                stop.join(timeout=self._remaining(3, cleanup=True))
                require(not stop.is_alive(), 'followup_browser_http_shutdown_unknown')
            self.server.server_close()
        if self.server_thread is not None and self.server_thread.ident is not None:
            self.server_thread.join(timeout=self._remaining(3, cleanup=True))
            require(not self.server_thread.is_alive(), 'followup_browser_http_not_closed')
        with self.requests_condition:
            require(self.requests_condition.wait_for(lambda: not self.requests,
                timeout=self._remaining(3, cleanup=True)), 'followup_browser_request_not_closed')
        self.server = self.server_thread = None

    def _environment(self, directory):
        keep = ('PATH', 'SYSTEMROOT', 'WINDIR', 'LANG', 'LC_ALL', 'TZ')
        env = {key: os.environ[key] for key in keep if key in os.environ}
        env.update(HOME=directory, USERPROFILE=directory, TMPDIR=directory, TMP=directory,
                   TEMP=directory, CI='true', BROWSER='none')
        return env

    def _drain(self, pipe):
        count = 0
        try:
            while True:
                raw = pipe.read(1024)
                if not raw:
                    return
                count += len(raw)
                require(count <= 65536)
        except Exception:
            self._failure('followup_browser_child_output_refused')

    def _vite_stdout(self, pipe):
        count = 0
        try:
            while True:
                raw = pipe.readline(65537)
                if not raw:
                    return
                count += len(raw)
                require(count <= 65536)
                if raw.strip() == b'NexaWeave offline browser fixture: http://127.0.0.1:4317/tests/browser-rendering.html':
                    self.vite_ready.set()
        except Exception:
            self._failure('followup_browser_vite_output_refused')

    def _stdout(self, pipe):
        try:
            for _ in range(COMMANDS):
                raw = pipe.readline(WIRE + 1)
                if not raw:
                    return
                require(raw.endswith(b'\n') and len(raw) <= WIRE and b'\r' not in raw
                        and self.token.encode('ascii') not in raw)
                value = strict(raw)
                require(type(value) is dict)
                self.replies.put_nowait(value)
            require(not pipe.read(1), 'followup_browser_extra_reply')
        except Exception:
            self._failure('followup_browser_protocol_refused')
            try:
                self.replies.put_nowait(None)
            except queue.Full:
                pass

    def _spawn(self, args, *, protocol=False):
        directory = tempfile.TemporaryDirectory(prefix='followup-browser-', dir=self.directory, delete=False)
        owner = OwnedProcess()
        owner.bind_private_directory(directory)
        entry = dict(owner=owner, directory=directory, process=None, threads=[], group=None, identity=None)
        self.children.append(entry)
        env = self._environment(directory.name)
        if protocol:
            env['NEXAWEAVE_RESEARCH_BROWSER_NODE'] = str(self.node)
            env['NEXAWEAVE_RESEARCH_BROWSER_CHROME'] = str(self.chrome_executable)
        process = owner.start(subprocess.Popen, args, cwd=directory.name, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, close_fds=True, start_new_session=True, bufsize=0)
        entry['process'], entry['group'] = process, process.pid
        require(os.getpgid(process.pid) == process.pid)
        entry['identity'] = self._process(process.pid)
        require(entry['identity'] and entry['identity']['group'] == process.pid)
        for pipe, target in ((process.stdout, self._stdout if protocol else self._vite_stdout),
                             (process.stderr, self._drain)):
            thread = threading.Thread(target=target, args=(pipe,), daemon=True)
            entry['threads'].append(thread)
            thread.start()
        return entry

    def _send(self, command, end):
        raw = encoded(command) + b'\n'
        require(len(raw) <= WIRE and self.driver['process'].poll() is None)
        errors = []

        def write():
            try:
                pipe = self.driver['process'].stdin
                position = 0
                while position < len(raw):
                    require(time.monotonic() < end)
                    count = pipe.write(raw[position:])
                    require(type(count) is int and count > 0)
                    position += count
                pipe.flush()
            except Exception:
                errors.append(True)

        thread = threading.Thread(target=write, name='followup-browser-command', daemon=True)
        self.driver['threads'].append(thread)
        thread.start()
        thread.join(timeout=max(0, end - time.monotonic()))
        require(not thread.is_alive() and not errors, 'followup_browser_command_refused')

    def __enter__(self):
        require(not self.entered)
        self.entered = True
        try:
            require(os.name == 'posix' and sys.platform == 'linux')
            require(type(self.deadline) in (float, int) and math.isfinite(self.deadline)
                    and 15 < self.deadline - time.monotonic() <= 600)
            require(self.directory.is_absolute() and self.directory.is_dir()
                    and not self.directory.is_symlink())
            keys(self.report_reference, ('schema_version', 'report_id', 'plan_sha256'))
            require(self.report_reference['schema_version'] == 1)
            uuid(self.report_reference['report_id']); digest(self.report_reference['plan_sha256'])
            require(self.report_result['state'] == 'completed'
                    and all(self.report_result[key] == self.report_reference[key]
                            for key in ('report_id', 'plan_sha256')))
            require(self.prep.account_id == self.native_host.account_id == self.reports.account_id
                    == self.followups.account_id and self.followups.parent is self.reports)
            self.token = secrets.token_hex(32)
            self.settings = replace(self.prep.settings, token=self.token)
            self.node = Path(os.environ['NEXAWEAVE_RESEARCH_BROWSER_NODE'])
            self.chrome_executable = Path(os.environ['NEXAWEAVE_RESEARCH_BROWSER_CHROME'])
            require(all(path.is_absolute() and path.is_file() and os.access(path, os.X_OK)
                        and not path.is_symlink() for path in (self.node, self.chrome_executable)))
            self._start_http()
            self.vite = self._spawn([str(self.node), str(ROOT / 'frontend/tests/serve-browser-fixture.mjs')])
            end = time.monotonic() + self._remaining(15)
            while True:
                require(time.monotonic() < end and self.vite['process'].poll() is None,
                        'followup_browser_vite_deadline')
                if not self.vite_ready.wait(timeout=min(.05, max(0, end - time.monotonic()))):
                    continue
                connection = http.client.HTTPConnection('127.0.0.1', 4317,
                    timeout=min(1, max(.001, end - time.monotonic())))
                try:
                    connection.request('GET', '/research')
                    response = connection.getresponse()
                    raw = response.read(262145)
                    require(response.status == 200 and len(raw) <= 262144)
                    break
                finally:
                    connection.close()
            self.driver = self._spawn([str(self.node), str(ROOT / 'frontend/tests/run-followup-browser.mjs')],
                                      protocol=True)
            self.profile_root = Path(self.driver['directory'].name) / 'profile'
            self.profile_root.mkdir()
            self.perform('connect')
            self._pin_chrome()
            return self
        except BaseException as error:
            self.work_failed = True
            try:
                self._cleanup()
            except BaseException as cleanup:
                raise BaseExceptionGroup('followup_browser_start_and_cleanup', [error, cleanup]) from None
            raise

    async def __aenter__(self):
        return await asyncio.to_thread(self.__enter__)

    async def __aexit__(self, kind, error, traceback):
        return await asyncio.to_thread(self.__exit__, kind, error, traceback)

    def _payload(self, stage, payload):
        if stage == 'connect':
            require(not self.connected and self.sequence == 0 and payload is None)
            return dict(connection=dict(api_origin='http://127.0.0.1:' + str(self.port),
                display_graph_id=self.prep.display_graph_id, bearer_token=self.token),
                report={key: self.report_reference[key] for key in ('report_id', 'plan_sha256')},
                profile_root=str(self.profile_root))
        require(self.connected and not self.driver_closed)
        value = {} if payload is None else payload
        require(type(value) is dict)
        if stage == 'followup-plan':
            keys(value, ('question', 'output_language', 'expected_history_sha256'))
            require(len(self.turns) < 2 and len(self.ready) == len(self.turns))
            require(type(value['question']) is str and 0 < len(value['question']) <= 4000
                    and value['output_language'] in {'en', 'zh', 'ms'})
            digest(value['expected_history_sha256'])
        elif stage == 'followup-history':
            keys(value, ('schema_version', 'report_id', 'report_plan_sha256', 'before_ordinal'))
            require(value['schema_version'] == 1 and value['before_ordinal'] is None
                    and value['report_id'] == self.report_reference['report_id']
                    and value['report_plan_sha256'] == self.report_reference['plan_sha256'])
        elif stage == 'locale':
            keys(value, ('locale',))
            require(value['locale'] in {'en', 'zh', 'ms'} and value['locale'] not in self.locales)
        elif stage == 'close':
            keys(value, ())
        else:
            extra = ('kind',) if stage == 'followup-export' else ('reload',) if stage == 'followup-recover' else ()
            keys(value, ('schema_version', 'turn_id', 'plan_sha256', *extra))
            require(type(value['schema_version']) is int and value['schema_version'] == 1)
            uuid(value['turn_id']); digest(value['plan_sha256'])
            require(self.turns.get(value['turn_id']) == value['plan_sha256'])
            if stage == 'followup-start':
                require(value['turn_id'] not in self.starts and self.selected == value['turn_id'])
            elif stage == 'followup-ready':
                require(value['turn_id'] in self.starts and value['turn_id'] not in self.ready)
            else:
                require(value['turn_id'] in self.ready)
            if stage == 'followup-export':
                require(value['kind'] in {'answer', 'conversation'}
                        and self.exports.get((value['turn_id'], value['kind']), 0) < 2)
            if stage == 'followup-recover':
                require(type(value['reload']) is bool)
        require(len(encoded(value)) <= WIRE)
        return value

    def _one(self, events, family, method):
        found = [event for event in events
                 if event['method'] == 'POST' and event['path'] == self._route(family, method)]
        require(len(found) == 1, 'followup_browser_http_witness_missing')
        return found[0]

    def _turn(self, data, value, *, read=False):
        turn = data['turn'] if read else data
        require(type(turn) is dict and all(turn[key] == value[key] for key in ('turn_id', 'plan_sha256')))
        report = turn['binding']['report']
        require(report['report_id'] == self.report_reference['report_id']
                and report['plan_sha256'] == self.report_reference['plan_sha256']
                and report['receipt_sha256'] == self.report_result['receipt_sha256'])
        require(turn['binding']['display_graph_id'] == self.prep.display_graph_id
                and turn['binding']['principal'] == self.settings.principal
                and turn['binding']['scope'] == self.settings.scope)
        return turn

    def _verify(self, stage, payload, proof, events):
        if stage == 'connect':
            keys(proof, ('display_graph_id', 'report_id', 'plan_sha256', 'token_cleared'))
            require(proof == dict(display_graph_id=self.prep.display_graph_id,
                report_id=self.report_reference['report_id'], plan_sha256=self.report_reference['plan_sha256'],
                token_cleared=True))
            graph = [event for event in events if event['method'] == 'GET'
                     and event['path'] == '/api/graph/data/' + self.prep.display_graph_id]
            require(len(graph) == 1 and graph[0]['data']['graph_id'] == self.prep.display_graph_id)
            parent = self._one(events, 'report', 'status')
            require(parent['request'] == self.report_reference
                    and parent['data']['receipt_sha256'] == self.report_result['receipt_sha256']
                    and parent['data']['state'] == 'completed')
            self.connected = True
            return proof
        if stage == 'locale':
            keys(proof, ('locale', 'lang'))
            require(proof == dict(locale=payload['locale'], lang=payload['locale']) and not events)
            self.locales.add(payload['locale'])
            return proof
        if stage == 'close':
            keys(proof, ('context_closed', 'browser_closed', 'private_tree_removed'))
            require(all(value is True for value in proof.values()) and not events
                    and not any(self.profile_root.iterdir()))
            self.driver_closed = True
            return proof
        method = {'followup-plan': 'plan', 'followup-start': 'start', 'followup-ready': 'status',
            'followup-read': 'read', 'followup-export': 'download', 'followup-history': 'history',
            'followup-recover': 'status'}[stage]
        event = self._one(events, 'followup', method)
        body, data = event['request'], event['data']
        self.last_request = body
        require(event['request_sha256'] == sha(encoded(body)))
        if stage == 'followup-plan':
            keys(proof, ('turn_id', 'plan_sha256', 'question_sha256', 'output_language'))
            keys(body, ('schema_version', 'turn_id', 'report_id', 'report_plan_sha256',
                        'question', 'output_language', 'expected_history_sha256'))
            uuid(body['turn_id']); digest(data['plan_sha256'])
            require(body['schema_version'] == 1 and body['turn_id'] not in self.turns
                    and body['report_id'] == self.report_reference['report_id']
                    and body['report_plan_sha256'] == self.report_reference['plan_sha256']
                    and all(body[key] == payload[key] for key in payload))
            self._turn(data, dict(turn_id=body['turn_id'], plan_sha256=data['plan_sha256']))
            history = self._one(events, 'followup', 'history')
            require(events.index(history) < events.index(event)
                    and history['request'] == dict(schema_version=1,
                        report_id=self.report_reference['report_id'],
                        report_plan_sha256=self.report_reference['plan_sha256'], before_ordinal=None)
                    and history['data']['head_sha256'] == payload['expected_history_sha256']
                    and history['data']['total_completed'] == len(self.ready)
                    and data['history']['head_sha256'] == payload['expected_history_sha256']
                    and data['history']['total_completed'] == len(self.ready)
                    and data['state'] == 'planned')
            require(proof == dict(turn_id=body['turn_id'], plan_sha256=data['plan_sha256'],
                question_sha256=sha(payload['question'].encode('utf-8')), output_language=payload['output_language']))
            self.turns[data['turn_id']] = data['plan_sha256']
            self.selected = data['turn_id']
        elif stage == 'followup-history':
            keys(proof, ('report_id', 'total_completed', 'head_sha256'))
            require(body == payload and proof == dict(report_id=payload['report_id'],
                total_completed=data['total_completed'], head_sha256=data['head_sha256'])
                and data['total_completed'] == len(self.ready))
            binding = data['binding']
            require(binding['display_graph_id'] == self.prep.display_graph_id
                    and binding['principal'] == self.settings.principal and binding['scope'] == self.settings.scope)
        elif stage == 'followup-export':
            keys(proof, ('turn_id', 'plan_sha256', 'kind', 'name', 'size', 'sha256'))
            require(body == payload)
            artifact = data['artifact']
            require(type(artifact['size']) is int and 0 < artifact['size'] <= 1048576)
            digest(artifact['sha256'])
            raw = base64.b64decode(artifact['content_base64'], validate=True)
            require(len(raw) == artifact['size'] and sha(raw) == artifact['sha256'])
            require(all(data[key] == payload[key] for key in ('turn_id', 'plan_sha256')))
            expected = {key: payload[key] for key in ('turn_id', 'plan_sha256', 'kind')}
            expected.update({key: artifact[key] for key in ('name', 'size', 'sha256')})
            require(proof == expected)
            marker = (payload['turn_id'], payload['kind'])
            self.exports[marker] = self.exports.get(marker, 0) + 1
            return proof
        else:
            expected_body = {key: payload[key] for key in ('schema_version', 'turn_id', 'plan_sha256')}
            require(body == expected_body)
            turn = self._turn(data, payload, read=stage == 'followup-read')
            extras = ('rendered',) if stage == 'followup-read' else ('recovered',) if stage == 'followup-recover' else ()
            keys(proof, ('turn_id', 'plan_sha256', 'state', *extras))
            require(all(proof[key] == payload[key] for key in ('turn_id', 'plan_sha256'))
                    and proof['state'] == turn['state'])
            if stage == 'followup-start':
                require(turn['state'] in {'queued', 'generating', 'completed'})
                self.starts.add(payload['turn_id'])
            else:
                require(turn['state'] == 'completed')
            if stage == 'followup-ready':
                self.ready.add(payload['turn_id'])
            if stage == 'followup-read':
                require(proof['rendered'] is True and type(data['content']) is str
                        and '[[source:' in data['content'] and '[[native:' in data['content'])
            if stage == 'followup-recover':
                require(proof['recovered'] is True)
                self.selected = payload['turn_id']
                if payload['reload']:
                    parent = self._one(events, 'report', 'status')
                    require(parent['request'] == self.report_reference
                            and parent['data']['state'] == 'completed'
                            and parent['data']['receipt_sha256'] == self.report_result['receipt_sha256'])
        return data

    def perform(self, stage, payload=None):
        require(stage in STAGES and not self.closed and self.sequence < COMMANDS)
        value = self._payload(stage, payload)
        timeout = self._remaining(15, cleanup=stage == 'close')
        timeout_ms = int(timeout * 1000)
        require(1 <= timeout_ms <= 15000)
        end = time.monotonic() + timeout_ms / 1000
        with self.requests_condition:
            require(not self.requests, 'followup_browser_request_in_flight')
        with self.lock:
            require(not self.failures)
            self.sequence += 1
            self.active, self.command_payload = stage, value
            self.events = deque()
        self.last_request, self.last_proof = None, {}
        command = dict(schema_version=1, sequence=self.sequence, stage=stage,
                       timeout_ms=timeout_ms, payload=value)
        self._send(command, end)
        try:
            reply = self.replies.get(timeout=max(.001, end - time.monotonic()))
        except queue.Empty:
            raise RuntimeError('followup_browser_deadline') from None
        require(time.monotonic() <= end and type(reply) is dict)
        require(type(reply.get('schema_version')) is int and reply['schema_version'] == 1
                and type(reply.get('sequence')) is int and reply['sequence'] == self.sequence
                and reply.get('stage') == stage and type(reply.get('ok')) is bool)
        if reply['ok'] is False:
            keys(reply, ('schema_version', 'sequence', 'stage', 'ok', 'error'))
            require(reply['error'] in ERRORS)
            raise RuntimeError(reply['error'])
        keys(reply, ('schema_version', 'sequence', 'stage', 'ok', 'result'))
        require(type(reply['result']) is dict and len(encoded(reply['result'])) <= WIRE)
        with self.requests_condition:
            require(self.requests_condition.wait_for(lambda: not self.requests,
                timeout=max(0, end - time.monotonic())), 'followup_browser_request_not_closed')
        with self.lock:
            require(not self.failures)
            events = list(self.events)
        self.last_proof = reply['result']
        result = self._verify(stage, value, self.last_proof, events)
        require(time.monotonic() <= end, 'followup_browser_deadline')
        with self.lock:
            self.active, self.command_payload = None, None
        return result

    def _process(self, pid):
        require(type(pid) is int and pid > 1)
        path = Path('/proc') / str(pid) / 'stat'
        if not path.exists():
            return None
        with path.open('rb') as stream:
            raw = stream.read(4097)
        require(len(raw) <= 4096)
        fields = raw[raw.rfind(b')') + 2:].split()
        require(len(fields) >= 20)
        return dict(pid=pid, parent=int(fields[1]), group=int(fields[2]), start=int(fields[19]))

    def _pin_chrome(self):
        pid = self.driver['process'].pid
        path = Path('/proc') / str(pid) / 'task' / str(pid) / 'children'
        with path.open('rb') as stream:
            raw = stream.read(4097)
        require(len(raw) <= 4096 and len(raw.split()) <= 16)
        matches = []
        for child in raw.split():
            observed = self._process(int(child))
            if observed and observed['parent'] == pid:
                executable = (Path('/proc') / child.decode('ascii') / 'exe').resolve(strict=True)
                if executable == self.chrome_executable.resolve(strict=True):
                    matches.append(observed)
        require(len(matches) == 1, 'followup_browser_chrome_identity')
        self.chrome = matches[0]
        require(self.chrome['group'] in {self.chrome['pid'], self.driver['group']})

    def _stop_group(self, group, identity, *, terminate=False):
        current = self._process(group)
        if current is None:
            end = time.monotonic() + self._remaining(3, cleanup=True)
            while time.monotonic() < end:
                try:
                    os.killpg(group, 0)
                except ProcessLookupError:
                    return
                time.sleep(min(.01, max(0, end - time.monotonic())))
            raise RuntimeError('followup_browser_group_leader_unavailable')
        require(identity and current['start'] == identity['start'] and current['group'] == group,
                'followup_browser_group_identity_changed')
        if terminate:
            try:
                os.killpg(group, signal.SIGTERM)
            except ProcessLookupError:
                pass
            return
        end = time.monotonic() + self._remaining(3, cleanup=True)
        while time.monotonic() < end:
            try:
                os.killpg(group, 0)
            except ProcessLookupError:
                return
            time.sleep(min(.01, max(0, end - time.monotonic())))
        raise RuntimeError('followup_browser_group_cleanup_unknown')

    def _cleanup(self):
        errors = []
        if self.driver and self.chrome is None and self.driver['process'].poll() is None:
            try:
                self._pin_chrome()
            except Exception as error:
                errors.append(error)
        if self.driver and self.connected and not self.driver_closed:
            try:
                self.perform('close')
            except Exception as error:
                errors.append(error)
        if self.driver and self.driver_closed:
            try:
                require(self.driver['process'].wait(timeout=self._remaining(3, cleanup=True)) == 0)
            except Exception as error:
                errors.append(error)
        if self.chrome:
            try:
                identity = self.chrome if self.chrome['group'] == self.chrome['pid'] else self.driver['identity']
                self._stop_group(self.chrome['group'], identity, terminate=True)
            except Exception as error:
                errors.append(error)
        for entry in reversed(self.children):
            process = entry['process']
            if entry is self.vite and process is not None and process.poll() is None:
                try:
                    process.stdin.write(b'stop\n')
                    process.stdin.flush()
                    require(process.wait(timeout=self._remaining(3, cleanup=True)) == 0)
                except Exception as error:
                    errors.append(error)
            try:
                if process is not None:
                    self._stop_group(entry['group'], entry['identity'], terminate=True)
            except Exception as error:
                errors.append(error)
            try:
                entry['owner'].stop(entry['threads'], timeout=self._remaining(3, cleanup=True))
                require(entry['owner'].closed and all(not thread.is_alive() for thread in entry['threads']))
                if process is not None:
                    self._stop_group(entry['group'], entry['identity'])
            except Exception as error:
                errors.append(error)
        if self.chrome:
            try:
                identity = self.chrome if self.chrome['group'] == self.chrome['pid'] else self.driver['identity']
                self._stop_group(self.chrome['group'], identity)
                observed = self._process(self.chrome['pid'])
                require(observed is None or observed['start'] != self.chrome['start'],
                        'followup_browser_chrome_not_absent')
            except Exception as error:
                errors.append(error)
        try:
            self._stop_http()
        except Exception as error:
            errors.append(error)
        while True:
            try:
                self.replies.get_nowait()
                errors.append(RuntimeError('followup_browser_extra_reply'))
            except queue.Empty:
                break
        errors.extend(RuntimeError(code) for code in self.failures)
        if not errors and not self.work_failed:
            for entry in reversed(self.children):
                try:
                    entry['owner'].cleanup_private_directory(entry['directory'])
                    require(not Path(entry['directory'].name).exists())
                except Exception as error:
                    errors.append(error)
        self.closed = not errors
        if errors:
            raise ExceptionGroup('followup_browser_cleanup_refused', errors)

    def __exit__(self, kind, error, traceback):
        self.work_failed = error is not None
        try:
            self._cleanup()
        except BaseException as cleanup:
            if error is not None:
                raise BaseExceptionGroup('followup_browser_work_and_cleanup', [error, cleanup]) from None
            raise
        return False

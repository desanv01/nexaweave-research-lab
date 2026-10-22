"""One owned real /research session; Main admits execution, never a test suite."""
from collections import deque
from dataclasses import replace
import asyncio
import base64
import hashlib
import http.client
import json
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

from app.utils.owned_process import OwnedProcess

ROOT = Path(__file__).resolve().parents[3]
STAGES = ('connect', 'preparation-plan', 'preparation-start', 'preparation-ready',
          'native-start', 'native-ready', 'report-start', 'report-read',
          'pdf-read-after-restart', 'close')
SECONDS = (60, 60, 60, 60, 60, 180, 180, 120, 60, 15)
WIRE_BYTES = 7200
FAILURE_PHASES = frozenset(('browser-launch', 'page-load', 'graph-connect',
    'source-catalog', 'source-inspect', 'pdf-metadata', 'pdf-download', 'protocol', 'close'))
LAUNCH_ERRORS = frozenset(('launch-timeout', 'executable-missing', 'permission-refused',
    'sandbox-refused', 'browser-closed', 'context-create-failed', 'page-create-failed', 'launch-unknown',
    'namespace-refused', 'runtime-library-missing', 'crashpad-database-missing',
    'profile-lock-refused', 'runtime-resource-refused', 'browser-process-sigtrap',
    'browser-process-sigabrt', 'browser-process-exit1', 'cdp-protocol-refused'))


def require(value, code='research_browser_refused'):
    if not value:
        raise RuntimeError(code)


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, 'research_browser_duplicate_key')
        result[key] = value
    return result


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def failure_reply(reply, stage):
    require(type(reply) is dict
            and set(reply) in ({'stage', 'ok', 'error'}, {'stage', 'ok', 'error', 'phase'},
                               {'stage', 'ok', 'error', 'phase', 'launch_error'})
            and reply['stage'] == stage and reply['ok'] is False
            and type(reply['error']) is str
            and reply['error'] in {'research_browser_failed', 'research_browser_cleanup_failed'},
            'research_browser_failure_reply_refused')
    require(reply['error'] != 'research_browser_cleanup_failed' or stage == 'close',
            'research_browser_failure_reply_refused')
    code = reply['error']
    if 'phase' in reply:
        require(type(reply['phase']) is str and reply['phase'] in FAILURE_PHASES,
                'research_browser_failure_reply_refused')
        code += ':' + reply['phase']
    if 'launch_error' in reply:
        require(reply['error'] == 'research_browser_failed' and reply['phase'] == 'browser-launch'
                and type(reply['launch_error']) is str and reply['launch_error'] in LAUNCH_ERRORS,
                'research_browser_failure_reply_refused')
        code += ':' + reply['launch_error']
    return RuntimeError(code)


class ResearchBrowserSession:
    """Same Flask/PG/Temporal hosts; only the actual UI dispatches mutations."""
    def __init__(self, directory, prep, native_host, reports, selected, report_log):
        require(os.name == 'posix' and sys.platform == 'linux')
        self.directory, self.prep = Path(directory), prep
        self.native_host, self.reports, self.selected = native_host, reports, selected
        self.report_log = Path(report_log)
        self.report_plan_no_calls = False
        self.token = secrets.token_hex(32)
        self.settings = replace(prep.settings, token=self.token)
        self.deadline = time.monotonic() + 600
        self.step, self.active = 0, None
        self.server = self.server_thread = self.driver = None
        self.port = 0
        self.lock = threading.Lock()
        self.events = deque()
        self.event_bytes = 0
        self.failures = []
        self.mutations = {}
        self.children = []
        self.chrome = None
        self.vite_ready = threading.Event()
        self.requests = set()
        self.requests_condition = threading.Condition()
        self.closed = False
        self.work_failed = False
        self.replies = queue.Queue(maxsize=2)
        self.references = {}
        self.catalog = []
        self.last_proof = {}

    def _remaining(self, maximum):
        remaining = min(maximum, self.deadline - time.monotonic())
        require(remaining > 0, 'research_browser_deadline')
        return remaining

    def _failure(self, code):
        with self.lock:
            if code not in self.failures and len(self.failures) < 16:
                self.failures.append(code)

    def _app(self):
        from app import Config, create_app
        from app.services.knowledge_source_facade import KnowledgeSourceFacade
        from app.services.knowledge_ingestion_facade import KnowledgeIngestionFacade
        from app.services.knowledge_preparation_facade import KnowledgePreparationFacade
        from app.services.native_launch_facade import NativeLaunchFacade
        from app.services.native_observations_facade import NativeObservationsFacade
        from app.services.native_observations_host import NativeObservationsHost
        from app.services.connected_report_facade import ConnectedReportFacade
        from flask import request, g

        class BrowserConfig(Config):
            DEBUG = False
            TESTING = False
            NEXAWEAVE_APP_MODE = MIROFISH_APP_MODE = 'research_local'
            NEXAWEAVE_ALLOWED_ORIGINS = MIROFISH_ALLOWED_ORIGINS = ('http://127.0.0.1:4317',)

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
                connected_report_facade=ConnectedReportFacade(self.settings, host=self.reports))
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        @app.before_request
        def entered():
            g.research_browser_request = True
            with self.requests_condition:
                self.requests.add(threading.get_ident())

        @app.teardown_request
        def left(_error):
            if getattr(g, 'research_browser_request', False):
                with self.requests_condition:
                    self.requests.discard(threading.get_ident())
                    self.requests_condition.notify_all()

        @app.after_request
        def observe(response):
            # Passive response-only witness: never consumes request streams or changes replies.
            if not request.path.startswith('/api/') or request.method == 'OPTIONS':
                return response
            try:
                path = request.path
                mutation = (path.startswith('/api/preparation/') and path.rsplit('/', 1)[-1] in {'plan', 'start'}
                    or path.startswith('/api/native-launch/') and path.split('/')[3] in {'plan', 'start', 'cancel'}
                    or path.startswith('/api/connected-report/') and path.split('/')[3] in {'plan', 'start', 'cancel'}
                    or path.startswith(('/api/source/retain', '/api/source/ingestion/execute')))
                if mutation:
                    allowed = {
                        'preparation-plan': ('/api/preparation/' + self.prep.display_graph_id + '/plan',),
                        'preparation-start': ('/api/preparation/' + self.prep.display_graph_id + '/start',),
                        'native-start': tuple('/api/native-launch/' + kind + '/' + self.prep.display_graph_id
                                              for kind in ('plan', 'start')),
                        'report-start': tuple('/api/connected-report/' + kind + '/' + self.prep.display_graph_id
                                              for kind in ('plan', 'start'))}
                    with self.lock:
                        self.mutations[path] = self.mutations.get(path, 0) + 1
                        valid = path in allowed.get(self.active, ()) and self.mutations[path] == 1
                    if not valid:
                        self._failure('research_browser_duplicate_or_unexpected_mutation')
                if response.status_code != 200:
                    return response
                raw = response.get_data()
                require(len(raw) <= 262144, 'research_browser_witness_bytes')
                value = json.loads(raw, object_pairs_hook=pairs)
                require(type(value) is dict and value.get('success') is True and 'data' in value)
                if path == '/api/connected-report/plan/' + self.prep.display_graph_id:
                    require(value['data']['state'] == 'planned' and not self.report_log.exists(),
                            'research_browser_report_plan_dispatched')
                    self.report_plan_no_calls = True
                with self.lock:
                    require(len(self.events) < 64 and self.event_bytes + len(raw) <= 2097152,
                            'research_browser_witness_bound')
                    self.events.append((self.active, path, value['data']))
                    self.event_bytes += len(raw)
            except Exception:
                self._failure('research_browser_witness_refused')
            return response
        return app

    def _start_http(self):
        from werkzeug.serving import make_server
        self.server = make_server('127.0.0.1', self.port, self._app(), threaded=True)
        self.port = self.server.server_port
        self.server_thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=.05), name='research-browser-http')
        self.server_thread.start()

    def _stop_http(self):
        if self.server is not None:
            if self.server_thread is not None and self.server_thread.is_alive():
                self.server.shutdown()
            self.server.server_close()
        if self.server_thread is not None and self.server_thread.ident is not None:
            self.server_thread.join(timeout=3)
            require(not self.server_thread.is_alive(), 'research_browser_http_not_closed')
        with self.requests_condition:
            require(self.requests_condition.wait_for(lambda: not self.requests, timeout=3),
                    'research_browser_http_request_not_closed')
        self.server = self.server_thread = None

    def restart(self):
        require(self.step == 8, 'research_browser_restart_stage')
        self._stop_http()
        self._start_http()  # Same port/token/facades/hosts and real stores.

    def _environment(self, directory):
        keep = ('PATH', 'SYSTEMROOT', 'WINDIR', 'LANG', 'LC_ALL', 'TZ')
        env = {key: os.environ[key] for key in keep if key in os.environ}
        env.update(HOME=directory, USERPROFILE=directory, TMPDIR=directory, TMP=directory, TEMP=directory,
                   CI='true', BROWSER='none')
        return env

    def _drain(self, pipe):
        count = 0
        try:
            while True:
                raw = pipe.read(1024)
                if not raw:
                    return
                count += len(raw)
                if count > 65536:
                    self._failure('research_browser_child_output_bound')
                    return
        except Exception:
            self._failure('research_browser_child_output_refused')

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
            self._failure('research_browser_vite_output_refused')

    def _stdout(self, pipe):
        try:
            for _ in STAGES:
                raw = pipe.readline(WIRE_BYTES + 1)
                if not raw:
                    return
                require(raw.endswith(b'\n') and len(raw) <= WIRE_BYTES and b'\r' not in raw)
                require(self.token.encode() not in raw, 'research_browser_secret_output')
                value = json.loads(raw, object_pairs_hook=pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                require(type(value) is dict)
                self.replies.put_nowait(value)
            require(not pipe.read(1), 'research_browser_extra_reply')
        except Exception:
            self._failure('research_browser_protocol_refused')
            try:
                self.replies.put_nowait(None)
            except queue.Full:
                pass

    def _spawn(self, args, *, protocol=False):
        # Explicit cleanup only; failed owned profiles/diagnostics must remain private.
        directory = tempfile.TemporaryDirectory(prefix='research-browser-', dir=self.directory, delete=False)
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

    def __enter__(self):
        try:
            self.node = Path(os.environ['NEXAWEAVE_RESEARCH_BROWSER_NODE'])
            self.chrome_executable = Path(os.environ['NEXAWEAVE_RESEARCH_BROWSER_CHROME'])
            require(all(path.is_absolute() and path.is_file() and os.access(path, os.X_OK)
                        for path in (self.node, self.chrome_executable)))
            self._start_http()
            self.vite = self._spawn([str(self.node), str(ROOT / 'frontend/tests/serve-browser-fixture.mjs')])
            ready = time.monotonic() + self._remaining(20)
            while True:
                require(self.vite['process'].poll() is None, 'research_browser_vite_exit')
                try:
                    require(time.monotonic() < ready, 'research_browser_vite_deadline')
                    if not self.vite_ready.wait(timeout=.05):
                        continue
                    connection = http.client.HTTPConnection('127.0.0.1', 4317, timeout=1)
                    try:
                        connection.request('GET', '/research')
                        response = connection.getresponse()
                        raw = response.read(262145)
                        require(len(raw) <= 262144)
                        if response.status == 200:
                            break
                    finally:
                        connection.close()
                except OSError:
                    pass
                require(time.monotonic() < ready, 'research_browser_vite_deadline')
                time.sleep(.05)
            self.driver = self._spawn([str(self.node), str(ROOT / 'frontend/tests/run-research-browser.mjs')],
                                      protocol=True)
            self.profile_root = Path(self.driver['directory'].name) / 'profile'
            self.profile_root.mkdir()
            self.perform('connect')
            return self
        except BaseException as error:
            self.work_failed = True
            try:
                self._cleanup()
            except BaseException as cleanup:
                raise BaseExceptionGroup('research_browser_start_and_cleanup', [error, cleanup]) from None
            raise

    async def __aenter__(self):
        return await asyncio.to_thread(self.__enter__)

    async def __aexit__(self, kind, error, traceback):
        return await asyncio.to_thread(self.__exit__, kind, error, traceback)

    def _send(self, value, timeout):
        raw = encoded(value) + b'\n'
        require(len(raw) <= WIRE_BYTES, 'research_browser_command_bound')
        failures = []
        def write():
            try:
                offset = 0
                while offset < len(raw):
                    count = self.driver['process'].stdin.write(raw[offset:])
                    require(count is not None and count > 0)
                    offset += count
            except Exception:
                failures.append(True)
        thread = threading.Thread(target=write, daemon=True)
        self.driver['threads'].append(thread)
        thread.start()
        thread.join(timeout=timeout)
        require(not thread.is_alive() and not failures, 'research_browser_command_refused')

    def _route(self, stage):
        graph = self.prep.display_graph_id
        return {
            'preparation-plan': '/api/preparation/' + graph + '/plan',
            'preparation-start': '/api/preparation/' + graph + '/start',
            'preparation-ready': '/api/preparation/' + graph + '/status',
            'native-start': '/api/native-launch/start/' + graph,
            'native-ready': '/api/native-launch/status/' + graph,
            'report-start': '/api/connected-report/start/' + graph,
            'report-read': '/api/connected-report/read/' + graph,
            'pdf-read-after-restart': '/api/source/original/' + graph + '/' + self.selected.revisions[0],
        }.get(stage)

    def _reference(self, stage, data, reply):
        if stage.startswith('preparation-'):
            expected = {key: data[key] for key in ('operation_id', 'plan_sha256')}
            if stage == 'preparation-ready':
                expected.update(simulation_id=data['receipt']['simulation_id'],
                                artifact_sha256=data['receipt']['artifact_sha256'])
        elif stage.startswith('native-'):
            expected = dict(launch_id=data['request']['run_id'], launch_sha256=data['launch_sha256'])
            if stage == 'native-ready':
                expected['evidence_sha256'] = data['receipt']['evidence_sha256']
        elif stage.startswith('report-'):
            report = data['report'] if stage == 'report-read' else data
            expected = {key: report[key] for key in ('report_id', 'plan_sha256')}
            if stage == 'report-read':
                expected['receipt_sha256'] = report['receipt_sha256']
        else:
            expected = dict(source_revision=self.selected.revisions[0])
        require(type(reply['reference']) is dict and expected.items() <= reply['reference'].items(),
                'research_browser_reference_mismatch')
        self.references[stage] = expected

    def perform(self, stage, request=None):
        require(self.step < len(STAGES) and STAGES[self.step] == stage, 'research_browser_stage_order')
        timeout = self._remaining(SECONDS[self.step])
        end = time.monotonic() + timeout
        with self.lock:
            self.active, self.events, self.event_bytes = stage, deque(), 0
        command = dict(stage=stage)
        if request is not None:
            command['request'] = request
        if stage == 'connect':
            command.update(connection=dict(api_origin=f'http://127.0.0.1:{self.port}',
                graph_id=self.prep.display_graph_id, token=self.token),
                sources=[dict(source_revision=source['source_revision'], source_name=source['source_name'])
                         for source in self.selected.snapshots],
                profile_root=str(self.profile_root), pdf=dict(source_revision=self.selected.revisions[0],
                    size=len(self.selected.pdf), sha256=hashlib.sha256(self.selected.pdf).hexdigest()))
        self._send(command, max(.001, end - time.monotonic()))
        try:
            reply = self.replies.get(timeout=max(.001, end - time.monotonic()))
        except queue.Empty:
            raise RuntimeError('research_browser_reply_deadline') from None
        if type(reply) is dict and reply.get('ok') is False:
            raise failure_reply(reply, stage)
        require(type(reply) is dict and set(reply) == {'stage', 'ok', 'reference', 'proof'}
                and reply['stage'] == stage and reply['ok'] is True
                and type(reply['proof']) is dict and not self.failures, 'research_browser_stage_refused')
        with self.lock:
            events = list(self.events)
        self.last_proof = reply['proof']
        if stage == 'connect':
            graph = self.prep.display_graph_id
            require(reply['reference'].get('display_graph_id') == graph
                    and reply['reference'].get('project_id') == self.prep.scope_dto['project_id'])
            require(any(path == '/api/graph/data/' + graph for _, path, _ in events))
            catalog = [data for _, path, data in events if path == '/api/source/library/' + graph]
            require(catalog and catalog[-1]['has_more'] is False)
            self.catalog = catalog[-1]['sources']
            expected = {source['source_revision']: source['source_sha256'] for source in self.selected.snapshots}
            order = [self.selected.revisions[0]] + [row['source_revision'] for row in self.catalog
                if row['source_revision'] in expected and row['source_revision'] != self.selected.revisions[0]]
            require(len(order) == 4 and len(set(order)) == 4)
            require(reply['reference'].get('source_revisions') == order
                    and reply['proof'].get('source_revisions') == order
                    and reply['proof'].get('source_sha256') == [expected[revision] for revision in order]
                    and reply['proof'].get('token_cleared') is True)
            pdf = reply['proof'].get('pdf', {})
            require(pdf.get('size') == len(self.selected.pdf)
                    and pdf.get('sha256') == hashlib.sha256(self.selected.pdf).hexdigest())
            self._pin_chrome()
            result = self.catalog
        elif stage == 'close':
            require(reply['proof'].get('context_closed') is True
                    and reply['proof'].get('browser_closed') is True
                    and reply['proof'].get('private_tree_removed') is True
                    and not any(self.profile_root.iterdir()))
            result = reply['proof']
        else:
            candidates = [data for _, path, data in events if path == self._route(stage)]
            require(candidates, 'research_browser_http_witness_missing')
            result = candidates[-1]
            self._reference(stage, result, reply)
            if stage in {'preparation-start', 'native-start', 'report-start'}:
                require(reply['proof'].get('dispatches') == 1, 'research_browser_dispatch_count')
            if 'source_revisions' in reply['proof']:
                require(reply['proof']['source_revisions'] == self.selected.revisions
                        and reply['proof'].get('source_sha256') ==
                        [source['source_sha256'] for source in self.selected.snapshots])
            if stage == 'preparation-plan':
                require(result['schema_version'] == request['schema_version']
                        and result['source']['source_revision'] == request['source_revision']
                        and [source['source_revision'] for source in result['sources']] == request['source_revisions']
                        and result['options'] == request['options'], 'research_browser_preparation_form_mismatch')
            if stage == 'native-start':
                plans = [data for _, path, data in events
                         if path == '/api/native-launch/plan/' + self.prep.display_graph_id]
                require(len(plans) == 1 and plans[0]['state'] == 'planned'
                        and plans[0]['launch_sha256'] == result['launch_sha256']
                        and plans[0]['request'] == result['request'],
                        'research_browser_native_review_mismatch')
                self.native_reviewed = plans[0]
                require(all(result['preparation'][key] == value for key, value in request['preparation'].items()),
                        'research_browser_native_form_mismatch')
            if stage == 'report-start':
                plans = [data for _, path, data in events
                         if path == '/api/connected-report/plan/' + self.prep.display_graph_id]
                require(len(plans) == 1 and self.report_plan_no_calls)
                require(plans[0]['report_id'] == result['report_id']
                        and plans[0]['plan_sha256'] == result['plan_sha256'])
                self.report_started = result
                result = plans[0]  # Genuine reviewed DTO; UI already dispatched its matching start.
                require(result['binding']['native']['run_id'] == request['launch_id']
                        and result['binding']['native']['launch_sha256'] == request['launch_sha256']
                        and result['options']['requirement'] == request['requirement']
                        and result['options']['output_language'] == request['output_language']
                        and result['binding']['source_set']['source_revisions'] == request['source_revisions'],
                        'research_browser_report_form_mismatch')
            if stage == 'report-read':
                self._download_proof(events, reply['proof'])
            if stage == 'pdf-read-after-restart':
                raw = base64.b64decode(result['content_base64'], validate=True)
                require(raw == self.selected.pdf and result['binary'] == self.selected.receipts[0]['binary'])
                proof = reply['proof'].get('pdf', reply['proof'])
                require(proof.get('size') == len(raw) and proof.get('sha256') == hashlib.sha256(raw).hexdigest())
                require(all(reply['proof'].get(key) is True
                            for key in ('auth_denied', 'origin_denied', 'token_cleared')))
                self.verify_restart()
        self.step += 1
        return result

    def ordered_sources(self):
        original = {source['source_revision']: index for index, source in enumerate(self.selected.snapshots)}
        anchor = self.selected.revisions[0]
        order = [anchor] + [row['source_revision'] for row in self.catalog
                            if row['source_revision'] in original and row['source_revision'] != anchor]
        require(len(order) == 4 and set(order) == set(original), 'research_browser_catalog_mismatch')
        indices = [original[revision] for revision in order]
        for key in ('records', 'snapshots', 'receipts'):
            previous = getattr(self.selected, key)
            setattr(self.selected, key, [previous[index] for index in indices])
        self.selected.revisions = order

    def _download_proof(self, events, proof):
        actual = {}
        for _, path, data in events:
            if path == '/api/connected-report/download/' + self.prep.display_graph_id:
                artifact = data['artifact']
                raw = base64.b64decode(artifact['content_base64'], validate=True)
                require(len(raw) == artifact['size'] and hashlib.sha256(raw).hexdigest() == artifact['sha256'])
                actual[artifact['name']] = dict(name=artifact['name'], size=len(raw), sha256=artifact['sha256'])
        require({'full_report.md', 'native_evidence.json'} <= set(actual), 'research_browser_download_missing')
        for name, key in (('full_report.md', 'report_download'),
                          ('native_evidence.json', 'native_evidence_download')):
            require(proof.get(key) == {field: actual[name][field] for field in ('size', 'sha256')},
                    'research_browser_download_hash_mismatch')

    def _http(self, route, *, token=None, origin=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=self._remaining(60))
        headers = {}
        if token is not None:
            headers['Authorization'] = 'Bearer ' + token
        if origin is not None:
            headers['Origin'] = origin
        try:
            connection.request('GET', route, headers=headers)
            response = connection.getresponse()
            raw = response.read(4195329)
            require(len(raw) <= 4195328)
            return response.status, dict(response.getheaders()), json.loads(raw)
        finally:
            connection.close()

    def verify_restart(self):
        from nexaweave_storage import NotFound
        graph = self.prep.display_graph_id
        for record, snapshot in zip(self.selected.records, self.selected.snapshots, strict=True):
            status, _, reply = self._http(f'/api/source/item/{graph}/{record.source_revision}', token=self.token)
            require(status == 200 and reply['data']['text'] == snapshot['text'])
            expected = [{key: p[key] for key in ('evidence_id', 'start', 'end', 'page', 'excerpt_sha256')}
                        for p in snapshot['passages']]
            require(reply['data']['passages'] == expected)
            require(self.prep.sources.get_source('owner', record.project_id, record.source_revision) == record)
        route = '/api/source/original/' + graph + '/' + self.selected.revisions[0]
        status, headers, reply = self._http(route, token=self.token)
        require(status == 200 and headers['Cache-Control'] == 'no-store'
                and headers['X-Content-Type-Options'] == 'nosniff')
        require(base64.b64decode(reply['data']['content_base64'], validate=True) == self.selected.pdf)
        require(self._http(route)[0] == 401 and self._http(route, token='foreign-token')[0] == 401)
        require(self._http(route, token=self.token, origin='http://denied.example')[0] == 403)
        require(self._http('/api/source/original/foreign_graph/' + self.selected.revisions[0],
                           token=self.token)[0] == 404)
        from uuid import uuid4
        require(self._http('/api/source/original/' + graph + '/' + str(uuid4()), token=self.token)[0] == 404)
        try:
            self.prep.sources.get_original('other', self.selected.records[0].project_id,
                                           self.selected.records[0].source_revision)
        except NotFound:
            pass
        else:
            raise RuntimeError('research_browser_foreign_original_accepted')
        for record, receipt in zip(self.selected.records, self.selected.receipts, strict=True):
            require(hashlib.sha256((self.selected.upload_root / record.name).read_bytes()).hexdigest()
                    == receipt['extraction']['input_sha256'])

    def _process(self, pid):
        require(type(pid) is int and pid > 1)
        path = Path('/proc') / str(pid) / 'stat'
        if not path.exists():
            return None
        raw = path.read_bytes()
        require(len(raw) <= 4096)
        fields = raw[raw.rfind(b')') + 2:].split()
        require(len(fields) >= 20)
        return dict(pid=pid, parent=int(fields[1]), group=int(fields[2]), start=int(fields[19]))

    def _pin_chrome(self):
        # Read only this owned Node's direct children; never accept a PID from browser input.
        pid = self.driver['process'].pid
        path = Path('/proc') / str(pid) / 'task' / str(pid) / 'children'
        raw = path.read_bytes()
        require(len(raw) <= 4096)
        children = raw.split()
        require(len(children) <= 16)
        matches = []
        for child in children:
            observed = self._process(int(child))
            if observed and observed['parent'] == pid:
                executable = (Path('/proc') / child.decode('ascii') / 'exe').resolve(strict=True)
                if executable.name in {'chrome', 'chromium', 'chromium-browser', 'google-chrome'}:
                    matches.append(observed)
        require(len(matches) == 1, 'research_browser_chrome_identity')
        self.chrome = matches[0]
        require(self.chrome and self.chrome['parent'] == self.driver['process'].pid
                and self.chrome['group'] in {self.chrome['pid'], self.driver['group']},
                'research_browser_chrome_identity')

    def _stop_group(self, group, identity, *, terminate=False):
        current = self._process(group)
        if current is None:
            end = time.monotonic() + 3
            while time.monotonic() < end:
                try:
                    os.killpg(group, 0)  # Observe only; an absent leader never authorizes a signal.
                except ProcessLookupError:
                    return
                time.sleep(.01)
            raise RuntimeError('research_browser_group_leader_unavailable')
        require(identity and current['start'] == identity['start'] and current['group'] == group,
                'research_browser_group_identity_changed')
        if terminate:
            try:
                os.killpg(group, signal.SIGTERM)
            except ProcessLookupError:
                pass
            return  # OwnedProcess.stop reaps its root before group-absence proof.
        end = time.monotonic() + 3
        while time.monotonic() < end:
            try:
                os.killpg(group, 0)
            except ProcessLookupError:
                return
            time.sleep(.01)
        raise RuntimeError('research_browser_group_cleanup_unknown')

    def _cleanup(self):
        errors = []
        if self.driver and self.chrome is None and self.driver['process'].poll() is None:
            try:
                self._pin_chrome()
            except Exception:
                # Startup may precede Chrome creation; stage error is still primary.
                pass
        if self.driver and self.step == 9:
            try:
                self.perform('close')
                require(self.driver['process'].wait(timeout=3) == 0)
            except Exception as error:
                errors.append(error)
        elif self.driver:
            try:
                if self.driver['process'].poll() is None:
                    self._send(dict(stage='close'), 15)
                    self.driver['process'].wait(timeout=15)
            except Exception as error:
                errors.append(error)
        if self.chrome:
            try:
                current = self._process(self.chrome['pid'])
                require(current is None or current['start'] == self.chrome['start'])
                identity = (self.chrome if self.chrome['group'] == self.chrome['pid']
                            else self.driver['identity'])
                self._stop_group(self.chrome['group'], identity, terminate=True)
            except Exception as error:
                errors.append(error)
        for entry in reversed(self.children):
            process = entry['process']
            try:
                if entry is getattr(self, 'vite', None) and process:
                    if process.poll() is None:
                        process.stdin.write(b'stop\n')
                    require(process.wait(timeout=3) == 0, 'research_browser_vite_stop_failed')
            except Exception as error:
                errors.append(error)
            try:
                if process:
                    self._stop_group(entry['group'], entry['identity'], terminate=True)
            except Exception as error:
                errors.append(error)
            try:
                entry['owner'].stop(entry['threads'])
                require(entry['owner'].closed and all(not t.is_alive() for t in entry['threads']))
                if process:
                    self._stop_group(entry['group'], entry['identity'])
            except Exception as error:
                errors.append(error)
        # Drain after owned reader joins so a final cleanup reply cannot race this read.
        while True:
            try:
                pending = self.replies.get_nowait()
            except queue.Empty:
                break
            if type(pending) is dict and (pending.get('ok') is False or 'error' in pending):
                try:
                    stage = ('close' if pending.get('error') == 'research_browser_cleanup_failed'
                             else self.active)
                    errors.append(failure_reply(pending, stage))
                except Exception as error:
                    errors.append(error)
        if self.chrome:
            try:
                identity = (self.chrome if self.chrome['group'] == self.chrome['pid']
                            else self.driver['identity'])
                self._stop_group(self.chrome['group'], identity)
            except Exception as error:
                errors.append(error)
        try:
            self._stop_http()
        except Exception as error:
            errors.append(error)
        errors.extend(RuntimeError(code) for code in self.failures)
        if not errors and not self.work_failed:
            for entry in reversed(self.children):
                try:
                    entry['owner'].cleanup_private_directory(entry['directory'])
                except Exception as error:
                    errors.append(error)
        self.closed = not errors
        if errors:
            raise ExceptionGroup('research_browser_cleanup_refused', errors)

    def __exit__(self, kind, error, traceback):
        self.work_failed = error is not None
        try:
            self._cleanup()
        except BaseException as cleanup:
            if error is not None:
                raise BaseExceptionGroup('research_browser_work_and_cleanup', [error, cleanup]) from None
            raise
        return False

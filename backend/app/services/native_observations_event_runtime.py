"""Trusted default-off composition and a fixed owned event child."""
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading

from .knowledge_transport import (_json_object, _private_environment, _read_response,
                                  _stop_owned, _trusted_environment, _trusted_file, _write_request)
from .native_observations_client import NativeObservationsError, encoded, sha

_WIRE_BYTES = 32768
_CHILD = 'native_observation_event_bootstrap.py'
_PG_FIELDS = ('HOST', 'PORT', 'DATABASE', 'USER', 'PASSWORD')
_NEO_FIELDS = ('URI', 'USER', 'PASSWORD')


@dataclass(frozen=True)
class EventRuntime:
    root: Path
    runtime_sha256: str
    python: str
    script: str
    script_sha256: str
    environment: dict = field(repr=False)

    @classmethod
    def capture(cls, settings):
        if os.environ.get('NEXAWEAVE_OBSERVATION_EVENTS_ENABLED') != '1':
            return None
        try:
            from nexaweave_knowledge.read_runtime import ReadSettings

            root = Path(os.environ['NEXAWEAVE_OBSERVATION_ARTIFACT_ROOT'])
            read_bootstrap = Path(settings.bootstrap)
            if (not root.is_absolute() or read_bootstrap.name != 'read_bootstrap.py'
                    or read_bootstrap.parent.name != 'nexaweave_knowledge'
                    or 'site-packages' not in read_bootstrap.parts):
                raise ValueError
            extra = {key: settings.child_environment[key] for key in (
                'KNOWLEDGE_PRINCIPAL', 'KNOWLEDGE_DISPLAY_GRAPH_ID', 'KNOWLEDGE_BOUND_SCOPE_JSON',
                *('KNOWLEDGE_PG_' + field for field in _PG_FIELDS),
            )}
            for field in _NEO_FIELDS:
                value = os.environ['NEXAWEAVE_OBSERVATION_NEO4J_' + field]
                if not value or len(value.encode('utf-8')) > 1024:
                    raise ValueError
                extra['KNOWLEDGE_NEO4J_' + field] = value
            environment = _trusted_environment(extra)
            ReadSettings.from_mapping(environment)
            return cls(root, sha(os.environ['NEXAWEAVE_OBSERVATION_RUNTIME_SHA256']),
                       _trusted_file(settings.python, script=False),
                       _trusted_file(read_bootstrap.with_name(_CHILD), script=True),
                       sha(os.environ['NEXAWEAVE_OBSERVATION_CHILD_SHA256']), environment)
        except (ImportError, KeyError, ValueError, TypeError, OSError):
            return None

    def host(self, settings):
        import psycopg
        from nexaweave_execution.native_owned_binding import _safe_ancestors
        from .durable_native_launch_host import DurableNativeLaunchHost
        from .durable_preparation_host import DurablePreparationHost
        from .knowledge_read_facade import KnowledgeReadFacade
        from .native_observations_host import NativeObservationsHost

        if (not _safe_ancestors(self.root) or not self.root.is_dir()
                or self.root.resolve(strict=True) != self.root):
            raise NativeObservationsError('observations_unavailable')
        if any(key.upper().startswith('PG') for key in os.environ):
            raise NativeObservationsError('observations_unavailable')
        self._check_script()
        host, port, database, user, password = (
            self.environment['KNOWLEDGE_PG_' + field] for field in _PG_FIELDS)

        def connect():
            return psycopg.connect(host=host, port=int(port), dbname=database, user=user,
                                   password=password, connect_timeout=3)

        preparation = DurablePreparationHost(settings=settings, connection_factory=connect,
            read_facade=KnowledgeReadFacade(settings), artifact_root=self.root)
        launch = DurableNativeLaunchHost(preparation_host=preparation,
            connection_factory=connect, runtime_sha256=self.runtime_sha256)
        return NativeObservationsHost(launch_host=launch, event_writer=self.write, event_verifier=self.verify)

    def _check_script(self):
        from nexaweave_execution.native_owned_binding import _regular_bound_file

        path = Path(self.script)
        source = _regular_bound_file(path.parent, _CHILD, 16000)
        if hashlib.sha256(source).hexdigest() != self.script_sha256:
            raise ValueError

    def write(self, value):
        return self._call(value, [])

    def verify(self, value):
        return self._call(value, ['--verify-uncertain'])

    def _call(self, value, arguments):
        from ..utils.owned_process import OwnedProcess

        raw = encoded(value)
        if not 0 < len(raw) <= _WIRE_BYTES:
            raise ValueError
        self._check_script()
        owner = OwnedProcess()
        directory = tempfile.TemporaryDirectory(prefix='nexaweave-native-event-')
        owner.bind_private_directory(directory)
        threads = []
        try:
            process = owner.start(subprocess.Popen, [self.python, '-I', '-u', self.script, *arguments],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                shell=False, close_fds=True, cwd=directory.name,
                env=_private_environment(directory.name, self.python, self.environment))
            events = queue.Queue()
            threads.extend((
                threading.Thread(target=_write_request, args=(process.stdin, len(raw).to_bytes(4, 'big') + raw, events), daemon=True),
                threading.Thread(target=_read_response, args=(process.stdout, events, _WIRE_BYTES), daemon=True),
            ))
            for thread in threads:
                thread.start()
            if process.wait(timeout=20) != 0:
                raise ValueError
            first, second = events.get(timeout=1), events.get(timeout=1)
            result = dict((first, second))
            if result.keys() != {'writer_done', 'reader_done'}:
                raise ValueError
            return _json_object(result['reader_done'])
        finally:
            try:
                _stop_owned(owner, threads)
            finally:
                owner.cleanup_private_directory(directory)

"""Opt-in, locally owned Temporal composition for protected research startup."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.metadata as metadata
import importlib.util
import os
from pathlib import Path
import re
import signal
import socket
import sys
from threading import Lock
from contextlib import AsyncExitStack
from uuid import UUID

from .knowledge_read_facade import KnowledgeReadFacade, ReadHostSettings


_PREFIX = "NEXAWEAVE_MANAGED_"
_PACKAGE_NAMES = frozenset({"nexaweave_execution", "nexaweave_knowledge", "nexaweave_storage"})
_MAX_PINNED_BYTES = 16 * 1024 * 1024
_CHILD_FILES = (
    ("read_bootstrap.py", 762, "c9813d78cffa1452f296fa8e9a9d999ed62f26a64e5348321bbd4c96ca1cf73d"),
    ("read_runtime.py", 7118, "cd7651176e2be705400022029349d70b73893a9dc489908985b124c92b991fb4"),
    ("stdio.py", 3372, "070de56d1bb0630c31ad306fd06081167ecd5db97e6503e3ffed12476bcc6993"),
    ("source_bootstrap.py", 2801, "852b7de8d1bb7def8183d8fc8ac87e852f98738ab62278f75a507502690274f9"),
)


def _sha_setting(name):
    value = os.environ[_PREFIX + name]
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("invalid managed digest")
    return value


def _bounded_file(path, available):
    if path.resolve(strict=True) != path or not path.is_file():
        raise ValueError("invalid managed file")
    with path.open("rb") as stream:
        data = stream.read(available + 1)
    if len(data) > available:
        raise ValueError("managed file budget exceeded")
    return data


def _verify_installed_overlay():
    if (sys.platform != "win32" or sys.implementation.name != "cpython"
            or sys.version_info[:3] != (3, 12, 13) or sys.prefix == sys.base_prefix):
        raise ValueError("unqualified managed interpreter")
    versions = {
        "camel-oasis": "0.2.5", "camel-ai": "0.2.78", "neo4j": "5.23.0",
        "psycopg": "3.3.6", "psycopg-binary": "3.3.6",
        "temporalio": "1.33.0", "nexaweave-knowledge": "0.1.0",
    }
    site = Path(sys.prefix).resolve(strict=True) / "Lib" / "site-packages"
    for name, expected in versions.items():
        dist = metadata.distribution(name)
        if dist.version != expected or Path(dist.locate_file("")).resolve(strict=True) != site:
            raise ValueError("unqualified managed installation")
    dist = metadata.distribution("nexaweave-knowledge")
    entries = dist.files or ()
    records = [entry for entry in entries if len(entry.parts) == 2
               and entry.parts[0] == "nexaweave_knowledge-0.1.0.dist-info"
               and entry.name == "RECORD"]
    if len(records) != 1:
        raise ValueError("missing managed overlay record")
    record = _bounded_file(site / records[0], 256 * 1024)
    if hashlib.sha256(record).hexdigest() != _sha_setting("OVERLAY_RECORD_SHA256"):
        raise ValueError("managed overlay identity mismatch")
    remaining = _MAX_PINNED_BYTES
    checked = set()
    for entry in entries:
        if entry.parts[0] not in _PACKAGE_NAMES or entry.suffix not in {".py", ".sql"}:
            continue
        path = site / entry
        if not path.is_relative_to(site / entry.parts[0]) or ".." in entry.parts:
            raise ValueError("invalid managed overlay path")
        data = _bounded_file(path, remaining)
        remaining -= len(data)
        actual = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii").rstrip("=")
        if entry.size != len(data) or entry.hash is None or entry.hash.mode != "sha256" or entry.hash.value != actual:
            raise ValueError("managed overlay file mismatch")
        checked.add(entry.as_posix())
    for module in _PACKAGE_NAMES:
        name = f"{module}/__init__.py"
        spec = importlib.util.find_spec(module)
        if spec is None or spec.origin is None or Path(spec.origin) != site / name or name not in checked:
            raise ValueError("managed overlay import origin mismatch")


def _verify_knowledge_child(settings):
    binary = Path(settings.python)
    prefix = binary.parent.parent
    if (binary.name.lower() != "python.exe" or binary.parent.name.lower() != "scripts"
            or prefix.resolve(strict=True) != prefix
            or binary.resolve(strict=True) == Path(sys.executable).resolve(strict=True)
            or prefix == Path(sys.prefix).resolve(strict=True)):
        raise ValueError("separate installed knowledge interpreter required")
    # Pin the executable invoked by the existing transport and its venv binding.
    for path, maximum, setting in (
        (binary, _MAX_PINNED_BYTES, "KNOWLEDGE_PYTHON_SHA256"),
        (prefix / "pyvenv.cfg", 16 * 1024, "KNOWLEDGE_PYVENV_SHA256"),
    ):
        if hashlib.sha256(_bounded_file(path, maximum)).hexdigest() != _sha_setting(setting):
            raise ValueError("knowledge interpreter identity mismatch")

    package = prefix / "Lib" / "site-packages" / "nexaweave_knowledge"
    if Path(settings.bootstrap) != package / "read_bootstrap.py":
        raise ValueError("knowledge bootstrap does not belong to the configured interpreter")
    if _sha_setting("KNOWLEDGE_BOOTSTRAP_SHA256") != _CHILD_FILES[0][2]:
        raise ValueError("knowledge bootstrap configuration mismatch")
    for name, size, digest in _CHILD_FILES:
        data = _bounded_file(package / name, size)
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("knowledge child file mismatch")


def _verify_local_neo4j(settings):
    uri = settings.child_environment["KNOWLEDGE_NEO4J_URI"]
    match = re.fullmatch(r"bolt://(?:127\.0\.0\.1|\[::1\])(?::([1-9][0-9]{0,4}))?", uri)
    if match is None or (match[1] is not None and int(match[1]) > 65535):
        raise ValueError("managed graph reads require a direct loopback Bolt target")


def _bounded_temporal_settings():
    address = os.environ[_PREFIX + "TEMPORAL_ADDRESS"]
    match = re.fullmatch(r"127\.0\.0\.1:([1-9][0-9]{0,4})", address)
    namespace = os.environ[_PREFIX + "TEMPORAL_NAMESPACE"]
    queue = os.environ[_PREFIX + "QUEUE_PREFIX"]
    if (match is None or int(match[1]) > 65535
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", namespace) is None
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", queue) is None):
        raise ValueError("invalid managed Temporal settings")
    return address, namespace, queue


def _operator_store(settings):
    import psycopg
    from nexaweave_execution.budget import BudgetLedger
    from nexaweave_knowledge.bindings import ScopeBindingStore
    from nexaweave_storage import ProjectStore

    if any(key.upper().startswith("PG") for key in os.environ):
        raise ValueError("managed store requires explicit connection settings")
    pg = settings.child_environment
    if pg["KNOWLEDGE_PG_HOST"] not in {"127.0.0.1", "::1"}:
        raise ValueError("managed store requires an owned loopback database")
    database, owner = pg["KNOWLEDGE_PG_DATABASE"], pg["KNOWLEDGE_PG_USER"]
    scope = settings.scope
    if scope["layer"] != "source" or scope["run_id"] is not None or scope["branch_id"] is not None:
        raise ValueError("managed startup requires the bound source scope")
    account_raw = os.environ[_PREFIX + "BUDGET_ACCOUNT_ID"]
    account = UUID(account_raw)
    if account.int == 0 or str(account) != account_raw:
        raise ValueError("invalid managed account identity")

    def connect():
        return psycopg.connect(
            host=pg["KNOWLEDGE_PG_HOST"], port=int(pg["KNOWLEDGE_PG_PORT"]),
            dbname=database, user=owner, password=pg["KNOWLEDGE_PG_PASSWORD"], connect_timeout=3,
            options="-c statement_timeout=5000 -c lock_timeout=2000 -c idle_in_transaction_session_timeout=10000",
        )

    with connect() as conn:
        authority = conn.execute(
            "SELECT current_database(), session_user, current_user, pg_get_userbyid(datdba) "
            "FROM pg_catalog.pg_database WHERE datname = current_database()"
        ).fetchone()
        if authority != (database, owner, owner, owner):
            raise ValueError("managed database ownership mismatch")
    revision = os.environ[_PREFIX + "PROJECT_REVISION"]
    if re.fullmatch(r"[1-9][0-9]{0,18}", revision) is None or int(revision) > 2**63 - 1:
        raise ValueError("invalid managed project revision")
    binding = ScopeBindingStore(connect).resolve(settings.principal, settings.display_graph_id)
    project = ProjectStore(connect).get(settings.principal, UUID(scope["project_id"]))
    if (binding.scope.model_dump(mode="json") != scope
            or str(project.workspace_id) != scope["workspace_id"]
            or str(project.project_id) != scope["project_id"]
            or project.principal != settings.principal or project.revision != int(revision)):
        raise ValueError("managed project or scope mismatch")
    balance = BudgetLedger(connect).status(settings.principal, account)
    if balance.project_id != project.project_id:
        raise ValueError("managed budget account ownership mismatch")
    return connect, account


def _native_bridge(loop, temporal):
    methods = {"start": temporal.start, "local_status": temporal.local_status, "cancel": temporal.cancel}

    def call(method, request, reference):
        operation = methods[method]
        args = (request, reference) if method == "cancel" else (request,)
        # The listener runs on its own threads. A timeout leaves the workflow
        # outcome to durable authority, with no cancellation or redispatch.
        future = asyncio.run_coroutine_threadsafe(operation(*args), loop)
        return future.result(timeout=12)

    return call


def _interrupt_http(connection):
    try:
        connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


async def _serve(app, host, port):
    import io
    from threading import Event
    from time import monotonic
    from werkzeug.serving import WSGIRequestHandler, make_server

    sockets = set()
    sockets_lock = Lock()
    closing = Event()

    class ManagedReader(io.RawIOBase):
        def __init__(self, connection, idle_timeout):
            self.connection = connection
            self.idle_timeout = idle_timeout

        def readable(self):
            return True

        def readinto(self, buffer):
            connection = self.connection
            previous_timeout = connection.gettimeout()
            deadline = monotonic() + self.idle_timeout
            try:
                while True:
                    if closing.is_set():
                        raise ConnectionAbortedError("Managed HTTP reader is closing")
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise socket.timeout("timed out")
                    connection.settimeout(min(0.1, remaining))
                    try:
                        size = connection.recv_into(buffer)
                    except socket.timeout:
                        continue
                    except OSError:
                        if closing.is_set():
                            raise ConnectionAbortedError("Managed HTTP reader is closing") from None
                        raise
                    if closing.is_set():
                        raise ConnectionAbortedError("Managed HTTP reader is closing")
                    return size
            finally:
                connection.settimeout(previous_timeout)

    class ManagedRequestHandler(WSGIRequestHandler):
        timeout = 15.0

        def setup(self):
            with sockets_lock:
                if closing.is_set():
                    _interrupt_http(self.request)
                else:
                    sockets.add(self.request)
            try:
                super().setup()
                self.rfile.close()
                self.rfile = io.BufferedReader(ManagedReader(self.connection, self.timeout))
            except BaseException:
                with sockets_lock:
                    sockets.discard(self.request)
                raise

        def finish(self):
            try:
                super().finish()
            finally:
                with sockets_lock:
                    sockets.discard(self.request)

    loop = asyncio.get_running_loop()
    stop = loop.create_future()

    def request_stop(*_):
        loop.call_soon_threadsafe(set_stop)

    def set_stop():
        if not stop.done():
            stop.set_result(None)

    previous_term = signal.signal(signal.SIGTERM, request_stop)
    try:
        server = make_server(host, port, app, threaded=True, request_handler=ManagedRequestHandler)
        # Wait for admitted request threads while the Temporal loop remains open.
        server.daemon_threads = False
        serving = asyncio.create_task(asyncio.to_thread(server.serve_forever))
        try:
            await asyncio.wait((serving, stop), return_when=asyncio.FIRST_COMPLETED)
        finally:
            # Stop admission before interrupting accepted socket I/O. A handler
            # whose setup races shutdown gets the same closed admission state.
            with sockets_lock:
                closing.set()
                accepted = tuple(sockets)
            for connection in accepted:
                _interrupt_http(connection)
            try:
                await asyncio.to_thread(server.shutdown)
                await serving
            finally:
                await asyncio.to_thread(server.server_close)
    finally:
        signal.signal(signal.SIGTERM, previous_term)


async def _run_workers(config_class, settings, host, port, connect, account, root, runtime, temporal_config):
    from temporalio.client import Client
    from nexaweave_execution.temporal_native_host import TemporalNativeHost
    from nexaweave_execution.temporal_preparation_host import TemporalPreparationHost
    from .. import create_app
    from .durable_native_launch_host import DurableNativeLaunchHost
    from .durable_preparation_host import DurablePreparationHost
    from .knowledge_preparation_facade import KnowledgePreparationFacade
    from .native_launch_facade import NativeLaunchFacade

    reader = KnowledgeReadFacade(settings)
    preparation = DurablePreparationHost(
        settings=settings, connection_factory=connect, read_facade=reader, artifact_root=root,
        account_id=account,
    )
    launch = DurableNativeLaunchHost(
        preparation_host=preparation, connection_factory=connect, runtime_sha256=runtime,
        account_id=account,
    )
    address, namespace, queue = temporal_config
    async with AsyncExitStack() as workers:
        async with asyncio.timeout(15):
            client = await Client.connect(address, namespace=namespace)
            prep_worker = TemporalPreparationHost(
                client=client, task_queue=queue + "-preparation", trusted_host=preparation,
            )
            native_worker = TemporalNativeHost(
                client=client, task_queue=queue + "-native", trusted_principal=settings.principal,
                supervisor_factory=launch.supervisor_factory, max_retained=1, max_concurrent_activities=1,
            )
            loop = asyncio.get_running_loop()
            preparation.scheduler = prep_worker.scheduler_for(loop)
            launch.attach_temporal(native_worker, _native_bridge(loop, native_worker))
            await workers.enter_async_context(prep_worker.worker())
            await workers.enter_async_context(native_worker.worker())
        app = create_app(
            config_class, read_facade=reader,
            preparation_facade=KnowledgePreparationFacade(settings, host=preparation),
            native_launch_facade=NativeLaunchFacade(settings, host=launch),
        )
        # No model factory, authorizer, or Temporal dispatch permit is installed.
        # These workers cannot create a managed native owner in this profile.
        await _serve(app, host, port)


def run_managed(*, config_class, host, port):
    if host not in {"127.0.0.1", "::1"} or type(port) is not int or not 0 <= port <= 65535 or config_class.DEBUG:
        raise ValueError("invalid managed listener settings")
    settings = ReadHostSettings.from_config(config_class)
    _verify_local_neo4j(settings)
    _verify_installed_overlay()
    _verify_knowledge_child(settings)
    temporal_config = _bounded_temporal_settings()
    runtime = _sha_setting("RUNTIME_SHA256")
    root = Path(os.environ["KNOWLEDGE_PREPARATION_ARTIFACT_ROOT"])
    if not root.is_absolute() or root.resolve(strict=True) != root or not root.is_dir() or root.parent == root:
        raise ValueError("owned preparation artifact root required")
    connect, account = _operator_store(settings)
    try:
        asyncio.run(_run_workers(config_class, settings, host, port, connect, account, root, runtime, temporal_config))
    except KeyboardInterrupt:
        pass

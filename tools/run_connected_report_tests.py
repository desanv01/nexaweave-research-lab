"""Main-owned connected report qualification with exact target accounting."""
from __future__ import annotations
import argparse
import importlib.util
import hashlib
import json
import math
import os
import stat
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_unit_tests import LoopbackOnlySockets, _unit_environment

UNIT_TARGETS = tuple(ROOT / 'backend/tests' / name for name in (
    'test_connected_report_client.py', 'test_connected_report_host.py',
    'test_connected_report_api.py', 'test_connected_report_dependencies.py',
    'test_connected_report_sources.py', 'test_connected_report_source_prompts.py'))
ENGINE_TARGET = ROOT / 'backend/engine_tests/test_connected_report_process.py'
PG_TARGET = ROOT / 'services/knowledge/tests/test_connected_native_report.py'
PG_STORE_TARGET = ROOT / 'services/knowledge/tests/test_report_store.py'
MARKERS = ('postgres: approved disposable PostgreSQL authority\n'
           'connected_report_engine: actual locked connected report\n'
           'connected_report_temporal: freshly inherited-generated real PGTemporal connected report\n'
           'native_launch_temporal: inherited actual connected Temporal native launch\n'
           'native_launch_engine: inherited actual generated-preparation owned OASIS native launch')

# Reviewed Group1 input sizes are read caps, not new application allowances.
FRESH_SOURCES = (('nexaweave_execution', 'report_contracts.py', 24865),
                 ('nexaweave_knowledge', 'source_library.py', 18952))


def _plain_source_path(value, *, directory=False):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('installed_source_path')
    for index, part in enumerate((path, *path.parents)):
        info = os.lstat(part)
        if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
                or not (stat.S_ISDIR(info.st_mode) if index or directory else stat.S_ISREG(info.st_mode))):
            raise ValueError('installed_source_path')
    if path.resolve(strict=True) != path:
        raise ValueError('installed_source_path')
    return path


def _source_stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            getattr(info, 'st_birthtime_ns', None), getattr(info, 'st_birthtime', None))


def _source_identity(info):
    if os.name == 'nt':
        # Windows path ctime is birthtime in CPython 3.12, while fstat uses ChangeTime.
        # Cross-bind birthtime; each API still checks its own full stamp for drift.
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_birthtime_ns)
    return _source_stamp(info)


def _bounded_source_digest(path, cap):
    path = _plain_source_path(path)
    before = os.lstat(path)
    if not 1 <= before.st_size <= cap:
        raise ValueError('installed_source_size')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or getattr(opened, 'st_file_attributes', 0) & 0x400
                or _source_identity(opened) != _source_identity(before)):
            raise ValueError('installed_source_changed')
        _plain_source_path(path)
        data = os.read(descriptor, before.st_size)
        if (len(data) != before.st_size or _source_stamp(os.fstat(descriptor)) != _source_stamp(opened)
                or _source_stamp(os.lstat(_plain_source_path(path))) != _source_stamp(before)):
            raise ValueError('installed_source_changed')
        return hashlib.sha256(data).digest()
    finally:
        os.close(descriptor)


def require_installed_freshness(package_specs, *, root=None, prefix=None):
    """Inspect ordinary files from top-level specs only; never import targets."""
    try:
        root = _plain_source_path(ROOT if root is None else root, directory=True)
        prefix = _plain_source_path(sys.prefix if prefix is None else prefix, directory=True)
        for package, filename, cap in FRESH_SOURCES:
            spec = package_specs[package]
            origin = _plain_source_path(spec.origin)
            package_root = origin.parent
            locations = spec.submodule_search_locations
            if (origin.name != '__init__.py' or package_root.name != package
                    or not origin.is_relative_to(prefix)
                    or 'site-packages' not in origin.relative_to(prefix).parts
                    or locations is None or len(locations) != 1
                    or _plain_source_path(locations[0], directory=True) != package_root):
                raise ValueError('installed_source_location')
            installed_source = package_root / filename
            reviewed_source = root / 'services' / 'knowledge' / 'src' / package / filename
            if _bounded_source_digest(installed_source, cap) != _bounded_source_digest(reviewed_source, cap):
                raise ValueError('installed_source_stale')
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ValueError('connected_report_installed_source_mismatch') from None


def child(mode: str, journey: bool = False, actual_graph: bool = False, store_only: bool = False) -> int:
    guard = LoopbackOnlySockets()
    guard.install()
    try:
        installed = Path(sys.prefix).resolve()
        package_specs = {}
        for package in ('nexaweave_execution', 'nexaweave_storage', 'nexaweave_knowledge'):
            spec = importlib.util.find_spec(package)
            if (spec is None or spec.origin is None or
                    not Path(spec.origin).resolve().is_relative_to(installed) or
                    'site-packages' not in Path(spec.origin).parts):
                print('connected_report_installed_package_required', file=sys.stderr)
                return 1
            package_specs[package] = spec
        try:
            require_installed_freshness(package_specs)
        except ValueError:
            print('connected_report_installed_source_mismatch', file=sys.stderr)
            return 1
        import pytest
        class Accounting:
            collected = 0
            passed = 0
            skipped = 0
            paths = {}
            targets = {path.name: 0 for path in (*UNIT_TARGETS, PG_STORE_TARGET, PG_TARGET, ENGINE_TARGET)}

            diagnostic_origin = None
            diagnostic_records = 0
            capture_manager = None

            def diagnostic(self, record, *, fixture=False):
                try:
                    if self.diagnostic_records >= 4096:
                        return
                    expected = ({'schema_version', 'suite', 'phase', 'event', 'monotonic_seconds', 'elapsed_seconds'}
                                if fixture else {'schema_version', 'suite', 'nodeid', 'event', 'when', 'outcome',
                                                 'duration_seconds', 'monotonic_seconds', 'elapsed_seconds'})
                    allowed_suites = ('report-ordinary', 'report-cooperative-heartbeat') if fixture else ('report',)
                    if set(record) != expected or type(record['schema_version']) is not int or record['schema_version'] != 1 or record['suite'] not in allowed_suites:
                        return
                    for key in ('monotonic_seconds', 'elapsed_seconds', *(() if fixture else ('duration_seconds',))):
                        if type(record[key]) not in (int, float) or not math.isfinite(record[key]) or record[key] < 0:
                            return
                    self.diagnostic_records += 1
                    prefix = 'NEXAWEAVE_FIXTURE_PHASE ' if fixture else 'NEXAWEAVE_TEST_PHASE '
                    line = prefix + json.dumps(record, allow_nan=False)
                    if self.capture_manager is not None:
                        with self.capture_manager.global_and_fixture_disabled():
                            print(line, flush=True)
                    else:
                        print(line, flush=True)
                except Exception:
                    # Diagnostic availability never changes qualification; controls
                    # still propagate into the runner's existing owned finally.
                    pass

            def fixture_phase(self, record):
                self.diagnostic(record, fixture=True)

            def node_phase(self, nodeid, event, when, outcome, duration):
                try:
                    now = time.monotonic()
                    if self.diagnostic_origin is None:
                        self.diagnostic_origin = now
                    self.diagnostic(dict(schema_version=1, suite='report', nodeid=nodeid, event=event,
                                         when=when, outcome=outcome, duration_seconds=duration,
                                         monotonic_seconds=now, elapsed_seconds=max(0.0, now - self.diagnostic_origin)))
                except Exception:
                    pass

            def pytest_runtest_logstart(self, nodeid, location):
                self.node_phase(nodeid, 'start', 'node', None, 0.0)
            def pytest_collection_finish(self, session):
                self.collected = len(session.items)
                self.paths = {item.nodeid: Path(item.path).name for item in session.items}
                for item in session.items:
                    if Path(item.path).resolve() == PG_TARGET.resolve():
                        item.module._actual_graph_journey = actual_graph
                try:
                    self.capture_manager = session.config.pluginmanager.getplugin('capturemanager')
                    for item in session.items:
                        if Path(item.path).resolve() == PG_TARGET.resolve():
                            item.module._fixture_phase_sink = self.fixture_phase
                except Exception:
                    pass
            def pytest_runtest_logreport(self, report):
                if report.skipped:
                    self.skipped += 1
                if report.when == 'call' and report.passed:
                    self.passed += 1
                    name = self.paths.get(report.nodeid)
                    if name in self.targets:
                        self.targets[name] += 1
                self.node_phase(report.nodeid, 'end', report.when, report.outcome, report.duration)
        accounting = Accounting()
        if mode == 'engine':
            targets = (ENGINE_TARGET,)
            selection = 'not postgres'
            required = (ENGINE_TARGET.name,)
        elif mode == 'integration':
            targets = ((PG_STORE_TARGET,) if store_only else
                       (PG_TARGET,) if journey else (PG_STORE_TARGET, PG_TARGET))
            selection = 'postgres'
            required = tuple(path.name for path in targets)
        else:
            targets = UNIT_TARGETS
            selection = 'not postgres'
            required = tuple(path.name for path in UNIT_TARGETS)
        if not all(path.is_file() for path in targets):
            print('connected_report_fixture_missing', file=sys.stderr)
            return 1
        selected_targets = [
            str(PG_TARGET) + '::test_actual_native_receipt_to_inherited_report_preserves_inputs_and_disabled_reads[ordinary]'
        ] if actual_graph else list(map(str, targets))
        result = int(pytest.main([
            '-q', '--durations=0', '--rootdir', str(ROOT), '-p', 'pytest_asyncio.plugin',
            '-o', 'asyncio_default_fixture_loop_scope=function', '-o', 'markers=' + MARKERS,
            '-m', selection, *selected_targets,
        ], plugins=[accounting]))
        complete = (result == 0 and accounting.collected > 0 and
                    accounting.collected == accounting.passed and accounting.skipped == 0 and
                    all(accounting.targets[name] > 0 for name in required))
        if actual_graph:
            complete = (complete and accounting.collected == accounting.passed == 1
                        and accounting.targets[PG_TARGET.name] == 1)
        if store_only:
            expected = {PG_STORE_TARGET.name: 21}
            collected_targets = {name: sum(path == name for path in accounting.paths.values())
                                 for name in set(accounting.paths.values())}
            passed_targets = {name: count for name, count in accounting.targets.items() if count}
            complete = (complete and accounting.collected == accounting.passed == sum(expected.values())
                        and accounting.skipped == 0 and collected_targets == passed_targets == expected)
        # Native runner legacy path fallback must never shadow an installed
        # execution/storage/knowledge package during this owned qualification.
        for name, module in tuple(sys.modules.items()):
            if name.split('.')[0] not in ('nexaweave_execution', 'nexaweave_storage', 'nexaweave_knowledge'):
                continue
            origin = getattr(module, '__file__', None)
            if origin is not None and (not Path(origin).resolve().is_relative_to(installed)
                                      or 'site-packages' not in Path(origin).parts):
                print('connected_report_loaded_source_package_refused', file=sys.stderr)
                complete = False
        print(json.dumps(dict(connected_report_mode=mode, actual_graph_journey=actual_graph, store_only=store_only,
            collected=accounting.collected,
            passed=accounting.passed, skipped=accounting.skipped, targets=accounting.targets,
            complete=complete), sort_keys=True))
        if not complete:
            print('connected_report_qualification_incomplete', file=sys.stderr)
            result = 1
        if guard.blocked_attempts:
            print('connected_report_non_loopback_attempt_blocked', file=sys.stderr)
            result = 1
        return result
    finally:
        guard.restore()

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    for mode in ('unit', 'integration', 'engine'):
        group.add_argument('--' + mode, action='store_true')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    subset = parser.add_mutually_exclusive_group()
    subset.add_argument('--journey', action='store_true', help='Select only the actual combined journey for concrete fixture corrections')
    subset.add_argument('--store-only', action='store_true', help='Require exact database store/budget case counts without the native journey')
    parser.add_argument('--actual-graph', action='store_true',
                        help='Require the ordinary four-format journey with real co-located Graphiti stores')
    args = parser.parse_args()
    mode = 'engine' if args.engine else 'integration' if args.integration else 'unit'
    if args.journey and mode != 'integration':
        parser.error('--journey requires --integration')
    if args.actual_graph and (mode != 'integration' or not args.journey):
        parser.error('--actual-graph requires --integration --journey')
    if args.store_only and mode != 'integration':
        parser.error('--store-only requires --integration')
    if args.store_only and args.actual_graph:
        parser.error('--store-only is incompatible with --actual-graph')
    if args.child:
        return child(mode, args.journey, args.actual_graph, args.store_only)
    password = os.environ.get('PROJECT_STORE_TEST_PASSWORD')
    if mode == 'integration' and not password:
        parser.error('explicit approved disposable PostgreSQL fixture required')
    if mode == 'integration' and os.environ.get('TEMPORAL_TEST_ADDRESS') != '127.0.0.1:17233':
        parser.error('explicit approved local Temporal fixture required')
    graph_environment = {}
    if args.actual_graph:
        knowledge_python = Path(os.environ.get('KNOWLEDGE_PYTHON', ''))
        bootstrap = Path(os.environ.get('KNOWLEDGE_BOOTSTRAP_SCRIPT', ''))
        knowledge_prefix = ROOT / 'services/knowledge/.venv'
        expected_python = knowledge_prefix / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        # Preserve the locked venv executable path; resolving its interpreter
        # symlink would select the base runtime instead of this environment.
        if (knowledge_python != expected_python or not knowledge_python.is_file()
                or not bootstrap.is_absolute() or 'site-packages' not in bootstrap.parts
                or bootstrap.name != 'read_bootstrap.py'
                or not bootstrap.is_relative_to(knowledge_prefix)
                or not bootstrap.is_file()
                or not bootstrap.with_name('source_ingestion_bootstrap.py').is_file()
                or not bootstrap.with_name('read_session_bootstrap.py').is_file()):
            parser.error('explicit installed locked knowledge runtime required')
        neo_password = os.environ.get('KNOWLEDGE_TEST_PASSWORD')
        if (not neo_password or len(neo_password) > 128
                or neo_password.lower() in {'password', 'neo4j', 'test', 'changeme'}):
            parser.error('explicit disposable Neo4j credential required')
        graph_environment = dict(KNOWLEDGE_PYTHON=str(knowledge_python),
            KNOWLEDGE_BOOTSTRAP_SCRIPT=str(bootstrap),
            NEXAWEAVE_WORKBENCH_BACKEND_PYTHON=str(Path(sys.executable).absolute()),
            KNOWLEDGE_TEST_PASSWORD=neo_password, KNOWLEDGE_INTEGRATION='1',
            GRAPHITI_TELEMETRY_ENABLED='false')
    spec = importlib.util.spec_from_file_location('_connected_report_owned', ROOT / 'backend/app/utils/owned_process.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    directory = tempfile.TemporaryDirectory(prefix='nexaweave-connected-report-')
    owner = helper.OwnedProcess()
    owner.bind_private_directory(directory)
    try:
        env = _unit_environment(Path(directory.name))
        env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        env['PYTHONPATH'] = os.pathsep.join((str(ROOT), str(ROOT / 'backend'),
            str(ROOT / 'backend/tests'), str(ROOT / 'backend/engine_tests'),
            str(ROOT / 'services/knowledge/tests')))
        env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                   DO_NOT_TRACK='1', NEXAWEAVE_NATIVE_TEST_OFFLINE='1', NEXAWEAVE_REPORT_TEST_OFFLINE='1')
        env.update(graph_environment)
        if mode == 'integration':
            from psycopg.conninfo import make_conninfo
            env.update(PROJECT_STORE_POSTGRES_INTEGRATION='1', KNOWLEDGE_POSTGRES_INTEGRATION='1')
            dsn = make_conninfo(host='127.0.0.1', port=15432, dbname='mirofish_operations_test',
                               user='mirofish_fixture', password=password, connect_timeout=5)
            env['PROJECT_STORE_POSTGRES_TEST_DSN'] = dsn
            env['KNOWLEDGE_POSTGRES_TEST_DSN'] = dsn
            env.update(TEMPORAL_EXECUTION_INTEGRATION='1', TEMPORAL_TEST_ADDRESS='127.0.0.1:17233')
        process = owner.start(subprocess.Popen,
            [sys.executable, str(Path(__file__).resolve()), '--child', '--' + mode,
             *(['--journey'] if args.journey else []), *(['--actual-graph'] if args.actual_graph else []),
             *(['--store-only'] if args.store_only else [])],
            cwd=directory.name, env=env, stdin=subprocess.DEVNULL, shell=False, close_fds=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            return process.wait(timeout=180 if mode == 'unit' else 300 if mode == 'engine' else 600)
        except subprocess.TimeoutExpired:
            print('connected_report_qualification_timeout', file=sys.stderr)
            return 1
    finally:
        owner.stop([])
        assert owner.closed and (os.name != 'nt' or owner.tree_empty)
        owner.cleanup_private_directory(directory)
        print('connected_report_owned_tree_private_cwd_closed', flush=True)

if __name__ == '__main__':
    raise SystemExit(main())

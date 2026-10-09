"""Main-owned connected followup qualification with exact target accounting."""
from __future__ import annotations
import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_unit_tests import LoopbackOnlySockets, _unit_environment

UNIT_TARGETS = tuple(ROOT / 'backend/tests' / name for name in (
    'test_connected_followup_client.py', 'test_connected_followup_host.py',
    'test_connected_followup_api.py', 'test_connected_followup_dependencies.py',
    'test_connected_report_models.py'))
ENGINE_TARGET = ROOT / 'backend/engine_tests/test_connected_followup_process.py'
PG_TARGET = ROOT / 'services/knowledge/tests/test_connected_followup_integration.py'
PG_STORE_TARGET = ROOT / 'services/knowledge/tests/test_followup_store.py'
PG_BUDGET_TARGET = ROOT / 'services/knowledge/tests/test_followup_budget.py'
MARKERS = ('postgres: approved disposable PostgreSQL authority\n'
           'connected_followup_engine: actual locked connected followup\n'
           'connected_followup_temporal: freshly inherited-generated real PGTemporal connected followup\n'
           'native_launch_temporal: inherited actual connected Temporal native launch\n'
           'native_launch_engine: inherited actual generated-preparation owned OASIS native launch')

def child(mode: str, journey: bool = False) -> int:
    guard = LoopbackOnlySockets()
    guard.install()
    try:
        installed = Path(sys.prefix).resolve()
        for package in ('nexaweave_execution', 'nexaweave_storage', 'nexaweave_knowledge'):
            spec = importlib.util.find_spec(package)
            if (spec is None or spec.origin is None or
                    not Path(spec.origin).resolve().is_relative_to(installed) or
                    'site-packages' not in Path(spec.origin).parts):
                print('connected_followup_installed_package_required', file=sys.stderr)
                return 1
        import pytest
        class Accounting:
            collected = 0
            passed = 0
            skipped = 0
            paths = {}
            targets = {path.name: 0 for path in (*UNIT_TARGETS, PG_STORE_TARGET, PG_BUDGET_TARGET, PG_TARGET, ENGINE_TARGET)}

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
                    allowed_suites = ('followup',) if fixture else ('followup',)
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
                    self.diagnostic(dict(schema_version=1, suite='followup', nodeid=nodeid, event=event,
                                         when=when, outcome=outcome, duration_seconds=duration,
                                         monotonic_seconds=now, elapsed_seconds=max(0.0, now - self.diagnostic_origin)))
                except Exception:
                    pass

            def pytest_runtest_logstart(self, nodeid, location):
                self.node_phase(nodeid, 'start', 'node', None, 0.0)
            def pytest_collection_finish(self, session):
                self.collected = len(session.items)
                self.paths = {item.nodeid: Path(item.path).name for item in session.items}
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
            targets = (PG_TARGET,) if journey else (PG_STORE_TARGET, PG_BUDGET_TARGET, PG_TARGET)
            selection = 'postgres'
            required = (PG_TARGET.name,) if journey else (PG_STORE_TARGET.name, PG_BUDGET_TARGET.name, PG_TARGET.name)
        else:
            targets = UNIT_TARGETS
            selection = 'not postgres'
            required = tuple(path.name for path in UNIT_TARGETS)
        if not all(path.is_file() for path in targets):
            print('connected_followup_fixture_missing', file=sys.stderr)
            return 1
        result = int(pytest.main([
            '-q', '--durations=0', '--rootdir', str(ROOT), '-p', 'pytest_asyncio.plugin',
            '-o', 'asyncio_default_fixture_loop_scope=function', '-o', 'markers=' + MARKERS,
            '-m', selection, *map(str, targets),
        ], plugins=[accounting]))
        complete = (result == 0 and accounting.collected > 0 and
                    accounting.collected == accounting.passed and accounting.skipped == 0 and
                    all(accounting.targets[name] > 0 for name in required))
        # Native runner legacy path fallback must never shadow an installed
        # execution/storage/knowledge package during this owned qualification.
        for name, module in tuple(sys.modules.items()):
            if name.split('.')[0] not in ('nexaweave_execution', 'nexaweave_storage', 'nexaweave_knowledge'):
                continue
            origin = getattr(module, '__file__', None)
            if origin is not None and (not Path(origin).resolve().is_relative_to(installed)
                                      or 'site-packages' not in Path(origin).parts):
                print('connected_followup_loaded_source_package_refused', file=sys.stderr)
                complete = False
        print(json.dumps(dict(connected_followup_mode=mode, collected=accounting.collected,
            passed=accounting.passed, skipped=accounting.skipped, targets=accounting.targets,
            complete=complete), sort_keys=True))
        if not complete:
            print('connected_followup_qualification_incomplete', file=sys.stderr)
            result = 1
        if guard.blocked_attempts:
            print('connected_followup_non_loopback_attempt_blocked', file=sys.stderr)
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
    parser.add_argument('--journey', action='store_true', help='Select only the actual combined journey for concrete fixture corrections')
    args = parser.parse_args()
    mode = 'engine' if args.engine else 'integration' if args.integration else 'unit'
    if args.journey and mode != 'integration':
        parser.error('--journey requires --integration')
    if args.child:
        return child(mode, args.journey)
    password = os.environ.get('PROJECT_STORE_TEST_PASSWORD')
    if mode == 'integration' and not password:
        parser.error('explicit approved disposable PostgreSQL fixture required')
    if mode == 'integration' and os.environ.get('TEMPORAL_TEST_ADDRESS') != '127.0.0.1:17233':
        parser.error('explicit approved local Temporal fixture required')
    spec = importlib.util.spec_from_file_location('_connected_followup_owned', ROOT / 'backend/app/utils/owned_process.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    directory = tempfile.TemporaryDirectory(prefix='nexaweave-connected-followup-')
    owner = helper.OwnedProcess()
    owner.bind_private_directory(directory)
    try:
        env = _unit_environment(Path(directory.name))
        env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        env['PYTHONPATH'] = os.pathsep.join((str(ROOT), str(ROOT / 'backend'),
            str(ROOT / 'backend/tests'), str(ROOT / 'backend/engine_tests'),
            str(ROOT / 'services/knowledge/tests')))
        env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                   DO_NOT_TRACK='1', MIROFISH_NATIVE_TEST_OFFLINE='1', MIROFISH_REPORT_TEST_OFFLINE='1')
        if mode == 'integration':
            from psycopg.conninfo import make_conninfo
            env.update(PROJECT_STORE_POSTGRES_INTEGRATION='1', KNOWLEDGE_POSTGRES_INTEGRATION='1')
            dsn = make_conninfo(host='127.0.0.1', port=15432, dbname='mirofish_operations_test',
                               user='mirofish_fixture', password=password, connect_timeout=5)
            env['PROJECT_STORE_POSTGRES_TEST_DSN'] = dsn
            env['KNOWLEDGE_POSTGRES_TEST_DSN'] = dsn
            env.update(TEMPORAL_EXECUTION_INTEGRATION='1', TEMPORAL_TEST_ADDRESS='127.0.0.1:17233')
        process = owner.start(subprocess.Popen,
            [sys.executable, str(Path(__file__).resolve()), '--child', '--' + mode, *(['--journey'] if args.journey else [])],
            cwd=directory.name, env=env, stdin=subprocess.DEVNULL, shell=False, close_fds=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            return process.wait(timeout=180 if mode == 'unit' else 300 if mode == 'engine' else 600)
        except subprocess.TimeoutExpired:
            print('connected_followup_qualification_timeout', file=sys.stderr)
            return 1
    finally:
        owner.stop([])
        assert owner.closed and (os.name != 'nt' or owner.tree_empty)
        owner.cleanup_private_directory(directory)
        print('connected_followup_owned_tree_private_cwd_closed', flush=True)

if __name__ == '__main__':
    raise SystemExit(main())

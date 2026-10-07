"""Main-owned installed-package connected native-launch qualification, offline only."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_unit_tests import LoopbackOnlySockets, _unit_environment

NEW_TARGETS = (
    ROOT / 'backend/tests/test_native_launch_api.py',
    ROOT / 'backend/tests/test_durable_native_launch.py',
    ROOT / 'services/knowledge/tests/test_native_launch_store.py',
    ROOT / 'services/knowledge/tests/test_native_launch_budget.py',
    ROOT / 'services/knowledge/tests/test_temporal_connected_launch.py',
)
ENGINE_TARGET = ROOT / 'backend/engine_tests/test_connected_preparation_native_launch.py'
UNIT_REGRESSIONS = (ROOT / 'services/knowledge/tests/test_budget.py',)
PG_REGRESSIONS = (
    ROOT / 'services/knowledge/tests/test_budget_postgres.py',
    ROOT / 'services/knowledge/tests/test_preparation_store.py',
)
MARKERS = ('postgres: disposable PostgreSQL integration\n'
           'native_launch_temporal: actual connected Temporal native launch\n'
           'native_launch_engine: actual generated-preparation owned OASIS native launch\n'
           'preparation_temporal: actual Temporal preparation integration')


def child(mode: str) -> int:
    guard = LoopbackOnlySockets()
    guard.install()
    try:
        installed = Path(sys.prefix).resolve()
        for package in ('nexaweave_execution', 'nexaweave_storage', 'nexaweave_knowledge'):
            spec = importlib.util.find_spec(package)
            if (spec is None or spec.origin is None
                    or not Path(spec.origin).resolve().is_relative_to(installed)
                    or 'site-packages' not in Path(spec.origin).parts):
                print('native_launch_installed_package_required', file=sys.stderr)
                return 1
        import pytest

        class Qualification:
            collected = 0
            passed = 0
            skipped = 0
            node_paths = {}
            targets = {p.name: 0 for p in (*NEW_TARGETS, ENGINE_TARGET)}

            def pytest_collection_finish(self, session):
                self.collected = len(session.items)
                self.node_paths = {item.nodeid: Path(item.path).name for item in session.items}

            def pytest_runtest_logreport(self, report):
                if report.skipped:
                    self.skipped += 1
                if report.when == 'call' and report.passed:
                    self.passed += 1
                    name = self.node_paths.get(report.nodeid)
                    if name in self.targets:
                        self.targets[name] += 1

        proof = Qualification()
        if mode == 'fixture':
            targets = (NEW_TARGETS[1], NEW_TARGETS[3])
            selection = 'spawn_gate_module_import_is_lightweight or gate_failure_diagnostics_do_not_refresh_authority_or_leak_exception_text'
            required = ('test_durable_native_launch.py', 'test_native_launch_budget.py')
        elif mode == 'temporal':
            targets = (NEW_TARGETS[-1],)
            selection = 'native_launch_temporal'
            required = ('test_temporal_connected_launch.py',)
        elif mode == 'engine':
            targets = (ENGINE_TARGET,)
            selection = 'native_launch_engine'
            required = (ENGINE_TARGET.name,)
        elif mode == 'integration':
            targets = NEW_TARGETS + PG_REGRESSIONS
            selection = '(postgres or native_launch_temporal) and not native_launch_engine'
            required = ('test_native_launch_store.py', 'test_native_launch_budget.py',
                        'test_temporal_connected_launch.py')
        else:
            targets = NEW_TARGETS + UNIT_REGRESSIONS
            selection = 'not postgres and not native_launch_temporal and not native_launch_engine'
            required = ('test_native_launch_api.py', 'test_durable_native_launch.py',
                        'test_native_launch_budget.py')
        if not all(path.is_file() for path in targets):
            print('native_launch_fixture_missing', file=sys.stderr)
            return 1
        result = int(pytest.main([
            '-q', '--rootdir', str(ROOT), '-p', 'pytest_asyncio.plugin',
            '-o', 'asyncio_default_fixture_loop_scope=function', '-o', 'markers=' + MARKERS,
            '-k' if mode == 'fixture' else '-m', selection, *map(str, targets),
        ], plugins=[proof]))
        complete = proof.collected > 0 and proof.skipped == 0 and all(proof.targets[n] > 0 for n in required)
        print(json.dumps(dict(native_launch_mode=mode, collected=proof.collected, passed=proof.passed,
                              skipped=proof.skipped, targets=proof.targets, complete=complete), sort_keys=True))
        if not complete:
            print('native_launch_qualification_incomplete', file=sys.stderr)
            result = 1
        if guard.blocked_attempts:
            print('native_launch_non_loopback_attempt_blocked', file=sys.stderr)
            result = 1
        return result
    finally:
        guard.restore()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    for mode in ('unit', 'fixture', 'integration', 'temporal', 'engine'):
        group.add_argument('--' + mode, action='store_true')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    mode = 'engine' if args.engine else 'temporal' if args.temporal else 'integration' if args.integration else 'fixture' if args.fixture else 'unit'
    if args.child:
        return child(mode)
    if mode not in ('unit', 'fixture'):
        password = os.environ.get('PROJECT_STORE_TEST_PASSWORD')
        if not password or os.environ.get('TEMPORAL_TEST_ADDRESS') != '127.0.0.1:17233':
            parser.error('explicit approved disposable PostgreSQL/Temporal fixture required')
    spec = importlib.util.spec_from_file_location('_native_launch_owned', ROOT / 'backend/app/utils/owned_process.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    directory = tempfile.TemporaryDirectory(prefix='nexaweave-native-launch-')
    owner = helper.OwnedProcess()
    owner.bind_private_directory(directory)
    try:
        env = _unit_environment(Path(directory.name))
        env['PYTHONPATH'] = os.pathsep.join((str(ROOT), str(ROOT / 'backend'),
            str(ROOT / 'backend/tests'), str(ROOT / 'backend/engine_tests'),
            str(ROOT / 'services/knowledge/tests')))
        env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                   DO_NOT_TRACK='1', NEXAWEAVE_NATIVE_TEST_OFFLINE='1')
        if mode not in ('unit', 'fixture'):
            from psycopg.conninfo import make_conninfo
            env.update(PROJECT_STORE_POSTGRES_INTEGRATION='1', KNOWLEDGE_POSTGRES_INTEGRATION='1',
                       TEMPORAL_EXECUTION_INTEGRATION='1', TEMPORAL_TEST_ADDRESS='127.0.0.1:17233')
            dsn = make_conninfo(host='127.0.0.1', port=15432, dbname='mirofish_operations_test',
                               user='mirofish_fixture', password=password, connect_timeout=5)
            env['PROJECT_STORE_POSTGRES_TEST_DSN'] = dsn
            env['KNOWLEDGE_POSTGRES_TEST_DSN'] = dsn
        process = owner.start(subprocess.Popen,
            [sys.executable, str(Path(__file__).resolve()), '--child', '--' + mode],
            cwd=directory.name, env=env, stdin=subprocess.DEVNULL, shell=False, close_fds=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            return process.wait(timeout=180 if mode in ('unit', 'fixture') else 600)
        except subprocess.TimeoutExpired:
            print('native_launch_qualification_timeout', file=sys.stderr)
            return 1
    finally:
        owner.stop([])
        assert owner.closed and (os.name != 'nt' or owner.tree_empty)
        owner.cleanup_private_directory(directory)
        print('native_launch_owned_tree_private_cwd_closed', flush=True)


if __name__ == '__main__':
    raise SystemExit(main())

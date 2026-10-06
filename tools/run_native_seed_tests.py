"""Main-owned native seed fidelity qualification with exact target accounting."""
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

UNIT_TARGETS = tuple(ROOT / 'backend/tests' / name for name in (
    'test_native_seed_posts.py', 'test_native_seed_binding.py'))
ENGINE_TARGET = ROOT / 'backend/engine_tests/test_native_seed_fidelity.py'
PG_TARGET = ROOT / 'services/knowledge/tests/test_connected_native_seed_fidelity.py'
MARKERS = ('postgres: approved disposable PostgreSQL authority\n'
           'native_seed_engine: actual locked native seed fidelity\n'
           'native_seed_temporal: freshly inherited-generated real PGTemporal native seed fidelity\n'
           'native_launch_temporal: inherited actual connected Temporal native launch\n'
           'native_launch_engine: inherited actual generated-preparation owned OASIS native launch')

def child(mode: str) -> int:
    guard = LoopbackOnlySockets()
    guard.install()
    try:
        installed = Path(sys.prefix).resolve()
        for package in ('mirofish_execution', 'mirofish_storage', 'mirofish_knowledge'):
            spec = importlib.util.find_spec(package)
            if (spec is None or spec.origin is None or
                    not Path(spec.origin).resolve().is_relative_to(installed) or
                    'site-packages' not in Path(spec.origin).parts):
                print('native_seed_installed_package_required', file=sys.stderr)
                return 1
        import pytest
        class Accounting:
            collected = 0
            passed = 0
            skipped = 0
            paths = {}
            targets = {path.name: 0 for path in (*UNIT_TARGETS, PG_TARGET, ENGINE_TARGET)}
            def pytest_collection_finish(self, session):
                self.collected = len(session.items)
                self.paths = {item.nodeid: Path(item.path).name for item in session.items}
            def pytest_runtest_logreport(self, report):
                if report.skipped:
                    self.skipped += 1
                if report.when == 'call' and report.passed:
                    self.passed += 1
                    name = self.paths.get(report.nodeid)
                    if name in self.targets:
                        self.targets[name] += 1
        accounting = Accounting()
        if mode == 'engine':
            targets = (ENGINE_TARGET,)
            selection = 'not postgres'
            required = (ENGINE_TARGET.name,)
        elif mode == 'integration':
            targets = (PG_TARGET,)
            selection = 'postgres and native_seed_temporal'
            required = (PG_TARGET.name,)
        else:
            targets = UNIT_TARGETS
            selection = 'not postgres'
            required = tuple(path.name for path in UNIT_TARGETS)
        if not all(path.is_file() for path in targets):
            print('native_seed_fixture_missing', file=sys.stderr)
            return 1
        result = int(pytest.main([
            '-q', '--rootdir', str(ROOT), '-p', 'pytest_asyncio.plugin',
            '-o', 'asyncio_default_fixture_loop_scope=function', '-o', 'markers=' + MARKERS,
            '-m', selection, *map(str, targets),
        ], plugins=[accounting]))
        complete = (result == 0 and accounting.collected > 0 and
                    accounting.collected == accounting.passed and accounting.skipped == 0 and
                    all(accounting.targets[name] > 0 for name in required))
        # Native runner legacy path fallback must never shadow an installed
        # execution/storage/knowledge package during this owned qualification.
        for name, module in tuple(sys.modules.items()):
            if name.split('.')[0] not in ('mirofish_execution', 'mirofish_storage', 'mirofish_knowledge'):
                continue
            origin = getattr(module, '__file__', None)
            if origin is not None and (not Path(origin).resolve().is_relative_to(installed)
                                      or 'site-packages' not in Path(origin).parts):
                print('native_seed_loaded_source_package_refused', file=sys.stderr)
                complete = False
        print(json.dumps(dict(native_seed_mode=mode, collected=accounting.collected,
            passed=accounting.passed, skipped=accounting.skipped, targets=accounting.targets,
            complete=complete), sort_keys=True))
        if not complete:
            print('native_seed_qualification_incomplete', file=sys.stderr)
            result = 1
        if guard.blocked_attempts:
            print('native_seed_non_loopback_attempt_blocked', file=sys.stderr)
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
    args = parser.parse_args()
    mode = 'engine' if args.engine else 'integration' if args.integration else 'unit'
    if args.child:
        return child(mode)
    password = os.environ.get('PROJECT_STORE_TEST_PASSWORD')
    if mode == 'integration' and not password:
        parser.error('explicit approved disposable PostgreSQL fixture required')
    if mode == 'integration' and os.environ.get('TEMPORAL_TEST_ADDRESS') != '127.0.0.1:17233':
        parser.error('explicit approved local Temporal fixture required')
    spec = importlib.util.spec_from_file_location('_native_seed_owned', ROOT / 'backend/app/utils/owned_process.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    directory = tempfile.TemporaryDirectory(prefix='nexaweave-native-seed-')
    owner = helper.OwnedProcess()
    owner.bind_private_directory(directory)
    try:
        env = _unit_environment(Path(directory.name))
        env['PYTHONPATH'] = os.pathsep.join((str(ROOT), str(ROOT / 'backend'),
            str(ROOT / 'backend/tests'), str(ROOT / 'backend/engine_tests'),
            str(ROOT / 'services/knowledge/tests')))
        env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                   DO_NOT_TRACK='1', MIROFISH_NATIVE_TEST_OFFLINE='1')
        if mode == 'integration':
            from psycopg.conninfo import make_conninfo
            env.update(PROJECT_STORE_POSTGRES_INTEGRATION='1', KNOWLEDGE_POSTGRES_INTEGRATION='1')
            dsn = make_conninfo(host='127.0.0.1', port=15432, dbname='mirofish_operations_test',
                               user='mirofish_fixture', password=password, connect_timeout=5)
            env['PROJECT_STORE_POSTGRES_TEST_DSN'] = dsn
            env['KNOWLEDGE_POSTGRES_TEST_DSN'] = dsn
            env.update(TEMPORAL_EXECUTION_INTEGRATION='1', TEMPORAL_TEST_ADDRESS='127.0.0.1:17233')
        process = owner.start(subprocess.Popen,
            [sys.executable, str(Path(__file__).resolve()), '--child', '--' + mode],
            cwd=directory.name, env=env, stdin=subprocess.DEVNULL, shell=False, close_fds=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            return process.wait(timeout=180 if mode == 'unit' else 300 if mode == 'engine' else 600)
        except subprocess.TimeoutExpired:
            print('native_seed_qualification_timeout', file=sys.stderr)
            return 1
    finally:
        owner.stop([])
        assert owner.closed and (os.name != 'nt' or owner.tree_empty)
        owner.cleanup_private_directory(directory)
        print('native_seed_owned_tree_private_cwd_closed', flush=True)

if __name__ == '__main__':
    raise SystemExit(main())

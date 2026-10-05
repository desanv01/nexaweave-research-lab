"""Main-owned bounded durable preparation qualification, with offline models only."""
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
    ROOT / 'backend/tests/test_durable_preparation.py',
    ROOT / 'backend/tests/test_preparation_api.py',
    ROOT / 'services/knowledge/tests/test_preparation_store.py',
    ROOT / 'services/knowledge/tests/test_temporal_preparation.py',
)
UNIT_REGRESSIONS = (
    ROOT / 'backend/tests/test_provider_neutral_preparation.py',
    ROOT / 'services/knowledge/tests/test_budget.py',
)
PG_REGRESSIONS = (ROOT / 'services/knowledge/tests/test_budget_postgres.py',)


def child(integration: bool) -> int:
    guard = LoopbackOnlySockets()
    guard.install()
    try:
        # Qualification must consume the noneditable installed package. Backend
        # app source remains explicit; no credential/env-selected import path.
        installed = Path(sys.prefix).resolve()
        for package in ('mirofish_execution', 'mirofish_storage', 'mirofish_knowledge'):
            spec = importlib.util.find_spec(package)
            if (spec is None or spec.origin is None
                    or not Path(spec.origin).resolve().is_relative_to(installed)
                    or 'site-packages' not in Path(spec.origin).parts):
                print('preparation_installed_package_required', file=sys.stderr)
                return 1
        import pytest

        class Qualification:
            passed = 0
            skipped = 0
            new_passed = 0
            collected = 0
            targets = {path.name: 0 for path in NEW_TARGETS}
            node_paths = {}

            def pytest_collection_finish(self, session):
                self.collected = len(session.items)
                self.node_paths = {item.nodeid: Path(item.path).name for item in session.items}

            def pytest_runtest_logreport(self, report):
                if report.skipped:
                    self.skipped += 1
                if report.when == 'call' and report.passed:
                    self.passed += 1
                    for name in self.targets:
                        if self.node_paths.get(report.nodeid) == name:
                            self.targets[name] += 1
                            self.new_passed += 1

        proof = Qualification()
        targets = NEW_TARGETS + (PG_REGRESSIONS if integration else UNIT_REGRESSIONS)
        if not all(path.is_file() for path in targets):
            print('preparation_fixture_missing', file=sys.stderr)
            return 1
        selection = 'postgres or preparation_temporal' if integration else 'not postgres and not preparation_temporal'
        result = int(pytest.main([
            '-q', '--rootdir', str(ROOT), '-p', 'pytest_asyncio.plugin',
            '-o', 'asyncio_default_fixture_loop_scope=function',
            '-o', 'markers=postgres: disposable PostgreSQL integration\npreparation_temporal: disposable Temporal preparation integration',
            '-m', selection, *map(str, targets),
        ], plugins=[proof]))
        expected = ('test_preparation_store.py', 'test_temporal_preparation.py') if integration else (
            'test_durable_preparation.py', 'test_preparation_api.py', 'test_temporal_preparation.py')
        complete = (proof.collected > 0 and proof.new_passed > 0 and proof.skipped == 0
                    and all(proof.targets[name] > 0 for name in expected))
        print(json.dumps(dict(preparation_mode='integration' if integration else 'unit',
                              collected=proof.collected, passed=proof.passed,
                              skipped=proof.skipped, new_passed=proof.new_passed,
                              targets=proof.targets, complete=complete), sort_keys=True))
        if not complete:
            print('preparation_qualification_incomplete', file=sys.stderr)
            result = 1
    finally:
        guard.restore()
    if guard.blocked_attempts:
        print('non_loopback_attempt', file=sys.stderr)
        return 1
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--unit', action='store_true')
    mode.add_argument('--integration', action='store_true')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        return child(args.integration)
    if args.integration:
        password = os.environ.get('PROJECT_STORE_TEST_PASSWORD')
        if not password or os.environ.get('TEMPORAL_TEST_ADDRESS') != '127.0.0.1:17233':
            parser.error('explicit approved disposable PostgreSQL/Temporal fixture required')
    spec = importlib.util.spec_from_file_location('_preparation_owned_process', ROOT / 'backend/app/utils/owned_process.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    directory = tempfile.TemporaryDirectory(prefix='mirofish-preparation-')
    owner = helper.OwnedProcess()
    owner.bind_private_directory(directory)
    try:
        env = _unit_environment(Path(directory.name))
        env['PYTHONPATH'] = os.pathsep.join((str(ROOT), str(ROOT / 'backend'), str(ROOT / 'backend/tests'), str(ROOT / 'services/knowledge/tests')))
        env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                   DO_NOT_TRACK='1', MIROFISH_NATIVE_TEST_OFFLINE='1')
        if args.integration:
            from psycopg.conninfo import make_conninfo
            env.update(PROJECT_STORE_POSTGRES_INTEGRATION='1', KNOWLEDGE_POSTGRES_INTEGRATION='1',
                       TEMPORAL_EXECUTION_INTEGRATION='1', TEMPORAL_TEST_ADDRESS='127.0.0.1:17233')
            dsn = make_conninfo(host='127.0.0.1', port=15432, dbname='mirofish_operations_test',
                               user='mirofish_fixture', password=password, connect_timeout=5)
            env['PROJECT_STORE_POSTGRES_TEST_DSN'] = dsn
            env['KNOWLEDGE_POSTGRES_TEST_DSN'] = dsn
        process = owner.start(subprocess.Popen,
            [sys.executable, str(Path(__file__).resolve()), '--child', '--integration' if args.integration else '--unit'],
            cwd=directory.name, env=env, stdin=subprocess.DEVNULL, shell=False, close_fds=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            return process.wait(timeout=600 if args.integration else 180)
        except subprocess.TimeoutExpired:
            print('preparation_qualification_timeout', file=sys.stderr)
            return 1
    finally:
        owner.stop([])
        assert owner.closed and (os.name != 'nt' or owner.tree_empty)
        owner.cleanup_private_directory(directory)
        print('preparation_owned_tree_private_cwd_closed', flush=True)


if __name__ == '__main__':
    raise SystemExit(main())

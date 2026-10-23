"""Owned installed-package transaction-setting qualification, without models."""
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


def child(postgres):
    guard = LoopbackOnlySockets()
    guard.install()
    try:
        prefix = Path(sys.prefix).resolve()
        spec = importlib.util.find_spec('nexaweave_storage.transaction_settings')
        if (spec is None or spec.origin is None or 'site-packages' not in Path(spec.origin).parts
                or not Path(spec.origin).resolve().is_relative_to(prefix)):
            print('transaction_settings_installed_package_required', file=sys.stderr)
            return 1
        import pytest
        target = ROOT / 'services/knowledge/tests' / ('test_transaction_settings_postgres.py' if postgres else 'test_transaction_settings.py')
        class Accounting:
            collected = passed = skipped = 0
            def pytest_collection_finish(self, session): self.collected = len(session.items)
            def pytest_runtest_logreport(self, report):
                self.skipped += int(report.skipped)
                self.passed += int(report.when == 'call' and report.passed)
        accounting = Accounting()
        result = int(pytest.main(['-q', '--rootdir', str(ROOT), '-p', 'pytest_asyncio.plugin',
            '-o', 'asyncio_default_fixture_loop_scope=function',
            '-o', 'markers=postgres: approved owned PostgreSQL fixture', str(target)], plugins=[accounting]))
        complete = (result == 0 and accounting.collected > 0 and accounting.collected == accounting.passed
                    and accounting.skipped == 0 and not guard.blocked_attempts)
        module = sys.modules.get('nexaweave_storage.transaction_settings')
        origin = getattr(module, '__file__', None)
        if origin is None or not Path(origin).resolve().is_relative_to(prefix) or 'site-packages' not in Path(origin).parts:
            complete = False
        print(json.dumps(dict(transaction_settings_mode='postgres' if postgres else 'unit',
            collected=accounting.collected, passed=accounting.passed, skipped=accounting.skipped,
            complete=complete), sort_keys=True), flush=True)
        return result if complete else 1
    finally:
        guard.restore()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--unit', action='store_true')
    group.add_argument('--postgres', action='store_true')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child: return child(args.postgres)
    password = os.environ.get('PROJECT_STORE_TEST_PASSWORD')
    if args.postgres and not password:
        parser.error('explicit owned PostgreSQL fixture required')
    spec = importlib.util.spec_from_file_location('_transaction_settings_owner', ROOT / 'backend/app/utils/owned_process.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    directory = tempfile.TemporaryDirectory(prefix='nexaweave-transaction-settings-')
    owner = helper.OwnedProcess()
    owner.bind_private_directory(directory)
    try:
        environment = _unit_environment(Path(directory.name))
        environment.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        environment['PYTHONPATH'] = os.pathsep.join((str(ROOT), str(ROOT / 'services/knowledge/tests')))
        if args.postgres:
            from psycopg.conninfo import make_conninfo
            dsn = make_conninfo(host='127.0.0.1', port=15432, dbname='mirofish_operations_test',
                               user='mirofish_fixture', password=password, connect_timeout=5)
            environment.update(PROJECT_STORE_POSTGRES_INTEGRATION='1', KNOWLEDGE_POSTGRES_INTEGRATION='1',
                               PROJECT_STORE_POSTGRES_TEST_DSN=dsn, KNOWLEDGE_POSTGRES_TEST_DSN=dsn)
        process = owner.start(subprocess.Popen,
            [sys.executable, str(Path(__file__).resolve()), '--child', '--postgres' if args.postgres else '--unit'],
            cwd=directory.name, env=environment, stdin=subprocess.DEVNULL, shell=False, close_fds=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return process.wait(timeout=120 if args.postgres else 60)
    finally:
        try:
            owner.stop([])
            assert owner.closed and (os.name != 'nt' or owner.tree_empty)
        finally:
            owner.cleanup_private_directory(directory)
        print('transaction_settings_owned_tree_private_cwd_closed', flush=True)


if __name__ == '__main__':
    raise SystemExit(main())

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
HOSTED_COUNTS = {
    'test_preparation_store.py': 10,
    'test_budget_postgres.py': 10,
    'test_temporal_preparation.py': 1,
}


def child(integration: bool, *, hosted: bool = False) -> int:
    guard = LoopbackOnlySockets()
    guard.install()
    try:
        # Qualification must consume the noneditable installed package. Backend
        # app source remains explicit; no credential/env-selected import path.
        installed = Path(sys.prefix).resolve()
        for package in ('nexaweave_execution', 'nexaweave_storage', 'nexaweave_knowledge'):
            spec = importlib.util.find_spec(package)
            if (spec is None or spec.origin is None
                    or not Path(spec.origin).resolve().is_relative_to(installed)
                    or 'site-packages' not in Path(spec.origin).parts):
                print('preparation_installed_package_required', file=sys.stderr)
                return 1
        import pytest

        new_targets = tuple(path.name for path in NEW_TARGETS)

        class Qualification:
            passed = 0
            skipped = 0
            new_passed = 0
            collected = 0
            targets = dict.fromkeys(new_targets, 0)
            node_paths = {}
            if hosted:
                targets[PG_REGRESSIONS[0].name] = 0
            passed_nodes = set()

            def pytest_collection_finish(self, session):
                self.collected = len(session.items)
                self.node_paths = {item.nodeid: Path(item.path).name for item in session.items}

            def pytest_runtest_logreport(self, report):
                if report.skipped:
                    self.skipped += 1
                if report.when == 'call' and report.passed:
                    self.passed += 1
                    self.passed_nodes.add(report.nodeid)
                    name = self.node_paths.get(report.nodeid)
                    if name in self.targets:
                        self.targets[name] += 1
                    if name in new_targets:
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
        mode = 'integration' if integration else 'unit'
        if hosted:
            mode = 'hosted-integration'
            complete = (complete and result == 0
                        and proof.collected == proof.passed == sum(HOSTED_COUNTS.values())
                        and proof.passed_nodes == proof.node_paths.keys()
                        and len(proof.passed_nodes) == proof.collected
                        and all(proof.targets[name] == count for name, count in HOSTED_COUNTS.items()))
        print(json.dumps(dict(preparation_mode=mode,
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
    group = parser.add_mutually_exclusive_group(required=True)
    for mode in ('unit', 'integration'):
        group.add_argument('--' + mode, dest='mode', action='store_const', const=mode)
    group.add_argument('--hosted-integration', dest='mode', action='store_const',
                       const='hosted-integration', help=argparse.SUPPRESS)
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    integration = args.mode != 'unit'
    if args.child:
        return child(integration, hosted=args.mode == 'hosted-integration')
    if integration:
        password = os.environ.get('PROJECT_STORE_TEST_PASSWORD')
        if not password or os.environ.get('TEMPORAL_TEST_ADDRESS') != '127.0.0.1:17233':
            parser.error('explicit approved disposable PostgreSQL/Temporal fixture required')
    spec = importlib.util.spec_from_file_location('_preparation_owned_process', ROOT / 'backend/app/utils/owned_process.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    directory = tempfile.TemporaryDirectory(prefix='nexaweave-preparation-')
    owner = helper.OwnedProcess()
    owner.bind_private_directory(directory)
    try:
        env = _unit_environment(Path(directory.name))
        env['PYTHONPATH'] = os.pathsep.join((str(ROOT), str(ROOT / 'backend'), str(ROOT / 'backend/tests'), str(ROOT / 'services/knowledge/tests')))
        env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                   DO_NOT_TRACK='1', NEXAWEAVE_NATIVE_TEST_OFFLINE='1')
        if integration:
            from psycopg.conninfo import make_conninfo
            env.update(PROJECT_STORE_POSTGRES_INTEGRATION='1', KNOWLEDGE_POSTGRES_INTEGRATION='1',
                       TEMPORAL_EXECUTION_INTEGRATION='1', TEMPORAL_TEST_ADDRESS='127.0.0.1:17233')
            dsn = make_conninfo(host='127.0.0.1', port=15432, dbname='mirofish_operations_test',
                               user='mirofish_fixture', password=password, connect_timeout=5)
            env['PROJECT_STORE_POSTGRES_TEST_DSN'] = dsn
            env['KNOWLEDGE_POSTGRES_TEST_DSN'] = dsn
        process = owner.start(subprocess.Popen,
            [sys.executable, str(Path(__file__).resolve()), '--child', '--' + args.mode],
            cwd=directory.name, env=env, stdin=subprocess.DEVNULL, shell=False, close_fds=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            return process.wait(timeout=600 if integration else 180)
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

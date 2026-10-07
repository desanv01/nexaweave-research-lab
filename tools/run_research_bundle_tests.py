"""Main-owned portable retained-source artifact qualification."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_unit_tests import LoopbackOnlySockets, _unit_environment


def child(integration):
    import pytest
    targets = ['test_research_bundle.py']
    if integration:
        targets.append('test_research_bundle_postgres.py')
    guard = LoopbackOnlySockets()
    guard.install()
    class Results:
        passed = {name: 0 for name in targets}
        unexpected_skips = 0
        def pytest_runtest_logreport(self, report):
            if report.skipped and not (
                os.name == 'nt' and 'test_link_input_and_ancestor_denied' in report.nodeid
                and 'symlink creation unavailable' in str(report.longrepr)
            ):
                self.unexpected_skips += 1
            if report.when == 'call' and report.passed:
                for name in targets:
                    if name + '::' in report.nodeid:
                        self.passed[name] += 1
    results = Results()
    try:
        status = int(pytest.main(['-q', '-p', 'pytest_asyncio.plugin', '-o', 'markers=postgres: guarded PostgreSQL integration',
            *(str(ROOT / 'services/knowledge/tests' / name) for name in targets)], plugins=[results]))
    finally:
        guard.restore()
    if guard.blocked_attempts or results.unexpected_skips or not all(results.passed.values()):
        print('research_bundle_qualification_incomplete', file=sys.stderr)
        return 1
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--integration', action='store_true')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        return child(args.integration)
    with tempfile.TemporaryDirectory(prefix='nexaweave-research-bundle-') as directory:
        env = _unit_environment(Path(directory))
        env['PYTHONPATH'] = os.pathsep.join([str(ROOT), str(ROOT / 'services/knowledge/src'),
                                           str(ROOT / 'services/knowledge/tests')])
        env['GRAPHITI_TELEMETRY_ENABLED'] = 'false'
        if args.integration:
            from psycopg.conninfo import make_conninfo
            password = os.environ.get('PROJECT_STORE_TEST_PASSWORD')
            if not password:
                parser.error('approved disposable fixture password required')
            installed = subprocess.run([sys.executable, '-I', '-c',
                "import nexaweave_storage.research_bundle_cli as m; from pathlib import Path; "
                "assert 'site-packages' in Path(m.__file__).resolve().parts"],
                env=env, cwd=directory, capture_output=True, timeout=30, check=False)
            if installed.returncode or installed.stderr:
                parser.error('non-editable installed candidate storage package required')
            env['PROJECT_STORE_POSTGRES_INTEGRATION'] = '1'
            env['PROJECT_STORE_POSTGRES_TEST_DSN'] = make_conninfo(host='127.0.0.1', port=15432,
                dbname='mirofish_operations_test', user='mirofish_fixture', password=password, connect_timeout=5)
        command = [sys.executable, str(Path(__file__).resolve()), '--child']
        if args.integration:
            command.append('--integration')
        try:
            return subprocess.run(command, env=env, cwd=directory, timeout=300, check=False).returncode
        except subprocess.TimeoutExpired:
            print('research_bundle_qualification_timeout', file=sys.stderr)
            return 1


if __name__ == '__main__':
    raise SystemExit(main())

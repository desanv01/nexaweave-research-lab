"""Main-owned actual PostgreSQL and fresh DOCX source CLI qualification."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_unit_tests import LoopbackOnlySockets, _unit_environment


def child():
    import pytest
    guard = LoopbackOnlySockets()
    guard.install()
    class Results:
        passed = 0
        unexpected_skips = 0
        def pytest_runtest_logreport(self, report):
            if report.when == 'call' and report.passed:
                self.passed += 1
            if report.skipped and not (
                sys.platform == 'win32'
                and 'test_actual_owned_linked_ancestor_refused' in report.nodeid
                and 'host does not permit directory symlink creation' in str(report.longrepr)
            ):
                self.unexpected_skips += 1
    results = Results()
    try:
        status = int(pytest.main(['-q', '-p', 'pytest_asyncio.plugin',
                                 'tests/test_document_source_integration.py'], plugins=[results]))
    finally:
        guard.restore()
    if guard.blocked_attempts or results.unexpected_skips or not results.passed:
        print('document_source_qualification_incomplete', file=sys.stderr)
        return 1
    return status


def main():
    if sys.argv[1:] == ['--child']:
        return child()
    if sys.argv[1:]:
        raise SystemExit('no arguments accepted')
    from psycopg.conninfo import make_conninfo
    password = os.environ.get('PROJECT_STORE_TEST_PASSWORD')
    if not password:
        raise SystemExit('PROJECT_STORE_TEST_PASSWORD required')
    with tempfile.TemporaryDirectory(prefix='mirofish-document-source-') as directory:
        env = _unit_environment(Path(directory))
        env['PROJECT_STORE_POSTGRES_INTEGRATION'] = '1'
        env['PROJECT_STORE_POSTGRES_TEST_DSN'] = make_conninfo(
            host='127.0.0.1', port=15432, dbname='mirofish_operations_test',
            user='mirofish_fixture', password=password, connect_timeout=5)
        try:
            return subprocess.run([sys.executable, str(Path(__file__).resolve()), '--child'],
                                  env=env, cwd=ROOT / 'services/knowledge',
                                  timeout=600, check=False).returncode
        except subprocess.TimeoutExpired:
            print('document_source_qualification_timeout', file=sys.stderr)
            return 1


if __name__ == '__main__':
    raise SystemExit(main())

"""Main-owned knowledge-local policy and real SDK protocol qualification."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_unit_tests import LoopbackOnlySockets, _unit_environment


def child(neo4j, integration_only=False):
    import pytest
    targets = ['test_local_provider_policy.py', 'test_local_provider_http.py']
    if neo4j:
        targets.append('test_local_provider_integration.py')
    if integration_only:
        targets = ['test_local_provider_integration.py']
    guard = LoopbackOnlySockets()
    guard.install()
    class Results:
        passed = {name: 0 for name in targets}
        skipped = 0
        def pytest_runtest_logreport(self, report):
            self.skipped += int(report.skipped)
            if report.when == 'call' and report.passed:
                for name in targets:
                    if name + '::' in report.nodeid:
                        self.passed[name] += 1
    results = Results()
    try:
        if neo4j:
            from neo4j import GraphDatabase
            with GraphDatabase.driver('bolt://127.0.0.1:17687',
                                      auth=('neo4j', os.environ['KNOWLEDGE_TEST_PASSWORD'])) as driver:
                rows, _, _ = driver.execute_query(
                    "SHOW SETTINGS YIELD name, value WHERE name = 'dbms.usage_report.enabled' RETURN value",
                    routing_='r')
                if len(rows) != 1 or rows[0]['value'] != 'false':
                    raise RuntimeError('neo4j_usage_reporting_not_disabled')
            print('neo4j_usage_reporting_disabled', flush=True)
        status = int(pytest.main(['-q', '-p', 'pytest_asyncio.plugin',
                                 *(str(ROOT / 'services/knowledge/tests' / name) for name in targets)], plugins=[results]))
    finally:
        guard.restore()
    if guard.blocked_attempts or results.skipped or not all(results.passed.values()):
        print('local_provider_qualification_incomplete', file=sys.stderr)
        return 1
    return status


def main():
    args = sys.argv[1:]
    if args in (['--child'], ['--child', '--neo4j'], ['--child', '--neo4j', '--integration-only']):
        return child('--neo4j' in args, '--integration-only' in args)
    if args not in ([], ['--neo4j'], ['--neo4j', '--integration-only']):
        raise SystemExit('only --neo4j [--integration-only] accepted')
    with tempfile.TemporaryDirectory(prefix='mirofish-local-provider-') as directory:
        env = _unit_environment(Path(directory))
        env['PYTHONPATH'] += os.pathsep + str(ROOT / 'services/knowledge/src')
        env['GRAPHITI_TELEMETRY_ENABLED'] = 'false'
        if '--neo4j' in args:
            password = os.environ.get('KNOWLEDGE_TEST_PASSWORD')
            if not password:
                raise SystemExit('KNOWLEDGE_TEST_PASSWORD required')
            env['KNOWLEDGE_TEST_PASSWORD'] = password
            env['KNOWLEDGE_INTEGRATION'] = '1'
            env['KNOWLEDGE_TEST_NEO4J_URI'] = 'bolt://127.0.0.1:17687'
        try:
            return subprocess.run([sys.executable, str(Path(__file__).resolve()), '--child', *args],
                                  env=env, cwd=ROOT / 'services/knowledge',
                                  timeout=300, check=False).returncode
        except subprocess.TimeoutExpired:
            print('local_provider_qualification_timeout', file=sys.stderr)
            return 1


if __name__ == '__main__':
    raise SystemExit(main())

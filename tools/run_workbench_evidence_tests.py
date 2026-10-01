"""Main-owned evidence dispatcher/graph-pipe and optional real HTTP qualification."""
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
    guard = LoopbackOnlySockets()
    guard.install()
    targets = ['test_evidence_dispatcher.py', 'test_stdio_transport.py']
    if integration:
        targets.append('test_workbench_http_integration.py')
    class Results:
        passed = {name: 0 for name in targets}
        unexpected_skips = 0
        def pytest_runtest_logreport(self, report):
            if report.skipped and not (
                os.name == 'nt' and 'test_posix_interpreter_link_retains_original_path' in report.nodeid
                and 'POSIX virtualenv interpreter links' in str(report.longrepr)
            ):
                self.unexpected_skips += 1
            if report.when == 'call' and report.passed:
                for name in targets:
                    if name + '::' in report.nodeid:
                        self.passed[name] += 1
    results = Results()
    try:
        status = int(pytest.main(['-q', '-p', 'pytest_asyncio.plugin', '-o', 'asyncio_mode=auto',
            '-o', 'markers=neo4j: guarded Neo4j integration\npostgres: guarded PostgreSQL integration',
            *(str(ROOT / 'services/knowledge/tests' / name) for name in targets)], plugins=[results]))
    finally:
        guard.restore()
    if guard.blocked_attempts or results.unexpected_skips or not all(results.passed.values()):
        print('workbench_evidence_qualification_incomplete', file=sys.stderr)
        return 1
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--integration', action='store_true')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        return child(args.integration)
    with tempfile.TemporaryDirectory(prefix='mirofish-workbench-evidence-') as directory:
        env = _unit_environment(Path(directory))
        env['PYTHONPATH'] = os.pathsep.join([str(ROOT), str(ROOT / 'services/knowledge/src'),
                                           str(ROOT / 'services/knowledge/tests')])
        env['GRAPHITI_TELEMETRY_ENABLED'] = 'false'
        if args.integration:
            from psycopg.conninfo import make_conninfo
            pg = os.environ.get('PROJECT_STORE_TEST_PASSWORD')
            neo = os.environ.get('KNOWLEDGE_TEST_PASSWORD')
            backend = os.environ.get('MIROFISH_WORKBENCH_BACKEND_PYTHON')
            if not pg or not neo or not backend or not Path(backend).is_absolute() or not Path(backend).is_file():
                parser.error('approved fixture passwords and locked backend interpreter required')
            # -I verifies the actual installed package, ignoring source PYTHONPATH.
            installed = subprocess.run([sys.executable, '-I', '-c',
                "import mirofish_knowledge; from pathlib import Path; p=Path(mirofish_knowledge.__file__).resolve(); "
                "assert 'site-packages' in p.parts; assert p.with_name('evidence_bootstrap.py').is_file(); "
                "print(p.with_name('read_bootstrap.py'))"], env=env, cwd=directory,
                capture_output=True, timeout=30, check=False)
            if installed.returncode or installed.stderr:
                parser.error('non-editable installed knowledge package required')
            env.update(PROJECT_STORE_POSTGRES_INTEGRATION='1', KNOWLEDGE_INTEGRATION='1',
                       KNOWLEDGE_TEST_PASSWORD=neo, MIROFISH_WORKBENCH_BACKEND_PYTHON=backend,
                       KNOWLEDGE_PYTHON=str(Path(sys.executable).absolute()),
                       KNOWLEDGE_BOOTSTRAP_SCRIPT=installed.stdout.decode().strip())
            env['PROJECT_STORE_POSTGRES_TEST_DSN'] = make_conninfo(host='127.0.0.1', port=15432,
                dbname='mirofish_operations_test', user='mirofish_fixture', password=pg, connect_timeout=5)
        command = [sys.executable, str(Path(__file__).resolve()), '--child']
        if args.integration:
            command.append('--integration')
        try:
            return subprocess.run(command, env=env, cwd=directory, timeout=600, check=False).returncode
        except subprocess.TimeoutExpired:
            print('workbench_evidence_qualification_timeout', file=sys.stderr)
            return 1


if __name__ == '__main__':
    raise SystemExit(main())

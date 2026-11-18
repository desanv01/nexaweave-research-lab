"""Guarded evidence and workbench HTTP tests."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_unit_tests import LoopbackOnlySockets, _unit_environment

_HTTP_FILE = 'test_workbench_http_integration.py'
_RETAINED_CASE = 'test_actual_http_installed_pipe_retained_evidence_history_and_denial'
_DISPATCH_CASE = 'test_actual_protected_restored_event_dispatch_replay_and_lost_ack'
_PROBE = """
import hashlib, importlib.util, json, sys
from pathlib import Path

profile = sys.argv[1]
prefix = Path(sys.prefix).resolve()
assert prefix != Path(sys.base_prefix).resolve()
if profile == 'pruned':
    assert all(importlib.util.find_spec(name) is None for name in ('psycopg', 'pydantic'))
    print(json.dumps([str(prefix), None]))
    sys.exit(0)
for name in ('nexaweave_execution', 'nexaweave_knowledge'):
    package = Path(importlib.util.find_spec(name).origin).resolve().parent
    assert 'site-packages' in package.parts and package.is_relative_to(prefix)
if profile in ('authority', 'fixture'):
    assert sys.implementation.name == 'cpython' and sys.version_info[:3] == (3, 12, 13)
    import psycopg, pydantic
    assert all(Path(module.__file__).resolve().is_relative_to(prefix) for module in (psycopg, pydantic))
child = package / 'native_observation_event_bootstrap.py'
assert not child.is_symlink() and 0 < child.stat().st_size <= 16000
assert hashlib.sha256(child.read_bytes()).hexdigest() == sys.argv[2]
assert all((package / name).is_file() for name in ('read_bootstrap.py', 'evidence_bootstrap.py'))
print(json.dumps([str(prefix), str(package / 'read_bootstrap.py')]))
"""


def probe(executable, profile, expected, env, directory):
    result = subprocess.run([executable, '-I', '-c', _PROBE, profile, expected],
        env=env, cwd=directory, capture_output=True, timeout=30, check=False)
    if result.returncode or result.stderr or not 0 < len(result.stdout) <= 8192:
        raise ValueError
    return json.loads(result.stdout)


def child(integration, event_dispatch):
    import pytest
    guard = LoopbackOnlySockets()
    guard.install()
    targets = [] if event_dispatch else ['test_evidence_dispatcher.py', 'test_stdio_transport.py']
    http_case = None
    if event_dispatch:
        http_case = _DISPATCH_CASE
    elif integration:
        http_case = _RETAINED_CASE
    if http_case:
        targets.append(_HTTP_FILE)
    test_root = ROOT / 'services/knowledge/tests'
    nodes = [f'{test_root / name}::{http_case}' if name == _HTTP_FILE else str(test_root / name)
             for name in targets]
    class Results:
        passed = {name: 0 for name in targets}
        http_passed = set()
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
                        if name == _HTTP_FILE:
                            self.http_passed.add(report.nodeid.rsplit('::', 1)[-1])
    results = Results()
    try:
        status = int(pytest.main(['-q', '-p', 'pytest_asyncio.plugin', '-o', 'asyncio_mode=auto',
            '-o', 'markers=neo4j: guarded Neo4j integration\npostgres: guarded PostgreSQL integration',
            *nodes], plugins=[results]))
    finally:
        guard.restore()
    if (guard.blocked_attempts or results.unexpected_skips or not all(results.passed.values())
            or http_case and (results.http_passed != {http_case} or results.passed[_HTTP_FILE] != 1)):
        print('workbench_evidence_qualification_incomplete', file=sys.stderr)
        return 1
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--integration', action='store_true')
    mode.add_argument('--event-dispatch', action='store_true',
                      help='Select only the restored dispatch fixture in the locked combined backend')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        return child(args.integration, args.event_dispatch)
    with tempfile.TemporaryDirectory(prefix='nexaweave-workbench-evidence-') as directory:
        env = _unit_environment(Path(directory))
        paths = [ROOT, ROOT / 'services/knowledge/tests']
        if not args.event_dispatch:
            paths.insert(1, ROOT / 'services/knowledge/src')
        env['PYTHONPATH'] = os.pathsep.join(map(str, paths))
        env['GRAPHITI_TELEMETRY_ENABLED'] = 'false'
        if args.integration or args.event_dispatch:
            from psycopg.conninfo import make_conninfo
            pg = os.environ.get('PROJECT_STORE_TEST_PASSWORD')
            neo = os.environ.get('KNOWLEDGE_TEST_PASSWORD')
            authority_variable = ('NEXAWEAVE_WORKBENCH_AUTHORITY_PYTHON' if args.event_dispatch
                                  else 'NEXAWEAVE_WORKBENCH_BACKEND_PYTHON')
            backend = os.environ.get(authority_variable)
            fixture = str(Path(sys.executable).absolute())
            knowledge = os.environ.get('KNOWLEDGE_PYTHON') if args.event_dispatch else fixture
            if not pg or not neo or not backend or not Path(backend).is_absolute() or not Path(backend).is_file():
                parser.error('fixture passwords and backend required')
            if not knowledge or not Path(knowledge).is_absolute() or not Path(knowledge).is_file():
                parser.error('explicit installed knowledge interpreter required')
            try:
                candidate = ROOT / 'services/knowledge/src/nexaweave_knowledge/native_observation_event_bootstrap.py'
                expected = sha256(candidate.read_bytes()).hexdigest()
                profile = 'authority' if args.event_dispatch else 'pruned'
                backend_prefix, _ = probe(backend, profile, expected, env, directory)
                knowledge_prefix, bootstrap = probe(knowledge, 'knowledge', expected, env, directory)
                if backend_prefix == knowledge_prefix:
                    raise ValueError
                if args.event_dispatch:
                    fixture_prefix, _ = probe(fixture, 'fixture', expected, env, directory)
                    if (Path(fixture_prefix) != (ROOT / 'backend/.venv').resolve()
                            or Path(knowledge_prefix) != (ROOT / 'services/knowledge/.venv').resolve()
                            or len({fixture_prefix, backend_prefix, knowledge_prefix}) != 3):
                        raise ValueError
            except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                parser.error('workbench profile or event child mismatch')
            env.update(PROJECT_STORE_POSTGRES_INTEGRATION='1', KNOWLEDGE_INTEGRATION='1',
                       KNOWLEDGE_TEST_PASSWORD=neo, KNOWLEDGE_PYTHON=knowledge,
                       KNOWLEDGE_BOOTSTRAP_SCRIPT=bootstrap)
            env[authority_variable] = backend
            env['PROJECT_STORE_POSTGRES_TEST_DSN'] = make_conninfo(host='127.0.0.1', port=15432,
                dbname='mirofish_operations_test', user='mirofish_fixture', password=pg, connect_timeout=5)
        command = [sys.executable, __file__, '--child']
        if args.event_dispatch:
            command.append('--event-dispatch')
        elif args.integration:
            command.append('--integration')
        try:
            return subprocess.run(command, env=env, cwd=directory, timeout=600, check=False).returncode
        except subprocess.TimeoutExpired:
            print('workbench_evidence_qualification_timeout', file=sys.stderr)
            return 1


if __name__ == '__main__':
    raise SystemExit(main())

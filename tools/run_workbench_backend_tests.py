"""Main-owned offline API and inherited cold/read/population regressions."""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_unit_tests import LoopbackOnlySockets, _unit_environment

TARGETS = ('test_knowledge_evidence_client.py', 'test_knowledge_evidence_app.py',
           'test_knowledge_reader.py', 'test_knowledge_read_app.py',
           'test_knowledge_population.py', 'test_knowledge_cold_start.py',
           'test_source_library_api.py')


def child():
    import pytest
    guard = LoopbackOnlySockets()
    guard.install()
    class Results:
        passed = {name: 0 for name in TARGETS}
        skipped = 0
        def pytest_runtest_logreport(self, report):
            self.skipped += int(report.skipped)
            if report.when == 'call' and report.passed:
                for name in TARGETS:
                    if name + '::' in report.nodeid:
                        self.passed[name] += 1
    results = Results()
    try:
        status = int(pytest.main(['-q', *(str(ROOT / 'backend/tests' / n) for n in TARGETS)], plugins=[results]))
    finally:
        guard.restore()
    if guard.blocked_attempts or results.skipped or not all(results.passed.values()):
        print('workbench_backend_qualification_incomplete', file=sys.stderr)
        return 1
    return status


def main():
    if sys.argv[1:] == ['--child']:
        return child()
    if sys.argv[1:]:
        raise SystemExit('no arguments accepted')
    with tempfile.TemporaryDirectory(prefix='mirofish-workbench-backend-') as directory:
        try:
            return subprocess.run([sys.executable, str(Path(__file__).resolve()), '--child'],
                                  env=_unit_environment(Path(directory)), cwd=directory,
                                  timeout=240, check=False).returncode
        except subprocess.TimeoutExpired:
            print('workbench_backend_qualification_timeout', file=sys.stderr)
            return 1


if __name__ == '__main__':
    raise SystemExit(main())

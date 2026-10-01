"""Main-owned offline cohort/recording contracts; Linux qualifies symlinks."""
from pathlib import Path
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
    targets = ('test_native_recordings_contract.py', 'test_native_experiments_contract.py')
    class Results:
        passed = {name: 0 for name in targets}
        unexpected_skips = 0
        def pytest_runtest_logreport(self, report):
            if report.skipped and not (
                sys.platform == 'win32'
                and 'test_symlink_source_and_bundle_rejected' in report.nodeid
                and 'OS symlink privilege unavailable' in str(report.longrepr)
            ):
                self.unexpected_skips += 1
            if report.when == 'call' and report.passed:
                for name in targets:
                    if name in report.nodeid:
                        self.passed[name] += 1
    results = Results()
    try:
        status = int(pytest.main(['-q', *(str(ROOT / 'backend/tests' / n) for n in targets)], plugins=[results]))
    finally:
        guard.restore()
    if guard.blocked_attempts or results.unexpected_skips or not all(results.passed.values()):
        print('native_experiment_contract_qualification_incomplete', file=sys.stderr)
        return 1
    return status


def main():
    if sys.argv[1:] == ['--child']:
        return child()
    if sys.argv[1:]:
        raise SystemExit('no arguments accepted')
    with tempfile.TemporaryDirectory(prefix='mirofish-experiment-contract-') as directory:
        try:
            return subprocess.run([sys.executable, str(Path(__file__).resolve()), '--child'],
                                  env=_unit_environment(Path(directory)), cwd=directory,
                                  timeout=240, check=False).returncode
        except subprocess.TimeoutExpired:
            print('native_experiment_contract_qualification_timeout', file=sys.stderr)
            return 1


if __name__ == '__main__':
    raise SystemExit(main())

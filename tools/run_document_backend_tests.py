"""Main-owned DOCX, saved parser and upload regressions with offline guards."""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_unit_tests import LoopbackOnlySockets, _unit_environment

TARGETS = ('test_docx_extraction.py', 'test_file_parser_bounds.py',
           'test_parser_process.py', 'test_upload_admission.py')


def child():
    import pytest
    guard = LoopbackOnlySockets()
    guard.install()
    class Results:
        passed = {name: 0 for name in TARGETS}
        unexpected_skips = 0
        def pytest_runtest_logreport(self, report):
            known_windows = sys.platform == 'win32' and (
                ('test_file_parser_bounds.py::test_directory_and_link_rejected' in report.nodeid
                 and 'Windows symlinks unavailable' in str(report.longrepr)) or
                ('test_file_parser_bounds.py::test_fifo_rejected_without_opening' in report.nodeid
                 and ('Windows FIFO unavailable' in str(report.longrepr)
                      or 'Windows FIFO creation unavailable' in str(report.longrepr))))
            if report.skipped and not known_windows:
                self.unexpected_skips += 1
            if report.when == 'call' and report.passed:
                for name in TARGETS:
                    if name + '::' in report.nodeid:
                        self.passed[name] += 1
    results = Results()
    try:
        status = int(pytest.main(['-q', *(str(ROOT / 'backend/tests' / n) for n in TARGETS)], plugins=[results]))
    finally:
        guard.restore()
    if guard.blocked_attempts or results.unexpected_skips or not all(results.passed.values()):
        print('document_backend_qualification_incomplete', file=sys.stderr)
        return 1
    return status


def main():
    if sys.argv[1:] == ['--child']:
        return child()
    if sys.argv[1:]:
        raise SystemExit('no arguments accepted')
    with tempfile.TemporaryDirectory(prefix='nexaweave-document-backend-') as directory:
        try:
            return subprocess.run([sys.executable, str(Path(__file__).resolve()), '--child'],
                                  env=_unit_environment(Path(directory)), cwd=directory,
                                  timeout=300, check=False).returncode
        except subprocess.TimeoutExpired:
            print('document_backend_qualification_timeout', file=sys.stderr)
            return 1


if __name__ == '__main__':
    raise SystemExit(main())

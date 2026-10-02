"""Main optional PDF profile qualification; installed package, owned child, no network."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.run_unit_tests import _unit_environment, LoopbackOnlySockets

TARGETS = ('test_pdf_source.py', 'test_source_library.py')
if sys.argv[1:] == ['--child']:
    import pytest
    guard = LoopbackOnlySockets()
    guard.install()
    class Results:
        passed = {name: 0 for name in TARGETS}
        skips = []
        def pytest_runtest_logreport(self, report):
            if report.skipped:
                self.skips.append(report.nodeid)
            if report.when == 'call' and report.passed:
                for name in TARGETS:
                    if name + '::' in report.nodeid:
                        self.passed[name] += 1
    results = Results()
    try:
        status = int(pytest.main(['-q', '-p', 'pytest_asyncio.plugin', '--tb=short', *(str(REPO / 'services/knowledge/tests' / name) for name in TARGETS)], plugins=[results]))
    finally:
        guard.restore()
    raise SystemExit(1 if guard.blocked_attempts or results.skips or not all(results.passed.values()) else status)

spec = importlib.util.spec_from_file_location('main_pdf_owner', REPO / 'backend/app/utils/owned_process.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
directory = tempfile.TemporaryDirectory(prefix='mirofish-pdf-profile-unit-')
owner = helper.OwnedProcess()
owner.bind_private_directory(directory)
threads, errors = [], []
def forward(pipe):
    try:
        while True:
            chunk = pipe.read1(65536)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
    except (OSError, ValueError):
        errors.append(True)
try:
    child = owner.start(subprocess.Popen, [sys.executable, str(Path(__file__).resolve()), '--child'], cwd=directory.name,
        env=_unit_environment(Path(directory.name)), shell=False, close_fds=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    reader = threading.Thread(target=forward, args=(child.stdout,), daemon=True)
    threads.append(reader)
    reader.start()
    status = child.wait(timeout=150)
finally:
    try:
        owner.stop(threads)
        assert owner.closed and owner.tree_empty and not errors
    finally:
        owner.cleanup_private_directory(directory)
raise SystemExit(status)

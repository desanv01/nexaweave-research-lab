"""Main-owned actual native recording/fresh-process qualification, offline."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_unit_tests import LoopbackOnlySockets, _unit_environment


def child():
    guard = LoopbackOnlySockets()
    guard.install()
    try:
        import pytest
        class Qualification:
            passed = 0
            skipped = 0
            def pytest_runtest_logreport(self, report):
                self.passed += int(report.when == 'call' and report.passed)
                self.skipped += int(report.skipped)
        result = Qualification()
        status = int(pytest.main(['-q', '-p', 'pytest_asyncio.plugin',
            str(ROOT / 'backend/engine_tests/test_native_recordings.py')], plugins=[result]))
    finally:
        guard.restore()
    if guard.blocked_attempts or result.skipped or result.passed == 0:
        print('native_recording_qualification_incomplete', file=sys.stderr)
        return 1
    return status


def main():
    if sys.argv[1:] == ['--child']:
        return child()
    with tempfile.TemporaryDirectory(prefix='nexaweave-native-recording-') as directory:
        env = _unit_environment(Path(directory))
        env['PYTHONPATH'] += os.pathsep + str(ROOT / 'services/knowledge/src')
        env.update(NEXAWEAVE_NATIVE_TEST_OFFLINE='1', HF_HUB_OFFLINE='1',
                   TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1')
        try:
            return subprocess.run([sys.executable, str(Path(__file__).resolve()), '--child'],
                                  cwd=directory, env=env, timeout=300, check=False).returncode
        except subprocess.TimeoutExpired:
            print('native_recording_qualification_timeout', file=sys.stderr)
            return 1


if __name__ == '__main__':
    raise SystemExit(main())

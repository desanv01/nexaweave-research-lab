"""Main-owned full native engine with disposable PostgreSQL qualification."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_unit_tests import LoopbackOnlySockets, _unit_environment


def child() -> int:
    guard = LoopbackOnlySockets()
    guard.install()
    try:
        import pytest

        class Qualification:
            passed = 0
            skipped = 0
            targets = {'test_native_experiment_http.py': 0,
                       'test_native_experiment_socket.py': 0}

            def __init__(self):
                self.diagnostic_origin = None
                self.diagnostic_records = 0
                self.diagnostic_nodes = frozenset()
                self.diagnostic_capturemanager = None
                try:
                    self.diagnostic_origin = time.monotonic()
                except Exception:
                    pass

            def pytest_collection_finish(self, session):
                # Only source nodes selected by the existing fixed pytest argv.
                try:
                    self.diagnostic_nodes = frozenset(item.nodeid for item in session.items)
                    self.diagnostic_capturemanager = session.config.pluginmanager.getplugin('capturemanager')
                    for item in session.items:
                        if Path(item.path).name == 'test_native_experiments.py':
                            item.module._native_cohort_phase_sink = self.fixture_phase
                except Exception:
                    pass

            def diagnostic(self, nodeid, event, when=None, outcome=None, duration_seconds=0.0):
                if nodeid not in self.diagnostic_nodes or self.diagnostic_records >= 4096:
                    return
                # Count attempted records too: a failed sink cannot expand output.
                self.diagnostic_records += 1
                try:
                    if self.diagnostic_origin is None or not math.isfinite(self.diagnostic_origin):
                        return
                    now = time.monotonic()
                    duration = float(duration_seconds)
                    elapsed = max(0.0, now - self.diagnostic_origin)
                    if not all(math.isfinite(value) and value >= 0 for value in (now, duration, elapsed)):
                        return
                    record = dict(schema_version=1, suite='native-store', nodeid=nodeid,
                                  event=event, when=when, outcome=outcome,
                                  duration_seconds=duration, monotonic_seconds=now,
                                  elapsed_seconds=elapsed)
                    line = 'NEXAWEAVE_TEST_PHASE ' + json.dumps(record, allow_nan=False)
                    if self.diagnostic_capturemanager is None:
                        print(line, flush=True)
                    else:
                        # Disable capture only for this fixed metadata record;
                        # unrelated test output keeps its original capture policy.
                        with self.diagnostic_capturemanager.global_and_fixture_disabled():
                            print(line, flush=True)
                except Exception:
                    # Diagnostic failures never alter qualification. Controls are
                    # BaseException and propagate through the existing finally.
                    pass

            def fixture_phase(self, record):
                try:
                    if self.diagnostic_records >= 4096:
                        return
                    if (set(record) != {'schema_version', 'suite', 'phase', 'event', 'monotonic_seconds', 'elapsed_seconds'}
                            or type(record['schema_version']) is not int or record['schema_version'] != 1
                            or record['suite'] != 'native-store-cohort'
                            or record['event'] not in ('start', 'end', 'observed_closed')):
                        return
                    for key in ('monotonic_seconds', 'elapsed_seconds'):
                        if type(record[key]) not in (int, float) or not math.isfinite(record[key]) or record[key] < 0:
                            return
                    self.diagnostic_records += 1
                    line = 'NEXAWEAVE_FIXTURE_PHASE ' + json.dumps(record, allow_nan=False)
                    if self.diagnostic_capturemanager is None:
                        print(line, flush=True)
                    else:
                        with self.diagnostic_capturemanager.global_and_fixture_disabled():
                            print(line, flush=True)
                except Exception:
                    pass

            def pytest_runtest_logstart(self, nodeid, location):
                self.diagnostic(nodeid, 'start')

            def pytest_runtest_logreport(self, report):
                if "native_store_tests" in report.nodeid:
                    if report.skipped:
                        self.skipped += 1
                    if report.when == "call" and report.passed:
                        self.passed += 1
                        for name in self.targets:
                            if name + '::' in report.nodeid:
                                self.targets[name] += 1
                # Emit only actual reports; no end from finally on interruption.
                if report.when in ('setup', 'call', 'teardown') and report.outcome in ('passed', 'failed', 'skipped'):
                    self.diagnostic(report.nodeid, 'end', report.when, report.outcome, report.duration)

        qualification = Qualification()
        result = int(pytest.main([
            "-q", "--durations=0", "-p", "pytest_asyncio.plugin",
            "-o", "markers=postgres: guarded disposable PostgreSQL integration",
            str(ROOT / "backend" / "native_store_tests"),
        ], plugins=[qualification]))
        if (qualification.skipped or qualification.passed == 0
                or qualification.targets['test_native_experiment_http.py'] < 10
                or qualification.targets['test_native_experiment_socket.py'] < 2):
            print("native_store_qualification_incomplete", file=sys.stderr)
            result = 1
    finally:
        guard.restore()
    if guard.blocked_attempts:
        print("non_loopback_attempt", file=sys.stderr)
        return 1
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres", action="store_true")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.postgres:
        parser.error("explicit disposable PostgreSQL opt-in required")
    if args.child:
        return child()
    password = os.environ.get("PROJECT_STORE_TEST_PASSWORD")
    if not password:
        parser.error("approved disposable PostgreSQL fixture password required")
    from psycopg.conninfo import make_conninfo

    with tempfile.TemporaryDirectory(prefix="nexaweave-native-store-") as directory:
        env = _unit_environment(Path(directory))
        env["PYTHONPATH"] = os.pathsep.join([
            str(ROOT), str(ROOT / "backend"),
            str(ROOT / "services" / "knowledge" / "src"),
            str(ROOT / "services" / "knowledge" / "tests"),
            str(ROOT / "backend" / "engine_tests"),
            str(ROOT / "backend" / "native_store_tests"),
        ])
        env.update(NEXAWEAVE_NATIVE_TEST_OFFLINE="1", HF_HUB_OFFLINE="1",
                   TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                   DO_NOT_TRACK="1", PROJECT_STORE_POSTGRES_INTEGRATION="1")
        for name in ('NEXAWEAVE_EXPERIMENT_TEST_PYTHON',
                     'NEXAWEAVE_EXPERIMENT_TEST_HTTP_PYTHON',
                     'NEXAWEAVE_EXPERIMENT_TEST_BOOTSTRAP'):
            value = os.environ.get(name)
            if not value or not Path(value).is_absolute() or not Path(value).is_file():
                parser.error('explicit installed experiment test runtimes required')
            env[name] = value
        env["PROJECT_STORE_POSTGRES_TEST_DSN"] = make_conninfo(
            host="127.0.0.1", port=15432, dbname="mirofish_operations_test",
            user="mirofish_fixture", password=password, connect_timeout=5)
        try:
            return subprocess.run([
                sys.executable, str(Path(__file__).resolve()),
                "--child", "--postgres",
            ], cwd=directory, env=env, check=False, timeout=600).returncode
        except subprocess.TimeoutExpired:
            print("native_store_qualification_timeout", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())

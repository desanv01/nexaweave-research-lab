"""Main-owned prepared native engine with real Temporal and PostgreSQL qualification."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

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

            def pytest_runtest_logreport(self, report):
                if "native_temporal_tests" in report.nodeid:
                    if report.skipped:
                        self.skipped += 1
                    if report.when == "call" and report.passed:
                        self.passed += 1

        qualification = Qualification()
        result = int(pytest.main([
            "-q", "-p", "pytest_asyncio.plugin",
            "-o", "markers=postgres: guarded disposable PostgreSQL integration",
            str(ROOT / "backend" / "native_temporal_tests"),
        ], plugins=[qualification]))
        if qualification.skipped or qualification.passed == 0:
            print("native_temporal_qualification_incomplete", file=sys.stderr)
            result = 1
    finally:
        guard.restore()
    if guard.blocked_attempts:
        print("non_loopback_attempt", file=sys.stderr)
        return 1
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--integration", action="store_true")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.integration:
        parser.error("explicit disposable Temporal/PostgreSQL opt-in required")
    if args.child:
        return child()
    password = os.environ.get("PROJECT_STORE_TEST_PASSWORD")
    if not password:
        parser.error("approved disposable PostgreSQL fixture password required")
    if os.environ.get("TEMPORAL_TEST_ADDRESS") != "127.0.0.1:17233":
        parser.error("approved disposable loopback Temporal fixture required")
    from psycopg.conninfo import make_conninfo

    with tempfile.TemporaryDirectory(prefix="nexaweave-native-temporal-") as directory:
        env = _unit_environment(Path(directory))
        env["PYTHONPATH"] = os.pathsep.join([
            str(ROOT), str(ROOT / "backend"),
            str(ROOT / "services" / "knowledge" / "src"),
            str(ROOT / "services" / "knowledge" / "tests"),
            str(ROOT / "backend" / "engine_tests"),
            str(ROOT / "backend" / "native_store_tests"),
            str(ROOT / "backend" / "native_temporal_tests"),
        ])
        env.update(NEXAWEAVE_NATIVE_TEST_OFFLINE="1", HF_HUB_OFFLINE="1",
                   TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                   DO_NOT_TRACK="1", PROJECT_STORE_POSTGRES_INTEGRATION="1",
                   TEMPORAL_EXECUTION_INTEGRATION="1",
                   TEMPORAL_TEST_ADDRESS="127.0.0.1:17233")
        env["PROJECT_STORE_POSTGRES_TEST_DSN"] = make_conninfo(
            host="127.0.0.1", port=15432, dbname="mirofish_operations_test",
            user="mirofish_fixture", password=password, connect_timeout=5)
        try:
            return subprocess.run([
                sys.executable, str(Path(__file__).resolve()),
                "--child", "--integration",
            ], cwd=directory, env=env, check=False, timeout=600).returncode
        except subprocess.TimeoutExpired:
            print("native_temporal_qualification_timeout", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())

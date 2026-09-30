"""Main-run isolated native ownership tests; disposable PostgreSQL is opt-in."""
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


def child(postgres: bool) -> int:
    guard = LoopbackOnlySockets()
    guard.install()
    try:
        import pytest
        targets = ["tests/test_native_run_ownership.py", "tests/test_native_process_driver.py",
                   "tests/test_native_run_supervisor.py"]
        if postgres:
            targets.append("tests/test_native_run_ownership_integration.py")
            targets.append("tests/test_native_process_driver_integration.py")
            targets.append("tests/test_native_run_supervisor_integration.py")
        class Qualification:
            passed = {"test_native_run_ownership_integration.py": 0,
                      "test_native_process_driver_integration.py": 0,
                      "test_native_run_supervisor_integration.py": 0}
            skipped = 0
            def pytest_runtest_logreport(self, report):
                for filename in self.passed:
                    if filename + "::" in report.nodeid:
                        if report.skipped:
                            self.skipped += 1
                        if report.when == "call" and report.passed:
                            self.passed[filename] += 1
        qualification = Qualification()
        result = int(pytest.main(["-q", "-p", "pytest_asyncio.plugin", *targets],
                                 plugins=[qualification]))
        if postgres and (qualification.skipped or not all(qualification.passed.values())):
            print("native_postgres_qualification_incomplete", file=sys.stderr)
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
    if args.child:
        return child(args.postgres)
    with tempfile.TemporaryDirectory(prefix="mirofish-native-run-tests-") as directory:
        env = _unit_environment(Path(directory))
        env["MIROFISH_NATIVE_TEST_OFFLINE"] = "1"
        if args.postgres:
            from psycopg.conninfo import make_conninfo
            password = os.environ.get("PROJECT_STORE_TEST_PASSWORD")
            if not password:
                parser.error("approved disposable PostgreSQL fixture password required")
            env["PROJECT_STORE_POSTGRES_INTEGRATION"] = "1"
            env["PROJECT_STORE_POSTGRES_TEST_DSN"] = make_conninfo(
                host="127.0.0.1", port=15432, dbname="mirofish_operations_test",
                user="mirofish_fixture", password=password, connect_timeout=5)
        command = [sys.executable, str(Path(__file__).resolve()), "--child"]
        if args.postgres:
            command.append("--postgres")
        try:
            return subprocess.run(command, cwd=ROOT / "services" / "knowledge",
                                  env=env, check=False, timeout=600).returncode
        except subprocess.TimeoutExpired:
            print("native_qualification_timeout", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())

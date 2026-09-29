"""Main-run isolated metadata-store qualification; fixture PostgreSQL is opt-in."""

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
        targets = ["tests/test_project_store.py", "tests/test_source_store.py",
                   "tests/test_source_bridge.py", "tests/test_budget.py"]
        if postgres:
            targets.extend(["tests/test_project_store_postgres.py", "tests/test_source_store_postgres.py",
                            "tests/test_source_bridge_postgres.py", "tests/test_budget_postgres.py"])
        class Qualification:
            passed = {"test_project_store_postgres.py": 0, "test_source_store_postgres.py": 0,
                      "test_source_bridge_postgres.py": 0, "test_budget_postgres.py": 0}
            skipped = 0
            def pytest_runtest_logreport(self, report):
                for filename in self.passed:
                    if filename + "::" in report.nodeid:
                        if report.skipped:
                            self.skipped += 1
                        if report.when == "call" and report.passed:
                            self.passed[filename] += 1
        qualification = Qualification()
        result = int(pytest.main(["-q", "-p", "pytest_asyncio.plugin", *targets], plugins=[qualification]))
        if postgres and (qualification.skipped or not all(qualification.passed.values())):
            print("postgres_qualification_incomplete", file=sys.stderr)
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
    with tempfile.TemporaryDirectory(prefix="mirofish-project-store-tests-") as directory:
        env = _unit_environment(Path(directory))
        if args.postgres:
            from psycopg.conninfo import make_conninfo
            password = os.environ.get("PROJECT_STORE_TEST_PASSWORD")
            if not password:
                parser.error("PROJECT_STORE_TEST_PASSWORD required for --postgres")
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
            print("qualification_timeout", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())

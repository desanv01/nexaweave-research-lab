"""Main-owned offline/optional disposable PostgreSQL ledger qualification.

No provider credentials are inherited. Libpq uses native sockets, so the Python
socket guard is not its isolation boundary: the only accepted database route is
the explicitly constructed loopback fixture DSN below. Never use a user DSN.
"""
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


def child(integration: bool) -> int:
    guard = LoopbackOnlySockets()
    guard.install()
    try:
        import pytest
        targets = ["tests/test_operations.py", "tests/test_ingestion.py"]
        if integration:
            targets.extend(["tests/test_operations_postgres.py", "tests/test_ingestion_postgres.py"])
        class Qualification:
            postgres_passed = {"test_operations_postgres.py": 0, "test_ingestion_postgres.py": 0}
            postgres_skipped = 0

            def pytest_runtest_logreport(self, report):
                for filename in self.postgres_passed:
                    if filename + "::" in report.nodeid:
                        if report.skipped:
                            self.postgres_skipped += 1
                        if report.when == "call" and report.passed:
                            self.postgres_passed[filename] += 1

        qualification = Qualification()
        result = int(pytest.main(["-q", "-p", "pytest_asyncio.plugin", *targets], plugins=[qualification]))
        if integration and (qualification.postgres_skipped or not all(qualification.postgres_passed.values())):
            print("Requested PostgreSQL qualification was skipped or empty", file=sys.stderr)
            result = 1
    finally:
        guard.restore()
    if guard.blocked_attempts:
        print("Ledger tests attempted non-loopback Python network access", file=sys.stderr)
        return 1
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres", action="store_true")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        return child(args.postgres)
    with tempfile.TemporaryDirectory(prefix="mirofish-operations-tests-") as directory:
        env = _unit_environment(Path(directory))
        env["GRAPHITI_TELEMETRY_ENABLED"] = "false"
        if args.postgres:
            from psycopg.conninfo import make_conninfo
            password = os.environ.get("KNOWLEDGE_POSTGRES_TEST_PASSWORD")
            if not password:
                parser.error("KNOWLEDGE_POSTGRES_TEST_PASSWORD required for --postgres")
            env["KNOWLEDGE_POSTGRES_INTEGRATION"] = "1"
            env["KNOWLEDGE_POSTGRES_TEST_DSN"] = make_conninfo(
                host="127.0.0.1", port=15432, dbname="mirofish_operations_test",
                user="mirofish_fixture", password=password, connect_timeout=5,
            )
        command = [sys.executable, str(Path(__file__).resolve()), "--child"]
        if args.postgres:
            command.append("--postgres")
        return subprocess.run(command, cwd=ROOT / "services" / "knowledge",
                              env=env, check=False, timeout=600).returncode


if __name__ == "__main__":
    raise SystemExit(main())

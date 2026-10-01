"""Isolated Temporal execution tests; real loopback services are opt-in."""
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
        targets = ["tests/test_temporal_execution.py",
                   "tests/test_temporal_native_execution.py"]
        integration_names = ("test_temporal_execution_integration.py",
                             "test_temporal_native_execution_integration.py")
        if integration:
            targets.extend("tests/" + name for name in integration_names)
        class Qualification:
            def __init__(self):
                self.passed = dict.fromkeys(integration_names, 0)
                self.skipped = dict.fromkeys(integration_names, 0)

            def pytest_runtest_logreport(self, report):
                for name in integration_names:
                    if name + "::" in report.nodeid:
                        if report.skipped:
                            self.skipped[name] += 1
                        if report.when == "call" and report.passed:
                            self.passed[name] += 1
        qualification = Qualification()
        result = int(pytest.main(["-q", "-p", "pytest_asyncio.plugin", *targets],
                                 plugins=[qualification]))
        if integration and any(qualification.skipped[name]
                               or qualification.passed[name] < 1
                               for name in integration_names):
            print("temporal_integration_incomplete", file=sys.stderr)
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
    if args.child:
        return child(args.integration)
    with tempfile.TemporaryDirectory(prefix="mirofish-temporal-tests-") as directory:
        env = _unit_environment(Path(directory))
        env["GRAPHITI_TELEMETRY_ENABLED"] = "false"
        if args.integration:
            from psycopg.conninfo import make_conninfo
            password = os.environ.get("PROJECT_STORE_TEST_PASSWORD")
            address = os.environ.get("TEMPORAL_TEST_ADDRESS")
            if not password or address != "127.0.0.1:17233":
                parser.error("approved fixture password and loopback Temporal address required")
            env["PROJECT_STORE_POSTGRES_INTEGRATION"] = "1"
            env["PROJECT_STORE_POSTGRES_TEST_DSN"] = make_conninfo(
                host="127.0.0.1", port=15432, dbname="mirofish_operations_test",
                user="mirofish_fixture", password=password, connect_timeout=5)
            env["TEMPORAL_EXECUTION_INTEGRATION"] = "1"
            env["TEMPORAL_TEST_ADDRESS"] = address
        command = [sys.executable, str(Path(__file__).resolve()), "--child"]
        if args.integration:
            command.append("--integration")
        try:
            return subprocess.run(command, cwd=ROOT / "services" / "knowledge",
                                  env=env, check=False, timeout=600).returncode
        except subprocess.TimeoutExpired:
            print("temporal_qualification_timeout", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())

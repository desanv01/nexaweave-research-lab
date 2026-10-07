"""Main-owned evidence dossier qualification with optional real local stores."""
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
        targets = ["test_evidence_dossier.py"]
        if integration:
            targets.append("test_evidence_dossier_integration.py")

        class Qualification:
            passed = {target: 0 for target in targets}
            skipped = 0

            def pytest_runtest_logreport(self, report):
                for target in self.passed:
                    if target + "::" in report.nodeid:
                        if report.skipped:
                            self.skipped += 1
                        if report.when == "call" and report.passed:
                            self.passed[target] += 1

        qualification = Qualification()
        result = int(pytest.main([
            "-q", "-p", "pytest_asyncio.plugin", "-o", "asyncio_mode=auto",
            "-o", "markers=neo4j: guarded Neo4j integration\npostgres: guarded PostgreSQL integration",
            *[str(ROOT / "services" / "knowledge" / "tests" / name) for name in targets],
        ], plugins=[qualification]))
        if qualification.skipped or not all(qualification.passed.values()):
            print("evidence_dossier_qualification_incomplete", file=sys.stderr)
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
    with tempfile.TemporaryDirectory(prefix="nexaweave-evidence-dossier-") as directory:
        env = _unit_environment(Path(directory))
        env["PYTHONPATH"] = os.pathsep.join([
            str(ROOT), str(ROOT / "services" / "knowledge" / "src"),
            str(ROOT / "services" / "knowledge" / "tests"),
        ])
        env["GRAPHITI_TELEMETRY_ENABLED"] = "false"
        if args.integration:
            from psycopg.conninfo import make_conninfo
            pg_password = os.environ.get("PROJECT_STORE_TEST_PASSWORD")
            neo_password = os.environ.get("KNOWLEDGE_TEST_PASSWORD")
            if not pg_password or not neo_password:
                parser.error("approved disposable PostgreSQL/Neo4j passwords required")
            env.update(PROJECT_STORE_POSTGRES_INTEGRATION="1", KNOWLEDGE_INTEGRATION="1",
                       KNOWLEDGE_TEST_PASSWORD=neo_password)
            env["PROJECT_STORE_POSTGRES_TEST_DSN"] = make_conninfo(
                host="127.0.0.1", port=15432, dbname="mirofish_operations_test",
                user="mirofish_fixture", password=pg_password, connect_timeout=5)
        command = [sys.executable, str(Path(__file__).resolve()), "--child"]
        if args.integration:
            command.append("--integration")
        try:
            return subprocess.run(command, cwd=directory, env=env, check=False, timeout=600).returncode
        except subprocess.TimeoutExpired:
            print("evidence_dossier_qualification_timeout", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())

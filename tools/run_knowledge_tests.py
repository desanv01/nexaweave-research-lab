"""Main-owned qualification launcher: fake models, optional real local Neo4j.

No caller model credentials are inherited. The socket guard is an in-process
test safeguard, not an OS-level network sandbox. This is not live-model proof.
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
        files = ["tests/test_contracts.py", "tests/test_graph_reads.py", "tests/test_commands.py",
                 "tests/test_stdio_transport.py", "tests/test_read_runtime.py"]
        if integration:
            files.append("tests/test_integration_neo4j.py")
        result = int(pytest.main(["-q", "-p", "pytest_asyncio.plugin", *files]))
    finally:
        guard.restore()
    if guard.blocked_attempts:
        print(f"Knowledge tests blocked {guard.blocked_attempts} external network attempt(s)", file=sys.stderr)
        return 1
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--neo4j", action="store_true", help="Require disposable local Neo4j integration")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        return child(args.neo4j)
    with tempfile.TemporaryDirectory(prefix="mirofish-knowledge-tests-") as directory:
        env = _unit_environment(Path(directory))
        env["GRAPHITI_TELEMETRY_ENABLED"] = "false"
        if args.neo4j:
            password = os.environ.get("KNOWLEDGE_TEST_PASSWORD")
            if not password:
                parser.error("KNOWLEDGE_TEST_PASSWORD is required for --neo4j")
            env["KNOWLEDGE_INTEGRATION"] = "1"
            env["KNOWLEDGE_TEST_PASSWORD"] = password
        command = [sys.executable, str(Path(__file__).resolve()), "--child"]
        if args.neo4j:
            command.append("--neo4j")
        return subprocess.run(command, cwd=ROOT / "services" / "knowledge", env=env, check=False, timeout=600).returncode


if __name__ == "__main__":
    raise SystemExit(main())

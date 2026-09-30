"""Main-owned native OASIS primitive tests, offline in disposable storage.

Requires the full locked backend runtime. This does not qualify autonomous
decisions, Twitter embedding recommendations, agent memory or restored runs.
"""
from __future__ import annotations

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
        result = int(pytest.main(["-q", "-p", "pytest_asyncio.plugin", str(ROOT / "backend" / "engine_tests")]))
    finally:
        guard.restore()
    if guard.blocked_attempts:
        print(f"Native engine tests blocked {guard.blocked_attempts} external attempt(s)", file=sys.stderr)
        return 1
    return result


def main() -> int:
    if sys.argv[1:] == ["--child"]:
        return child()
    with tempfile.TemporaryDirectory(prefix="mirofish-native-engine-") as directory:
        env = _unit_environment(Path(directory))
        env["PYTHONPATH"] += os.pathsep + str(ROOT / "services" / "knowledge" / "src")
        env["MIROFISH_NATIVE_TEST_OFFLINE"] = "1"
        env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
        # Native OASIS creates ./log during imports. Keep it out of the repo.
        return subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child"], cwd=directory, env=env, timeout=300, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())

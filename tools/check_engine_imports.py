"""Offline native engine import smoke check; not an autonomous simulation test."""
from __future__ import annotations

import importlib
from importlib.metadata import version
import json
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
        results = {}
        for package, module in (("torch", "torch"), ("camel-ai", "camel"), ("camel-oasis", "oasis")):
            importlib.import_module(module)
            results[package] = version(package)
        print(json.dumps({"native_imports": results, "simulation_executed": False}))
    finally:
        guard.restore()
    return int(bool(guard.blocked_attempts))


def main() -> int:
    if sys.argv[1:] == ["--child"]:
        return child()
    with tempfile.TemporaryDirectory(prefix="mirofish-engine-imports-") as directory:
        env = _unit_environment(Path(directory))
        env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
        return subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child"], cwd=directory, env=env, timeout=120, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())

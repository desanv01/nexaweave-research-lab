"""Offline native engine import smoke check; not an autonomous simulation test."""
from __future__ import annotations

import importlib
from importlib.metadata import version
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_unit_tests import LoopbackOnlySockets, _unit_environment

SCRIPT_HANDLERS = {
    "run_twitter_simulation.py": "IPCHandler",
    "run_reddit_simulation.py": "IPCHandler",
    "run_parallel_simulation.py": "ParallelIPCHandler",
}


def child(script: str | None = None) -> int:
    guard = LoopbackOnlySockets()
    guard.install()
    try:
        results = {}
        for package, module in (("torch", "torch"), ("camel-ai", "camel"), ("camel-oasis", "oasis")):
            importlib.import_module(module)
            results[package] = version(package)
        if script is not None:
            # A separate child for every script contains import-time builtins
            # changes. Never execute the scripts' __main__ simulation entry.
            handler_name = SCRIPT_HANDLERS[script]
            namespace = runpy.run_path(str(ROOT / "backend" / "scripts" / script), run_name="offline_import_fixture")
            if not isinstance(namespace.get(handler_name), type):
                raise RuntimeError("Native script handler was not imported")
            from app.services.simulation_ipc import SimulationIPCServer
            if namespace.get("SimulationIPCServer") is not SimulationIPCServer:
                raise RuntimeError("Native script did not import the shared IPC server")
        print(json.dumps({"native_imports": results, "script": script, "simulation_executed": False}))
    finally:
        guard.restore()
    return int(bool(guard.blocked_attempts))


def main() -> int:
    if sys.argv[1:] == ["--child"]:
        return child()
    if len(sys.argv) == 3 and sys.argv[1] == "--script-child" and sys.argv[2] in SCRIPT_HANDLERS:
        return child(sys.argv[2])
    if len(sys.argv) != 1:
        raise SystemExit("Unexpected import-check arguments")
    with tempfile.TemporaryDirectory(prefix="mirofish-engine-imports-") as directory:
        env = _unit_environment(Path(directory))
        env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
        for script in SCRIPT_HANDLERS:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--script-child", script], cwd=directory, env=env, timeout=120, check=False)
            if result.returncode:
                return result.returncode
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Main-owned native OASIS primitive tests, offline in disposable storage.

Requires the full locked backend runtime. This does not qualify autonomous
decisions, Twitter embedding recommendations, agent memory or restored runs.
"""
from __future__ import annotations

import importlib.util
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
        # Connected preparation/launch requires job-local PostgreSQL and Temporal.
        # Connected fixtures are mandatory in run_native_launch_tests.py --engine
        # and run_native_observations_tests.py --engine after those services start;
        # Report child fixtures also have their own mandatory report --engine
        # step and explicit helper path; this step qualifies SQLite primitives.
        result = int(pytest.main([
            "-q", "-p", "pytest_asyncio.plugin",
            "--ignore", str(ROOT / "backend" / "engine_tests" / "test_connected_preparation_native_launch.py"),
            "--ignore", str(ROOT / "backend" / "engine_tests" / "test_native_seed_ensemble.py"),
            "--ignore", str(ROOT / "backend" / "engine_tests" / "test_protected_reddit_checkpoint.py"),
            "--ignore", str(ROOT / "backend" / "engine_tests" / "test_protected_paired_checkpoint.py"),
            "--ignore", str(ROOT / "backend" / "engine_tests" / "test_connected_native_observations.py"),
            "--ignore", str(ROOT / "backend" / "engine_tests" / "test_connected_report_process.py"),
            str(ROOT / "backend" / "engine_tests"),
        ]))
    finally:
        guard.restore()
    if guard.blocked_attempts:
        print(f"Native engine tests blocked {guard.blocked_attempts} external attempt(s)", file=sys.stderr)
        return 1
    return result


def main() -> int:
    if sys.argv[1:] == ["--child"]:
        return child()
    spec = importlib.util.spec_from_file_location(
        "_native_engine_owned", ROOT / "backend/app/utils/owned_process.py",
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    owner = helper.OwnedProcess()
    directory = tempfile.TemporaryDirectory(prefix="nexaweave-native-engine-")
    owner.bind_private_directory(directory)
    try:
        env = _unit_environment(Path(directory.name))
        env["PYTHONPATH"] += os.pathsep + str(ROOT / "backend" / "tests")
        env["PYTHONPATH"] += os.pathsep + str(ROOT / "services" / "knowledge" / "src")
        env["NEXAWEAVE_NATIVE_TEST_OFFLINE"] = "1"
        env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
        # Native OASIS creates ./log during imports. Keep it out of the repo.
        process = owner.start(
            subprocess.Popen, [sys.executable, str(Path(__file__).resolve()), "--child"],
            cwd=directory.name, env=env, stdin=subprocess.DEVNULL, shell=False, close_fds=True,
        )
        try:
            return process.wait(timeout=300)
        except subprocess.TimeoutExpired:
            print("native_engine_qualification_timeout", file=sys.stderr, flush=True)
            return 1
    finally:
        try:
            owner.stop([])
            if not owner.closed or (
                os.name == "nt" and not owner.tree_empty and (owner.process is not None or owner.started)
            ):
                raise RuntimeError("Native engine test process closure unverified")
        except BaseException:
            # Preserve the directory if process closure cannot be proved.
            directory._finalizer.detach()
            print("native_engine_owned_tree_closure_failed", file=sys.stderr, flush=True)
            raise
        owner.cleanup_private_directory(directory)
        print("native_engine_owned_tree_private_cwd_closed", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())

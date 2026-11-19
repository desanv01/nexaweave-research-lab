"""Main-owned prepared native engine with real Temporal and PostgreSQL qualification."""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_unit_tests import LoopbackOnlySockets, _unit_environment


def child() -> int:
    guard = LoopbackOnlySockets()
    guard.install()
    try:
        import pytest

        class Qualification:
            passed = 0
            skipped = 0

            def pytest_runtest_logreport(self, report):
                if "native_temporal_tests" in report.nodeid:
                    if report.skipped:
                        self.skipped += 1
                    if report.when == "call" and report.passed:
                        self.passed += 1

        qualification = Qualification()
        result = int(pytest.main([
            "-q", "-p", "pytest_asyncio.plugin",
            "-o", "markers=postgres: guarded disposable PostgreSQL integration",
            str(ROOT / "backend" / "native_temporal_tests"),
        ], plugins=[qualification]))
        if qualification.skipped or qualification.passed == 0:
            print("native_temporal_qualification_incomplete", file=sys.stderr)
            result = 1
    finally:
        guard.restore()
    if guard.blocked_attempts:
        print("non_loopback_attempt", file=sys.stderr)
        return 1
    return result


def _owner_type():
    spec = importlib.util.spec_from_file_location(
        "_native_temporal_owned", ROOT / "backend/app/utils/owned_process.py",
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper.OwnedProcess


def _run_owned(command, make_environment, *, max_seconds=600):
    # The private test seam can shorten the bound. Production uses the default.
    deadline = time.monotonic() + max_seconds
    work_deadline = deadline - min(3.0, max_seconds / 2)
    owner_type = _owner_type()

    class DeadlineOwner(owner_type):
        def stop(self, threads, *, timeout=3.0):
            # start() may call this for rollback before the runner's finally.
            # Bound the Job deadline itself, since root wait is only one step.
            now = time.monotonic()
            if self.cleanup_deadline is None:
                self.cleanup_deadline = min(deadline, now + min(3.0, timeout))
                self.cleanup_timeout = max(0.0, self.cleanup_deadline - now)
            return super().stop(
                threads, timeout=min(timeout, max(0.0, self.cleanup_deadline - now)),
            )

    owner = DeadlineOwner()
    directory = tempfile.TemporaryDirectory(prefix="nexaweave-native-temporal-")
    owner.bind_private_directory(directory)
    timed_out = False
    try:
        env = make_environment(Path(directory.name))
        if time.monotonic() >= work_deadline:
            raise subprocess.TimeoutExpired(command, max_seconds)
        process = owner.start(
            subprocess.Popen, command, cwd=directory.name, env=env,
            shell=False, close_fds=True,
        )
        remaining = work_deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, max_seconds)
        result = process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        timed_out = True
        result = 1
    finally:
        try:
            if not owner.closed:
                owner.stop([])
            root = owner.process
            if (not owner.closed or (root is None and owner.started)
                    or (root is not None and (root.returncode is None
                                              or (os.name == "nt" and not owner.tree_empty)))):
                raise RuntimeError("Native Temporal test process closure unverified")
            if time.monotonic() >= deadline:
                raise RuntimeError("Native Temporal cleanup deadline exhausted")
            owner.cleanup_private_directory(directory)
            if os.path.lexists(directory.name) or time.monotonic() > deadline:
                raise RuntimeError("Native Temporal private directory cleanup unverified")
        except BaseException:
            # Preserve evidence and avoid an unqualified destructor cleanup.
            directory._finalizer.detach()
            raise
        finally:
            if timed_out:
                print("native_temporal_qualification_timeout", file=sys.stderr, flush=True)

    if os.name == "nt" and owner.process is not None:
        print("native_temporal_owned_tree_private_cwd_closed", flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--integration", action="store_true")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.integration:
        parser.error("explicit disposable Temporal/PostgreSQL opt-in required")
    if args.child:
        return child()
    password = os.environ.get("PROJECT_STORE_TEST_PASSWORD")
    if not password:
        parser.error("approved disposable PostgreSQL fixture password required")
    if os.environ.get("TEMPORAL_TEST_ADDRESS") != "127.0.0.1:17233":
        parser.error("approved disposable loopback Temporal fixture required")
    from psycopg.conninfo import make_conninfo

    def make_environment(directory):
        env = _unit_environment(directory)
        env["PYTHONPATH"] = os.pathsep.join([
            str(ROOT), str(ROOT / "backend"),
            str(ROOT / "services" / "knowledge" / "src"),
            str(ROOT / "services" / "knowledge" / "tests"),
            str(ROOT / "backend" / "engine_tests"),
            str(ROOT / "backend" / "native_store_tests"),
            str(ROOT / "backend" / "native_temporal_tests"),
        ])
        env.update(NEXAWEAVE_NATIVE_TEST_OFFLINE="1", HF_HUB_OFFLINE="1",
                   TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                   DO_NOT_TRACK="1", PROJECT_STORE_POSTGRES_INTEGRATION="1",
                   TEMPORAL_EXECUTION_INTEGRATION="1",
                   TEMPORAL_TEST_ADDRESS="127.0.0.1:17233")
        env["PROJECT_STORE_POSTGRES_TEST_DSN"] = make_conninfo(
            host="127.0.0.1", port=15432, dbname="mirofish_operations_test",
            user="mirofish_fixture", password=password, connect_timeout=5)
        return env

    return _run_owned([
        sys.executable, str(Path(__file__).resolve()), "--child", "--integration",
    ], make_environment)


if __name__ == "__main__":
    raise SystemExit(main())

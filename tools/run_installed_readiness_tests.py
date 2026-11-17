"""Main-owned, installed-only W8.1 check against a fresh disposable PostgreSQL fixture."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
TEST = ROOT / "services/knowledge/tests/test_installed_readiness_http_integration.py"
CASE = "test_clean_installed_migrations_and_owned_metadata_restart"
MIGRATIONS = {
    "nexaweave_storage": (
        "0001_project_revisions.sql",
        "0002_source_evidence.sql",
        "0003_research_imports.sql",
        "0004_source_binaries.sql",
    ),
    "nexaweave_knowledge": ("0001_knowledge_operations.sql", "0002_scope_bindings.sql"),
    "nexaweave_execution": ("native_runs_0001.sql", "native_runs_0002.sql"),
}
DRIVERS = {
    "nexaweave_storage": ("store.py", 13387, "786a1f8161c3f75d38e6ea8156525af4c18452dfedcf8c6b5273306cdb606ec1"),
    "nexaweave_knowledge": ("operations.py", 27448, "fc82dcd6cb8b5353228b74e95cc68dcdc8fe5c33f13590d5610a8bdba53419f4"),
    "nexaweave_execution": ("native_run_store.py", 23088, "c135398234904947818a7c6709f68c8e24ed2b6dac40cbe36a6828a21828f9f7"),
}
STARTUP = (
    ("backend/run.py", 1681, "cc39a7ead1df509de326128801d475d2d70eb00722d94c5a2895df0496d87b26"),
    ("backend/app/config.py", 3932, "35b2b414b0313ffae66279a857228ef20806ee2ffb49f2232a6dca542908ccbf"),
    ("backend/app/services/knowledge_read_facade.py", 8858,
     "259a6169b16a13182931776971766bde546eda3c04ab1f11d05bef9a22739039"),
)
PG_HOST, PG_PORT = "127.0.0.1", 15432
PG_DATABASE, PG_USER = "mirofish_operations_test", "mirofish_fixture"


def _sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _verify_pin(path, size, digest):
    if path.stat().st_size != size or _sha256(path) != digest:
        raise ValueError("W8.1 source pin mismatch")


def _installed_bootstrap():
    """Confirm the isolated interpreter uses the reviewed wheel inputs."""
    distribution = importlib.metadata.distribution("nexaweave-knowledge")
    locator = json.loads(distribution.read_text("direct_url.json") or "{}")
    if locator.get("dir_info", {}).get("editable"):
        raise ValueError("editable knowledge installation")
    files = {path.as_posix() for path in distribution.files or ()}
    prefix = Path(sys.prefix).resolve()
    roots = {}
    for package, (driver, size, digest) in DRIVERS.items():
        spec = importlib.util.find_spec(package)
        if spec is None or spec.origin is None:
            raise ValueError("knowledge package not installed")
        origin = Path(spec.origin).resolve(strict=True)
        recorded = Path(distribution.locate_file(f"{package}/__init__.py")).resolve(strict=True)
        if (origin != recorded or not origin.is_relative_to(prefix)
                or "site-packages" not in origin.relative_to(prefix).parts
                or origin.parent.name != package
                or f"{package}/__init__.py" not in files):
            raise ValueError("unexpected knowledge installation")
        roots[package] = origin.parent
        source = ROOT / "services/knowledge/src" / package
        _verify_pin(source / driver, size, digest)
        _verify_pin(origin.parent / driver, size, digest)
        names = [f"migrations/{name}" for name in MIGRATIONS[package]]
        if package == "nexaweave_knowledge":
            names.extend(("read_bootstrap.py", "source_bootstrap.py"))
        for name in (driver, *names):
            relative = f"{package}/{name}"
            installed = origin.parent / name
            if (relative not in files or installed.resolve(strict=True) != installed
                    or _sha256(installed) != _sha256(source / name)):
                raise ValueError("installed knowledge input differs from candidate")
    for name, size, digest in STARTUP:
        _verify_pin(ROOT / name, size, digest)
    return roots["nexaweave_knowledge"] / "read_bootstrap.py"


@contextmanager
def owned_process(command, **kwargs):
    # Load the existing generic owner in either locked interpreter without
    # importing Flask or the application on the knowledge side.
    spec = importlib.util.spec_from_file_location(
        "_installed_readiness_owner", ROOT / "backend/app/utils/owned_process.py",
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    owner = helper.OwnedProcess()
    try:
        process = owner.start(subprocess.Popen, command, shell=False, close_fds=True, **kwargs)
        yield process
    finally:
        owner.stop([])
        if not owner.closed or (os.name == "nt" and not owner.tree_empty):
            raise RuntimeError("W8.1 process owner did not close")


def _child():
    if not sys.flags.isolated:
        return 1
    from tools.run_unit_tests import LoopbackOnlySockets

    guard = LoopbackOnlySockets()
    guard.install()
    try:
        ready = _installed_bootstrap()
        from psycopg.conninfo import make_conninfo
        import pytest

        os.environ.update(
            KNOWLEDGE_PYTHON=str(Path(sys.executable).absolute()),
            KNOWLEDGE_BOOTSTRAP_SCRIPT=str(ready),
            PROJECT_STORE_POSTGRES_TEST_DSN=make_conninfo(
                host=PG_HOST, port=PG_PORT, dbname=PG_DATABASE, user=PG_USER,
                password=os.environ.pop("PROJECT_STORE_TEST_PASSWORD"), connect_timeout=3,
            ),
        )

        class Results:
            collected = 0
            passed = 0
            skipped = 0

            def pytest_collection_finish(self, session):
                self.collected = len(session.items)

            def pytest_runtest_logreport(self, report):
                self.passed += int(report.when == "call" and report.passed)
                self.skipped += int(report.skipped)

        results = Results()
        # Pytest owns the new leaf. Its parent is exclusive even for direct
        # isolated child invocation from a directory with existing fixtures.
        case_root = Path(tempfile.mkdtemp(prefix="nexaweave-readiness-cases-"))
        status = int(pytest.main([
            "-q", "-o", "markers=postgres: approved disposable PostgreSQL",
            "--basetemp", str(case_root / "cases"), f"{TEST}::{CASE}",
        ], plugins=[results]))
    except (OSError, ValueError, KeyError, importlib.metadata.PackageNotFoundError):
        print("installed_readiness_preflight_refused", file=sys.stderr)
        return 1
    finally:
        guard.restore()

    if status or guard.blocked_attempts or results.skipped or results.collected != 1 or results.passed != 1:
        print("installed_readiness_qualification_incomplete", file=sys.stderr)
        print(f"Installed readiness case evidence retained in {case_root}", file=sys.stderr)
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        return _child()

    from tools.run_unit_tests import _unit_environment

    password = os.environ.get("PROJECT_STORE_TEST_PASSWORD")
    backend = Path(os.environ.get("NEXAWEAVE_WORKBENCH_BACKEND_PYTHON", ""))
    if not password or not backend.is_absolute() or not backend.is_file():
        parser.error("fresh owned fixture password and locked backend interpreter required")

    directory = Path(tempfile.mkdtemp(prefix="nexaweave-installed-readiness-"))
    env = _unit_environment(directory)
    for key in list(env):
        if key.upper().startswith(("PG", "KNOWLEDGE_", "LLM_", "OPENAI_", "DEEPSEEK_", "ZEP_", "TEMPORAL_")):
            env.pop(key)
    env.update(
        PROJECT_STORE_TEST_PASSWORD=password,
        NEXAWEAVE_WORKBENCH_BACKEND_PYTHON=str(backend),
        NEXAWEAVE_W81_INSTALLED_READINESS="1",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        GRAPHITI_TELEMETRY_ENABLED="false",
    )
    try:
        command = [sys.executable, "-I", "-u", str(Path(__file__).resolve()), "--child"]
        with owned_process(command, cwd=directory, env=env, stdin=subprocess.DEVNULL) as process:
            result = process.wait(timeout=360)
    except subprocess.TimeoutExpired:
        print("installed_readiness_timeout", file=sys.stderr)
        result = 1
    if result:
        print(f"Installed readiness private diagnostics retained in {directory}", file=sys.stderr)
    return result


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())

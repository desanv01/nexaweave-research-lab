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
MANAGED_SELECTION = "NEXAWEAVE_W81_MANAGED_READINESS"
MANAGED_ADDRESS = "NEXAWEAVE_W81_MANAGED_TEMPORAL_ADDRESS"
MANAGED_FIXTURE = "127.0.0.1:17233"
MANAGED_WITNESS = "installed_managed_readiness_passed"

MIGRATIONS = {
    "nexaweave_storage": (
        "0001_project_revisions.sql",
        "0002_source_evidence.sql",
        "0003_research_imports.sql",
        "0004_source_binaries.sql",
    ),
    "nexaweave_knowledge": ("0001_knowledge_operations.sql", "0002_scope_bindings.sql"),
    "nexaweave_execution": (
        "0001_budget_admission.sql", "preparations_0001.sql", "preparations_0002.sql",
        "native_runs_0001.sql", "native_runs_0002.sql", "native_runs_0003.sql",
        "native_launch_0001.sql", "native_launch_0002.sql", "native_launch_0003.sql",
        "report_0001.sql", "followup_0001.sql",
    ),
}
MIGRATION_PINS = {
    ("nexaweave_execution", "migrations/preparations_0002.sql"): (
        492, "2e8da8a6f5d82519da3cdb245bdfa453140614a87311b3b19c406c99c7e6d24f",
    ),
    ("nexaweave_execution", "migrations/native_runs_0003.sql"): (
        1608, "e68d365e7398ba1d520906af69961c6b14bddee35ba5e5ebebb7f9a550701015",
    ),
    ("nexaweave_execution", "migrations/native_launch_0003.sql"): (
        4641, "0ab6321d7e3fed751cdaec95b8afd48b7e4f26a1237cb699deef04df07fa1a95",
    ),
}
DRIVERS = {
    "nexaweave_storage": (
        ("store.py", 13387, "786a1f8161c3f75d38e6ea8156525af4c18452dfedcf8c6b5273306cdb606ec1"),
        ("__main__.py", 11634, "401c434163982ebee95aa7c81f761297109cc33c3e339b0c5fc7e20dadb60588"),
    ),
    "nexaweave_knowledge": (
        ("operations.py", 35987, "58a5d0285deac764e6abfb4f2e3baaca6387b93c73897d7422d9ecb4ece9867d"),
        ("bindings.py", 6586, "ec2a023aa08815eec80e23f756e5436d7fa16f5debc03e481f5644f3629c90f1"),
    ),
    "nexaweave_execution": (
        ("budget.py", 27827, "6d613d17b5cfce7323e3d015b2b82550273addc008884ceb9b3d611178d0ed7e"),
        ("preparation_store.py", 16440, "5a6e6eb8ad545c2d68b4d82ce970ee63b1d4562ad5639463bdba8f4cae836105"),
        ("native_run_store.py", 28219, "62b9b60835c7162b319f2d26a75f499c5749b053ba7c63ea5175038ecdaa85f9"),
        ("native_launch_store.py", 32462, "404934b84c48c97b59e066a8ad4f3380017572024be831cd2b22cef1bd93c8f2"),
        ("report_store.py", 22419, "46ab0e298dd8d47a9e223d5135e86197c8db72d3e2404f6545ffbd4bc36fab46"),
        ("followup_store.py", 30580, "d78548efd8b08bc95c3003b8fc2c42ea7ab358526f21d3f433e6a06c814b0aac"),
    ),
}
STARTUP = (
    ("backend/run.py", 2562, "07e6dd51ac86f117ab9f84e8468c1c7b2ac694ea1cd9041a405f829753f6833c"),
    ("backend/app/services/managed_research_startup.py", 15292, "04dda9aa19c55805369e0599d251a0d4a5d39771f56751faa992e6caf59c8fe5"),
    ("backend/app/config.py", 4021, "af4cc31f93e9ba5a3c2a195d7e443d36df9fcb821228067260c8737420d723e6"),
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
        print(f"installed_readiness_pin_mismatch {path.name}", file=sys.stderr)
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
    for package, drivers in DRIVERS.items():
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
        names = [f"migrations/{name}" for name in MIGRATIONS[package]]
        for driver, size, digest in drivers:
            _verify_pin(source / driver, size, digest)
            _verify_pin(origin.parent / driver, size, digest)
            names.append(driver)
        if package == "nexaweave_knowledge":
            names.extend(("read_bootstrap.py", "source_bootstrap.py"))
        if package == "nexaweave_execution":
            names.append("operator_bootstrap.py")
        for name in names:
            pin = MIGRATION_PINS.get((package, name))
            if pin is not None:
                _verify_pin(source / name, *pin)
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


def _managed_requested():
    selection = os.environ.get(MANAGED_SELECTION, "0")
    if selection == "0":
        return False
    if selection != "1" or os.name != "nt" or os.environ.get(MANAGED_ADDRESS) != MANAGED_FIXTURE:
        raise ValueError("managed readiness requires the approved Windows Temporal fixture")
    return True


def _child(*, managed_requested):
    if not sys.flags.isolated:
        return 1
    from tools.run_unit_tests import LoopbackOnlySockets

    guard = LoopbackOnlySockets()
    guard.install()
    try:
        if _managed_requested() != managed_requested:
            raise ValueError("managed readiness selection did not reach the child")
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
            managed_passed = 0

            def pytest_collection_finish(self, session):
                self.collected = len(session.items)

            def pytest_runtest_logreport(self, report):
                if report.when == "call" and report.passed:
                    self.passed += 1
                    self.managed_passed += report.user_properties.count((MANAGED_WITNESS, "passed"))
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

    if (status or guard.blocked_attempts or results.skipped or results.collected != 1 or results.passed != 1
            or results.managed_passed != int(managed_requested)):
        print("installed_readiness_qualification_incomplete", file=sys.stderr)
        print(f"Installed readiness case evidence retained in {case_root}", file=sys.stderr)
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--managed-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        return _child(managed_requested=args.managed_child)
    if args.managed_child:
        parser.error("managed child flag requires isolated child mode")
    try:
        managed = _managed_requested()
    except ValueError as error:
        parser.error(str(error))

    from tools.run_unit_tests import _unit_environment

    password = os.environ.get("PROJECT_STORE_TEST_PASSWORD")
    backend = Path(os.environ.get("NEXAWEAVE_WORKBENCH_BACKEND_PYTHON", ""))
    if not password or not backend.is_absolute() or not backend.is_file():
        parser.error("fresh owned fixture password and locked backend interpreter required")

    directory = Path(tempfile.mkdtemp(prefix="nexaweave-installed-readiness-"))
    env = _unit_environment(directory)
    for key in list(env):
        upper = key.upper()
        if (upper in {MANAGED_SELECTION, MANAGED_ADDRESS}
                or upper.startswith(("PG", "KNOWLEDGE_", "LLM_", "OPENAI_", "DEEPSEEK_", "ZEP_", "TEMPORAL_"))):
            env.pop(key)
    if managed:
        env.update({MANAGED_SELECTION: "1", MANAGED_ADDRESS: MANAGED_FIXTURE})
    env.update(
        PROJECT_STORE_TEST_PASSWORD=password,
        NEXAWEAVE_WORKBENCH_BACKEND_PYTHON=str(backend),
        NEXAWEAVE_W81_INSTALLED_READINESS="1",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        GRAPHITI_TELEMETRY_ENABLED="false",
    )
    try:
        command = [sys.executable, "-I", "-u", str(Path(__file__).resolve()), "--child"]
        if managed:
            command.append("--managed-child")
        with owned_process(command, cwd=directory, env=env, stdin=subprocess.DEVNULL) as process:
            result = process.wait(timeout=360)
    except subprocess.TimeoutExpired:
        print("installed_readiness_timeout", file=sys.stderr)
        result = 1
    if result:
        print(f"Installed readiness private diagnostics retained in {directory}", file=sys.stderr)
    elif managed:
        print(MANAGED_WITNESS, flush=True)
    return result


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())

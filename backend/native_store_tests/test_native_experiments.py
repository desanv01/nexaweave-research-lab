"""Main-executed actual owned native/PG recording/comparison/CLI source.

Uses synthetic offline responses, not provider-quality or causal evidence.
"""
from collections import Counter
from dataclasses import replace
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import sqlite3
import statistics
import subprocess
import sys
from uuid import uuid4

import pytest

from test_native_prepared_host import connection_factory, _snapshot, _close
from offline_fixture import prepared, offline_models

pytestmark = pytest.mark.postgres


def unavailable_models():
    """Actual child binding failure after owned attachment, never a fake store."""
    return {}


def install_offline_boundary(monkeypatch):
    for key in list(os.environ):
        upper = key.upper()
        if upper != "PROJECT_STORE_POSTGRES_TEST_DSN" and (
                any(w in upper for w in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))
                or upper.startswith(("LLM_", "OPENAI_", "DEEPSEEK_", "ZEP_", "ANTHROPIC_"))):
            monkeypatch.delenv(key, raising=False)
    for name in ("connect", "connect_ex"):
        original = getattr(socket.socket, name)
        def guarded(sock, address, original=original):
            if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1", "localhost"):
                raise AssertionError("external fixture socket attempted")
            return original(sock, address)
        monkeypatch.setattr(socket.socket, name, guarded)
    monkeypatch.setenv("MIROFISH_NATIVE_TEST_OFFLINE", "1")


@pytest.fixture(autouse=True)
def offline_boundary(monkeypatch):
    install_offline_boundary(monkeypatch)


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def actual_run(factory, parent, project, simulation, seed, *, fail=False):
    from app.services.native_prepared_host import NativePreparedHost
    from app.services.native_recording_contracts import RecordingAnchors
    from app.services.native_recordings import capture_recording
    from app.services.native_experiment_contracts import ExperimentMember, RecordingPin
    from mirofish_execution.native_owned_binding import NativeOwnedSessionFactory, _manifest
    from mirofish_execution.native_run_contracts import NativeRunRequest, RunState
    from mirofish_execution.native_run_store import NativeRunStore

    parent.mkdir()
    root = prepared(parent)
    for name in ("state.json", "simulation_config.json"):
        value = json.loads((root / name).read_bytes())
        value["simulation_id"] = simulation
        (root / name).write_text(json.dumps(value), encoding="utf-8")
    names = ("state.json", "simulation_config.json", "source_grounding.json", "twitter_profiles.csv", "reddit_profiles.json")
    request = NativeRunRequest.from_wire(dict(schema_version=1, principal="owner", project_id=project,
        project_revision=1, simulation_id=simulation, run_id=uuid4(), artifact_sha256=_manifest(root, names, 2 * 1024 * 1024),
        runtime_sha256="b" * 64, platforms=["twitter", "reddit"], seed=seed, max_rounds=1))
    binding = NativeOwnedSessionFactory(str(root), "graph-fixture", simulation, "owner", str(project), 1,
        ("twitter", "reddit"), seed, 1, "b" * 64, unavailable_models if fail else offline_models)
    host = NativePreparedHost(principal="owner", request=request, session_factory=binding,
        connection_factory=factory, dispatch_allowed=lambda _: True, lease_seconds=240,
        poll_seconds=0.2, call_budget_seconds=20, handshake_seconds=10, go_timeout_seconds=30, join_seconds=3)
    try:
        host.start(35)
        result = host.wait(180)
        assert result.run_state == (RunState.failed if fail else RunState.completed)
        saved = NativeRunStore(factory).get("owner", request.run_id)
        assert saved.receipt == result.receipt and saved.receipt is not None
    finally:
        _close(host)
    if fail:
        return ExperimentMember(simulation, "Failed owned fixture", "declared-case", request), root, None
    before = hashes(root)
    expected = {}
    for p in request.platforms:
        events = [json.loads(line) for line in (root / p / "actions.jsonl").read_bytes().splitlines()]
        actions = dict(Counter(e["action_type"] for e in events if "event_type" not in e))
        with sqlite3.connect(f"{(root / (p + '_simulation.db')).as_uri()}?mode=ro", uri=True) as conn:
            expected[p] = {"actions": actions, "total": sum(actions.values()),
                "tables": {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in ("post", "follow", "trace")}}
    anchors = RecordingAnchors("graph-fixture", simulation, str(request.run_id), "fixture-branch", str(project), 1)
    bundle = parent / "recording"
    revision = capture_recording(root, bundle, anchors=anchors, platforms=request.platforms,
        closed_run_confirmed=True, runtime_versions={"oasis": importlib.metadata.version("camel-oasis"),
            "camel": importlib.metadata.version("camel-ai")}, runtime_sha256=request.runtime_sha256)
    assert hashes(root) == before
    return ExperimentMember(simulation, "Owned fixture " + simulation, "declared-case", request,
        RecordingPin(str(bundle), revision, anchors)), root, expected


CHILD = r'''
import builtins, pathlib, runpy, socket, sys
cli = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(cli.parent))
forbidden = {"app", "flask", "camel", "oasis", "openai", "graphiti_core", "neo4j",
             "zep_cloud", "temporalio", "torch", "transformers", "dotenv"}
original_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split(".")[0] in forbidden:
        raise AssertionError("engine/provider/Temporal import attempted")
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded
for method in ("connect", "connect_ex"):
    original = getattr(socket.socket, method)
    def guard(self, address, original=original):
        if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError("external socket attempted")
        return original(self, address)
    setattr(socket.socket, method, guard)
import sqlite3
sqlite_opened, sqlite_closed = [], []
original_sqlite_connect = sqlite3.connect
class ObservedSQLite(sqlite3.Connection):
    def close(self):
        sqlite_closed.append(self)
        return super().close()
def observed_sqlite(*args, **kwargs):
    kwargs["factory"] = ObservedSQLite
    connection = original_sqlite_connect(*args, **kwargs)
    sqlite_opened.append(connection)
    return connection
sqlite3.connect = observed_sqlite
closed = []
def observe(frame, event, arg):
    if event == "call" and frame.f_code.co_name == "close":
        obj = frame.f_locals.get("self")
        if obj is not None and type(obj).__module__.startswith("psycopg"):
            closed.append(obj)
sys.setprofile(observe)
sys.argv = [str(cli), *sys.argv[2:]]
status = None
try:
    runpy.run_path(str(cli), run_name="__main__")
except SystemExit as error:
    status = error.code
finally:
    sys.setprofile(None)
assert not forbidden.intersection(n.split(".")[0] for n in sys.modules)
if status == 0:
    assert len(closed) >= 5 and all(conn.closed for conn in closed), "actual PG reads must close resources"
    assert len(sqlite_opened) >= 8 and sqlite_opened == sqlite_closed, "actual SQLite reads must close resources"
raise SystemExit(status)
'''


def cli(cohort, tmp_path, raw, *, principal="owner", pin=None):
    from app.services.native_experiment_contracts import canonical, sha
    path = tmp_path / ("manifest-" + uuid4().hex + ".json")
    data = canonical(cohort.wire())
    path.write_bytes(data)
    script = Path(__file__).resolve().parents[1] / "app" / "services" / "native_experiment_cli.py"
    allowed = {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "TMPDIR", "COMSPEC", "SYSTEMDRIVE", "PATHEXT", "LANG", "LC_ALL"}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env["MIROFISH_APPSTORE_DSN"] = os.environ["PROJECT_STORE_POSTGRES_TEST_DSN"]
    proc = subprocess.run([sys.executable, "-I", "-c", CHILD, str(script), "--principal", principal,
        "--manifest", str(path), "--manifest-sha256", pin or sha(data)], input=raw,
        capture_output=True, cwd=tmp_path, env=env, timeout=120, check=False)
    assert proc.stderr == b""
    assert path.read_bytes() == data
    assert len(proc.stdout) <= 512 * 1024 + 128
    lines = proc.stdout.splitlines()
    assert len(lines) == 1
    return proc.returncode, json.loads(lines[0])


@pytest.fixture(scope="module")
def cohort_fixture(connection_factory, tmp_path_factory):
    from app.services.native_experiment_contracts import ExperimentCohort
    from mirofish_storage import ProjectStore
    root = tmp_path_factory.mktemp("owned-experiments")
    project = uuid4()
    ProjectStore(connection_factory).create("owner", uuid4(), project, "proj_1", _snapshot())
    with pytest.MonkeyPatch.context() as monkeypatch:
        install_offline_boundary(monkeypatch)
        monkeypatch.chdir(root)
        runs = [actual_run(connection_factory, root / "a", project, "exp-a", 7),
                actual_run(connection_factory, root / "b", project, "exp-b", 11),
                actual_run(connection_factory, root / "f", project, "exp-f", 13, fail=True)]
    cohort = ExperimentCohort("owner", tuple(run[0] for run in runs))
    return cohort, runs, root


def test_actual_owned_successes_failed_member_and_independent_distributions(connection_factory, cohort_fixture):
    from app.services.native_experiment_contracts import canonical, sha
    from app.services.native_experiments import NativeExperimentComparator
    cohort, runs, _ = cohort_fixture
    before = [hashes(root) for _, root, _ in runs]
    result = NativeExperimentComparator(cohort=cohort, connection_factory=connection_factory).compare(
        canonical({"version": 1, "title": "Actual synthetic cohort", "member_ids": ["exp-b", "exp-f", "exp-a"]}))
    assert result["accounting"] == {"successful": 2, "failed": 1, "cancelled": 0, "pending": 0, "uncertain": 0}
    assert result["members"][1]["state"] == "failed" and result["members"][1]["metrics"] is None
    assert result["distinct_successful_seed_count"] == 2
    for group in result["distributions"]:
        p = group["platform"]
        values = [run[2][p]["total"] for run in runs[:2]]
        d = group["metrics"]["logged_action_total"]
        assert d == {"sample_count": 2, "missing_count": 1, "min": min(values), "max": max(values),
            "arithmetic_mean": statistics.mean(values), "median": statistics.median(values),
            "population_standard_deviation": statistics.pstdev(values)}
        for table in ("post", "follow", "trace"):
            values = [run[2][p]["tables"][table] for run in runs[:2]]
            assert group["metrics"]["final_table_counts"][table]["arithmetic_mean"] == statistics.mean(values)
    for index, source in ((0, runs[1]), (2, runs[0])):
        for p in ("twitter", "reddit"):
            assert result["members"][index]["metrics"][p]["logged_action_by_type"] == source[2][p]["actions"]
    assert before == [hashes(root) for _, root, _ in runs]
    assert result["causal_attribution_supported"] is False and result["actual_provider_spend"] is None
    digest = result.pop("result_digest")
    assert digest == sha(canonical(result))


def test_actual_fresh_saved_cli_reads_store_recordings_and_closes_resources(connection_factory, cohort_fixture, tmp_path):
    from app.services.native_experiment_contracts import canonical
    from app.services.native_experiments import NativeExperimentComparator
    cohort, runs, _ = cohort_fixture
    before = [hashes(root) for _, root, _ in runs]
    raw = canonical({"version": 1, "title": "Fresh CLI", "member_ids": [m.member_id for m in cohort.members]}) + b"\n"
    expected = NativeExperimentComparator(cohort=cohort, connection_factory=connection_factory).compare(raw)
    status, output = cli(cohort, tmp_path, raw)
    assert status == 0 and output == {"ok": True, "result": expected}
    assert before == [hashes(root) for _, root, _ in runs]
    status, output = cli(cohort, tmp_path, raw, principal="foreign")
    assert status == 2 and output == {"ok": False, "error": "invalid_binding"}
    status, output = cli(cohort, tmp_path, raw, pin="0" * 64)
    assert status == 2 and output == {"ok": False, "error": "invalid_binding"}
    status, output = cli(cohort, tmp_path, b'{}\n{}\n')
    assert status == 2 and output == {"ok": False, "error": "invalid_request"}


def test_actual_authority_rejects_cross_project_before_absent_recording(connection_factory, cohort_fixture, tmp_path):
    from app.services.native_experiment_contracts import ExperimentCohort, ExperimentError, canonical
    from app.services.native_experiments import NativeExperimentComparator
    from mirofish_storage import ProjectStore
    cohort, _, _ = cohort_fixture
    project = uuid4()
    ProjectStore(connection_factory).create("owner", uuid4(), project, "proj_1", _snapshot())
    m = cohort.members[0]
    wrong_request = replace(m.request, project_id=project)
    wrong_anchors = replace(m.recording.anchors, project_id=str(project))
    wrong = replace(m, request=wrong_request, recording=replace(m.recording,
        bundle=str(tmp_path / "absent-recording"), anchors=wrong_anchors))
    with pytest.raises(ExperimentError, match="authority_unavailable"):
        NativeExperimentComparator(cohort=ExperimentCohort("owner", (wrong,)), connection_factory=connection_factory).compare(
            canonical({"version": 1, "title": "Denied", "member_ids": [wrong.member_id]}))
    assert not (tmp_path / "absent-recording").exists()


def test_actual_prelaunch_cancel_intent_remains_declared_pending_and_read_only(connection_factory, cohort_fixture):
    from app.services.native_experiment_contracts import ExperimentCohort, ExperimentMember, canonical
    from app.services.native_experiments import NativeExperimentComparator
    from mirofish_execution.native_run_contracts import RunState
    from mirofish_execution.native_run_store import NativeRunStore
    cohort, _, _ = cohort_fixture
    request = replace(cohort.members[0].request, run_id=uuid4(), simulation_id="exp-cancel-" + uuid4().hex)
    store = NativeRunStore(connection_factory)
    declared = store.register(request)
    assert declared.state == RunState.declared and declared.cancel_requested is False
    saved = store.request_cancel("owner", request.run_id)
    assert saved.state == RunState.declared and saved.cancel_requested is True
    assert saved.receipt is None and saved.child is None and saved.attempt_id is None
    member = ExperimentMember("prelaunch-cancel", "Cancellation requested before launch", "declared-case", request)
    result = NativeExperimentComparator(cohort=ExperimentCohort("owner", (member,)),
        connection_factory=connection_factory).compare(canonical({"version": 1,
            "title": "Prelaunch cancellation intent", "member_ids": [member.member_id]}))
    item = result["members"][0]
    assert item["cancel_requested"] is True and item["state"] == "declared" and item["disposition"] == "pending"
    assert item["metrics"] is None and item["recording"] is None
    assert result["accounting"] == {"successful": 0, "failed": 0, "cancelled": 0, "pending": 1, "uncertain": 0}
    assert result["cancellation_intent"] == {"cancel_requested_count": 1, "overlaps_disposition_accounting": True}
    for group in result["distributions"]:
        metric = group["metrics"]["logged_action_total"]
        assert metric["sample_count"] == 0 and metric["missing_count"] == 1 and metric["arithmetic_mean"] is None
    after = store.get("owner", request.run_id)
    assert after == saved and after.receipt is None and after.child is None and after.attempt_id is None

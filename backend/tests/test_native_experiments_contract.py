"""Synthetic authority contracts; actual native/PG qualification is separate."""
from dataclasses import replace
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "knowledge" / "src"))
SERVICE = ROOT / "backend" / "app" / "services"
for name in ("native_recording_contracts", "native_recordings", "native_experiment_contracts", "native_experiments"):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, SERVICE / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)

from native_experiment_contracts import (ExperimentCohort, ExperimentMember, RecordingPin,
    ExperimentError, canonical, cohort_from_manifest, read_manifest, request_from_wire, sha)
from native_experiments import NativeExperimentComparator, distribution
import native_experiments as experiments
from native_recording_contracts import RecordingAnchors
from native_recordings import capture_recording
from mirofish_execution.native_run_contracts import NativeRunRequest, NativeRunReceipt, NativeChildIdentity, RunState
from mirofish_execution.native_run_store import NativeRunRecord
from test_native_recordings_contract import fixture_source, VERSIONS


def req(project=None, seed=7):
    return NativeRunRequest.from_wire(dict(schema_version=1, principal="owner",
        project_id=project or uuid4(), project_revision=1, simulation_id="s", run_id=uuid4(),
        artifact_sha256="a" * 64, runtime_sha256="1" * 64, platforms=["twitter", "reddit"], seed=seed, max_rounds=1))


def member(request=None, mid="one", pin=None, case="case"):
    return ExperimentMember(mid, "Synthetic " + str(mid), case, request or req(), pin)


def record(request, state="completed"):
    attempt, instance = uuid4(), uuid4()
    child = NativeChildIdentity(instance, 123, "c" * 64)
    receipt = None if state not in ("completed", "failed", "cancelled") else NativeRunReceipt(
        request.run_id, attempt, instance, request.fingerprint, state, "e" * 64)
    now = datetime.now(timezone.utc)
    return NativeRunRecord(request, request.fingerprint, RunState(state), False, attempt,
        uuid4(), None, child, receipt, now, now)


def wire(ids=("one",), **changes):
    return canonical(dict(version=1, title="Synthetic cohort", member_ids=list(ids), **changes))


def comparator(monkeypatch, cohort, records, **kwargs):
    class Store:
        def get(self, principal, run_id):
            assert principal == "owner"
            return records[run_id]
    monkeypatch.setattr(experiments, "NativeRunStore", lambda factory: Store())
    return NativeExperimentComparator(cohort=cohort, connection_factory=lambda: None, **kwargs)


def admitted(tmp_path, request, mid="one", case="case"):
    tmp_path.mkdir(exist_ok=True)
    root, _ = fixture_source(tmp_path)
    anchors = RecordingAnchors("g", "s", str(request.run_id), "branch", str(request.project_id), 1)
    bundle = tmp_path / "bundle"
    revision = capture_recording(root, bundle, anchors=anchors, platforms=request.platforms,
        closed_run_confirmed=True, runtime_versions=VERSIONS, runtime_sha256=request.runtime_sha256)
    return member(request, mid, RecordingPin(str(bundle), revision, anchors), case)


@pytest.mark.parametrize("raw", [b'{}', b'[]', b'NaN', b'Infinity', b'\xff', b'{"version":1,"version":1}',
    canonical({"version": True, "title": "T", "member_ids": ["one"]}),
    canonical({"version": 1, "title": "T", "member_ids": [True]}),
    canonical({"version": 1, "title": "T", "member_ids": []}),
    canonical({"version": 1, "title": "T", "member_ids": ["one", "one"]}),
    canonical({"version": 1, "title": "T", "member_ids": ["unknown"]}),
    canonical({"version": 1, "title": "T", "member_ids": ["one"], "path": "secret"}),
    b'x' * 8193], ids=["empty", "list", "nan", "infinite", "utf8", "duplicate-key", "bool-version",
        "bool-id", "no-selection", "duplicate-id", "unknown", "path", "oversize"])
def test_request_is_strict_selector_only(raw):
    cohort = ExperimentCohort("owner", (member(),))
    with pytest.raises(ExperimentError, match="invalid_request"):
        request_from_wire(raw, cohort)


def test_binding_scope_identity_and_manifest_roundtrip(tmp_path):
    one = member()
    cohort = ExperimentCohort("owner", (one,))
    raw = canonical(cohort.wire())
    path = tmp_path / "bindings.json"
    path.write_bytes(raw)
    assert cohort_from_manifest(read_manifest(str(path), sha(raw)), "owner") == cohort
    for changes in ((one, one), (one, member(req(), "two")), (replace(one, member_id="two"), one)):
        with pytest.raises(ExperimentError, match="invalid_binding"):
            ExperimentCohort("owner", changes)
    with pytest.raises(ExperimentError):
        ExperimentCohort("foreign", (one,))
    with pytest.raises(ExperimentError):
        ExperimentCohort("owner", (one, member(replace(one.request, run_id=uuid4(), project_revision=2), "two")))
    path.write_bytes(raw + b" ")
    with pytest.raises(ExperimentError):
        read_manifest(str(path), sha(raw))
    with pytest.raises(ExperimentError):
        read_manifest("relative.json", sha(raw))
    with pytest.raises(ExperimentError):
        cohort_from_manifest(b'{"version":1,"members":[],"dsn":"private"}', "owner")


@pytest.mark.parametrize("state", ["declared", "starting", "running", "uncertain", "failed", "cancelled"])
def test_non_successful_runs_never_touch_recordings_or_become_zero(monkeypatch, state):
    m = member()
    cohort = ExperimentCohort("owner", (m,))
    host = comparator(monkeypatch, cohort, {m.request.run_id: record(m.request, state)})
    monkeypatch.setattr(experiments, "NativeRecording", lambda *a, **k: pytest.fail("filesystem admission attempted"))
    result = host.compare(wire())
    assert result["accounting"]["successful"] == 0
    assert result["members"][0]["metrics"] is None
    for group in result["distributions"]:
        metric = group["metrics"]["logged_action_total"]
        assert metric["sample_count"] == 0 and metric["missing_count"] == 1 and metric["min"] is None


@pytest.mark.parametrize("state", ["declared", "running"])
def test_pending_cancellation_intent_is_explicit_without_terminal_samples(monkeypatch, state):
    m = member()
    saved = record(m.request, state)
    if state == "declared":
        saved = replace(saved, attempt_id=None, owner_id=None, child=None)
    records = {m.request.run_id: saved}
    host = comparator(monkeypatch, ExperimentCohort("owner", (m,)), records)
    monkeypatch.setattr(experiments, "NativeRecording", lambda *a, **k: pytest.fail("filesystem admission attempted"))
    ordinary = host.compare(wire())
    records[m.request.run_id] = replace(saved, cancel_requested=True)
    result = host.compare(wire())
    item = result["members"][0]
    assert item["cancel_requested"] is True and item["state"] == state and item["disposition"] == "pending"
    assert item["metrics"] is None and item["recording"] is None
    assert result["accounting"] == {"successful": 0, "failed": 0, "cancelled": 0, "pending": 1, "uncertain": 0}
    assert ordinary["members"][0]["cancel_requested"] is False
    assert item["record_digest"] != ordinary["members"][0]["record_digest"]
    assert experiments._record_value(records[m.request.run_id])["cancel_requested"] is True
    assert experiments._record_value(records[m.request.run_id])["receipt"] is None
    assert result["cancellation_intent"] == {"cancel_requested_count": 1, "overlaps_disposition_accounting": True}
    assert ordinary["cancellation_intent"]["cancel_requested_count"] == 0
    for group in result["distributions"]:
        metric = group["metrics"]["logged_action_total"]
        assert metric["sample_count"] == 0 and metric["missing_count"] == 1
        assert metric["arithmetic_mean"] is None and metric["min"] is None
    assert records[m.request.run_id].receipt is None


@pytest.mark.parametrize("kind", ["missing", "foreign", "fingerprint", "receipt", "no-receipt", "child"])
def test_all_authority_precedes_first_filesystem_admission(monkeypatch, kind):
    first = member()
    second = member(req(first.request.project_id), "two")
    records = {first.request.run_id: record(first.request), second.request.run_id: record(second.request)}
    bad = records[second.request.run_id]
    if kind == "missing":
        del records[second.request.run_id]
    elif kind == "foreign":
        records[second.request.run_id] = replace(bad, request=replace(second.request, principal="foreign"))
    elif kind == "fingerprint":
        records[second.request.run_id] = replace(bad, fingerprint="f" * 64)
    elif kind == "receipt":
        records[second.request.run_id] = replace(bad, receipt=replace(bad.receipt, outcome="failed"))
    elif kind == "no-receipt":
        records[second.request.run_id] = replace(bad, receipt=None)
    else:
        records[second.request.run_id] = replace(bad, child=None)
    host = comparator(monkeypatch, ExperimentCohort("owner", (first, second)), records)
    monkeypatch.setattr(experiments, "NativeRecording", lambda *a, **k: pytest.fail("premature filesystem admission"))
    with pytest.raises(ExperimentError, match="authority_unavailable"):
        host.compare(wire(("one", "two")))


def test_actual_recording_numeric_distribution_missing_tables_and_case_matrix(monkeypatch, tmp_path):
    a = req()
    b = req(a.project_id, seed=9)
    one = admitted(tmp_path / "a", a)
    two = admitted(tmp_path / "b", b, "two", "different-declaration")
    cohort = ExperimentCohort("owner", (one, two))
    host = comparator(monkeypatch, cohort, {a.run_id: record(a), b.run_id: record(b)})
    output = host.compare(wire(("two", "one")))
    assert [i["member_id"] for i in output["members"]] == ["two", "one"]
    assert output["distinct_successful_seed_count"] == 2
    for group in output["distributions"]:
        assert group["metrics"]["logged_action_total"]["arithmetic_mean"] == 2
        assert group["metrics"]["final_table_counts"]["like"]["sample_count"] == 0
        assert group["metrics"]["final_table_counts"]["like"]["missing_count"] == 1
        assert group["metrics"]["final_table_counts"]["post"]["arithmetic_mean"] == 1
    assert output["comparability_matrix"][0]["fields"]["seed"]["equal"] is False
    assert output["causal_attribution_supported"] is False
    assert "rows" not in str(output) and "Synthetic native" not in str(output)
    assert distribution([0, 2, 4], 4) == {"sample_count": 3, "missing_count": 1,
        "min": 0, "max": 4, "arithmetic_mean": 2, "median": 2,
        "population_standard_deviation": pytest.approx((8 / 3) ** 0.5)}


def test_recording_tamper_fails_whole_result(monkeypatch, tmp_path):
    m = admitted(tmp_path, req())
    host = comparator(monkeypatch, ExperimentCohort("owner", (m,)), {m.request.run_id: record(m.request)})
    (Path(m.recording.bundle) / "source_grounding.json").write_bytes(b"secret-path")
    with pytest.raises(ExperimentError, match="invalid_recording") as error:
        host.compare(wire())
    assert str(tmp_path) not in str(error.value)


def test_completed_requires_pin_and_runtime_agreement(monkeypatch, tmp_path):
    r = req()
    unpinned = member(r)
    host = comparator(monkeypatch, ExperimentCohort("owner", (unpinned,)), {r.run_id: record(r)})
    with pytest.raises(ExperimentError, match="invalid_binding"):
        host.compare(wire())
    pinned = admitted(tmp_path, r)
    wrong_request = replace(r, runtime_sha256="f" * 64)
    wrong = replace(pinned, request=wrong_request)
    host = comparator(monkeypatch, ExperimentCohort("owner", (wrong,)), {r.run_id: record(wrong_request)})
    with pytest.raises(ExperimentError, match="invalid_recording"):
        host.compare(wire())
    with pytest.raises(ExperimentError, match="invalid_binding"):
        replace(pinned, recording=replace(pinned.recording,
            anchors=replace(pinned.recording.anchors, run_id=str(uuid4()))))


def test_mixed_success_and_failure_counts_zero_and_unavailable_differently(monkeypatch, tmp_path):
    r = req()
    one = admitted(tmp_path, r)
    failed_request = req(r.project_id, 8)
    two = member(failed_request, "two")
    host = comparator(monkeypatch, ExperimentCohort("owner", (one, two)),
        {r.run_id: record(r), failed_request.run_id: record(failed_request, "failed")})
    result = host.compare(wire(("one", "two")))
    for group in result["distributions"]:
        post = group["metrics"]["final_table_counts"]["post"]
        absent = group["metrics"]["final_table_counts"]["like"]
        assert post["sample_count"] == 1 and post["missing_count"] == 1 and post["arithmetic_mean"] == 1
        assert absent["sample_count"] == 0 and absent["missing_count"] == 2 and absent["arithmetic_mean"] is None
    assert result["accounting"]["failed"] == 1


@pytest.mark.parametrize("raw", [b'NaN', b'{"version":true,"members":[]}',
    b'{"version":1,"members":[],"version":1}', b'x' * 65537],
    ids=["nonfinite", "bool-version", "duplicate-key", "oversize"])
def test_manifest_rejects_malformed_host_input(raw):
    with pytest.raises(ExperimentError, match="invalid_binding"):
        cohort_from_manifest(raw, "owner")


def test_member_and_cohort_bounds():
    r = req()
    with pytest.raises(ExperimentError):
        ExperimentCohort("owner", ())
    with pytest.raises(ExperimentError):
        ExperimentCohort("owner", tuple(member(replace(r, run_id=uuid4()), str(i)) for i in range(17)))
    for mid in (True, "../private", "x" * 129):
        with pytest.raises(ExperimentError):
            member(r, mid)
    for case in (False, "", "\n", "x" * 161):
        with pytest.raises(ExperimentError):
            member(r, case=case)


def test_authority_changed_after_recording_is_rejected(monkeypatch, tmp_path):
    m = admitted(tmp_path, req())
    saved = record(m.request)
    records = {m.request.run_id: saved}
    host = comparator(monkeypatch, ExperimentCohort("owner", (m,)), records)
    original = experiments.NativeRecording
    def admission(*a, **k):
        result = original(*a, **k)
        records[m.request.run_id] = replace(saved, receipt=replace(saved.receipt, evidence_sha256="f" * 64))
        return result
    monkeypatch.setattr(experiments, "NativeRecording", admission)
    with pytest.raises(ExperimentError, match="authority_unavailable"):
        host.compare(wire())


def test_cooperative_deadline_and_output_bound(monkeypatch):
    m = member()
    host = comparator(monkeypatch, ExperimentCohort("owner", (m,)), {m.request.run_id: record(m.request, "declared")})
    clock = iter([0, 0, 61])
    monkeypatch.setattr(experiments.time, "monotonic", lambda: next(clock))
    with pytest.raises(ExperimentError, match="limit_exceeded"):
        host.compare(wire())
    monkeypatch.setattr(experiments.time, "monotonic", lambda: 0)
    monkeypatch.setattr(experiments, "MAX_RESULT", 64)
    with pytest.raises(ExperimentError, match="limit_exceeded"):
        host.compare(wire())


@pytest.mark.parametrize("bound", [True, 0, -1, float("nan"), float("inf"), 121])
def test_deadline_must_be_finite_bounded_host_value(bound):
    with pytest.raises(ExperimentError, match="invalid_binding"):
        NativeExperimentComparator(cohort=ExperimentCohort("owner", (member(),)), connection_factory=lambda: None, time_limit_seconds=bound)

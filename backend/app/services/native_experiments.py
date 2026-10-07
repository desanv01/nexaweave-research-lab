"""Read-only owned cohort comparison; synchronous cooperative work bounds."""
from __future__ import annotations

from collections import Counter
import math
import statistics
import time

from nexaweave_execution.native_run_contracts import NativeRunReceipt, RunState
from nexaweave_execution.native_run_store import NativeRunStore

try:
    from .native_experiment_contracts import (ExperimentCohort, ExperimentError,
        MAX_RESULT, request_from_wire, sha)
    from .native_recording_contracts import canonical, strict_json
    from .native_recordings import NativeRecording, TABLES
except ImportError:
    from native_experiment_contracts import (ExperimentCohort, ExperimentError,
        MAX_RESULT, request_from_wire, sha)
    from native_recording_contracts import canonical, strict_json
    from native_recordings import NativeRecording, TABLES


def distribution(values, eligible):
    """Unavailable members never enter the arithmetic as zero."""
    n = len(values)
    return {"sample_count": n, "missing_count": eligible - n,
        "min": min(values) if n else None, "max": max(values) if n else None,
        "arithmetic_mean": statistics.mean(values) if n else None,
        "median": statistics.median(values) if n else None,
        "population_standard_deviation": statistics.pstdev(values) if n else None}


def _record_value(record):
    return {"request_fingerprint": record.fingerprint, "state": str(record.state),
        "cancel_requested": record.cancel_requested,
        "attempt_id": None if record.attempt_id is None else str(record.attempt_id),
        "child": None if record.child is None else {"instance_id": str(record.child.instance_id),
            "process_id": record.child.process_id, "process_fingerprint": record.child.process_fingerprint},
        "receipt": None if record.receipt is None else record.receipt.to_wire()}


def _authorized(store, principal, member):
    try:
        record = store.get(principal, member.request.run_id)
        if (record.request != member.request or record.fingerprint != member.request.fingerprint
                or record.state not in tuple(RunState)):
            raise ExperimentError("authority_unavailable")
        terminal = record.state in (RunState.completed, RunState.failed, RunState.cancelled)
        if terminal:
            receipt = NativeRunReceipt.from_wire(record.receipt)
            if (record.child is None or receipt.run_id != member.request.run_id
                    or receipt.attempt_id != record.attempt_id
                    or receipt.instance_id != record.child.instance_id
                    or receipt.request_fingerprint != member.request.fingerprint
                    or receipt.outcome != str(record.state)):
                raise ExperimentError("authority_unavailable")
        elif record.receipt is not None:
            raise ExperimentError("authority_unavailable")
        return record
    except Exception:
        raise ExperimentError("authority_unavailable") from None


class NativeExperimentComparator:
    def __init__(self, *, cohort, connection_factory, time_limit_seconds=60):
        if (type(cohort) is not ExperimentCohort or not callable(connection_factory)
                or type(time_limit_seconds) not in (int, float)
                or not math.isfinite(time_limit_seconds) or not 0 < time_limit_seconds <= 120):
            raise ExperimentError("invalid_binding")
        self._cohort = cohort
        self._store = NativeRunStore(connection_factory)
        self._seconds = time_limit_seconds

    def compare(self, raw):
        request = request_from_wire(raw, self._cohort)
        deadline = time.monotonic() + self._seconds
        def bounded():
            if time.monotonic() >= deadline:
                raise ExperimentError("limit_exceeded")
        lookup = {m.member_id: m for m in self._cohort.members}
        selected = [lookup[i] for i in request["member_ids"]]
        # Complete authority preflight for ALL members before any filesystem read.
        records = []
        for m in selected:
            bounded()
            records.append(_authorized(self._store, self._cohort.principal, m))
            bounded()
        output = []
        accounting = Counter(successful=0, failed=0, cancelled=0, pending=0, uncertain=0)
        for member, record in zip(selected, records):
            bounded()
            state = str(record.state)
            disposition = "successful" if state == "completed" else (
                state if state in ("failed", "cancelled", "uncertain") else "pending")
            accounting[disposition] += 1
            r = member.request
            item = {"member_id": member.member_id, "member_label": member.member_label,
                "case_label": member.case_label, "run_id": str(r.run_id), "state": state,
                "cancel_requested": record.cancel_requested,
                "disposition": disposition, "seed": r.seed, "max_rounds": r.max_rounds,
                "project_revision": r.project_revision, "runtime_sha256": r.runtime_sha256,
                "prepared_artifact_sha256": r.artifact_sha256, "platforms": list(r.platforms),
                "request_fingerprint": r.fingerprint, "record_digest": sha(canonical(_record_value(record))),
                "recording": None, "metrics": None}
            if disposition == "successful":
                pin = member.recording
                if pin is None:
                    raise ExperimentError("invalid_binding")
                try:
                    recording = NativeRecording(pin.bundle, anchors=pin.anchors, expected_revision=pin.revision)
                    bounded()
                    metadata = recording.describe()
                    if (metadata["anchors"] != pin.anchors.wire() or metadata["platforms"] != list(r.platforms)
                            or metadata["runtime_sha256"] != r.runtime_sha256
                            or metadata["recording_revision"] != pin.revision):
                        raise ExperimentError("invalid_recording")
                    metrics = {}
                    for platform in r.platforms:
                        bounded()
                        read = recording.read(canonical({"version": 1, "operation": "metrics", "platform": platform, "limit": 1}))
                        bounded()
                        if read["anchors"] != pin.anchors.wire() or read["recording_revision"] != pin.revision:
                            raise ExperimentError("invalid_recording")
                        actions = read["logged_action_counts"]
                        if len(actions) > 128:
                            raise ExperimentError("limit_exceeded")
                        metrics[platform] = {"logged_action_total": sum(actions.values()),
                            "logged_action_by_type": dict(actions),
                            "final_table_counts": {table: (read["final_native_tables"][table]["count"]
                                if read["final_native_tables"][table]["present"] else None) for table in TABLES}}
                        del read
                    item["recording"], item["metrics"] = metadata, metrics
                    del recording
                except ExperimentError:
                    raise
                except Exception:
                    raise ExperimentError("invalid_recording") from None
                after = _authorized(self._store, self._cohort.principal, member)
                if after != record:
                    raise ExperimentError("authority_unavailable")
                bounded()
            output.append(item)
        groups = []
        for case in dict.fromkeys(m.case_label for m in selected):
            cases = [i for i in output if i["case_label"] == case]
            for platform in ("twitter", "reddit"):
                eligible = [i for i in cases if platform in i["platforms"]]
                if not eligible:
                    continue
                completed = [i["metrics"][platform] for i in eligible if i["metrics"] is not None]
                actions = sorted({a for i in completed for a in i["logged_action_by_type"]})
                groups.append({"case_label": case, "platform": platform,
                    "member_count": len(eligible), "successful_count": len(completed),
                    "non_successful_count": len(eligible) - len(completed),
                    "distinct_declared_seed_count": len({i["seed"] for i in eligible}),
                    "distinct_successful_seed_count": len({i["seed"] for i in eligible if i["metrics"] is not None}),
                    "metrics": {"logged_action_total": distribution([i["logged_action_total"] for i in completed], len(eligible)),
                        "logged_action_by_type": {a: distribution([i["logged_action_by_type"].get(a, 0) for i in completed], len(eligible)) for a in actions},
                        "final_table_counts": {t: distribution([i["final_table_counts"][t] for i in completed if i["final_table_counts"][t] is not None], len(eligible)) for t in TABLES}}})
                bounded()
        matrix = []
        fields = ("seed", "max_rounds", "runtime_sha256", "platforms", "project_revision", "prepared_artifact_sha256")
        for index, left in enumerate(output):
            for right in output[index + 1:]:
                comparison = {f: {"left": left[f], "right": right[f], "equal": left[f] == right[f]} for f in fields}
                for f in ("artifact_sha256", "runtime_versions"):
                    l = None if left["recording"] is None else left["recording"][f]
                    r = None if right["recording"] is None else right["recording"][f]
                    comparison[f] = {"left": l, "right": r, "equal": None if l is None or r is None else l == r}
                matrix.append({"left_member_id": left["member_id"], "right_member_id": right["member_id"], "fields": comparison})
        result = {"version": 1, "title": request["title"], "project_id": str(selected[0].request.project_id),
            "project_revision": selected[0].request.project_revision, "cohort_manifest_digest": self._cohort.digest,
            "members": output, "accounting": dict(accounting), "distributions": groups,
            "cancellation_intent": {"cancel_requested_count": sum(i["cancel_requested"] for i in output),
                "overlaps_disposition_accounting": True},
            "distinct_declared_seed_count": len({m.request.seed for m in selected}),
            "distinct_successful_seed_count": len({i["seed"] for i in output if i["disposition"] == "successful"}),
            "comparability_matrix": matrix, "causal_attribution_supported": False,
            "provider_quality_assessed": False, "shared_budget_enforcement_supported": False,
            "actual_provider_spend": None, "ensemble_launch_supported": False,
            "coverage": {"atomic_cohort_snapshot": False, "statistics": "descriptive_completed_available_observations_only",
                "labels_prove_controlled_intervention": False, "hashes_prove_semantic_equivalence": False,
                "digests_are_signatures": False, "possible_initial_log_duplicates": True,
                "post_log_interviews_may_exist_in_trace": True, "exact_event_row_links": False,
                "historical_or_causal_truth": False, "missing_metrics_are_zero": False}}
        bounded()
        encoded = canonical(result)
        result["result_digest"] = sha(encoded)
        encoded = canonical(result)
        if len(encoded) > MAX_RESULT:
            raise ExperimentError("limit_exceeded")
        bounded()
        return strict_json(encoded, MAX_RESULT)

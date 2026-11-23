"""Compare an owned paired restore with an independently run native reference."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
import sqlite3
from time import monotonic
from datetime import datetime, timezone
from uuid import uuid4


_PLATFORMS = ("twitter", "reddit")
_PREPARED = (
    "state.json", "simulation_config.json", "source_grounding.json",
    "twitter_profiles.csv", "reddit_profiles.json",
)
_MIB = 1024 * 1024
_MEMBERS = {"manifest.json": 8 * _MIB}
for _platform in _PLATFORMS:
    _MEMBERS[f"{_platform}.sqlite3"] = 64 * _MIB
    _MEMBERS[f"{_platform}.jsonl"] = 8 * _MIB


def _contextual_model(platform):
    from test_native_prepared_workflow import _offline_model

    model = _offline_model()
    # Use the real CAMEL backend interface without a process-local response sequence.
    def reply(messages, response_format=None, tools=None):
        response = model._response(tools)
        model.calls = 0
        calls = response.choices[0].message.tool_calls
        if calls:
            call, = calls
            assert call.function.name == "create_post"
            prefix = f"Paired {platform} checkpoint post"
            observations = "\n".join(str(message.get("content", "")) for message in messages
                                     if message.get("role") in ("user", "tool"))
            seed_visible = int("Synthetic initial event" in observations)
            prior_visible = int(prefix in observations)
            memory_visible = int(any(
                message.get("role") == "assistant" and prefix in str(message.get("tool_calls", ""))
                for message in messages
            ))
            call.id = f"paired-{platform}-{len(messages)}"
            call.function.arguments = json.dumps({
                "content": f"{prefix} seed={seed_visible} prior={prior_visible} memory={memory_visible}",
            })
        return response

    async def reply_async(messages, response_format=None, tools=None):
        return reply(messages, response_format, tools)

    model._run = reply
    model._arun = reply_async
    return model


def _paired_models():
    return {platform: _contextual_model(platform) for platform in _PLATFORMS}


@dataclass
class _PairedFixtureFactory:
    """Picklable fixture binding around the existing authenticated native child."""

    prepared: object
    _session: object = field(default=None, init=False, repr=False)

    def validate(self, request):
        self.prepared.validate(request)

    def create_session(self, request):
        from app.services.native_paired_simulation import NativePairedCheckpointSession

        self.validate(request)
        prepared = self.prepared
        self._session = NativePairedCheckpointSession(
            prepared.simulation_dir, graph_id=prepared.graph_id,
            simulation_id=prepared.simulation_id, models=prepared.model_factory(),
            seed=prepared.seed, max_rounds=prepared.max_rounds,
            timeout_seconds=35, runtime_sha256=prepared.runtime_sha256,
            scope={"principal": request.principal, "project_id": str(request.project_id),
                   "project_revision": request.project_revision},
        )
        return self._session

    def evidence(self, request):
        self.validate(request)
        return self.prepared.evidence(request)


def _read_bundle(directory):
    from nexaweave_execution.native_owned_binding import _regular_bound_file

    assert {path.name for path in directory.iterdir()} == _MEMBERS.keys()
    return {name: _regular_bound_file(directory, name, limit) for name, limit in _MEMBERS.items()}


def _native_db(path):
    assert path.is_file() and not path.is_symlink()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        assert db.execute("PRAGMA quick_check(1)").fetchall() == [("ok",)]
        trace = db.execute(
            "SELECT rowid, user_id, action, info, created_at FROM trace ORDER BY rowid"
        ).fetchall()
        recommendations = db.execute("SELECT user_id, post_id FROM rec ORDER BY user_id, post_id").fetchall()
        return tuple(db.iterdump()), trace, recommendations


def _events(raw):
    events = []
    for line in raw.splitlines():
        event = json.loads(line)
        # Current logger timestamps record host wall time, independent of the engine clock.
        assert isinstance(event.pop("timestamp"), str)
        events.append(event)
    return events


def test_owned_paired_checkpoint_matches_uninterrupted_round(tmp_path, monkeypatch):
    from native_twitter_fresh_fixture import _run_child
    from nexaweave_execution.native_owned_binding import NativeOwnedSessionFactory, _manifest, _regular_bound_file
    from nexaweave_execution.native_paired_checkpoint_binding import NativePairedCheckpointFactory
    from nexaweave_execution.native_run_contracts import NativeRunRequest, RunState
    from nexaweave_execution.native_run_store import NativeRunRecord
    from test_native_prepared_workflow import _prepared
    from test_owned_native_process import terminal

    tmp_path = tmp_path.resolve()
    monkeypatch.chdir(tmp_path)
    control = _prepared(tmp_path)
    config_path = control / "simulation_config.json"
    config = json.loads(config_path.read_bytes())
    config["time_config"].update(total_simulation_hours=2, minutes_per_round=60,
                                 agents_per_hour_min=1, agents_per_hour_max=1)
    for actor in config["agent_configs"]:
        actor["active_hours"] = [0, 1] if actor["agent_id"] == 0 else [2]
    config_path.write_bytes(json.dumps(config).encode("utf-8"))
    originals = {name: (control / name).read_bytes() for name in _PREPARED}
    producer, restored, checkpoint = (tmp_path / name for name in ("producer", "restored", "checkpoint"))

    project = uuid4()
    artifact = _manifest(control, _PREPARED, 2 * _MIB)
    prepared = NativeOwnedSessionFactory(
        str(control), "graph-fixture", "sim-fixture", "owner", str(project), 1,
        _PLATFORMS, 7, 2, "b" * 64, _paired_models,
    )
    request = NativeRunRequest.from_wire(dict(
        schema_version=1, principal="owner", project_id=project, project_revision=1,
        simulation_id="sim-fixture", run_id=uuid4(), artifact_sha256=artifact,
        runtime_sha256=prepared.runtime_sha256, platforms=_PLATFORMS, seed=7, max_rounds=2,
    ))
    instances = set()
    deadline = monotonic() + 150
    _, control_evidence = _run_child(
        _PairedFixtureFactory(prepared), request, terminal, deadline, instances, require_closed=True,
    )
    assert control_evidence == prepared.evidence(request)

    capture_request = replace(request, run_id=uuid4())
    scope = {'project_id': str(project), 'layer': 'source'}
    captured = None

    def observe_capture(driver, req, attempt, child, stop):
        nonlocal captured
        result = terminal(driver, req, attempt, child, stop)
        captured = result.receipt
        return result

    producer_child, digest = _run_child(
        NativePairedCheckpointFactory(prepared, capture_request, str(checkpoint), str(producer), scope),
        capture_request, observe_capture, deadline, instances, require_closed=True,
    )
    assert captured is not None
    now = datetime.now(timezone.utc)
    parent = NativeRunRecord(capture_request, capture_request.fingerprint, RunState.completed,
                             False, captured.attempt_id, uuid4(), None, producer_child, captured, now, now)
    bundle = _read_bundle(checkpoint)
    assert sha256(bundle["manifest.json"]).hexdigest() == digest
    manifest = json.loads(bundle["manifest.json"])
    assert manifest["completed_round"] == 1
    assert set(manifest["runtime_state"]["platforms"]) == set(_PLATFORMS)
    assert manifest["members"] == {
        name: {"bytes": len(raw), "sha256": sha256(raw).hexdigest()}
        for name, raw in bundle.items() if name != "manifest.json"
    }
    outputs = tuple(name for platform in _PLATFORMS for name in (
        f"{platform}_simulation.db", f"{platform}/actions.jsonl",
    ))
    producer_outputs = {name: _regular_bound_file(producer, name, 64 * _MIB) for name in outputs}

    restore_request = replace(request, schema_version=5, run_id=uuid4(),
                              source_run_id=capture_request.run_id, checkpoint_sha256=digest)
    assert NativeRunRequest.from_wire(restore_request.to_wire()) == restore_request
    restore_binding = replace(prepared, simulation_dir=str(restored))
    _, restore_evidence = _run_child(
        NativePairedCheckpointFactory(prepared, restore_request, str(checkpoint), str(restored), scope, parent),
        restore_request, terminal, deadline, instances, require_closed=True,
    )
    assert len(instances) == 3 and restore_evidence == restore_binding.evidence(restore_request)
    assert _read_bundle(checkpoint) == bundle
    assert {name: _regular_bound_file(producer, name, 64 * _MIB) for name in outputs} == producer_outputs
    for root in (control, producer, restored):
        assert {name: (root / name).read_bytes() for name in _PREPARED} == originals

    for platform in _PLATFORMS:
        db_name = f"{platform}_simulation.db"
        log_name = f"{platform}/actions.jsonl"
        reference_db, reference_trace, recommendations = _native_db(control / db_name)
        assert _native_db(restored / db_name) == (reference_db, reference_trace, recommendations)
        cut_path = checkpoint / f"{platform}.sqlite3"
        cut_db = _native_db(cut_path)
        assert cut_db == _native_db(producer / db_name)
        assert not cut_path.samefile(restored / db_name)
        cut_trace = cut_db[1]
        cut_state = manifest["runtime_state"]["platforms"][platform]
        assert cut_trace and cut_state["cursor"] == cut_trace[-1][0]
        assert reference_trace[:len(cut_trace)] == cut_trace
        next_post, = [row for row in reference_trace[len(cut_trace):] if row[2] == "create_post"]

        prefix = bundle[f"{platform}.jsonl"]
        cut_events = _events(prefix)
        assert cut_events[-1]["event_type"] == "round_end" and cut_events[-1]["round"] == 1
        assert producer_outputs[log_name].startswith(prefix)
        restored_log = _regular_bound_file(restored, log_name, 8 * _MIB)
        assert restored_log.startswith(prefix)
        reference_events = _events(_regular_bound_file(control, log_name, 8 * _MIB))
        assert reference_events[:len(cut_events)] == cut_events
        assert _events(restored_log[len(prefix):]) == reference_events[len(cut_events):]
        next_event, = [event for event in reference_events[len(cut_events):]
                       if event.get("round") == 2 and event.get("action_type") == "CREATE_POST"]
        assert next_event["agent_id"] == next_post[1]
        post = json.loads(next_post[3])["content"]
        assert post.startswith(f"Paired {platform} checkpoint post") and post.endswith(" memory=1")

"""Private fixture for three sequential, independently owned Twitter children."""
from __future__ import annotations

import json
import os
import random
import struct
from dataclasses import dataclass, field, replace
from datetime import datetime
from hashlib import sha256
from itertools import islice
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4


_PREPARED = ("state.json", "simulation_config.json", "source_grounding.json", "twitter_profiles.csv")
_PROFILE_LIMIT = 2 * 1024 * 1024
_DB_LIMIT = 64 * 1024 * 1024
_LOG_LIMIT = 1024 * 1024
_RECEIPT_LIMIT = 7200
_RESTORE_OUTPUTS = ("native.sqlite3", "actions.jsonl")
_FIRST_POST = "Synthetic native post 1"
_NEXT_POST = "Synthetic native post 2"


def _scripted_models(consumed):
    from test_native_prepared_workflow import _offline_model

    model = _offline_model()
    model.calls = consumed
    offline_response = model._response

    def scripted_response(tools):
        # Only scripted tool responses advance the shared fixture sequence.
        before = model.calls
        try:
            return offline_response(tools)
        finally:
            if not tools:
                model.calls = before

    model._response = scripted_response
    return {"twitter": model}


def _producer_models():
    return _scripted_models(0)


@dataclass
class _RestoredModels:
    """Install a one-shot memory probe when the trusted factory runs after scrub."""

    expected_frames: tuple[tuple[int, str, int, str], ...]
    probe_path: str
    _probe_installed: bool = field(default=False, init=False, repr=False)

    def __call__(self):
        models = _scripted_models(1)
        if not self._probe_installed:
            self._install_probe()
            self._probe_installed = True
        return models

    def _install_probe(self):
        from app.services import native_twitter_restore
        from app.services.native_checkpoint_memory import NativeMemoryCaptureBudget, capture_native_memory

        genuine_advance = native_twitter_restore._advance_restored_twitter_round

        async def observe_advance(child_dir, *, manifest, runner_state, config_bytes, graph,
                                  expected_post_content, deadline_monotonic):
            try:
                assert expected_post_content is None
                assert len(graph.agent_mappings) == len(self.expected_frames) == 2
                budget = NativeMemoryCaptureBudget(
                    max_bytes=sum(length for _, _, length, _ in self.expected_frames),
                    deadline_monotonic=deadline_monotonic,
                )
                observed_frames = []
                for (platform_id, agent), expected in zip(
                    sorted(graph.get_agents()), self.expected_frames, strict=True,
                ):
                    _, camel_id, _, _ = expected
                    assert platform_id == agent.social_agent_id and agent.agent_id == camel_id
                    payload = capture_native_memory(agent.memory, expected_agent_id=camel_id, budget=budget)
                    observed = (platform_id, agent.agent_id, len(payload), sha256(payload).hexdigest())
                    del payload
                    assert observed == expected
                    observed_frames.append(observed)

                result = await genuine_advance(
                    child_dir, manifest=manifest, runner_state=runner_state, config_bytes=config_bytes,
                    graph=graph, expected_post_content=expected_post_content,
                    deadline_monotonic=deadline_monotonic,
                )
                budget.check()
                step_fields = ("agent_id", "post_id", "after_step")
                assert set(result) == {*step_fields, "post_content_sha256"}
                expected_digest = sha256(_json_bytes(_NEXT_POST)).hexdigest()
                assert result["post_content_sha256"] == expected_digest
                step = {field: result[field] for field in step_fields}
                assert all(type(value) is int for value in step.values())
                proof = _json_bytes({"pid": os.getpid(), "frames": observed_frames, "step": step})
                assert len(proof) <= _RECEIPT_LIMIT
                with Path(self.probe_path).open("xb") as output:
                    written = output.write(proof)
                assert written == len(proof)
                return result
            finally:
                native_twitter_restore._advance_restored_twitter_round = genuine_advance

        native_twitter_restore._advance_restored_twitter_round = observe_advance


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")


def _rng_sha256(state):
    return sha256(_json_bytes(state)).hexdigest()


@dataclass(frozen=True)
class _UninterruptedTwitterFactory:
    """Observe the ordinary native runner without a capture or restore option."""

    prepared: object
    observation_path: str

    def validate(self, *args, **kwargs):
        return self.prepared.validate(*args, **kwargs)

    def create_session(self, *args, **kwargs):
        from oasis.social_platform import platform as native_platform
        from test_native_round_capture import _native_rows, _observed_tick, _twitter_graph_topology

        session = self.prepared.create_session(*args, **kwargs)
        original_begin = session.begin_capture_round
        original_completed = session.capture_completed_round
        original_recommender = native_platform.rec_sys_random
        original_sample = random.sample
        observation_path = Path(self.observation_path)
        database = observation_path.parent / "twitter_simulation.db"
        rounds = []
        pending = None

        def observe_begin(result, round_num, active_agents, prior_cursor):
            nonlocal pending
            assert pending is None and round_num == len(rounds) + 1
            selected, = [platform_id for platform_id, _ in active_agents]
            pending = {
                "selection": selected, "before_step": _observed_tick(result.env.platform.sandbox_clock),
            }
            original_step = result.env.step

            def observe_recommendation(post_table, rec_matrix, capacity):
                assert "sampling" not in pending and len(rec_matrix) == 2 and capacity == 2
                candidates = [row["post_id"] for row in post_table]
                assert 2 <= len(candidates) <= 3
                draws = []

                def observe_sample(population, k):
                    assert list(population) == candidates and k == capacity and len(draws) < 2
                    draw = original_sample(population, k)
                    draws.append(draw)
                    return draw

                random.sample = observe_sample
                try:
                    recommendation = original_recommender(post_table, rec_matrix, capacity)
                finally:
                    random.sample = original_sample
                pending["sampling"] = {"candidates": candidates, "draws": draws}
                return recommendation

            async def observe_step(actions):
                native_platform.rec_sys_random = observe_recommendation
                try:
                    return await original_step(actions)
                finally:
                    native_platform.rec_sys_random = original_recommender
                    result.env.step = original_step

            result.env.step = observe_step
            return original_begin(result, round_num, active_agents, prior_cursor)

        async def observe_completed(result, round_num, *args, **kwargs):
            nonlocal pending
            await original_completed(result, round_num, *args, **kwargs)
            assert pending is not None and "sampling" in pending
            clock = result.env.platform.sandbox_clock
            agents = dict(result.env.agent_graph.get_agents())
            pending["after_step"] = _observed_tick(clock)
            pending["rng_sha256"] = _rng_sha256(random.getstate())
            pending["topology"] = _twitter_graph_topology(result.env.agent_graph, agents)
            pending["owners"] = [_live_memory(platform_id, agent)
                                 for platform_id, agent in sorted(agents.items())]
            assert 0 < database.stat().st_size <= _DB_LIMIT
            rows = _native_rows(database)
            pending["rows_sha256"] = sha256(_json_bytes(rows)).hexdigest()
            rounds.append(pending)
            pending = None
            if round_num == 2:
                raw = _json_bytes({"pid": os.getpid(), "rounds": rounds})
                assert 0 < len(raw) <= _RECEIPT_LIMIT
                with observation_path.open("xb") as output:
                    written = output.write(raw)
                assert written == len(raw)

        session.begin_capture_round = observe_begin
        session.capture_completed_round = observe_completed
        return session

    def evidence(self, *args, **kwargs):
        from test_native_round_capture import _read_bounded

        self.prepared.evidence(*args, **kwargs)
        return sha256(_read_bounded(Path(self.observation_path), _RECEIPT_LIMIT)).hexdigest()


def _live_memory(platform_id, agent):
    assert agent.social_agent_id == agent.env.action.agent_id == platform_id
    memory = agent.memory
    assert agent.agent_id == memory.agent_id
    block = memory._chat_history_block
    records = block.storage.memory_list
    assert len(records) <= 128
    return {
        "platform_id": platform_id, "camel_id": agent.agent_id,
        "token_limit": memory.get_context_creator().token_limit,
        "window_size": memory._window_size, "keep_rate": block.keep_rate,
        "records": [record["message"]["__class__"] for record in records],
    }


def _prepared_twitter(parent, original_request, max_rounds, model_factory):
    from app.services.native_owned_session import _manifest
    from test_native_prepared_workflow import _prepared
    from test_native_round_capture import _read_bounded

    parent.mkdir()
    root = _prepared(parent)
    state_path = root / "state.json"
    state = json.loads(_read_bounded(state_path, _RECEIPT_LIMIT))
    state["enable_reddit"] = False
    state_path.write_bytes(_json_bytes(state))
    config_path = root / "simulation_config.json"
    config = json.loads(_read_bounded(config_path, _RECEIPT_LIMIT))
    del config["reddit_config"]
    config["time_config"].update(total_simulation_hours=2, minutes_per_round=60,
                                 agents_per_hour_min=1, agents_per_hour_max=1)
    for actor in config["agent_configs"]:
        actor["active_hours"] = [0, 1]
    config["event_config"]["initial_posts"].append({
        "poster_agent_id": 1, "content": "Second synthetic initial event",
    })
    config_path.write_bytes(_json_bytes(config))
    raw = {name: _read_bounded(root / name, _PROFILE_LIMIT) for name in _PREPARED}
    artifact = _manifest(root, _PREPARED, _PROFILE_LIMIT)
    request = replace(original_request, run_id=uuid4(), artifact_sha256=artifact,
                      platforms=("twitter",), max_rounds=max_rounds)
    assert config["graph_id"] == "graph-fixture"
    factory = _owned_factory(root, request, model_factory)
    return root, raw, factory, request


def _owned_factory(root, request, model_factory):
    from app.services.native_owned_session import NativeOwnedSessionFactory

    return NativeOwnedSessionFactory(
        str(root), "graph-fixture", request.simulation_id, request.principal, str(request.project_id),
        request.project_revision, request.platforms, request.seed, request.max_rounds,
        request.runtime_sha256, model_factory,
    )


def _run_child(factory, request, terminal, deadline, prior_pids):
    from nexaweave_execution.native_process_driver import NativeProcessDriver

    remaining = deadline - monotonic()
    assert remaining > 0
    driver = NativeProcessDriver(factory, handshake_seconds=min(10, remaining),
                                 go_timeout_seconds=30, join_seconds=3)
    attempt = uuid4()
    try:
        child = driver.launch(request, attempt)
        assert type(child.process_id) is int and child.process_id > 0
        assert child.process_id not in prior_pids
        observed = terminal(driver, request, attempt, child, deadline)
        assert observed.status == "completed" and observed.receipt is not None
        receipt = observed.receipt
        assert receipt.outcome == "completed" and receipt.run_id == request.run_id
        assert receipt.attempt_id == attempt and receipt.instance_id == child.instance_id
        assert receipt.request_fingerprint == request.fingerprint
    finally:
        driver.close()
    assert monotonic() < deadline
    return child.process_id, receipt.evidence_sha256


def _captured_checkpoint(directory, lineage, config_bytes):
    from test_native_round_capture import _artifact, _read_bounded

    database = directory / "native.sqlite3"
    memory = directory / "native-memory.bin"
    runner = directory / "native-runner.bin"
    entries = set(islice(directory.iterdir(), 6))
    assert len(entries) == 5 and {database, memory, runner} <= entries
    manifest_path, = (entry for entry in entries if entry.suffix == ".json")
    log, = entries - {database, memory, runner, manifest_path}
    limits = {database: _DB_LIMIT, memory: _PROFILE_LIMIT, runner: 2564,
              manifest_path: _RECEIPT_LIMIT, log: _LOG_LIMIT}
    pins = {path: _artifact(path, limit) for path, limit in limits.items()}
    manifest_raw = _read_bounded(manifest_path, _RECEIPT_LIMIT)
    assert sha256(manifest_raw).hexdigest() == lineage["manifest_sha256"]
    manifest = json.loads(manifest_raw)
    assert manifest["anchors"] == lineage["anchors"]
    assert manifest["round"] == 1 and manifest["continuation_supported"] is False
    assert pins[database][:2] == (manifest["database_bytes"], manifest["database_sha256"])
    assert pins[log][:2] == (manifest["log_bytes"], manifest["log_sha256"])
    for path, field in ((memory, "memory"), (runner, "runner_state")):
        assert pins[path][:2] == (manifest[field]["bytes"], manifest[field]["sha256"])

    raw = _read_bounded(runner, 2564)
    magic, version, completed, horizon, minutes, offset, actions, config_sha = struct.unpack_from(
        ">4sB5I32s", raw,
    )
    assert magic == b"NWR1" and version == 3 and len(raw) == 2564
    assert (completed, horizon, minutes, offset) == (1, 2, 60, 0)
    assert config_sha == sha256(config_bytes).digest()
    assert lineage["runner"] == {
        "completed_round": completed, "horizon": horizon, "minutes_per_round": minutes,
        "simulated_minutes": offset, "total_actions": actions,
    }
    words = struct.unpack_from(">624I", raw, 57)
    index, cache_tag, cache = struct.unpack_from(">HBd", raw, 2553)
    assert index <= 624 and cache_tag in (0, 1) and (cache_tag or raw[-8:] == bytes(8))
    rng_at_cut = (version, (*words, index), cache if cache_tag else None)
    return database, memory, log, manifest, rng_at_cut, limits, pins


def _captured_owners(path, expected_owners):
    from test_native_round_capture import _decimal, _json_without_duplicate_keys

    size = path.stat().st_size
    assert 0 < size <= _PROFILE_LIMIT
    with path.open("rb") as stream:
        magic_and_count = stream.read(7)
        assert magic_and_count == b"NWM1\n2\n"
        for owner in expected_owners:
            header = stream.readline(33)
            assert len(header) <= 32 and header.endswith(b"\n")
            platform_id, count = map(_decimal, header[:-1].split(b" "))
            assert platform_id == owner["platform_id"] and 0 < count <= size - stream.tell()
            raw = stream.read(count)
            assert len(raw) == count
            payload = _json_without_duplicate_keys(raw)
            assert payload["agent_id"] == owner["camel_id"]
            assert payload["token_limit"] == owner["token_limit"]
            owner_summary = {
                "platform_id": platform_id, "camel_id": payload["agent_id"],
                "token_limit": payload["token_limit"], "window_size": payload["window_size"],
                "keep_rate": payload["keep_rate"],
                "records": [record["message"]["__class__"] for record in payload["records"]],
            }
            yield owner_summary, (platform_id, owner["camel_id"], count, sha256(raw).hexdigest())
        assert stream.tell() == size and not stream.read(1)


def _events(raw):
    assert raw.endswith(b"\n")
    lines = raw.splitlines()
    assert 0 < len(lines) <= 128
    events = []
    for line in lines:
        event = json.loads(line)
        wall_time = event.pop("timestamp")
        assert type(wall_time) is str and len(wall_time) <= 32
        assert datetime.fromisoformat(wall_time).tzinfo is None
        events.append(event)
    return events


def _check_binding(lineage, producer_pid, anchors, originals, artifact_sha256):
    assert set(lineage) == {"version", "anchors", "producer_pid", "manifest_sha256", "prepared",
                            "owners", "runner"}
    assert type(lineage["version"]) is int and lineage["version"] == 1
    assert lineage["anchors"] == anchors and lineage["producer_pid"] == producer_pid
    assert lineage["prepared"] == {
        "artifact_sha256": artifact_sha256,
        "config_sha256": sha256(originals["simulation_config.json"]).hexdigest(),
        "profile_sha256": sha256(originals["twitter_profiles.csv"]).hexdigest(),
        "source_grounding_sha256": sha256(originals["source_grounding.json"]).hexdigest(),
    }
    config = json.loads(originals["simulation_config.json"])
    source = json.loads(originals["source_grounding.json"])
    assert source == {actor["entity_uuid"]: {"source_entity_uuid": actor["entity_uuid"]}
                      for actor in config["agent_configs"]}
    owner_ids = set()
    for owner, actor in zip(lineage["owners"], config["agent_configs"], strict=True):
        assert set(owner) == {"platform_id", "name", "camel_id", "token_limit"}
        assert owner["platform_id"] == actor["agent_id"] and owner["name"] == actor["entity_name"]
        assert str(UUID(owner["camel_id"])) == owner["camel_id"]
        assert type(owner["token_limit"]) is int and owner["token_limit"] > 0
        owner_ids.add(owner["camel_id"])
    assert len(owner_ids) == 2


def _check_reference(reference, checkpoint_rows, captured_owners, manifest, rng_at_cut):
    first, second = reference
    cursor, trace, posts, recommendations = checkpoint_rows
    assert cursor == manifest["cursor"]
    assert first["rows_sha256"] == sha256(_json_bytes(checkpoint_rows)).hexdigest()
    assert first["rng_sha256"] == _rng_sha256(rng_at_cut)
    assert first["topology"] == second["topology"] == manifest["topology"]
    captured_ids = {owner["camel_id"] for owner in captured_owners}
    independent_ids = {owner["camel_id"] for owner in first["owners"]}
    assert len(independent_ids) == 2 and captured_ids.isdisjoint(independent_ids)
    clock = manifest["engine_clock"]
    assert (first["before_step"], first["after_step"]) == (clock["before_step"], clock["after_step"])
    assert second["before_step"] == first["after_step"]
    assert second["after_step"] == second["before_step"] + 1
    for captured, independent, final in zip(captured_owners, first["owners"], second["owners"], strict=True):
        independent_id = independent["camel_id"]
        assert str(UUID(independent_id)) == independent_id != captured["camel_id"]
        assert independent_id == final["camel_id"]
        assert {key: value for key, value in captured.items() if key != "camel_id"} == {
            key: value for key, value in independent.items() if key != "camel_id"
        }
        assert ("FunctionCallingMessage" in captured["records"]) is (
            captured["platform_id"] == first["selection"]
        )
        for field in ("platform_id", "token_limit", "window_size", "keep_rate"):
            assert final[field] == independent[field]

    setup_posts = [post_id for post_id, _, _, _ in posts[:2]]
    assert first["sampling"] == {"candidates": setup_posts, "draws": []}
    assert recommendations == [(owner, post_id) for owner in (0, 1) for post_id in setup_posts]
    (first_post_id, first_owner, first_content, first_tick), = posts[2:]
    assert (first_owner, first_content, first_tick) == (first["selection"], _FIRST_POST, first["before_step"])
    assert first_post_id > setup_posts[-1]
    assert any(user == first_owner and action == "create_post" and created_at == first_tick
               and json.loads(info).get("post_id") == first_post_id
               for _, user, action, info, created_at in trace)

    # Independent clone of the observed first cut. Never set the reference's live RNG.
    oracle = random.Random(0)
    oracle.setstate(rng_at_cut)
    oracle.uniform(1, 1)
    for _ in (0, 1):
        oracle.random()
    expected_owner, = oracle.sample((0, 1), 1)
    post_ids = [post_id for post_id, *_ in posts]
    assert len(post_ids) == 3 > 2
    draws = [oracle.sample(post_ids, 2) for _ in (0, 1)]
    assert second["selection"] == expected_owner
    assert second["sampling"] == {"candidates": post_ids, "draws": draws}
    return sorted((owner, post_id) for owner, draw in enumerate(draws) for post_id in draw)


def assert_fresh_twitter_continuation(tmp_path, request, original_pid, terminal, deadline):
    from app.services.native_owned_session import _manifest
    from nexaweave_execution.native_twitter_checkpoint_binding import (
        NativeRestoredTwitterFactory, NativeTwitterCaptureFactory,
    )
    from test_native_round_capture import _artifact, _native_rows, _read_bounded

    producer, originals, prepared, capture_request = _prepared_twitter(
        tmp_path / "fresh-producer", request, 1, _producer_models,
    )
    checkpoint = producer.parent / "fresh-checkpoint"
    lineage_dir = tmp_path / "fresh-lineage"
    lineage_path = lineage_dir / "lineage.json"
    restore_dir = tmp_path / "fresh-restored"
    memory_probe_path = tmp_path / "fresh-restored-memory-probe.json"
    anchors = {
        "graph_id": "graph-fixture", "simulation_id": capture_request.simulation_id,
        "run_id": str(capture_request.run_id), "branch_id": str(uuid4()),
        "project_id": str(capture_request.project_id), "project_revision": capture_request.project_revision,
    }
    prior_pids = {os.getpid(), original_pid}
    capture_factory = NativeTwitterCaptureFactory(prepared, str(checkpoint), str(lineage_dir), anchors)
    producer_pid, producer_evidence = _run_child(capture_factory, capture_request, terminal, deadline, prior_pids)
    prior_pids.add(producer_pid)
    assert list(islice(lineage_dir.iterdir(), 2)) == [lineage_path]
    lineage_raw = _read_bounded(lineage_path, _RECEIPT_LIMIT)
    assert sha256(lineage_raw).hexdigest() == producer_evidence
    lineage = json.loads(lineage_raw)
    _check_binding(lineage, producer_pid, anchors, originals, capture_request.artifact_sha256)
    database, memory, log, manifest, rng_at_cut, limits, pins = _captured_checkpoint(
        checkpoint, lineage, originals["simulation_config.json"],
    )
    checkpoint_rows = _native_rows(database)
    producer_db = producer / "twitter_simulation.db"
    producer_log = producer / "twitter" / "actions.jsonl"
    assert 0 < producer_db.stat().st_size <= _DB_LIMIT
    assert _native_rows(producer_db) == checkpoint_rows
    prefix = _read_bounded(log, _LOG_LIMIT)
    original_log = _read_bounded(producer_log, _LOG_LIMIT)
    assert original_log.startswith(prefix)
    prefix_events = _events(prefix)
    assert prefix_events[-1]["event_type"] == "round_end" and prefix_events[-1]["round"] == 1
    end, = _events(original_log[len(prefix):])
    assert end == {"event_type": "simulation_end", "platform": "twitter", "total_rounds": 1,
                   "total_actions": lineage["runner"]["total_actions"]}
    pins.update({path: _artifact(path, limit) for path, limit in (
        (lineage_path, _RECEIPT_LIMIT), (producer_db, _DB_LIMIT), (producer_log, _LOG_LIMIT),
    )})
    limits.update({lineage_path: _RECEIPT_LIMIT, producer_db: _DB_LIMIT, producer_log: _LOG_LIMIT})
    captured_owners, expected_frames = zip(*_captured_owners(memory, lineage["owners"]), strict=True)

    reference, reference_raw, reference_prepared, reference_request = _prepared_twitter(
        tmp_path / "fresh-reference", request, 2, _producer_models,
    )
    assert reference_raw == originals and reference_request.artifact_sha256 == capture_request.artifact_sha256
    observation_path = reference / "reference_observation.json"
    reference_factory = _UninterruptedTwitterFactory(reference_prepared, str(observation_path))
    reference_pid, reference_evidence = _run_child(
        reference_factory, reference_request, terminal, deadline, prior_pids,
    )
    prior_pids.add(reference_pid)
    observation_raw = _read_bounded(observation_path, _RECEIPT_LIMIT)
    assert sha256(observation_raw).hexdigest() == reference_evidence
    observation = json.loads(observation_raw)
    assert observation["pid"] == reference_pid
    expected_recommendations = _check_reference(
        observation["rounds"], checkpoint_rows, captured_owners, manifest, rng_at_cut,
    )
    reference_db = reference / "twitter_simulation.db"
    reference_log = reference / "twitter" / "actions.jsonl"
    assert 0 < reference_db.stat().st_size <= _DB_LIMIT
    reference_rows = _native_rows(reference_db)
    assert observation["rounds"][1]["rows_sha256"] == sha256(_json_bytes(reference_rows)).hexdigest()
    reference_events = _events(_read_bounded(reference_log, _LOG_LIMIT))
    assert reference_events[:len(prefix_events)] == prefix_events

    restore_request = replace(capture_request, run_id=uuid4())
    restore_models = _RestoredModels(expected_frames, str(memory_probe_path))
    restore_prepared = _owned_factory(producer, restore_request, restore_models)
    restore_factory = NativeRestoredTwitterFactory(
        restore_prepared, str(checkpoint), str(lineage_dir), str(restore_dir),
        producer_evidence, producer_pid, str(capture_request.run_id), None,
    )
    assert not restore_dir.exists() and not memory_probe_path.exists()
    restored_pid, restore_evidence = _run_child(restore_factory, restore_request, terminal, deadline, prior_pids)
    proof_raw = _read_bounded(memory_probe_path, _RECEIPT_LIMIT)
    proof = json.loads(proof_raw)
    assert set(proof) == {"pid", "frames", "step"} and proof["pid"] == restored_pid
    assert proof["frames"] == [list(frame) for frame in expected_frames]
    assert {path.name for path in islice(restore_dir.iterdir(), 3)} == set(_RESTORE_OUTPUTS)
    assert _manifest(restore_dir, _RESTORE_OUTPUTS, _DB_LIMIT) == restore_evidence
    child_db = restore_dir / "native.sqlite3"
    assert not child_db.samefile(database) and not child_db.samefile(producer_db)
    assert not child_db.samefile(reference_db)
    assert 0 < child_db.stat().st_size <= _DB_LIMIT
    staged_bytes = sum(value[0] for value in pins.values()) + len(proof_raw)
    assert staged_bytes + reference_db.stat().st_size + child_db.stat().st_size <= 160 * 1024 * 1024
    child_rows = _native_rows(child_db)
    assert child_rows == reference_rows
    _, old_trace, old_posts, _ = checkpoint_rows
    cursor, trace, posts, recommendations = child_rows
    assert trace[:len(old_trace)] == old_trace and posts[:len(old_posts)] == old_posts
    assert recommendations == expected_recommendations
    (post_id, owner, content, tick), = posts[len(old_posts):]
    final = observation["rounds"][1]
    assert (owner, content, tick) == (final["selection"], _NEXT_POST, final["before_step"])
    assert type(tick) is int and tick + 1 == final["after_step"]
    assert proof["step"] == {"agent_id": owner, "post_id": post_id, "after_step": tick + 1}
    added_trace = trace[len(old_trace):]
    assert cursor == added_trace[-1][0] and all(row[1] == owner for row in added_trace)
    (_, _, _, info, trace_tick), = (row for row in added_trace if row[2] == "create_post")
    assert trace_tick == tick and json.loads(info)["content"] == content
    assert json.loads(info)["post_id"] == post_id

    promoted = _read_bounded(restore_dir / "actions.jsonl", _LOG_LIMIT)
    assert promoted.startswith(prefix)
    promoted_events = _events(promoted[len(prefix):])
    assert promoted_events == [event for event in reference_events if event.get("round") == 2]
    first, action, last = promoted_events
    assert first == {"round": 2, "event_type": "round_start", "simulated_hour": 1}
    assert action["action_type"] == "CREATE_POST" and action["agent_id"] == owner and action["success"] is True
    assert action["action_args"]["content"] == content and action["action_args"]["post_id"] == post_id
    assert last == {"round": 2, "event_type": "round_end", "actions_count": 1}

    assert {path: _artifact(path, limits[path]) for path in pins} == pins
    for root in (producer, reference):
        assert (root / ".native_prepared_start_claim").is_file()
        assert not (root / "reddit_simulation.db").exists()
        assert {name: _read_bounded(root / name, _PROFILE_LIMIT) for name in _PREPARED} == originals
    assert monotonic() < deadline

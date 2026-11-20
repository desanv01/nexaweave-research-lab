"""Private fixture for independently owned Twitter capture, reference, restore and fork."""
from __future__ import annotations

import asyncio
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


def _checkpoint_post(pid, frames):
    content = f'Scripted connected checkpoint post {pid}'
    if frames is not None:
        wire = json.dumps(frames, ensure_ascii=True, separators=(',', ':')).encode('ascii')
        content += ' context ' + sha256(wire).hexdigest()
    return content


_connected_restored_frames = None


def _restored_wait_marker(child_dir):
    return child_dir.with_name(child_dir.name + '.fixture-waiting')


def _gated_checkpoint_twitter_backends(*, timeout_seconds, max_tokens, max_retries):
    return _checkpoint_twitter_backends(timeout_seconds=timeout_seconds, max_tokens=max_tokens,
                                        max_retries=max_retries, hold_restored_advance=True)


def _scripted_checkpoint_twitter_backends(*, timeout_seconds, max_tokens, max_retries):
    return _checkpoint_twitter_backends(timeout_seconds=timeout_seconds, max_tokens=max_tokens,
                                        max_retries=max_retries, hold_restored_advance=False)


def _checkpoint_twitter_backends(*, timeout_seconds, max_tokens, max_retries, hold_restored_advance):
    """Offline tool replies attest to the real installed histories in the restoring child."""
    from app.services import native_twitter_restore
    from app.services.native_checkpoint_memory import NativeMemoryCaptureBudget, capture_native_memory
    from test_durable_native_launch import scripted_native_backends

    advance = native_twitter_restore._advance_restored_twitter_round
    if not getattr(advance, '_connected_fixture_probe', False):
        async def observe_advance(child_dir, **kwargs):
            global _connected_restored_frames
            assert _connected_restored_frames is None
            graph = kwargs['graph']
            budget = NativeMemoryCaptureBudget(max_bytes=2 * 1024 * 1024,
                                               deadline_monotonic=kwargs['deadline_monotonic'])
            assert len(graph.agent_mappings) == 2
            frames = []
            for platform_id, agent in sorted(graph.get_agents()):
                assert platform_id == agent.social_agent_id
                raw = capture_native_memory(agent.memory, expected_agent_id=agent.agent_id, budget=budget)
                frames.append((platform_id, agent.agent_id, len(raw), sha256(raw).hexdigest()))
                del raw
            _connected_restored_frames = tuple(frames)
            try:
                if hold_restored_advance:
                    deadline = kwargs['deadline_monotonic']
                    assert monotonic() < deadline
                    with _restored_wait_marker(child_dir).open('xb') as marker:
                        marker.write(f'{os.getpid()}\n'.encode('ascii'))
                    await asyncio.sleep(max(0, deadline - monotonic()))
                    raise AssertionError('restored owner was not cancelled before the native action deadline')
                return await advance(child_dir, **kwargs)
            finally:
                _connected_restored_frames = None
                native_twitter_restore._advance_restored_twitter_round = advance

        observe_advance._connected_fixture_probe = True
        native_twitter_restore._advance_restored_twitter_round = observe_advance

    model = scripted_native_backends(timeout_seconds=timeout_seconds, max_tokens=max_tokens,
                                     max_retries=max_retries)['twitter']
    genuine_reply = model.reply

    def reply(tools):
        result = genuine_reply(tools)
        calls = result.choices[0].message.tool_calls
        if calls:
            call, = calls
            assert call.function.name == 'create_post'
            call.function.arguments = json.dumps({'content': _checkpoint_post(os.getpid(), _connected_restored_frames)})
        return result

    model.reply = reply
    return {'twitter': model}


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
                                  expected_post_content, deadline_monotonic, intervention=None):
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
                    **({"intervention": intervention} if intervention is not None else {}),
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


_FORK_VALIDATION_SITES = {
    329: 'request_binding', 330: 'prepared_request', 336: 'producer_authority',
    337: 'prepared_root', 338: 'checkpoint_directory', 339: 'lineage_directory',
    340: 'fork_directory', 341: 'directory_overlap', 343: 'lineage_read',
    345: 'lineage_digest', 346: 'recording_import', 348: 'lineage_decode',
    352: 'lineage_authority', 353: 'lineage_anchors', 354: 'lineage_body',
    357: 'fork_manifest',
}


@dataclass
class _ObservedForkFactory:
    factory: object
    marker: Path
    first_validation: bool = True

    def _mark(self, value, mode):
        try:
            with self.marker.open(mode) as stream:
                stream.write(value.encode('ascii'))
        except OSError:
            pass

    def _rejection_site(self, error):
        binding_file = self.factory.validate.__func__.__code__.co_filename
        trace = error.__traceback__
        site = 'unknown'
        for _ in range(32):
            if trace is None:
                break
            code = trace.tb_frame.f_code
            if code.co_filename == binding_file:
                if code.co_name == '_checked_restore':
                    site = _FORK_VALIDATION_SITES.get(trace.tb_lineno, 'restore_other')
                elif code.co_name == 'validate' and trace.tb_lineno == 363:
                    site = 'prepared_files'
            trace = trace.tb_next
        return site

    def validate(self, request):
        if not self.first_validation:
            return self.factory.validate(request)
        self.first_validation = False
        same_pid = int(os.getpid() == self.factory.expected_producer_pid)
        self._mark(f'entered/{same_pid}\n', 'xb')
        try:
            result = self.factory.validate(request)
        except Exception as error:
            try:
                self._mark(f'rejected/{self._rejection_site(error)}\n', 'ab')
            except Exception:
                pass
            raise
        self._mark('returned\n', 'ab')
        return result

    def create_session(self, request):
        return self.factory.create_session(request)

    def evidence(self, request):
        return self.factory.evidence(request)

    def observation(self):
        try:
            with self.marker.open('rb') as stream:
                lines = stream.read(96).splitlines()
        except OSError:
            return 'unobserved'
        if len(lines) not in (1, 2) or lines[0] not in (b'entered/0', b'entered/1'):
            return 'invalid'
        allowed = (*_FORK_VALIDATION_SITES.values(), 'restore_other', 'prepared_files', 'unknown')
        if len(lines) == 2 and lines[1] != b'returned' and lines[1] not in {
            f'rejected/{site}'.encode('ascii') for site in allowed
        }:
            return 'invalid'
        return ' '.join(line.decode('ascii') for line in lines)


def _run_child(factory, request, terminal, deadline, prior_pids, *, require_closed=False):
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
        closed = driver.close()
    if require_closed:
        assert closed is True
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
    from nexaweave_execution.native_run_contracts import SwapActiveActorV1
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

    fork_dir = tmp_path / "fresh-fork"
    fork_probe_path = tmp_path / "fresh-fork-memory-probe.json"
    assert not fork_dir.exists() and not fork_probe_path.exists()
    fork_request = replace(
        capture_request, run_id=uuid4(), schema_version=3, source_run_id=capture_request.run_id,
        checkpoint_sha256=producer_evidence,
        intervention=SwapActiveActorV1("swap_active_actor_v1", lineage["manifest_sha256"],
                                       manifest["runner_state"]["sha256"]),
    )
    fork_prepared = _owned_factory(producer, fork_request, _RestoredModels(expected_frames, str(fork_probe_path)))
    fork_factory = NativeRestoredTwitterFactory(
        fork_prepared, str(checkpoint), str(lineage_dir), str(fork_dir),
        producer_evidence, producer_pid, str(capture_request.run_id), None,
    )
    prior_pids.add(restored_pid)
    observed_factory = _ObservedForkFactory(fork_factory, tmp_path / 'fresh-fork-validation.stage')
    try:
        fork_pid, fork_evidence = _run_child(
            observed_factory, fork_request, terminal, deadline, prior_pids, require_closed=True,
        )
    except Exception as error:
        error.add_note(f'fork_validation={observed_factory.observation()}')
        raise
    assert observed_factory.observation() == 'entered/0 returned'
    fork_proof_raw = _read_bounded(fork_probe_path, _RECEIPT_LIMIT)
    fork_db = fork_dir / "native.sqlite3"
    assert {path.name for path in islice(fork_dir.iterdir(), 3)} == set(_RESTORE_OUTPUTS)
    assert _manifest(fork_dir, _RESTORE_OUTPUTS, _DB_LIMIT) == fork_evidence
    assert all(not fork_db.samefile(path) for path in (database, producer_db, reference_db, child_db))
    assert 0 < fork_db.stat().st_size <= _DB_LIMIT
    fork_log = _read_bounded(fork_dir / "actions.jsonl", _LOG_LIMIT)
    assert (staged_bytes + reference_db.stat().st_size + child_db.stat().st_size + fork_db.stat().st_size
            + len(promoted) + len(fork_log) + len(fork_proof_raw)) <= 160 * 1024 * 1024

    fork_owner = 1 - final["selection"]
    fork_cursor, fork_trace, fork_posts, fork_recommendations = _native_rows(fork_db)
    assert fork_trace[:len(old_trace)] == old_trace and fork_posts[:len(old_posts)] == old_posts
    assert fork_recommendations == expected_recommendations
    assert fork_posts[len(old_posts):] == [(post_id, fork_owner, _NEXT_POST, final["before_step"])]
    fork_added_trace = fork_trace[len(old_trace):]
    assert fork_cursor == fork_added_trace[-1][0] and all(row[1] == fork_owner for row in fork_added_trace)
    (_, _, _, fork_info, fork_tick), = (row for row in fork_added_trace if row[2] == "create_post")
    assert fork_tick == tick and json.loads(fork_info)["post_id"] == post_id
    assert json.loads(fork_info)["content"] == _NEXT_POST
    assert json.loads(fork_proof_raw) == {
        "pid": fork_pid, "frames": [list(frame) for frame in expected_frames],
        "step": {"agent_id": fork_owner, "post_id": post_id, "after_step": final["after_step"]},
    }
    assert fork_log.startswith(prefix)
    fork_action = dict(action, agent_id=fork_owner, agent_name=lineage["owners"][fork_owner]["name"])
    assert _events(fork_log[len(prefix):]) == [first, fork_action, last]
    assert _manifest(restore_dir, _RESTORE_OUTPUTS, _DB_LIMIT) == restore_evidence

    assert {path: _artifact(path, limits[path]) for path in pins} == pins
    for root in (producer, reference):
        assert (root / ".native_prepared_start_claim").is_file()
        assert not (root / "reddit_simulation.db").exists()
        assert {name: _read_bounded(root / name, _PROFILE_LIMIT) for name in _PREPARED} == originals
    assert monotonic() < deadline

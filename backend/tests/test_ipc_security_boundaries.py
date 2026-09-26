"""File transport security tests; no agent process or model calls."""

import json
import os
import uuid

import pytest

from app.services import simulation_ipc as ipc
from app.services.simulation_ipc import (
    CommandStatus, CommandType, IPCCommand, IPCResponse,
    SimulationIPCClient, SimulationIPCServer,
)
from app.utils.safe_paths import InvalidResourcePath


def _link(link, target, is_directory=False):
    try:
        link.symlink_to(target, target_is_directory=is_directory)
    except (OSError, NotImplementedError) as exc:
        if os.name == "nt":
            pytest.skip(f"Windows link privilege unavailable: {exc}")
        raise


def _mixed_nesting(wrappers):
    value = []
    for index in range(wrappers):
        value = {"next": value} if index % 2 else [value]
    return value


@pytest.mark.parametrize("command_id", [
    "../escape", r"..\escape", "/absolute", r"C:\absolute", "CON",
    "Lpt9.json", "a:b", "a ", "a" * 124, None, 3,
])
def test_command_ids_rejected_before_file_access(tmp_path, command_id):
    server = SimulationIPCServer(str(tmp_path))
    with pytest.raises((InvalidResourcePath, ValueError, TypeError)):
        server.send_success(command_id, {"ok": True})
    assert list((tmp_path / "ipc_responses").iterdir()) == []


@pytest.mark.parametrize("payload", [
    [], "text", None,
    {"command_id": "cmd_valid", "command_type": "unknown", "args": {}},
    {"command_id": "cmd_valid", "command_type": "interview", "args": []},
    {"command_id": "CON", "command_type": "interview", "args": {}},
])
def test_invalid_command_shape_and_enum_rejected(payload):
    with pytest.raises((InvalidResourcePath, ValueError, KeyError, TypeError)):
        IPCCommand.from_dict(payload)


@pytest.mark.parametrize("payload", [
    [], "text", None,
    {"command_id": "cmd_valid", "status": "unknown"},
    {"command_id": "cmd_valid", "status": {"value": "completed"}},
    {"command_id": "cmd_valid", "status": "completed", "result": []},
])
def test_invalid_response_shape_and_status_rejected(payload):
    with pytest.raises((InvalidResourcePath, ValueError, KeyError, TypeError)):
        IPCResponse.from_dict(payload)


def test_filename_payload_id_mismatch_does_not_starve_valid_command(tmp_path):
    server = SimulationIPCServer(str(tmp_path))
    folder = tmp_path / "ipc_commands"
    bad = folder / "cmd_bad.json"
    bad.write_text(json.dumps({
        "command_id": "cmd_other", "command_type": "interview", "args": {},
    }), encoding="utf-8")
    good = folder / "cmd_good.json"
    good.write_text(json.dumps({
        "command_id": "cmd_good", "command_type": "batch_interview",
        "args": {"interviews": [{"agent_id": 4, "prompt": "你怎么看？"}]},
    }, ensure_ascii=False), encoding="utf-8")
    os.utime(bad, (1, 1))
    command = server.poll_commands()
    assert command.command_id == "cmd_good"
    assert command.args["interviews"][0]["prompt"] == "你怎么看？"
    assert bad.exists()


def test_mismatched_command_cannot_trigger_response_or_deletion(tmp_path):
    server = SimulationIPCServer(str(tmp_path))
    command_file = tmp_path / "ipc_commands" / "cmd_bad.json"
    command_file.write_text(json.dumps({
        "command_id": "cmd_other", "command_type": "close_env", "args": {},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="mismatch"):
        server.send_success("cmd_bad", {"ok": True})
    assert command_file.exists()
    assert list((tmp_path / "ipc_responses").iterdir()) == []


def test_corrupt_and_oversized_candidates_do_not_starve_valid_command(tmp_path):
    server = SimulationIPCServer(str(tmp_path))
    folder = tmp_path / "ipc_commands"
    (folder / "cmd_corrupt.json").write_text("{", encoding="utf-8")
    (folder / "cmd_large.json").write_bytes(b"x" * (ipc.MAX_IPC_MESSAGE_BYTES + 1))
    (folder / "cmd_valid.json").write_text(json.dumps({
        "command_id": "cmd_valid", "command_type": "close_env", "args": {},
    }), encoding="utf-8")
    assert server.poll_commands().command_id == "cmd_valid"
    assert (folder / "cmd_corrupt.json").exists()
    assert (folder / "cmd_large.json").exists()


def test_deep_json_candidate_does_not_end_command_poll(tmp_path):
    server = SimulationIPCServer(str(tmp_path))
    folder = tmp_path / "ipc_commands"
    nested = "[" * 1100 + "0" + "]" * 1100
    (folder / "cmd_deep.json").write_text(
        '{"command_id":"cmd_deep","command_type":"close_env","args":{"nested":'
        + nested + '}}', encoding="utf-8",
    )
    (folder / "cmd_valid.json").write_text(json.dumps({
        "command_id": "cmd_valid", "command_type": "close_env", "args": {},
    }), encoding="utf-8")
    assert server.poll_commands().command_id == "cmd_valid"
    assert (folder / "cmd_deep.json").exists()


def test_mixed_json_depth_boundary_for_inbound_command(tmp_path):
    server = SimulationIPCServer(str(tmp_path))
    folder = tmp_path / "ipc_commands"
    # Message root is depth 1, args is depth 2, nested [] is depth 3.
    # 61 wrappers reach depth 64; 62 wrappers reach depth 65.
    (folder / "cmd_too_deep.json").write_text(json.dumps({
        "command_id": "cmd_too_deep", "command_type": "close_env",
        "args": {"nested": _mixed_nesting(62)},
    }), encoding="utf-8")
    (folder / "cmd_boundary.json").write_text(json.dumps({
        "command_id": "cmd_boundary", "command_type": "close_env",
        "args": {"nested": _mixed_nesting(61)},
    }), encoding="utf-8")
    os.utime(folder / "cmd_too_deep.json", (1, 1))
    assert server.poll_commands().command_id == "cmd_boundary"
    assert (folder / "cmd_too_deep.json").exists()


def test_fifo_candidate_is_refused_without_blocking_later_command(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("Host has no FIFO support")
    server = SimulationIPCServer(str(tmp_path))
    folder = tmp_path / "ipc_commands"
    fifo = folder / "cmd_fifo.json"
    try:
        os.mkfifo(fifo)
    except OSError as exc:
        if os.name == "nt":
            pytest.skip(f"Windows FIFO unavailable: {exc}")
        raise
    (folder / "cmd_valid.json").write_text(json.dumps({
        "command_id": "cmd_valid", "command_type": "close_env", "args": {},
    }), encoding="utf-8")
    assert server.poll_commands().command_id == "cmd_valid"
    assert fifo.exists()


@pytest.mark.parametrize("kind", ["single", "batch"])
def test_unicode_client_server_round_trip(tmp_path, monkeypatch, kind):
    client = SimulationIPCClient(str(tmp_path))
    server = SimulationIPCServer(str(tmp_path))
    seen = []

    def answer(_interval):
        command = server.poll_commands()
        seen.append(command)
        server.send_success(command.command_id, {"answer": "你好 🌊"})

    monkeypatch.setattr(ipc.time, "sleep", answer)
    if kind == "single":
        response = client.send_interview(4, "你怎么看？", timeout=2)
        assert seen[0].command_type is CommandType.INTERVIEW
    else:
        response = client.send_batch_interview(
            [{"agent_id": 4, "prompt": "你怎么看？"}], timeout=2,
        )
        assert seen[0].command_type is CommandType.BATCH_INTERVIEW
    assert response.status is CommandStatus.COMPLETED
    assert response.result == {"answer": "你好 🌊"}
    assert list((tmp_path / "ipc_commands").iterdir()) == []
    assert list((tmp_path / "ipc_responses").iterdir()) == []


def test_wrong_response_id_is_not_accepted_or_deleted(tmp_path, monkeypatch):
    client = SimulationIPCClient(str(tmp_path))
    fixed_id = uuid.UUID(int=1)
    monkeypatch.setattr(ipc.uuid, "uuid4", lambda: fixed_id)
    response_path = tmp_path / "ipc_responses" / f"{fixed_id}.json"
    ticks = iter([0.0, 0.0, 0.005, 0.01, 0.015, 0.02])
    monkeypatch.setattr(ipc.time, "monotonic", lambda: next(ticks))

    def wrong_answer(_interval):
        response_path.write_text(json.dumps({
            "command_id": "cmd_other", "status": "completed", "result": {},
        }), encoding="utf-8")

    monkeypatch.setattr(ipc.time, "sleep", wrong_answer)
    with pytest.raises(TimeoutError):
        client.send_close_env(timeout=0.02)
    assert response_path.exists()
    assert list((tmp_path / "ipc_commands").iterdir()) == []


def test_malformed_response_does_not_end_poll_loop(tmp_path, monkeypatch):
    client = SimulationIPCClient(str(tmp_path))
    fixed_id = uuid.UUID(int=3)
    monkeypatch.setattr(ipc.uuid, "uuid4", lambda: fixed_id)
    response_path = tmp_path / "ipc_responses" / f"{fixed_id}.json"
    writes = []

    def answer(_interval):
        writes.append(True)
        response_path.write_text(json.dumps({
            "command_id": str(fixed_id),
            "status": {"invalid": "object"} if len(writes) == 1 else "completed",
            "result": {"ok": True},
        }), encoding="utf-8")

    monkeypatch.setattr(ipc.time, "sleep", answer)
    response = client.send_close_env(timeout=2)
    assert response.status is CommandStatus.COMPLETED
    assert len(writes) == 2
    assert not response_path.exists()


def test_deep_response_candidate_can_be_followed_by_valid_response(tmp_path, monkeypatch):
    client = SimulationIPCClient(str(tmp_path))
    fixed_id = uuid.UUID(int=4)
    monkeypatch.setattr(ipc.uuid, "uuid4", lambda: fixed_id)
    response_path = tmp_path / "ipc_responses" / f"{fixed_id}.json"
    writes = []

    def answer(_interval):
        writes.append(True)
        response_path.write_text(json.dumps({
            "command_id": str(fixed_id), "status": "completed",
            "result": {"nested": _mixed_nesting(62)} if len(writes) == 1 else {"ok": True},
        }), encoding="utf-8")

    monkeypatch.setattr(ipc.time, "sleep", answer)
    response = client.send_close_env(timeout=2)
    assert response.result == {"ok": True}
    assert len(writes) == 2


def test_outbound_unicode_size_limit_precedes_command_write(tmp_path, monkeypatch):
    monkeypatch.setattr(ipc, "MAX_IPC_MESSAGE_BYTES", 100)
    client = SimulationIPCClient(str(tmp_path))
    with pytest.raises(ValueError, match="size limit"):
        client.send_interview(4, "🌊" * 100, timeout=1)
    assert list((tmp_path / "ipc_commands").iterdir()) == []


def test_deep_outbound_value_is_stable_validation_error(tmp_path):
    client = SimulationIPCClient(str(tmp_path))
    nested = []
    for _ in range(1100):
        nested = [nested]
    with pytest.raises(ValueError, match="Invalid IPC message"):
        client.send_command(CommandType.CLOSE_ENV, {"nested": nested}, timeout=1)
    assert list((tmp_path / "ipc_commands").iterdir()) == []


def test_mixed_json_depth_boundary_for_outbound_command(tmp_path):
    client = SimulationIPCClient(str(tmp_path))
    with pytest.raises(TimeoutError):
        client.send_command(
            CommandType.CLOSE_ENV, {"nested": _mixed_nesting(61)}, timeout=0,
        )
    with pytest.raises(ValueError, match="Invalid IPC message"):
        client.send_command(
            CommandType.CLOSE_ENV, {"nested": _mixed_nesting(62)}, timeout=0,
        )
    assert list((tmp_path / "ipc_commands").iterdir()) == []


def test_tuple_arrays_follow_outbound_json_depth_limit(tmp_path):
    client = SimulationIPCClient(str(tmp_path))
    nested = ()
    for _ in range(62):
        nested = (nested,)
    with pytest.raises(ValueError, match="Invalid IPC message"):
        client.send_command(CommandType.CLOSE_ENV, {"nested": nested}, timeout=0)
    assert list((tmp_path / "ipc_commands").iterdir()) == []


def test_atomic_replace_failure_preserves_existing_target(tmp_path, monkeypatch):
    server = SimulationIPCServer(str(tmp_path))
    target = tmp_path / "ipc_responses" / "cmd_valid.json"
    target.write_text("old response", encoding="utf-8")

    def fail_replace(_source, _target):
        assert _source.endswith(".tmp")
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(ipc.os, "replace", fail_replace)
    with pytest.raises(OSError):
        server.send_success("cmd_valid", {"ok": True})
    assert target.read_text(encoding="utf-8") == "old response"
    assert list((tmp_path / "ipc_responses").iterdir()) == [target]


def test_command_temp_is_invisible_to_poller_before_replace(tmp_path, monkeypatch):
    server = SimulationIPCServer(str(tmp_path))
    client = SimulationIPCClient(str(tmp_path))
    original_replace = ipc.os.replace
    observed = []

    def inspect_before_replace(source, target):
        if "ipc_commands" in source:
            observed.append(server.poll_commands())
            assert source.endswith(".tmp")
        return original_replace(source, target)

    monkeypatch.setattr(ipc.os, "replace", inspect_before_replace)
    with pytest.raises(TimeoutError):
        client.send_close_env(timeout=0)
    assert observed == [None]


def test_linked_ipc_dir_and_root_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_root = tmp_path / "linked_root"
    _link(linked_root, outside, is_directory=True)
    with pytest.raises(InvalidResourcePath):
        SimulationIPCClient(str(linked_root))

    simulation = tmp_path / "simulation"
    simulation.mkdir()
    _link(simulation / "ipc_commands", outside, is_directory=True)
    with pytest.raises(InvalidResourcePath):
        SimulationIPCServer(str(simulation))


def test_linked_command_and_response_targets_preserve_outside(tmp_path):
    server = SimulationIPCServer(str(tmp_path))
    outside = tmp_path / "outside.json"
    outside.write_text("sentinel", encoding="utf-8")
    _link(tmp_path / "ipc_commands" / "cmd_valid.json", outside)
    with pytest.raises(InvalidResourcePath):
        server.send_success("cmd_valid", {"ok": True})
    assert outside.read_text(encoding="utf-8") == "sentinel"
    assert (tmp_path / "ipc_commands" / "cmd_valid.json").is_symlink()
    assert list((tmp_path / "ipc_responses").iterdir()) == []

    _link(tmp_path / "ipc_responses" / "cmd_linked.json", outside)
    with pytest.raises(InvalidResourcePath):
        server.send_success("cmd_linked", {"ok": True})
    assert outside.read_text(encoding="utf-8") == "sentinel"


def test_client_never_deletes_linked_response_target(tmp_path, monkeypatch):
    client = SimulationIPCClient(str(tmp_path))
    outside = tmp_path / "sentinel.json"
    outside.write_text("sentinel", encoding="utf-8")
    fixed_id = uuid.UUID(int=2)
    response_path = tmp_path / "ipc_responses" / f"{fixed_id}.json"
    _link(response_path, outside)
    monkeypatch.setattr(ipc.uuid, "uuid4", lambda: fixed_id)
    ticks = iter([0.0, 0.0, 0.005, 0.01])
    monkeypatch.setattr(ipc.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(ipc.time, "sleep", lambda _interval: None)
    with pytest.raises(TimeoutError):
        client.send_close_env(timeout=0.01)
    assert outside.read_text(encoding="utf-8") == "sentinel"
    assert response_path.is_symlink()
    assert list((tmp_path / "ipc_commands").iterdir()) == []


@pytest.mark.parametrize("timeout,poll", [
    (-1, 0.1), (float("nan"), 0.1), (float("inf"), 0.1),
    (1, 0), (1, -1), (1, float("nan")), (1, float("inf")),
])
def test_invalid_poll_timing_before_message_write(tmp_path, timeout, poll):
    client = SimulationIPCClient(str(tmp_path))
    with pytest.raises(ValueError):
        client.send_command(CommandType.CLOSE_ENV, {}, timeout, poll)
    assert list((tmp_path / "ipc_commands").iterdir()) == []


def test_zero_timeout_and_monotonic_deadline(tmp_path, monkeypatch):
    client = SimulationIPCClient(str(tmp_path))
    sleeps = []
    monkeypatch.setattr(ipc.time, "sleep", lambda seconds: sleeps.append(seconds))
    with pytest.raises(TimeoutError):
        client.send_close_env(timeout=0)
    assert sleeps == []
    assert list((tmp_path / "ipc_commands").iterdir()) == []

    ticks = iter([0.0, 0.0, 0.25, 0.5, 0.75, 1.0, 1.0])
    monkeypatch.setattr(ipc.time, "monotonic", lambda: next(ticks))
    with pytest.raises(TimeoutError):
        client.send_command(CommandType.CLOSE_ENV, {}, timeout=1, poll_interval=0.8)
    assert all(0 < seconds <= 0.8 for seconds in sleeps)


def test_env_status_is_bounded_and_atomic(tmp_path, monkeypatch):
    server = SimulationIPCServer(str(tmp_path))
    client = SimulationIPCClient(str(tmp_path))
    server.start()
    assert client.check_env_alive() is True
    server.stop()
    assert client.check_env_alive() is False
    (tmp_path / "env_status.json").write_text("[]", encoding="utf-8")
    assert client.check_env_alive() is False

    outside = tmp_path / "outside.json"
    outside.write_text("sentinel", encoding="utf-8")
    (tmp_path / "env_status.json").unlink()
    _link(tmp_path / "env_status.json", outside)
    with pytest.raises(InvalidResourcePath):
        server.start()
    assert outside.read_text(encoding="utf-8") == "sentinel"

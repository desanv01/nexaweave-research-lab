"""Actual script handler file transport tests, not native interview qualification.

The scripts mutate builtins or import native OASIS/CAMEL engines at module
import. Extract each complete IPC handler class plus its simple CommandType
constant class, then execute those unmodified class definitions with the real
shared IPC transport. No interview methods or simulation loops are executed.
"""

import ast
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from app.services import simulation_ipc as ipc_module
from app.services.simulation_ipc import (
    CommandStatus, IPCResponse, SimulationIPCClient, SimulationIPCServer,
)
from app.utils.safe_paths import InvalidResourcePath


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
HANDLERS = (
    ("run_twitter_simulation.py", "IPCHandler"),
    ("run_reddit_simulation.py", "IPCHandler"),
    ("run_parallel_simulation.py", "ParallelIPCHandler"),
)


def _handler_class(script_name, class_name):
    path = SCRIPTS / script_name
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes = [node for node in tree.body
             if isinstance(node, ast.ClassDef)
             and node.name in {"CommandType", class_name}]
    assert {node.name for node in nodes} == {"CommandType", class_name}
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    namespace = {
        "__name__": f"native_ipc_{script_name}",
        "Any": Any, "Dict": Dict, "List": List, "Optional": Optional,
        "Tuple": Tuple, "os": os, "datetime": datetime, "json": json,
        # Real transport implementations, not copied or stubbed methods.
        "SimulationIPCServer": SimulationIPCServer,
        "IPCResponse": IPCResponse,
        "CommandStatus": CommandStatus,
        "IPC_COMMANDS_DIR": "ipc_commands",
        "IPC_RESPONSES_DIR": "ipc_responses",
        "ENV_STATUS_FILE": "env_status.json",
    }
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[class_name]


def _handler(script_name, class_name, simulation_dir, *, twitter=True, reddit=True):
    handler_class = _handler_class(script_name, class_name)
    if class_name == "ParallelIPCHandler":
        return handler_class(
            str(simulation_dir), twitter_env=object() if twitter else None,
            twitter_agent_graph=object() if twitter else None,
            reddit_env=object() if reddit else None,
            reddit_agent_graph=object() if reddit else None,
        )
    return handler_class(str(simulation_dir), env=object(), agent_graph=object())


def _link(link, target, is_directory=False):
    try:
        link.symlink_to(target, target_is_directory=is_directory)
    except (OSError, NotImplementedError) as exc:
        if os.name == "nt":
            pytest.skip(f"Windows symlink privilege unavailable: {exc}")
        raise


@pytest.mark.parametrize("script_name,class_name", HANDLERS)
@pytest.mark.parametrize("kind", ("single", "batch", "close"))
def test_actual_handler_unicode_wire_round_trip(tmp_path, monkeypatch, script_name, class_name, kind):
    handler = _handler(script_name, class_name, tmp_path)
    client = SimulationIPCClient(str(tmp_path))
    observed = []

    def answer(_interval):
        command = handler.poll_command()
        assert isinstance(command, dict)
        observed.append(command)
        handler.send_response(command["command_id"], "completed", {"answer": "你好 🌊"})

    monkeypatch.setattr(ipc_module.time, "sleep", answer)
    if kind == "single":
        response = client.send_interview(4, "你怎么看？", platform="twitter", timeout=2)
        assert observed[0]["command_type"] == "interview"
        assert observed[0]["args"] == {"agent_id": 4, "prompt": "你怎么看？", "platform": "twitter"}
    elif kind == "batch":
        interviews = [{"agent_id": 4, "prompt": "你怎么看？", "platform": "reddit"}]
        response = client.send_batch_interview(interviews, platform="twitter", timeout=2)
        assert observed[0]["command_type"] == "batch_interview"
        assert observed[0]["args"] == {"interviews": interviews, "platform": "twitter"}
    else:
        response = client.send_close_env(timeout=2)
        assert observed[0]["command_type"] == "close_env"
        assert observed[0]["args"] == {}
    assert response.status is CommandStatus.COMPLETED
    assert response.result == {"answer": "你好 🌊"}
    assert handler.commands_dir == str(tmp_path / "ipc_commands")
    assert handler.responses_dir == str(tmp_path / "ipc_responses")
    assert handler.status_file == str(tmp_path / "env_status.json")
    assert list((tmp_path / "ipc_commands").iterdir()) == []
    assert list((tmp_path / "ipc_responses").iterdir()) == []


@pytest.mark.parametrize("script_name,class_name", HANDLERS)
def test_handler_skips_malformed_and_mismatched_commands(tmp_path, script_name, class_name):
    handler = _handler(script_name, class_name, tmp_path)
    folder = tmp_path / "ipc_commands"
    rejected_names = ["cmd_shape.json", "cmd_args.json", "cmd_mismatch.json"]
    if os.name != "nt":
        (folder / "CON.json").write_text("{}", encoding="utf-8")
        rejected_names.append("CON.json")
    (folder / "cmd_shape.json").write_text("[]", encoding="utf-8")
    (folder / "cmd_args.json").write_text(json.dumps({
        "command_id": "cmd_args", "command_type": "interview", "args": [],
    }), encoding="utf-8")
    (folder / "cmd_mismatch.json").write_text(json.dumps({
        "command_id": "cmd_other", "command_type": "close_env", "args": {},
    }), encoding="utf-8")
    valid = folder / "cmd_valid.json"
    valid.write_text(json.dumps({
        "command_id": "cmd_valid", "command_type": "close_env", "args": {},
    }), encoding="utf-8")
    for name in rejected_names:
        os.utime(folder / name, (1, 1))
    command = handler.poll_command()
    assert command["command_id"] == "cmd_valid"
    assert all((folder / name).exists() for name in rejected_names)


@pytest.mark.parametrize("script_name,class_name", HANDLERS)
def test_handler_rejects_invalid_response_id_without_writing(tmp_path, script_name, class_name):
    handler = _handler(script_name, class_name, tmp_path)
    with pytest.raises((InvalidResourcePath, ValueError)):
        handler.send_response("../outside", "completed", {"ok": True})
    with pytest.raises(ValueError):
        handler.send_response("cmd_valid", "not_a_status", {"ok": True})
    assert list((tmp_path / "ipc_responses").iterdir()) == []


@pytest.mark.parametrize("script_name,class_name", HANDLERS)
def test_handler_ignores_unpublished_temp_and_respects_bounds(tmp_path, script_name, class_name):
    handler = _handler(script_name, class_name, tmp_path)
    folder = tmp_path / "ipc_commands"
    (folder / "tmp_unpublished.tmp").write_text("{}", encoding="utf-8")
    (folder / "cmd_large.json").write_bytes(b"x" * (ipc_module.MAX_IPC_MESSAGE_BYTES + 1))
    nested = "[" * 65 + "0" + "]" * 65
    (folder / "cmd_deep.json").write_text(
        '{"command_id":"cmd_deep","command_type":"close_env","args":{"nested":'
        + nested + '}}', encoding="utf-8",
    )
    assert handler.poll_command() is None
    assert len(list(folder.iterdir())) == 3
    with pytest.raises(ValueError, match="size limit"):
        handler.send_response("cmd_oversize", "completed", {"answer": "🌊" * (ipc_module.MAX_IPC_MESSAGE_BYTES // 4)})
    assert list((tmp_path / "ipc_responses").iterdir()) == []


@pytest.mark.parametrize("script_name,class_name", HANDLERS)
def test_handler_linked_ipc_entries_are_rejected(tmp_path, script_name, class_name):
    outside = tmp_path / "outside"
    outside.mkdir()
    simulation = tmp_path / "simulation"
    simulation.mkdir()
    _link(simulation / "ipc_commands", outside, is_directory=True)
    with pytest.raises(InvalidResourcePath):
        _handler(script_name, class_name, simulation)

    simulation2 = tmp_path / "simulation2"
    handler = _handler(script_name, class_name, simulation2)
    sentinel = outside / "sentinel.json"
    sentinel.write_text("keep", encoding="utf-8")
    _link(simulation2 / "ipc_commands" / "cmd_link.json", sentinel)
    assert handler.poll_command() is None
    with pytest.raises(InvalidResourcePath):
        handler.send_response("cmd_link", "completed", {"ok": True})
    assert sentinel.read_text(encoding="utf-8") == "keep"
    same_root = simulation2 / "sibling.json"
    same_root.write_text("keep", encoding="utf-8")
    _link(simulation2 / "ipc_commands" / "cmd_same.json", same_root)
    assert handler.poll_command() is None
    with pytest.raises(InvalidResourcePath):
        handler.send_response("cmd_same", "completed", {"ok": True})
    assert same_root.read_text(encoding="utf-8") == "keep"
    assert list((simulation2 / "ipc_responses").iterdir()) == []


@pytest.mark.parametrize("script_name,class_name", HANDLERS)
def test_handler_status_and_atomic_failure(tmp_path, monkeypatch, script_name, class_name):
    handler = _handler(script_name, class_name, tmp_path)
    handler.update_status("waiting")
    status_file = tmp_path / "env_status.json"
    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["status"] == "waiting"
    assert isinstance(status["timestamp"], str)
    if class_name != "ParallelIPCHandler":
        assert "twitter_available" not in status
        assert "reddit_available" not in status
        assert handler._running is True

    previous = status_file.read_bytes()
    response_file = tmp_path / "ipc_responses" / "cmd_valid.json"
    response_file.write_text("old", encoding="utf-8")

    def fail_replace(source, target):
        assert source.endswith(".tmp")
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(ipc_module.os, "replace", fail_replace)
    with pytest.raises(OSError):
        handler.update_status("stopped")
    with pytest.raises(OSError):
        handler.send_response("cmd_valid", "completed", {"ok": True})
    assert status_file.read_bytes() == previous
    assert response_file.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob("tmp_*.tmp")) == []
    assert list((tmp_path / "ipc_responses").glob("tmp_*.tmp")) == []


@pytest.mark.parametrize("twitter,reddit", [
    (True, True), (True, False), (False, True), (False, False),
])
def test_parallel_platform_status_flags(tmp_path, twitter, reddit):
    handler = _handler("run_parallel_simulation.py", "ParallelIPCHandler", tmp_path,
                       twitter=twitter, reddit=reddit)
    handler.update_status("alive")
    status = json.loads((tmp_path / "env_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "alive"
    assert status["twitter_available"] is twitter
    assert status["reddit_available"] is reddit
    assert SimulationIPCClient(str(tmp_path)).check_env_alive() is True


def test_shared_status_writer_rejects_invalid_values_before_file_io(tmp_path):
    server = SimulationIPCServer(str(tmp_path))
    for status in ("", "x" * 65, "bad\nstatus", None):
        with pytest.raises(ValueError):
            server.update_status(status)
    with pytest.raises(ValueError):
        server.update_status("alive", twitter_available="yes")
    assert not (tmp_path / "env_status.json").exists()

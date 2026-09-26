"""File IPC and runner forwarding boundaries; no simulation process or answers."""

import json

import pytest

from app.services import simulation_ipc as ipc_module
from app.services import simulation_runner as runner_module
from app.services.simulation_ipc import (
    CommandStatus,
    CommandType,
    IPCCommand,
    IPCResponse,
    SimulationIPCClient,
    SimulationIPCServer,
)


def test_command_and_response_dict_round_trip():
    command = IPCCommand(command_id="fixture-id", command_type=CommandType.INTERVIEW, args={"agent_id": 4, "prompt": "你怎么看？"}, timestamp="2026-01-01T00:00:00")
    decoded = IPCCommand.from_dict(json.loads(json.dumps(command.to_dict(), ensure_ascii=False)))
    assert decoded == command
    response = IPCResponse(command_id="fixture-id", status=CommandStatus.FAILED, error="agent unavailable", timestamp="2026-01-01T00:00:01")
    assert IPCResponse.from_dict(json.loads(json.dumps(response.to_dict()))) == response


@pytest.mark.parametrize(
    ("kind", "expected_type", "expected_args"),
    [
        ("single", CommandType.INTERVIEW, {"agent_id": 4, "prompt": "你怎么看？", "platform": "twitter"}),
        ("batch", CommandType.BATCH_INTERVIEW, {"interviews": [{"agent_id": 4, "prompt": "你怎么看？", "platform": "reddit"}, {"agent_id": 5, "prompt": "Why?"}], "platform": "twitter"}),
    ],
)
def test_actual_client_server_file_serialization(tmp_path, monkeypatch, kind, expected_type, expected_args):
    client = SimulationIPCClient(str(tmp_path))
    server = SimulationIPCServer(str(tmp_path))
    observed = []

    def controlled_wait(_interval):
        command = server.poll_commands()
        assert command is not None
        observed.append(command)
        server.send_success(command.command_id, {"fixture_ack": True})

    monkeypatch.setattr(ipc_module.time, "sleep", controlled_wait)
    if kind == "single":
        response = client.send_interview(4, "你怎么看？", platform="twitter", timeout=2)
    else:
        response = client.send_batch_interview(expected_args["interviews"], platform="twitter", timeout=2)
    assert response.status is CommandStatus.COMPLETED
    assert response.result == {"fixture_ack": True}
    assert observed[0].command_type is expected_type
    assert observed[0].args == expected_args
    assert list((tmp_path / "ipc_commands").iterdir()) == []
    assert list((tmp_path / "ipc_responses").iterdir()) == []


def test_file_ipc_failed_response_and_timeout_without_real_wait(tmp_path, monkeypatch):
    client = SimulationIPCClient(str(tmp_path))
    server = SimulationIPCServer(str(tmp_path))

    def fail_immediately(_interval):
        command = server.poll_commands()
        assert command is not None
        server.send_error(command.command_id, "synthetic unavailable")

    monkeypatch.setattr(ipc_module.time, "sleep", fail_immediately)
    failed = client.send_interview(4, "Why?", timeout=2)
    assert failed.status is CommandStatus.FAILED
    assert failed.error == "synthetic unavailable"
    with pytest.raises(TimeoutError):
        client.send_interview(4, "Why?", timeout=0)
    assert list((tmp_path / "ipc_commands").iterdir()) == []


def test_completed_response_cleanup_when_server_removed_command(tmp_path, monkeypatch):
    client = SimulationIPCClient(str(tmp_path))
    server = SimulationIPCServer(str(tmp_path))

    def server_answers(_interval):
        command = server.poll_commands()
        assert command is not None
        server.send_success(command.command_id, {"fixture_ack": True})
        # send_success has already removed the generated command path.
        assert not (tmp_path / "ipc_commands" / f"{command.command_id}.json").exists()
        assert (tmp_path / "ipc_responses" / f"{command.command_id}.json").exists()

    monkeypatch.setattr(ipc_module.time, "sleep", server_answers)
    response = client.send_interview(4, "synthetic question", timeout=2)
    assert response.status is CommandStatus.COMPLETED
    assert list((tmp_path / "ipc_commands").iterdir()) == []
    assert list((tmp_path / "ipc_responses").iterdir()) == []


def test_runner_individual_batch_forwarding_failure_and_timeout(tmp_path, monkeypatch):
    simulation_id = "fixture-run"
    (tmp_path / simulation_id).mkdir()
    monkeypatch.setattr(runner_module.SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    calls = []

    class StubWaitingBoundary:
        def __init__(self, simulation_dir):
            assert simulation_dir == str(tmp_path / simulation_id)

        def check_env_alive(self):
            return True

        def send_interview(self, **kwargs):
            calls.append(("individual", kwargs))
            return IPCResponse(command_id="fixture", status=CommandStatus.COMPLETED, result={"fixture_ack": True})

        def send_batch_interview(self, **kwargs):
            calls.append(("batch", kwargs))
            return IPCResponse(command_id="fixture", status=CommandStatus.FAILED, error="synthetic unavailable")

    monkeypatch.setattr(runner_module, "SimulationIPCClient", StubWaitingBoundary)
    individual = runner_module.SimulationRunner.interview_agent(simulation_id, 4, "你怎么看？", platform="twitter", timeout=3)
    assert individual["success"] is True and individual["result"] == {"fixture_ack": True}
    interviews = [{"agent_id": 4, "prompt": "Why?", "platform": "reddit"}]
    batch = runner_module.SimulationRunner.interview_agents_batch(simulation_id, interviews, platform="twitter", timeout=5)
    assert batch["success"] is False and batch["error"] == "synthetic unavailable"
    assert calls == [
        ("individual", {"agent_id": 4, "prompt": "你怎么看？", "platform": "twitter", "timeout": 3}),
        ("batch", {"interviews": interviews, "platform": "twitter", "timeout": 5}),
    ]

    def timeout(**kwargs):
        raise TimeoutError("synthetic IPC timeout")

    monkeypatch.setattr(StubWaitingBoundary, "send_interview", lambda self, **kwargs: timeout(**kwargs))
    with pytest.raises(TimeoutError, match="synthetic IPC timeout"):
        runner_module.SimulationRunner.interview_agent(simulation_id, 4, "Why?", timeout=0)

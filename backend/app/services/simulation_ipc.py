"""Bounded file IPC between Flask and the simulation process.

Messages are small, atomic JSON files. This transport assumes a trusted local
filesystem; it cannot eliminate concurrent local link-swap races.
"""

import json
import math
import os
import stat
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger
from ..utils.safe_paths import InvalidResourcePath, safe_path, validate_resource_id


logger = get_logger('mirofish.simulation_ipc')
MAX_IPC_MESSAGE_BYTES = 1024 * 1024
MAX_IPC_JSON_DEPTH = 64
# The shared path helper bounds complete components to 128 characters. Leave
# five characters for the fixed .json suffix when validating command IDs.
MAX_IPC_COMMAND_ID_LENGTH = 123


class CommandType(str, Enum):
    INTERVIEW = "interview"
    BATCH_INTERVIEW = "batch_interview"
    CLOSE_ENV = "close_env"


class CommandStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


def _command_id(value: str) -> str:
    validate_resource_id(value)
    if len(value) > MAX_IPC_COMMAND_ID_LENGTH:
        raise InvalidResourcePath("Invalid resource path")
    return value


def _object(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Invalid IPC message")
    return value


def _optional_timestamp(value: Any) -> str:
    if value is None:
        return datetime.now().isoformat()
    if not isinstance(value, str):
        raise ValueError("Invalid IPC message")
    return value


@dataclass
class IPCCommand:
    command_id: str
    command_type: CommandType
    args: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command_id": self.command_id,
            "command_type": self.command_type.value,
            "args": self.args,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'IPCCommand':
        data = _object(data)
        return cls(
            command_id=_command_id(data["command_id"]),
            command_type=CommandType(data["command_type"]),
            args=_object(data.get("args", {})),
            timestamp=_optional_timestamp(data.get("timestamp")),
        )


@dataclass
class IPCResponse:
    command_id: str
    status: CommandStatus
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command_id": self.command_id,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'IPCResponse':
        data = _object(data)
        result = data.get("result")
        error = data.get("error")
        if result is not None:
            _object(result)
        if error is not None and not isinstance(error, str):
            raise ValueError("Invalid IPC message")
        return cls(
            command_id=_command_id(data["command_id"]),
            status=CommandStatus(data["status"]),
            result=result,
            error=error,
            timestamp=_optional_timestamp(data.get("timestamp")),
        )


def _check_simulation_dir(simulation_dir: str) -> None:
    """The configured root may be trusted, but the root entry cannot be a link."""
    path = Path(simulation_dir)
    try:
        details = path.lstat()
    except FileNotFoundError:
        # Preserve construction of a new trusted simulation directory.
        try:
            os.makedirs(path, exist_ok=True)
            details = path.lstat()
        except (OSError, RuntimeError) as exc:
            raise InvalidResourcePath("Invalid resource path") from exc
    except (OSError, RuntimeError) as exc:
        raise InvalidResourcePath("Invalid resource path") from exc
    reparse_flags = getattr(details, "st_file_attributes", 0)
    if (stat.S_ISLNK(details.st_mode)
            or reparse_flags & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
        raise InvalidResourcePath("Invalid resource path")
    try:
        if not path.is_dir():
            raise InvalidResourcePath("Invalid resource path")
    except (OSError, RuntimeError) as exc:
        raise InvalidResourcePath("Invalid resource path") from exc


def _path(simulation_dir: str, *parts: str) -> str:
    _check_simulation_dir(simulation_dir)
    return safe_path(simulation_dir, *parts)


def _validate_json_depth(value: Any) -> None:
    """Bound nested JSON containers independently of Python's recursion limit.

    A root dict/list has depth 1; each child dict/list adds one. Scalars do
    not add depth. Serialization happens first for outbound values, so cycles
    and unsupported values fail there before this iterative traversal.
    """
    pending = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if not isinstance(current, (dict, list)):
            continue
        if depth > MAX_IPC_JSON_DEPTH:
            raise ValueError("Invalid IPC message")
        children = current.values() if isinstance(current, dict) else current
        pending.extend((child, depth + 1) for child in children
                       if isinstance(child, (dict, list)))


def _message_bytes(data: Dict[str, Any]) -> bytes:
    try:
        encoded = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("Invalid IPC message") from exc
    if len(encoded) > MAX_IPC_MESSAGE_BYTES:
        raise ValueError("IPC message exceeds size limit")
    _decode_message(encoded)
    return encoded


def _decode_message(data: bytes) -> Dict[str, Any]:
    """Apply the same JSON shape and depth rules to reads and writes."""
    try:
        decoded = json.loads(data.decode("utf-8"))
    except RecursionError as exc:
        raise ValueError("Invalid IPC message") from exc
    _validate_json_depth(decoded)
    return _object(decoded)


def _read_message(path: str) -> Dict[str, Any]:
    # A FIFO or device can block despite the byte cap. The caller has already
    # checked this path against simulation_dir; refuse non-regular files here.
    if not stat.S_ISREG(os.lstat(path).st_mode):
        raise ValueError("Invalid IPC message file")
    with open(path, "rb") as file:
        data = file.read(MAX_IPC_MESSAGE_BYTES + 1)
    if len(data) > MAX_IPC_MESSAGE_BYTES:
        raise ValueError("IPC message exceeds size limit")
    return _decode_message(data)


def _atomic_write(simulation_dir: str, parts: tuple[str, ...], data: bytes) -> None:
    target = _path(simulation_dir, *parts)
    parent_parts = parts[:-1]
    directory = _path(simulation_dir, *parent_parts) if parent_parts else simulation_dir
    os.makedirs(directory, exist_ok=True)
    _path(simulation_dir, *parts)
    temp_name = f"tmp_{uuid.uuid4().hex}.tmp"
    temp_parts = (*parent_parts, temp_name)
    temp_path = _path(simulation_dir, *temp_parts)
    owned = False
    try:
        with open(temp_path, "xb") as file:
            owned = True
            file.write(data)
        _path(simulation_dir, *temp_parts)
        target = _path(simulation_dir, *parts)
        os.replace(temp_path, target)
        owned = False
    finally:
        if owned:
            try:
                os.remove(_path(simulation_dir, *temp_parts))
            except (InvalidResourcePath, OSError):
                # Never follow a replaced temp link during cleanup.
                logger.warning("IPC temporary file cleanup failed")


def _remove_exact(simulation_dir: str, *parts: str) -> None:
    try:
        os.remove(_path(simulation_dir, *parts))
    except FileNotFoundError:
        pass
    except (InvalidResourcePath, OSError):
        logger.warning("IPC exact-file cleanup skipped")


def _poll_timing(timeout: float, poll_interval: float) -> tuple[float, float]:
    try:
        timeout = float(timeout)
        poll_interval = float(poll_interval)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Invalid IPC polling interval") from exc
    if (not math.isfinite(timeout) or timeout < 0
            or not math.isfinite(poll_interval) or poll_interval <= 0):
        raise ValueError("Invalid IPC polling interval")
    return timeout, poll_interval


class SimulationIPCClient:
    """Flask-side command sender and response waiter."""

    def __init__(self, simulation_dir: str):
        self.simulation_dir = simulation_dir
        self.commands_dir = _path(simulation_dir, "ipc_commands")
        self.responses_dir = _path(simulation_dir, "ipc_responses")
        os.makedirs(self.commands_dir, exist_ok=True)
        os.makedirs(self.responses_dir, exist_ok=True)
        _path(simulation_dir, "ipc_commands")
        _path(simulation_dir, "ipc_responses")

    def send_command(
        self,
        command_type: CommandType,
        args: Dict[str, Any],
        timeout: float = 60.0,
        poll_interval: float = 0.5,
    ) -> IPCResponse:
        timeout, poll_interval = _poll_timing(timeout, poll_interval)
        command_type = CommandType(command_type)
        _object(args)
        command_id = str(uuid.uuid4())
        command = IPCCommand(command_id, command_type, args)
        payload = _message_bytes(command.to_dict())
        name = f"{command_id}.json"
        _atomic_write(self.simulation_dir, ("ipc_commands", name), payload)
        logger.info("IPC command sent: type=%s, id=%s", command_type.value, command_id)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                response_file = _path(self.simulation_dir, "ipc_responses", name)
                if os.path.exists(response_file):
                    data = _read_message(_path(self.simulation_dir, "ipc_responses", name))
                    response = IPCResponse.from_dict(data)
                    if response.command_id != command_id:
                        raise ValueError("IPC response ID mismatch")
                    # Cleanup remains independent: the server may already have
                    # removed the command file.
                    _remove_exact(self.simulation_dir, "ipc_commands", name)
                    _remove_exact(self.simulation_dir, "ipc_responses", name)
                    logger.info("IPC response received: id=%s, status=%s", command_id, response.status.value)
                    return response
            except (InvalidResourcePath, ValueError, KeyError, TypeError,
                    UnicodeError, json.JSONDecodeError, OSError):
                logger.warning("Rejected invalid IPC response candidate")
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(poll_interval, remaining))

        logger.warning("IPC response timed out: id=%s", command_id)
        _remove_exact(self.simulation_dir, "ipc_commands", name)
        raise TimeoutError(f"Waiting for IPC response timed out ({timeout} seconds)")

    def send_interview(
        self, agent_id: int, prompt: str, platform: str = None,
        timeout: float = 60.0,
    ) -> IPCResponse:
        args = {"agent_id": agent_id, "prompt": prompt}
        if platform:
            args["platform"] = platform
        return self.send_command(CommandType.INTERVIEW, args, timeout=timeout)

    def send_batch_interview(
        self, interviews: List[Dict[str, Any]], platform: str = None,
        timeout: float = 120.0,
    ) -> IPCResponse:
        args = {"interviews": interviews}
        if platform:
            args["platform"] = platform
        return self.send_command(CommandType.BATCH_INTERVIEW, args, timeout=timeout)

    def send_close_env(self, timeout: float = 30.0) -> IPCResponse:
        return self.send_command(CommandType.CLOSE_ENV, {}, timeout=timeout)

    def check_env_alive(self) -> bool:
        try:
            status_file = _path(self.simulation_dir, "env_status.json")
            if not os.path.exists(status_file):
                return False
            status = _read_message(_path(self.simulation_dir, "env_status.json"))
            return status.get("status") == "alive" and isinstance(status.get("timestamp"), str)
        except (InvalidResourcePath, ValueError, UnicodeError,
                json.JSONDecodeError, OSError):
            logger.warning("Rejected invalid IPC environment status")
            return False


class SimulationIPCServer:
    """Simulation-side command poller and response writer."""

    def __init__(self, simulation_dir: str):
        self.simulation_dir = simulation_dir
        self.commands_dir = _path(simulation_dir, "ipc_commands")
        self.responses_dir = _path(simulation_dir, "ipc_responses")
        os.makedirs(self.commands_dir, exist_ok=True)
        os.makedirs(self.responses_dir, exist_ok=True)
        _path(simulation_dir, "ipc_commands")
        _path(simulation_dir, "ipc_responses")
        self._running = False

    def start(self):
        self._running = True
        self._update_env_status("alive")

    def stop(self):
        self._running = False
        self._update_env_status("stopped")

    def _update_env_status(self, status: str):
        data = _message_bytes({"status": status, "timestamp": datetime.now().isoformat()})
        _atomic_write(self.simulation_dir, ("env_status.json",), data)

    def poll_commands(self) -> Optional[IPCCommand]:
        try:
            directory = _path(self.simulation_dir, "ipc_commands")
            entries = os.listdir(directory)
        except (InvalidResourcePath, OSError):
            logger.warning("IPC command directory unavailable")
            return None

        candidates = []
        for filename in entries:
            if not filename.endswith(".json"):
                continue
            stem = filename[:-5]
            try:
                _command_id(stem)
                filepath = _path(self.simulation_dir, "ipc_commands", filename)
                candidates.append((os.path.getmtime(filepath), filename))
            except (InvalidResourcePath, OSError):
                logger.warning("Rejected invalid IPC command entry")
        candidates.sort()

        for _, filename in candidates:
            try:
                filepath = _path(self.simulation_dir, "ipc_commands", filename)
                command = IPCCommand.from_dict(_read_message(filepath))
                if command.command_id != filename[:-5]:
                    raise ValueError("IPC command ID mismatch")
                return command
            except (InvalidResourcePath, ValueError, KeyError, TypeError,
                    UnicodeError, json.JSONDecodeError, OSError):
                logger.warning("Rejected invalid IPC command candidate")
        return None

    def send_response(self, response: IPCResponse):
        if not isinstance(response, IPCResponse):
            raise ValueError("Invalid IPC response")
        normalized = IPCResponse.from_dict({
            "command_id": response.command_id,
            "status": response.status,
            "result": response.result,
            "error": response.error,
            "timestamp": response.timestamp,
        })
        payload = _message_bytes(normalized.to_dict())
        name = f"{normalized.command_id}.json"
        # A linked command target is invalid even when the response target is
        # safe. Do not publish a response before checking both exact paths.
        _path(self.simulation_dir, "ipc_responses", name)
        command_file = _path(self.simulation_dir, "ipc_commands", name)
        if os.path.exists(command_file):
            command = IPCCommand.from_dict(
                _read_message(_path(self.simulation_dir, "ipc_commands", name))
            )
            if command.command_id != normalized.command_id:
                raise ValueError("IPC command ID mismatch")
        _atomic_write(self.simulation_dir, ("ipc_responses", name), payload)
        # Keep accepted independent command cleanup. If the command is absent
        # or has become an unsafe link, the response still remains available.
        _remove_exact(self.simulation_dir, "ipc_commands", name)

    def send_success(self, command_id: str, result: Dict[str, Any]):
        self.send_response(IPCResponse(command_id, CommandStatus.COMPLETED, result=result))

    def send_error(self, command_id: str, error: str):
        self.send_response(IPCResponse(command_id, CommandStatus.FAILED, error=error))

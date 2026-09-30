"""Spawn protocol source tests; no native model or external database."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import struct
import time
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

import pytest

from mirofish_execution import native_process_driver as driver_module
from mirofish_execution.native_owned_binding import _manifest
from mirofish_execution.native_process_driver import NativeProcessDriver
from mirofish_execution.native_process_worker import (MAX_MESSAGE, decode_message,
                                                     encode_message)
from mirofish_execution.native_run_contracts import (InvalidNativeRun,
    NativeRunRequest, NativeRunUnavailable)


def request():
    return NativeRunRequest.from_wire(dict(schema_version=1, principal="owner",
        project_id=uuid4(), project_revision=1, simulation_id="sim_1",
        run_id=uuid4(), artifact_sha256="a" * 64, runtime_sha256="b" * 64,
        platforms=("twitter",), seed=7, max_rounds=1))


@dataclass
class FileSession:
    marker: str
    mode: str

    def start(self):
        if self.mode == "crash":
            os._exit(9)
        if self.mode == "slow":
            time.sleep(5)
        Path(self.marker).write_text("started", encoding="ascii")

    def close(self):
        Path(self.marker + ".closed").write_text("closed", encoding="ascii")


@dataclass
class FileFactory:
    marker: str
    mode: str = "normal"

    def validate(self, request):
        assert request.simulation_id == "sim_1"

    def create_session(self, request):
        return FileSession(self.marker, self.mode)

    def evidence(self, request):
        return hashlib.sha256(Path(self.marker).read_bytes()).hexdigest()


@dataclass
class EnvironmentSession:
    marker: str

    def start(self):
        Path(self.marker).write_text(json.dumps({
            "secret": os.environ.get("DEEPSEEK_API_KEY"),
            "proxy": os.environ.get("HTTPS_PROXY"),
            "dotenv_disabled": os.environ.get("PYTHON_DOTENV_DISABLED")}),
            encoding="ascii")

    def close(self):
        pass


@dataclass
class EnvironmentFactory(FileFactory):
    def create_session(self, request):
        return EnvironmentSession(self.marker)


@dataclass
class ExternalSocketSession:
    marker: str

    def start(self):
        sock = socket.socket()
        sock.settimeout(0.1)
        try:
            try:
                sock.connect(("203.0.113.1", 443))
            except OSError as error:
                Path(self.marker).write_text(
                    "denied" if "external socket denied in native offline test"
                    in str(error) else "unexpected", encoding="ascii")
            else:
                Path(self.marker).write_text("unexpected", encoding="ascii")
        finally:
            sock.close()

    def close(self):
        pass


@dataclass
class ExternalSocketFactory(FileFactory):
    def create_session(self, request):
        return ExternalSocketSession(self.marker)


def adversarial_child(connection, request_bytes, attempt_text, instance_text,
                      factory, go_timeout):
    value = NativeRunRequest.from_wire(decode_message(request_bytes))
    if factory.mode == "silent-ready":
        Path(factory.marker).write_text("child-waiting", encoding="ascii")
        time.sleep(5)
        return
    if factory.mode == "malformed-ready":
        connection.send_bytes(b'{"kind":"ready"')
        return
    if factory.mode == "partial-ready":
        # Intentionally write only a declared frame prefix and body fragment.
        connection._send(struct.pack("!i", 100) + b'{"kind":')
        Path(factory.marker).write_text("partial-frame-sent", encoding="ascii")
        time.sleep(5)
        return
    base = {"run_id": str(value.run_id), "attempt_id": attempt_text,
            "instance_id": instance_text}
    connection.send_bytes(encode_message({"kind": "ready", **base,
        "request_fingerprint": value.fingerprint, "process_id": os.getpid()}))
    if not connection.poll(go_timeout):
        return
    decode_message(connection.recv_bytes(MAX_MESSAGE))
    terminal = {"kind": "terminal", **base,
                "request_fingerprint": value.fingerprint,
                "outcome": "completed", "evidence_sha256": "d" * 64}
    if factory.mode == "wrong-terminal":
        terminal["run_id"] = str(uuid4())
    connection.send_bytes(encode_message(terminal))
    if factory.mode == "duplicate-terminal":
        connection.send_bytes(encode_message(terminal))
    connection.close()


def await_terminal(driver, request, attempt, child, *, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        observation = driver.observe(request, attempt, child)
        if observation.status != "running":
            return observation
        time.sleep(0.02)
    pytest.fail("bounded native child observation timed out")


def test_spawn_ready_then_one_go_and_terminal_exit(tmp_path):
    marker = str(tmp_path / "started")
    driver = NativeProcessDriver(FileFactory(marker))
    value, attempt = request(), uuid4()
    try:
        child = driver.launch(value, attempt)
        assert child.process_id > 0 and not Path(marker).exists()
        final = await_terminal(driver, value, attempt, child)
        assert final.status == "completed" and final.receipt is not None
        assert Path(marker).read_text(encoding="ascii") == "started"
        assert Path(marker + ".closed").exists()
        assert driver.observe(value, attempt, child) == final
        with pytest.raises(InvalidNativeRun):
            driver.observe(value, uuid4(), child)
    finally:
        driver.close()
        driver.close()


def test_cancel_before_go_and_fresh_driver_cannot_adopt(tmp_path):
    marker = str(tmp_path / "not-started")
    driver = NativeProcessDriver(FileFactory(marker))
    value, attempt = request(), uuid4()
    other = NativeProcessDriver(FileFactory(marker))
    try:
        child = driver.launch(value, attempt)
        assert other.observe(value, attempt, child).status == "absent"
        result = driver.cancel(value, attempt, child)
        assert result.status == "cancelled" and result.receipt is not None
        assert driver.cancel(value, attempt, child) == result
        assert driver.observe(value, attempt, child) == result
        assert not Path(marker).exists()
    finally:
        driver.close()
        other.close()


def test_crash_has_no_terminal_proof(tmp_path):
    driver = NativeProcessDriver(FileFactory(str(tmp_path / "crash"), "crash"))
    value, attempt = request(), uuid4()
    try:
        child = driver.launch(value, attempt)
        result = await_terminal(driver, value, attempt, child)
        assert result.status == "unknown" and result.receipt is None
    finally:
        driver.close()


def test_cancel_during_owned_execution_is_bounded(tmp_path):
    marker = str(tmp_path / "slow")
    driver = NativeProcessDriver(FileFactory(marker, "slow"), grace_seconds=0.05)
    value, attempt = request(), uuid4()
    try:
        child = driver.launch(value, attempt)
        assert driver.observe(value, attempt, child).status == "running"
        result = driver.cancel(value, attempt, child)
        assert result.status == "cancelled"
        assert not Path(marker).exists()
    finally:
        driver.close()


def test_child_scrubs_secrets_and_proxy_without_mutating_parent(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "parent-only-secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://parent-proxy.invalid")
    marker = str(tmp_path / "environment")
    driver = NativeProcessDriver(EnvironmentFactory(marker))
    value, attempt = request(), uuid4()
    try:
        child = driver.launch(value, attempt)
        assert await_terminal(driver, value, attempt, child).status == "completed"
        observed = json.loads(Path(marker).read_text(encoding="ascii"))
        assert observed == {"secret": None, "proxy": None, "dotenv_disabled": "1"}
        assert os.environ["DEEPSEEK_API_KEY"] == "parent-only-secret"
        assert os.environ["HTTPS_PROXY"] == "http://parent-proxy.invalid"
    finally:
        driver.close()


def test_offline_child_denies_external_socket_before_connect(tmp_path, monkeypatch):
    monkeypatch.setenv("MIROFISH_NATIVE_TEST_OFFLINE", "1")
    marker = str(tmp_path / "socket")
    driver = NativeProcessDriver(ExternalSocketFactory(marker))
    value, attempt = request(), uuid4()
    try:
        child = driver.launch(value, attempt)
        assert await_terminal(driver, value, attempt, child).status == "completed"
        assert Path(marker).read_text(encoding="ascii") == "denied"
    finally:
        driver.close()


def test_control_message_rejects_duplicate_extra_and_oversized_fields():
    keys = frozenset({"kind"})
    with pytest.raises(ValueError):
        decode_message(b'{"kind":"go","kind":"cancel"}', keys)
    with pytest.raises(ValueError):
        decode_message(b'{"kind":"go","path":"secret"}', keys)
    with pytest.raises(ValueError):
        decode_message(b" " * (MAX_MESSAGE + 1), keys)


def test_manifest_hashes_exact_crlf_and_substitute_bytes(tmp_path):
    raw = b'{"value":"x"}\r\n\x1a'
    (tmp_path / "state.json").write_bytes(raw)
    expected = hashlib.sha256(json.dumps({"schema_version": 1, "files": [{
        "name": "state.json", "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw)}]}, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode("ascii")).hexdigest()
    assert _manifest(tmp_path, ("state.json",), 1024) == expected


@pytest.mark.parametrize("mode", ["wrong-terminal", "duplicate-terminal"])
def test_invalid_or_duplicate_terminal_is_unknown(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(driver_module, "child_main", adversarial_child)
    driver = NativeProcessDriver(FileFactory(str(tmp_path / "unused"), mode))
    value, attempt = request(), uuid4()
    try:
        child = driver.launch(value, attempt)
        assert await_terminal(driver, value, attempt, child).status == "unknown"
        assert driver.observe(value, attempt, child).status == "unknown"
    finally:
        driver.close()


@pytest.mark.skipif(os.name == "nt", reason="Windows PipeConnection is message-mode")
def test_partial_ready_frame_times_out_and_close_remains_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(driver_module, "child_main", adversarial_child)
    marker = str(tmp_path / "partial")
    driver = NativeProcessDriver(FileFactory(marker, "partial-ready"),
                                 handshake_seconds=2, join_seconds=0.2)
    with pytest.raises(NativeRunUnavailable):
        driver.launch(request(), uuid4())
    assert Path(marker).read_text(encoding="ascii") == "partial-frame-sent"
    assert driver.close() is True


def test_no_ready_timeout_is_bounded_on_message_and_stream_pipes(tmp_path, monkeypatch):
    monkeypatch.setattr(driver_module, "child_main", adversarial_child)
    marker = str(tmp_path / "silent")
    driver = NativeProcessDriver(FileFactory(marker, "silent-ready"),
                                 handshake_seconds=2, join_seconds=0.2)
    with pytest.raises(NativeRunUnavailable):
        driver.launch(request(), uuid4())
    assert Path(marker).read_text(encoding="ascii") == "child-waiting"
    assert driver.close() is True


def test_malformed_ready_message_is_rejected_on_windows_pipe(tmp_path, monkeypatch):
    monkeypatch.setattr(driver_module, "child_main", adversarial_child)
    driver = NativeProcessDriver(FileFactory(str(tmp_path / "unused"),
                                             "malformed-ready"))
    with pytest.raises(NativeRunUnavailable):
        driver.launch(request(), uuid4())
    assert driver.close() is True


def test_failed_cleanup_retains_owned_handle_for_close_retry(tmp_path, monkeypatch):
    driver = NativeProcessDriver(FileFactory(str(tmp_path / "unused")))
    value, attempt = request(), uuid4()
    child = driver.launch(value, attempt)
    original_stop = driver._stop
    try:
        monkeypatch.setattr(driver, "_stop", lambda _process: False)
        assert driver.close() is False
        assert driver._owned is not None and not driver._closed
    finally:
        monkeypatch.setattr(driver, "_stop", original_stop)
        assert driver.close() is True


def test_failed_launch_retains_process_when_first_stop_is_unproved(tmp_path, monkeypatch):
    monkeypatch.setattr(driver_module, "child_main", adversarial_child)
    driver = NativeProcessDriver(FileFactory(str(tmp_path / "unused"), "silent-ready"),
                                 handshake_seconds=0.1, join_seconds=0.2)
    original_stop = driver._stop
    try:
        monkeypatch.setattr(driver, "_stop", lambda _process: False)
        with pytest.raises(NativeRunUnavailable):
            driver.launch(request(), uuid4())
        assert driver._pending is not None and not driver._closed
    finally:
        monkeypatch.setattr(driver, "_stop", original_stop)
        assert driver.close() is True


def test_ready_child_without_go_exits_unknown(tmp_path):
    driver = NativeProcessDriver(FileFactory(str(tmp_path / "unused")),
                                 go_timeout_seconds=0.1)
    value, attempt = request(), uuid4()
    try:
        child = driver.launch(value, attempt)
        time.sleep(0.2)
        assert driver.observe(value, attempt, child).status == "unknown"
    finally:
        driver.close()


def test_no_ready_after_factory_binding_rejection(tmp_path):
    driver = NativeProcessDriver(FileFactory(str(tmp_path / "unused")),
                                 handshake_seconds=2)
    wrong = replace(request(), simulation_id="sim_other")
    with pytest.raises(NativeRunUnavailable):
        driver.launch(wrong, uuid4())
    assert driver.close() is True

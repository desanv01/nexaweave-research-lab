"""Regression cases for the U00 offline unit-test launcher."""

import os
from pathlib import Path
import socket

import pytest

from tools.run_unit_tests import LoopbackOnlySockets, _loopback_host, _unit_environment


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "127.4.5.6", "::1"])
def test_loopback_addresses_are_allowed(host):
    assert _loopback_host(host)


@pytest.mark.parametrize("host", ["example.com", "8.8.8.8", "::2", "0.0.0.0"])
def test_other_addresses_are_rejected(host):
    assert not _loopback_host(host)


def test_external_connect_and_dns_are_counted_even_when_caught():
    guard = LoopbackOnlySockets()
    guard.install()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            with pytest.raises(OSError, match="non-loopback"):
                sock.connect(("8.8.8.8", 443))
            with pytest.raises(OSError, match="non-loopback"):
                sock.connect_ex(("8.8.8.8", 443))
        with pytest.raises(OSError, match="non-loopback"):
            socket.getaddrinfo("example.com", 443)
    finally:
        guard.restore()
    assert guard.blocked_attempts == 3


def test_local_http_socket_remains_usable():
    guard = LoopbackOnlySockets()
    guard.install()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            with socket.create_connection(server.getsockname(), timeout=2):
                connection, _ = server.accept()
                connection.close()
    finally:
        guard.restore()
    assert guard.blocked_attempts == 0


def test_child_environment_does_not_inherit_provider_or_proxy_secrets(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("GITHUB_TOKEN", "private-token")
    monkeypatch.setenv("OPENAI_API_KEY", "private-api-key")
    monkeypatch.setenv("HTTPS_PROXY", "http://private-proxy")
    env = _unit_environment(tmp_path)
    assert "GITHUB_TOKEN" not in env
    assert "HTTPS_PROXY" not in env
    assert env["OPENAI_API_KEY"] == "unit-test-dummy-key"
    assert env["PYTHON_DOTENV_DISABLED"] == "1"
    assert env["HOME"] == os.fspath(tmp_path)

"""Run inherited pytest suites with dummy credentials and loopback-only sockets.

This is fixture qualification, not a live provider or simulation-engine check.
"""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def _loopback_host(host: object) -> bool:
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii")
        except UnicodeDecodeError:
            return False
    if not isinstance(host, str):
        return False
    if host.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback


class LoopbackOnlySockets:
    """Reject and count external DNS lookups and socket connections."""

    def __init__(self) -> None:
        self.blocked_attempts = 0
        self._original_connect = None
        self._original_connect_ex = None
        self._original_getaddrinfo = None

    def _reject(self) -> None:
        self.blocked_attempts += 1
        raise OSError("offline unit test blocked a non-loopback network attempt")

    def install(self) -> None:
        self._original_connect = socket.socket.connect
        self._original_connect_ex = socket.socket.connect_ex
        self._original_getaddrinfo = socket.getaddrinfo
        guard = self

        def connect(sock, address):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                if not isinstance(address, tuple) or not _loopback_host(address[0]):
                    guard._reject()
            return guard._original_connect(sock, address)

        def connect_ex(sock, address):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                if not isinstance(address, tuple) or not _loopback_host(address[0]):
                    guard._reject()
            return guard._original_connect_ex(sock, address)

        def getaddrinfo(host, *args, **kwargs):
            if host is not None and not _loopback_host(host):
                guard._reject()
            return guard._original_getaddrinfo(host, *args, **kwargs)

        socket.socket.connect = connect
        socket.socket.connect_ex = connect_ex
        socket.getaddrinfo = getaddrinfo

    def restore(self) -> None:
        if self._original_connect is not None:
            socket.socket.connect = self._original_connect
            socket.socket.connect_ex = self._original_connect_ex
            socket.getaddrinfo = self._original_getaddrinfo


def _unit_environment(temp_root: Path) -> dict[str, str]:
    # Allow only interpreter/OS necessities; never copy the caller's provider
    # credentials, tokens, proxy settings, or Python startup hooks.
    keep = (
        "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "OS",
        "SYSTEMDRIVE", "LANG", "LC_ALL", "TZ",
    )
    source = os.environ
    env = {name: source[name] for name in keep if name in source}
    env.update(
        PYTHONPATH=os.pathsep.join((str(ROOT), str(ROOT / "backend"))),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHON_DOTENV_DISABLED="1",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        LLM_API_KEY="unit-test-dummy-key",
        OPENAI_API_KEY="unit-test-dummy-key",
        ZEP_API_KEY="unit-test-dummy-key",
        DEEPSEEK_API_KEY="unit-test-dummy-key",
        LLM_BASE_URL="http://127.0.0.1:9/v1",
        HOME=str(temp_root),
        USERPROFILE=str(temp_root),
        XDG_CONFIG_HOME=str(temp_root / "config"),
        XDG_CACHE_HOME=str(temp_root / "cache"),
        TMPDIR=str(temp_root),
        TMP=str(temp_root),
        TEMP=str(temp_root),
    )
    return env


def _run_pytest_child() -> int:
    import pytest

    guard = LoopbackOnlySockets()
    guard.install()
    try:
        result = int(pytest.main(["-q", "backend/tests", "tests"]))
    finally:
        guard.restore()
    if guard.blocked_attempts:
        print(
            f"offline unit test blocked {guard.blocked_attempts} non-loopback "
            "network attempt(s)",
            file=sys.stderr,
        )
        return 1
    return result


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mirofish-unit-") as directory:
        temp_root = Path(directory)
        command = [
            sys.executable,
            "-c",
            "from tools.run_unit_tests import _run_pytest_child; "
            "raise SystemExit(_run_pytest_child())",
        ]
        completed = subprocess.run(
            command, cwd=ROOT, env=_unit_environment(temp_root), check=False
        )
        return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())

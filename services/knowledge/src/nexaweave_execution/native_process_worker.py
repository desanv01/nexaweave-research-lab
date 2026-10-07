"""Spawn target for one trusted native session. No native/provider imports here."""
from __future__ import annotations
from nexaweave_knowledge.configuration import environment

import hashlib
import ipaddress
import json
import os
import socket
from uuid import UUID

from .native_run_contracts import NativeRunRequest

MAX_MESSAGE = 4096
_GO = frozenset({"kind", "run_id", "attempt_id", "instance_id"})


def encode_message(value: dict) -> bytes:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True).encode("ascii")
    if len(raw) > MAX_MESSAGE:
        raise ValueError("oversized control message")
    return raw


def decode_message(raw: bytes, keys: frozenset[str] | None = None) -> dict:
    if len(raw) > MAX_MESSAGE:
        raise ValueError("oversized control message")
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate control key")
            value[key] = item
        return value
    value = json.loads(raw.decode("ascii"), object_pairs_hook=unique)
    if type(value) is not dict or (keys is not None and set(value) != keys):
        raise ValueError("invalid control message")
    return value


def scrub_child_environment() -> None:
    """Remove inherited provider credentials, proxies, and Python hooks."""
    offline_test = environment.get("NEXAWEAVE_NATIVE_TEST_OFFLINE") == "1"
    runtime_keys = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR",
                    "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                    "VIRTUAL_ENV", "LANG", "LC_ALL", "PYTHONUTF8",
                    "PYTHONIOENCODING", "COMSPEC"}
    for key in list(os.environ):
        if key.upper() not in runtime_keys:
            os.environ.pop(key, None)
    # The accepted backend app.config calls load_dotenv during later import.
    # Keep that inherited import from repopulating provider secrets in this child.
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    if offline_test:
        os.environ["NEXAWEAVE_NATIVE_TEST_OFFLINE"] = "1"
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_DATASETS_OFFLINE"] = "1"
        _deny_external_sockets()


def _deny_external_sockets() -> None:
    """Test-only child guard; default runtime makes no socket policy claim."""
    def local(address):
        if not isinstance(address, tuple) or not address:
            return True  # Unix/local socket addresses.
        host = address[0]
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except (ValueError, TypeError):
            return False

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_sendto = socket.socket.sendto
    original_getaddrinfo = socket.getaddrinfo

    def connect(sock, address):
        if not local(address):
            raise OSError("external socket denied in native offline test")
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if not local(address):
            raise OSError("external socket denied in native offline test")
        return original_connect_ex(sock, address)

    def sendto(sock, data, *args):
        if args and not local(args[-1]):
            raise OSError("external socket denied in native offline test")
        return original_sendto(sock, data, *args)

    def getaddrinfo(host, *args, **kwargs):
        if host is not None and not local((host, 0)):
            raise OSError("external DNS denied in native offline test")
        return original_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.sendto = sendto
    socket.getaddrinfo = getaddrinfo


def child_main(connection, request_bytes: bytes, attempt_text: str,
               instance_text: str, factory, go_timeout: float) -> None:
    """Validate, announce ready, await one go, then own session through close."""
    try:
        scrub_child_environment()
        request = NativeRunRequest.from_wire(decode_message(request_bytes))
        attempt = UUID(attempt_text)
        instance = UUID(instance_text)
        if str(attempt) != attempt_text or str(instance) != instance_text:
            return
        factory.validate(request)
        base = {"run_id": str(request.run_id), "attempt_id": attempt_text,
                "instance_id": instance_text}
        connection.send_bytes(encode_message({"kind": "ready", **base,
            "request_fingerprint": request.fingerprint, "process_id": os.getpid()}))
        if not connection.poll(go_timeout):
            return
        command = decode_message(connection.recv_bytes(MAX_MESSAGE), _GO)
        if any(command[key] != base[key] for key in base):
            return
        if command["kind"] == "cancel":
            return
        if command["kind"] != "go" or connection.poll(0):
            return
        session = None
        outcome = "failed"
        evidence = hashlib.sha256(b"native-session-failed").hexdigest()
        try:
            session = factory.create_session(request)
            factory.validate(request)
            session.start()
            outcome = "completed"
        except Exception:
            outcome = "failed"
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    outcome = "failed"
            if outcome == "completed":
                try:
                    evidence = factory.evidence(request)
                except Exception:
                    outcome = "failed"
        connection.send_bytes(encode_message({"kind": "terminal", **base,
            "request_fingerprint": request.fingerprint, "outcome": outcome,
            "evidence_sha256": evidence}))
    except Exception:
        # A missing ready/terminal is uncertainty to the parent. Never expose
        # exception text, model output, or a path through the control channel.
        pass
    finally:
        connection.close()

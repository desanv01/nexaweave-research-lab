"""One-shot private-pipe client for the isolated knowledge interpreter.

This module is stdlib-only. It is a process-failure boundary, not a sandbox.
"""

from __future__ import annotations

import json
import importlib.util
import math
import os
import queue
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from uuid import UUID


# Source-loaded transport tests intentionally do not import app (which starts
# Flask). Load the one shared stdlib helper only from this trusted source tree.
_owner_spec = importlib.util.spec_from_file_location(
    "_mirofish_owned_process", Path(__file__).resolve().parent.parent / "utils" / "owned_process.py")
_owned_process = importlib.util.module_from_spec(_owner_spec)
_owner_spec.loader.exec_module(_owned_process)
OwnedProcess = _owned_process.OwnedProcess


_REQUEST_MAX = 512 * 1024
_RESPONSE_MAX = 2 * 1024 * 1024
_MAX_DEPTH = 32
_METHODS = frozenset({"page", "entity", "search", "ingest"})
_ROOT_FIELDS = frozenset({"version", "request_id", "method", "scope", "payload"})
_ERROR_CODES = frozenset({
    "invalid_request", "unauthorized", "busy", "not_found", "conflict",
    "tombstoned", "uncertain", "unsupported", "timeout", "result_too_large",
    "model_calls_disabled", "internal_error",
})
_ENV_KEY = re.compile(r"KNOWLEDGE_[A-Z0-9_]+\Z")


class KnowledgeTransportError(RuntimeError):
    code = "transport_failure"
    outcome_unknown = True

    def __init__(self):
        super().__init__(self.code.replace("_", " "))


class KnowledgeInvalidRequest(KnowledgeTransportError):
    code = "invalid_request"
    outcome_unknown = False


class KnowledgeBusy(KnowledgeTransportError):
    code = "busy"
    outcome_unknown = False


class KnowledgeTransportFailure(KnowledgeTransportError):
    code = "transport_failure"

    def __init__(self, *, outcome_unknown: bool):
        self.outcome_unknown = outcome_unknown
        super().__init__()


class KnowledgeCooperativeAbort(BaseException):
    """Private trusted report control; bypass the reader's public error sanitizer."""

    def __init__(self, code):
        if code not in ('report_cancelled', 'timeout', 'report_uncertain'):
            code = 'report_uncertain'
        self.code = code
        super().__init__(code)


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def _constant(_value):
    raise ValueError


def _json_object(raw: bytes) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        if type(value) is not dict:
            raise ValueError
        stack = [(value, 1)]
        while stack:
            current, depth = stack.pop()
            if depth > _MAX_DEPTH:
                raise ValueError
            members = current.values() if isinstance(current, dict) else current
            for member in members:
                if isinstance(member, float) and not math.isfinite(member):
                    raise ValueError
                if isinstance(member, (dict, list)):
                    stack.append((member, depth + 1))
        return value
    except (UnicodeError, ValueError, TypeError, RecursionError, OverflowError):
        raise ValueError from None


def _uuid(value: object) -> str:
    if type(value) is not str:
        raise ValueError
    try:
        parsed = UUID(value)
    except ValueError:
        raise ValueError from None
    if str(parsed) != value:
        raise ValueError
    return value


def _request_id(raw: bytes) -> str:
    if type(raw) is not bytes or not 0 < len(raw) <= _REQUEST_MAX:
        raise KnowledgeInvalidRequest()
    try:
        value = _json_object(raw)
        if set(value) != _ROOT_FIELDS or type(value["version"]) is not int or value["version"] != 1:
            raise ValueError
        request_id = _uuid(value["request_id"])
        if type(value["method"]) is not str or value["method"] not in _METHODS:
            raise ValueError
        if type(value["scope"]) is not dict or type(value["payload"]) is not dict:
            raise ValueError
        return request_id
    except (KeyError, ValueError, TypeError):
        raise KnowledgeInvalidRequest() from None


def _reply(raw: bytes, request_id: str) -> None:
    try:
        value = _json_object(raw)
        if (type(value.get("version")) is not int or value["version"] != 1
                or value.get("request_id") != request_id or type(value.get("ok")) is not bool):
            raise ValueError
        if value["ok"]:
            if set(value) != {"version", "request_id", "ok", "result"} or type(value["result"]) is not dict:
                raise ValueError
        elif (set(value) != {"version", "request_id", "ok", "error"}
              or type(value["error"]) is not dict
              or set(value["error"]) != {"code"}
              or type(value["error"]["code"]) is not str
              or value["error"]["code"] not in _ERROR_CODES):
            raise ValueError
    except (KeyError, ValueError, TypeError):
        raise KnowledgeTransportFailure(outcome_unknown=True) from None


def _trusted_file(value, *, script: bool) -> str:
    try:
        path = Path(value)
        if not path.is_absolute() or not path.is_file():
            raise ValueError
        leaf = path.lstat()
        target = path.stat()  # follows a POSIX venv interpreter link
        if not stat.S_ISREG(target.st_mode):
            raise ValueError
        if script and path.is_symlink():
            raise ValueError
        if os.name == "nt" and leaf.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError
        if script and path.suffix.lower() != ".py":
            raise ValueError
        if not script and not os.access(path, os.X_OK):
            raise ValueError
        return str(path)
    except (OSError, TypeError, ValueError, AttributeError):
        raise ValueError("trusted executable and script files required") from None


def _trusted_environment(value) -> dict[str, str]:
    if value is None:
        return {}
    try:
        entries = value.items()
    except AttributeError:
        raise ValueError("invalid trusted child environment") from None
    result = {}
    seen = set()
    total = 0
    try:
        for key, item in entries:
            if type(key) is not str or type(item) is not str or not _ENV_KEY.fullmatch(key):
                raise ValueError
            folded = key.casefold()
            if folded in seen or "\x00" in key or "\x00" in item:
                raise ValueError
            seen.add(folded)
            total += len(key.encode("utf-8")) + len(item.encode("utf-8")) + 2
            if total > 64 * 1024:
                raise ValueError
            result[key] = item
        return result
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("invalid trusted child environment") from None


def _private_environment(directory: str, python_executable: str, extra: dict[str, str]) -> dict[str, str]:
    environment = {
        "HOME": directory, "TMPDIR": directory, "TMP": directory, "TEMP": directory,
        "GRAPHITI_TELEMETRY_ENABLED": "false", "PYTHONNOUSERSITE": "1",
    }
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
        environment["PATH"] = str(Path(python_executable).parent)
        if system_root:
            environment["SystemRoot"] = system_root
            environment["WINDIR"] = system_root
            environment["PATH"] += os.pathsep + str(Path(system_root) / "System32")
        environment["USERPROFILE"] = directory
    else:
        environment["PATH"] = os.defpath
        environment["LANG"] = "C.UTF-8"
    environment.update(extra)
    return environment


def _write_request(pipe, frame: bytes, events: queue.Queue) -> None:
    try:
        view = memoryview(frame)
        while view:
            written = pipe.write(view)
            if not written:
                raise OSError
            view = view[written:]
        pipe.flush()
        events.put(("writer_done", None))
    except (OSError, ValueError):
        events.put(("writer_error", None))
    finally:
        try:
            pipe.close()
        except (OSError, ValueError):
            pass


def _read_exact(pipe, length: int) -> bytes:
    result = bytearray()
    while len(result) < length:
        chunk = pipe.read(length - len(result))
        if not chunk:
            raise ValueError
        result.extend(chunk)
    return bytes(result)


def _read_response(pipe, events: queue.Queue, response_limit=_RESPONSE_MAX) -> None:
    try:
        header = _read_exact(pipe, 4)
        length = int.from_bytes(header, "big")
        if not 0 < length <= response_limit:
            raise ValueError
        body = _read_exact(pipe, length)
        if pipe.read(1) != b"":
            raise ValueError
        events.put(("reader_done", body))
    except (OSError, ValueError, TypeError):
        events.put(("reader_error", None))


def _stop_owned(owner, threads) -> None:
    owner.stop(threads)


class KnowledgeProcessClient:
    def _validate_request(self, raw):
        return _request_id(raw)

    def _validate_reply(self, raw, request_id):
        _reply(raw, request_id)

    def _response_limit(self, raw):
        return _RESPONSE_MAX

    def __init__(self, python_executable, bootstrap_script, *, timeout_seconds=120,
                 child_environment=None, cooperative_tick=None):
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= 300):
            raise ValueError("timeout_seconds must be finite and in (0, 300]")
        self._python = _trusted_file(python_executable, script=False)
        self._script = _trusted_file(bootstrap_script, script=True)
        self._extra_environment = _trusted_environment(child_environment)
        self._timeout = float(timeout_seconds)
        self._lock = threading.Lock()
        if cooperative_tick is not None and not callable(cooperative_tick):
            raise ValueError("cooperative_tick must be callable")
        self._cooperative_tick = cooperative_tick

    def call(self, raw: bytes) -> bytes:
        if not self._lock.acquire(blocking=False):
            raise KnowledgeBusy()
        process = None
        owner = OwnedProcess()
        threads = []
        spawned = False
        private_directory = None
        failure = None
        response = None
        try:
            request_id = self._validate_request(raw)
            response_limit = self._response_limit(raw)
            frame = len(raw).to_bytes(4, "big") + raw
            try:
                if self._cooperative_tick is not None:
                    self._cooperative_tick()
                private_directory = tempfile.TemporaryDirectory(prefix="mirofish-knowledge-")
                owner.bind_private_directory(private_directory)
                directory = private_directory.name
                events = queue.Queue()
                creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                process = owner.start(subprocess.Popen,
                    [self._python, "-I", "-u", self._script],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    shell=False, close_fds=True, cwd=directory,
                    env=_private_environment(directory, self._python, self._extra_environment),
                    creationflags=creationflags,
                )
                spawned = True
                deadline = time.monotonic() + self._timeout
                if self._cooperative_tick is not None:
                    self._cooperative_tick()
                writer = threading.Thread(target=_write_request,
                                          args=(process.stdin, frame, events), daemon=True,
                                          name="mirofish-knowledge-writer")
                reader = threading.Thread(target=_read_response,
                                          args=(process.stdout, events, response_limit), daemon=True,
                                          name="mirofish-knowledge-reader")
                threads = [writer, reader]
                writer.start()
                reader.start()
                completed = set()
                body = None
                while completed != {"writer_done", "reader_done"}:
                    if self._cooperative_tick is not None:
                        self._cooperative_tick()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise KnowledgeTransportFailure(outcome_unknown=True)
                    try:
                        event, value = events.get(timeout=min(1.0, remaining) if self._cooperative_tick is not None else remaining)
                    except queue.Empty:
                        if self._cooperative_tick is not None:
                            continue
                        raise KnowledgeTransportFailure(outcome_unknown=True) from None
                    if event in {"writer_error", "reader_error"}:
                        raise KnowledgeTransportFailure(outcome_unknown=True)
                    completed.add(event)
                    if event == "reader_done":
                        body = value
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise KnowledgeTransportFailure(outcome_unknown=True)
                if self._cooperative_tick is None:
                    process.wait(timeout=remaining)
                else:
                    while process.poll() is None:
                        self._cooperative_tick()
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise KnowledgeTransportFailure(outcome_unknown=True)
                        try:
                            process.wait(timeout=min(1.0, remaining))
                        except subprocess.TimeoutExpired:
                            pass
                    self._cooperative_tick()
                if process.returncode != 0:
                    raise KnowledgeTransportFailure(outcome_unknown=True)
                self._validate_reply(body, request_id)
                response = body
            except KnowledgeTransportError as exc:
                failure = exc
            except Exception:
                failure = KnowledgeTransportFailure(outcome_unknown=spawned or owner.started)
            except BaseException as exc:
                failure = exc
            finally:
                try:
                    _stop_owned(owner, threads)
                except BaseException as cleanup_error:
                    if not isinstance(failure, (KeyboardInterrupt, SystemExit)):
                        failure = (cleanup_error if isinstance(cleanup_error, (KeyboardInterrupt, SystemExit))
                                   else KnowledgeTransportFailure(outcome_unknown=spawned or owner.started))
                if private_directory is not None:
                    try:
                        owner.cleanup_private_directory(private_directory)
                    except BaseException as cleanup_error:
                        if not isinstance(failure, (KeyboardInterrupt, SystemExit)):
                            failure = (cleanup_error if isinstance(cleanup_error, (KeyboardInterrupt, SystemExit))
                                       else KnowledgeTransportFailure(outcome_unknown=spawned or owner.started))
            if failure is not None:
                raise failure
            return response
        finally:
            self._lock.release()

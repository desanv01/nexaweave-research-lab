"""One-shot, bounded child-process entry for FileParser.

This is a process-failure boundary, not an operating-system sandbox.
"""

import json
import math
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from pathlib import Path

from .owned_process import OwnedProcess, OwnedProcessError

from .file_parser import (
    FileParser,
    InvalidSourceError,
    MalformedDocumentError,
    ParseError,
    ParseLimitError,
    ParseLimits,
    UnsupportedDocumentError,
    _SCANNED_OCR_MAX_TEXT_BYTES,
)


_PROTOCOL_VERSION = 1
_OCR_PROTOCOL_VERSION = 2
_OCR_JOB_COMMIT_LIMIT_BYTES = 268435456
_MAX_REQUEST_BYTES = 16 * 1024
_WORKER_SCRIPT = Path(__file__).with_name("parser_worker.py")
_ERROR_TYPES = {
    "limit_exceeded": ParseLimitError,
    "unsupported_document": UnsupportedDocumentError,
    "malformed_document": MalformedDocumentError,
    "invalid_source": InvalidSourceError,
}


class ParserTimeoutError(ParseError):
    code = "parser_timeout"


class ParserFailedError(ParseError):
    code = "parser_failed"


class ParserProtocolError(ParseError):
    code = "parser_protocol_error"


_ERROR_TYPES.update(
    {
        "parse_error": ParseError,
        "parser_timeout": ParserTimeoutError,
        "parser_failed": ParserFailedError,
        "parser_protocol_error": ParserProtocolError,
    }
)


def _reject_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ParserProtocolError()
        value[key] = item
    return value


def _reject_constant(_constant):
    raise ParserProtocolError()


def _request_bytes(file_path, limits, *, version=_PROTOCOL_VERSION):
    if type(file_path) is str:
        path = file_path
    elif isinstance(file_path, os.PathLike):
        try:
            path = os.fspath(file_path)
        except (TypeError, ValueError, OSError):
            raise InvalidSourceError() from None
        if type(path) is not str:
            raise InvalidSourceError()
    else:
        raise InvalidSourceError()
    if not path or "\x00" in path:
        raise InvalidSourceError()
    try:
        absolute_path = os.path.abspath(path)
        request = {
            "version": version,
            "file_path": absolute_path,
            "limits": asdict(limits),
        }
        payload = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (OSError, TypeError, ValueError, UnicodeError):
        raise InvalidSourceError() from None
    if len(payload) > _MAX_REQUEST_BYTES:
        raise ParseLimitError()
    return payload


def _validated_timeout(timeout_seconds):
    if type(timeout_seconds) not in (int, float):
        raise ValueError("timeout_seconds must be finite and in (0, 120]")
    try:
        finite = math.isfinite(timeout_seconds)
    except OverflowError:
        finite = False
    if not finite or not 0 < timeout_seconds <= 120:
        raise ValueError("timeout_seconds must be finite and in (0, 120]")
    return float(timeout_seconds)


def _private_environment(directory):
    environment = {
        "HOME": directory,
        "TMPDIR": directory,
        "TMP": directory,
        "TEMP": directory,
    }
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
        if system_root:
            environment["SystemRoot"] = system_root
            environment["WINDIR"] = system_root
            environment["PATH"] = os.pathsep.join(
                (str(Path(sys.executable).parent), str(Path(system_root) / "System32"))
            )
        else:
            environment["PATH"] = str(Path(sys.executable).parent)
        environment["USERPROFILE"] = directory
    else:
        environment["PATH"] = os.defpath
        environment["LANG"] = "C.UTF-8"
    return environment


def _send_request(pipe, payload, events):
    try:
        remaining = memoryview(payload)
        while remaining:
            written = pipe.write(remaining)
            if not written:
                raise OSError("child stdin closed")
            remaining = remaining[written:]
        events.put(("writer_done", None))
    except (OSError, ValueError):
        events.put(("writer_error", None))
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _read_response(pipe, byte_limit, events):
    data = bytearray()
    try:
        while True:
            chunk = pipe.read(min(65536, byte_limit + 1 - len(data)))
            if not chunk:
                events.put(("reader_done", bytes(data)))
                return
            data.extend(chunk)
            if len(data) > byte_limit:
                events.put(("reader_overflow", None))
                return
    except (OSError, ValueError):
        events.put(("reader_error", None))


def _stop_and_reap(owner, threads):
    owner.stop(threads)


def _parse_response(data, limits, *, version=_PROTOCOL_VERSION):
    if not data.startswith(b"{") or not data.endswith(b"}"):
        raise ParserProtocolError()
    try:
        response = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise ParserProtocolError() from None
    if type(response) is not dict or type(response.get("version")) is not int:
        raise ParserProtocolError()
    if response["version"] != version:
        raise ParserProtocolError()
    if set(response) == {"version", "text"} and type(response["text"]) is str:
        text = response["text"]
        if len(text) > limits.max_text_chars:
            raise ParserProtocolError()
        if version == _OCR_PROTOCOL_VERSION:
            try:
                if not text.strip() or len(text.encode("utf-8")) > _SCANNED_OCR_MAX_TEXT_BYTES:
                    raise ParserProtocolError()
            except UnicodeError:
                raise ParserProtocolError() from None
        return text
    if set(response) == {"version", "error"} and type(response["error"]) is str:
        code = response["error"]
        if code == "missing_file":
            raise FileNotFoundError("source file not found")
        error_type = _ERROR_TYPES.get(code)
        if error_type is not None:
            raise error_type()
    raise ParserProtocolError()


def extract_text_isolated(file_path, *, limits=None, timeout_seconds=30) -> str:
    """Extract one source using the fixed sibling worker and a strict deadline."""
    return _extract_isolated(
        file_path, limits, timeout_seconds, version=_PROTOCOL_VERSION,
    )


def extract_scanned_pdf_text_isolated(file_path, *, limits=None, timeout_seconds=30) -> str:
    """Recognize one admitted grayscale scan in the owned OCR worker lane."""
    return _extract_isolated(
        file_path, limits, timeout_seconds, version=_OCR_PROTOCOL_VERSION,
    )


def _extract_isolated(file_path, limits, timeout_seconds, *, version):
    timeout = _validated_timeout(timeout_seconds)
    if limits is None:
        limits = ParseLimits()
    elif type(limits) is not ParseLimits:
        raise TypeError("limits must be ParseLimits")
    # A frozen dataclass can still be altered with object.__setattr__.
    # Reconstruct it so its field validation runs before any process launch.
    limits = ParseLimits(
        **{name: getattr(limits, name) for name in ParseLimits.__dataclass_fields__}
    )
    payload = _request_bytes(file_path, limits, version=version)
    char_limit = limits.max_text_chars
    ocr = version == _OCR_PROTOCOL_VERSION
    if ocr:
        char_limit = min(char_limit, _SCANNED_OCR_MAX_TEXT_BYTES)
    job_profile = {"job_commit_limit_bytes": _OCR_JOB_COMMIT_LIMIT_BYTES} if ocr else {}
    byte_limit = char_limit * 6 + 4096
    deadline = None
    process = None
    owner = OwnedProcess()
    threads = []
    events = queue.Queue()

    try:
        private_directory = tempfile.TemporaryDirectory(prefix="nexaweave-parser-")
        owner.bind_private_directory(private_directory)
    except OSError:
        raise ParserFailedError() from None
    try:
        directory = private_directory.name
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            process = owner.start(subprocess.Popen,
                [sys.executable, "-I", "-u", str(_WORKER_SCRIPT)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                close_fds=True,
                cwd=directory,
                env=_private_environment(directory),
                creationflags=creationflags,
                **job_profile,
            )
            deadline = time.monotonic() + timeout
            writer = threading.Thread(
                target=_send_request,
                args=(process.stdin, payload, events),
                daemon=True,
                name="nexaweave-parser-writer",
            )
            reader = threading.Thread(
                target=_read_response,
                args=(process.stdout, byte_limit, events),
                daemon=True,
                name="nexaweave-parser-reader",
            )
            threads = [writer, reader]
            writer.start()
            reader.start()

            completed = set()
            response_bytes = None
            while completed != {"writer_done", "reader_done"}:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ParserTimeoutError()
                try:
                    event, value = events.get(timeout=remaining)
                except queue.Empty:
                    raise ParserTimeoutError() from None
                if event == "reader_overflow":
                    raise ParserProtocolError()
                if event in {"writer_error", "reader_error"}:
                    raise ParserFailedError()
                completed.add(event)
                if event == "reader_done":
                    response_bytes = value
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ParserTimeoutError()
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                raise ParserTimeoutError() from None
            if process.returncode != 0:
                raise ParserFailedError()
        except (OSError, RuntimeError, subprocess.SubprocessError):
            raise ParserFailedError() from None
        finally:
            interrupted = isinstance(sys.exc_info()[1], (KeyboardInterrupt, SystemExit))
            try:
                _stop_and_reap(owner, threads)
            except OwnedProcessError:
                if not interrupted:
                    raise ParserFailedError() from None
    finally:
        interrupted = isinstance(sys.exc_info()[1], (KeyboardInterrupt, SystemExit))
        try:
            owner.cleanup_private_directory(private_directory)
        except (OSError, OwnedProcessError):
            if not interrupted:
                raise ParserFailedError() from None
    text = _parse_response(response_bytes, limits, version=version)
    if ocr:
        accounting = owner.terminal_job_memory
        if accounting is None or accounting[0] != _OCR_JOB_COMMIT_LIMIT_BYTES:
            raise ParserFailedError()
    return text

"""Fixed one-shot parser child. Do not import the application package here."""

import importlib.util
import json
import os
import sys
from pathlib import Path


_VERSION = 1
_MAX_REQUEST_BYTES = 16 * 1024
_LIMIT_FIELDS = frozenset(
    {
        "max_file_bytes",
        "max_text_chars",
        "max_pdf_pages",
        "max_files",
        "max_aggregate_chars",
    }
)
_ALLOWED_PARSE_ERRORS = frozenset(
    {
        "limit_exceeded",
        "unsupported_document",
        "malformed_document",
        "invalid_source",
        "parse_error",
    }
)


class _ProtocolFailure(Exception):
    pass


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _ProtocolFailure()
        result[key] = value
    return result


def _reject_constant(_value):
    raise _ProtocolFailure()


def _load_file_parser():
    source = Path(__file__).with_name("file_parser.py")
    spec = importlib.util.spec_from_file_location("_nexaweave_file_parser", source)
    if spec is None or spec.loader is None:
        raise ImportError("parser module unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _request():
    raw = sys.stdin.buffer.read(_MAX_REQUEST_BYTES + 1)
    if len(raw) > _MAX_REQUEST_BYTES:
        raise _ProtocolFailure()
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise _ProtocolFailure() from None
    if type(value) is not dict or set(value) != {"version", "file_path", "limits"}:
        raise _ProtocolFailure()
    if type(value["version"]) is not int or value["version"] != _VERSION:
        raise _ProtocolFailure()
    path = value["file_path"]
    if type(path) is not str or not path or "\x00" in path or not os.path.isabs(path):
        raise _ProtocolFailure()
    fields = value["limits"]
    if type(fields) is not dict or set(fields) != _LIMIT_FIELDS:
        raise _ProtocolFailure()
    if any(type(item) is not int or item <= 0 for item in fields.values()):
        raise _ProtocolFailure()
    return path, fields


def _run():
    try:
        path, fields = _request()
    except _ProtocolFailure:
        return {"version": _VERSION, "error": "parser_protocol_error"}
    try:
        parser = _load_file_parser()
        limits = parser.ParseLimits(**fields)
        text = parser.FileParser.extract_text(path, limits=limits)
        if type(text) is not str or len(text) > limits.max_text_chars:
            return {"version": _VERSION, "error": "parser_protocol_error"}
        return {"version": _VERSION, "text": text}
    except FileNotFoundError:
        return {"version": _VERSION, "error": "missing_file"}
    except Exception as error:
        code = getattr(error, "code", None)
        if type(code) is str and code in _ALLOWED_PARSE_ERRORS:
            return {"version": _VERSION, "error": code}
        return {"version": _VERSION, "error": "parser_failed"}


def main():
    result = _run()
    output = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(output)


if __name__ == "__main__":
    main()


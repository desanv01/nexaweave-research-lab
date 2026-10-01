"""Trusted local import command; stdout contains only fixed safe summaries."""
from __future__ import annotations

import os
import sys

from .research_bundle import BundleError, canonical, decode
from .research_bundle_cli import MAX_REQUEST_BYTES, _path, _read
from .research_import import ImportError, ResearchImportStore, prepare_import
from .validation import HEX, InvalidProject, principal_id, uuid_value


def parse_request(raw):
    try:
        if type(raw) is not bytes or len(raw) > MAX_REQUEST_BYTES:
            raise ValueError()
        request = decode(raw)
        if type(request) is not dict or set(request) != {
                "operation", "principal", "target_project_id", "expected_revision", "input", "expected_sha256"}:
            raise ValueError()
        if request["operation"] != "import":
            raise ValueError()
        principal_id(request["principal"])
        if type(request["target_project_id"]) is not str:
            raise ValueError()
        uuid_value(request["target_project_id"])
        if type(request["expected_revision"]) is not int or not 1 <= request["expected_revision"] <= 2_147_483_647:
            raise ValueError()
        if type(request["expected_sha256"]) is not str or not HEX.fullmatch(request["expected_sha256"]):
            raise ValueError()
        _path(request["input"])
        return request
    except (ValueError, TypeError, UnicodeError, RecursionError, InvalidProject):
        raise ImportError("invalid_request") from None


def _store():
    if any(key.upper().startswith("PG") for key in os.environ):
        raise ImportError("authority_unavailable")
    dsn = os.environ.get("MIROFISH_APPSTORE_DSN")
    if not dsn:
        raise ImportError("authority_unavailable")
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    try:
        settings = conninfo_to_dict(dsn)
    except psycopg.Error:
        raise ImportError("authority_unavailable") from None
    if any(key in settings for key in ("service", "servicefile", "passfile")):
        raise ImportError("authority_unavailable")
    return ResearchImportStore(lambda: psycopg.connect(dsn, connect_timeout=3, passfile=os.devnull))


def main(*, stdin=None, stdout=None):
    stdin = sys.stdin.buffer if stdin is None else stdin
    binary = stdout is None
    stdout = sys.stdout.buffer if binary else stdout
    try:
        request = parse_request(stdin.read(MAX_REQUEST_BYTES + 1))
        raw = _read(_path(request["input"]))
        arguments = (raw, request["principal"], request["target_project_id"],
                     request["expected_revision"], request["expected_sha256"])
        # Validate before reading DSN/constructing any database dependency.
        prepare_import(*arguments)
        receipt = _store().import_bundle(*arguments)
        reply, status = {"ok": True, "result": receipt.summary()}, 0
    except ImportError as error:
        reply, status = {"ok": False, "error": error.code}, 2
    except BundleError as error:
        code = "invalid_path" if error.code == "invalid_path" else "invalid_import"
        reply, status = {"ok": False, "error": code}, 2
    except OSError:
        reply, status = {"ok": False, "error": "invalid_path"}, 2
    except Exception as error:
        from .store import Conflict, NotFound, StorageError
        code = ("import_denied" if isinstance(error, NotFound) else
                "conflict" if isinstance(error, Conflict) else
                "storage_error" if isinstance(error, StorageError) else "authority_unavailable")
        reply, status = {"ok": False, "error": code}, 2
    encoded = canonical(reply) + b"\n"
    stdout.write(encoded if binary else encoded.decode("utf-8"))
    return status


if __name__ == "__main__":
    raise SystemExit(main())

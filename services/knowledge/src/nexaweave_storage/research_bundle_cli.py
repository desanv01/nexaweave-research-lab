"""Bounded stdin JSON export/inspect interface for trusted local files."""
from __future__ import annotations
from nexaweave_knowledge.configuration import environment

import os
from pathlib import Path
import stat
import sys

from .research_bundle import (BundleError, MAX_ARTIFACT_BYTES, canonical, decode,
                              export_bundle, inspect_bundle)
from .validation import InvalidProject, principal_id, uuid_value

MAX_REQUEST_BYTES = 32768


def _path(value):
    if (type(value) is not str or not value or len(value) > 4096
            or not os.path.isabs(value) or os.path.normpath(value) != value
            or value.startswith(("\\\\", "//")) or ":" in os.path.splitdrive(value)[1]
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or any(part in (".", "..") for part in Path(value).parts)):
        raise BundleError("invalid_request")
    value.encode("utf-8")
    return Path(value)


def parse_request(raw):
    try:
        if type(raw) is not bytes or len(raw) > MAX_REQUEST_BYTES:
            raise ValueError()
        data = decode(raw)
        if type(data) is not dict:
            raise ValueError()
        operation = data.get("operation")
        if operation == "inspect":
            if set(data) not in ({"operation", "input"}, {"operation", "input", "expected_sha256"}):
                raise ValueError()
            _path(data["input"])
            if "expected_sha256" in data:
                from .validation import HEX
                if type(data["expected_sha256"]) is not str or not HEX.fullmatch(data["expected_sha256"]):
                    raise ValueError()
        elif operation == "export":
            if set(data) != {"operation", "principal", "project_id", "revision", "source_revisions", "output"}:
                raise ValueError()
            principal_id(data["principal"])
            if type(data["project_id"]) is not str:
                raise ValueError()
            uuid_value(data["project_id"])
            if type(data["revision"]) is not int or not 1 <= data["revision"] <= 2_147_483_647:
                raise ValueError()
            selected = data["source_revisions"]
            if type(selected) is not list or not 1 <= len(selected) <= 16:
                raise ValueError()
            for item in selected:
                if type(item) is not str:
                    raise ValueError()
                uuid_value(item)
            if len(set(selected)) != len(selected):
                raise ValueError()
            _path(data["output"])
        else:
            raise ValueError()
        return data
    except (ValueError, TypeError, UnicodeError, RecursionError, InvalidProject):
        raise BundleError("invalid_request") from None


def _safe(value, directory=False):
    if (stat.S_ISLNK(value.st_mode)
            or getattr(value, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            or not (stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode))):
        raise BundleError("invalid_path")


def _identity(value):
    return value.st_dev, value.st_ino


def _ancestors(path):
    values = [(parent, parent.lstat()) for parent in reversed(path.parents)]
    for _, value in values:
        _safe(value, directory=True)
    return values


def _unchanged(values):
    for path, before in values:
        after = path.lstat()
        _safe(after, directory=True)
        if _identity(before) != _identity(after):
            raise BundleError("invalid_path")


def _read(path):
    ancestors = _ancestors(path)
    before = path.lstat()
    _safe(before)
    if before.st_size > MAX_ARTIFACT_BYTES:
        raise BundleError("invalid_bundle")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        _safe(opened)
        if _identity(before) != _identity(opened):
            raise BundleError("invalid_path")
        raw = stream.read(MAX_ARTIFACT_BYTES + 1)
        after = os.fstat(stream.fileno())
        if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise BundleError("invalid_path")
    final = path.lstat()
    _safe(final)
    # Windows can report different ctime semantics through path lstat and
    # descriptor fstat. Compare ctime within each API, never across them.
    if (_identity(final), final.st_size, final.st_mtime_ns, final.st_ctime_ns) != (
            _identity(before), before.st_size, before.st_mtime_ns, before.st_ctime_ns):
        raise BundleError("invalid_path")
    if (_identity(final), final.st_size, final.st_mtime_ns) != (
            _identity(opened), opened.st_size, opened.st_mtime_ns):
        raise BundleError("invalid_path")
    _unchanged(ancestors)
    return raw


def _write_new(path, raw):
    ancestors = _ancestors(path)
    created = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            opened = os.fstat(stream.fileno())
            created = _identity(opened)
            _safe(opened)
            _unchanged(ancestors)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        final = path.lstat()
        _safe(final)
        if _identity(final) != created or final.st_size != len(raw):
            raise BundleError("invalid_path")
        _unchanged(ancestors)
    except Exception:
        # Only remove the file we actually created, never an existing output or
        # a replacement. These checks do not promise a hostile filesystem sandbox.
        if created is not None:
            try:
                _unchanged(ancestors)
                current = path.lstat()
                _safe(current)
                if _identity(current) == created:
                    path.unlink()
            except (OSError, BundleError):
                pass
        raise


def _stores():
    # Reject all libpq ambient settings, including unlisted future overrides.
    if any(key.upper().startswith("PG") for key in os.environ):
        raise BundleError("authority_unavailable")
    dsn = environment.get("NEXAWEAVE_APPSTORE_DSN")
    if not dsn:
        raise BundleError("authority_unavailable")
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    from .store import ProjectStore
    from .source import SourceStore
    settings = conninfo_to_dict(dsn)
    if any(key in settings for key in ("service", "servicefile", "passfile")):
        raise BundleError("authority_unavailable")
    # Explicitly disable default password-file discovery. Request data cannot
    # supply a DSN, service, password file or connection option.
    def connect():
        return psycopg.connect(dsn, connect_timeout=3, passfile=os.devnull)
    return ProjectStore(connect), SourceStore(connect)


def main(*, stdin=None, stdout=None):
    stdin = sys.stdin.buffer if stdin is None else stdin
    binary = stdout is None
    stdout = sys.stdout.buffer if binary else stdout
    try:
        request = parse_request(stdin.read(MAX_REQUEST_BYTES + 1))
        if request["operation"] == "inspect":
            result = inspect_bundle(_read(_path(request["input"])), request.get("expected_sha256"))
        else:
            project_store, source_store = _stores()
            raw = export_bundle(project_store, source_store, request["principal"],
                                request["project_id"], request["revision"], request["source_revisions"])
            result = inspect_bundle(raw)
            _write_new(_path(request["output"]), raw)
        reply, status = {"ok": True, "result": result}, 0
    except BundleError as error:
        reply, status = {"ok": False, "error": error.code}, 2
    except OSError:
        reply, status = {"ok": False, "error": "invalid_path"}, 2
    except Exception as error:
        from .store import NotFound
        code = "bundle_denied" if isinstance(error, NotFound) else "authority_unavailable"
        reply, status = {"ok": False, "error": code}, 2
    encoded = canonical(reply) + b"\n"
    stdout.write(encoded if binary else encoded.decode("utf-8"))
    return status


if __name__ == "__main__":
    raise SystemExit(main())

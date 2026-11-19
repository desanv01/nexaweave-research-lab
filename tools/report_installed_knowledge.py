"""Compare installed knowledge-profile metadata with a trusted uv lock offline."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack, contextmanager
from email.parser import BytesHeaderParser
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tomllib


LOCK_NAME = "services/knowledge/uv.lock"
# Same source-blob and inventory record limits as tools/release/source_bundle.py.
MAX_LOCK_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 8192
MAX_HEADER_BYTES = 64 * 1024
MAX_METADATA_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
NAME = re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9._-]{0,126}[a-zA-Z0-9])?")
VERSION = re.compile(r"[vV]?[0-9][a-zA-Z0-9]*(?:[.!+_-][a-zA-Z0-9]+)*")
HEADER_END = re.compile(rb"\r?\n\r?\n")
PROFILE = "test+source-pdf"
REQUIRED = ("graphiti-core", "nexaweave-knowledge", "pymupdf", "pytest", "pytest-asyncio")
PINS = {"graphiti-core": "0.30.2", "pymupdf": "1.26.7"}
WINDOWS_PATH_HELP = (
    "On Windows, use operator-controlled lock and site-packages paths. "
    "Keep both paths and their parent directories stable throughout the report."
)
DIR_FDS = (os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd
           and os.scandir in os.supports_fd and hasattr(os, "O_NOFOLLOW")
           and hasattr(os, "O_DIRECTORY"))
READ_FLAGS = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
              | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))


class ReportError(ValueError):
    """Fixed diagnostic code safe for JSON output."""


def explicit_path(value: str) -> Path:
    if not value or len(value) > 4096:
        raise ReportError("path_invalid")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or len(path.parts) > 64:
        raise ReportError("path_invalid")
    if os.name == "nt":
        # Ordinary local drive paths only. Reject device names and ADS syntax.
        is_reserved = getattr(os.path, "isreserved", None)
        reserved = is_reserved(path) if is_reserved is not None else path.is_reserved()
        if not re.fullmatch(r"[A-Za-z]:", path.drive) or reserved or any(
            ":" in part or part.endswith((" ", ".")) for part in path.parts[1:]
        ):
            raise ReportError("path_invalid")
    elif path.anchor != "/":
        raise ReportError("path_invalid")
    return path


def require_entry(info: os.stat_result, *, directory: bool) -> None:
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ReportError("path_alias")
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode):
        raise ReportError("path_type")


def same_entry(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def confirm_named_entry(path: Path, before: os.stat_result, *, directory: bool) -> None:
    try:
        current = path.lstat()
        require_entry(current, directory=directory)
    except (OSError, ReportError):
        raise ReportError("path_changed") from None
    if not same_entry(before, current):
        raise ReportError("path_changed")


@contextmanager
def entry(parent: int | Path, name: str, *, directory: bool = False):
    relative = isinstance(parent, int)
    target = name if relative else parent / name
    options = {"dir_fd": parent} if relative else {}
    before = os.stat(target, follow_symlinks=False, **options)
    require_entry(before, directory=directory)
    if directory and not relative:
        try:
            yield target
        finally:
            confirm_named_entry(target, before, directory=True)
        return

    flags = READ_FLAGS | os.O_DIRECTORY if directory else READ_FLAGS
    opened = os.open(target, flags, **options)
    try:
        actual = os.fstat(opened)
        require_entry(actual, directory=directory)
        if not same_entry(before, actual):
            raise ReportError("path_changed")
        if not relative:
            confirm_named_entry(target, actual, directory=False)
        try:
            yield opened
        finally:
            if not relative:
                confirm_named_entry(target, actual, directory=False)
    finally:
        os.close(opened)


@contextmanager
def open_directory(path: Path):
    anchor = Path(path.anchor)
    with ExitStack() as stack:
        if DIR_FDS:
            parent = os.open(anchor, READ_FLAGS | os.O_DIRECTORY)
            stack.callback(os.close, parent)
        else:
            before = anchor.lstat()
            require_entry(before, directory=True)
            stack.callback(confirm_named_entry, anchor, before, directory=True)
            parent = anchor
        for part in path.parts[1:]:
            parent = stack.enter_context(entry(parent, part, directory=True))
        yield parent


def package_name(value: object) -> str | None:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        return None
    return re.sub(r"[-_.]+", "-", value).lower()


def package_version(value: object) -> str | None:
    return value if isinstance(value, str) and len(value) <= 128 and VERSION.fullmatch(value) else None


def read_prefix(fd: int, limit: int, *, header: bool = False) -> tuple[bytes, int, bool]:
    data = bytearray()
    while len(data) < limit:
        search_from = max(0, len(data) - 3)
        chunk = os.read(fd, min(limit - len(data), 4096 if header else 1024 * 1024))
        if not chunk:
            break
        data.extend(chunk)
        if header and (marker := HEADER_END.search(data, search_from)):
            return bytes(data[:marker.end()]), len(data), True
    return bytes(data), len(data), False


def unchanged_file(fd: int, before: os.stat_result) -> None:
    after = os.fstat(fd)
    if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
        before.st_size, before.st_mtime_ns, before.st_ctime_ns
    ):
        raise ReportError("path_changed")


def lock_inventory(path: Path, expected: str) -> dict[str, set[str]]:
    try:
        with open_directory(path.parent) as parent, entry(parent, path.name) as fd:
            before = os.fstat(fd)
            if before.st_size > MAX_LOCK_BYTES:
                raise ReportError("lock_limit")
            raw, count, _ = read_prefix(fd, before.st_size)
            unchanged_file(fd, before)
            if count != before.st_size:
                raise ReportError("path_changed")
    except OSError:
        raise ReportError("lock_unavailable") from None
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ReportError("lock_hash_mismatch")
    try:
        lock = tomllib.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        raise ReportError("lock_invalid") from None
    rows = lock.get("package")
    if type(lock.get("version")) is not int or lock["version"] != 1 or not isinstance(rows, list):
        raise ReportError("lock_invalid")
    if len(rows) > MAX_RECORDS:
        raise ReportError("lock_limit")
    declared: dict[str, set[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ReportError("lock_invalid")
        name = package_name(row.get("name"))
        version = package_version(row.get("version"))
        if name is None or version is None:
            raise ReportError("lock_invalid")
        declared.setdefault(name, set()).add(version)
    if not all(name in declared for name in REQUIRED) or any(
        version not in declared[name] for name, version in PINS.items()
    ):
        raise ReportError("profile_lock_invalid")
    return declared


def directory_names(root: int | Path) -> list[str]:
    names = []
    with os.scandir(root) as entries:
        for count, child in enumerate(entries, 1):
            if count > MAX_RECORDS:
                raise ReportError("record_limit")
            if child.name.endswith(".dist-info"):
                names.append(child.name)
    return sorted(names)


def metadata_identity(raw: bytes) -> tuple[str, str] | None:
    # Header-only parsing. Duplicate, folded or invalid identity values are
    # untrustworthy; other header values and any description body are discarded.
    if b"\0" in raw:
        return None
    message = BytesHeaderParser().parsebytes(raw)
    names = message.get_all("Name", [])
    versions = message.get_all("Version", [])
    if message.defects or len(names) != 1 or len(versions) != 1:
        return None
    name = package_name(names[0].strip(" \t") if isinstance(names[0], str) else None)
    version = package_version(versions[0].strip(" \t") if isinstance(versions[0], str) else None)
    return (name, version) if name is not None and version is not None else None


def directory_matches(folder: str, identity: tuple[str, str]) -> bool:
    name, version = identity
    stem = folder.removesuffix(".dist-info")
    suffix = "-" + version
    return stem.endswith(suffix) and package_name(stem[:-len(suffix)]) == name


def metadata_records(site_packages: Path):
    remaining = MAX_METADATA_BYTES
    with open_directory(site_packages) as root:
        for folder in directory_names(root):
            try:
                with entry(root, folder, directory=True) as distribution, entry(distribution, "METADATA") as fd:
                    before = os.fstat(fd)
                    limit = min(before.st_size, MAX_HEADER_BYTES, remaining)
                    try:
                        raw, count, ended = read_prefix(fd, limit, header=True)
                    except OSError:
                        # Abort if a failed partial read cannot be charged to the budget.
                        raise ReportError("metadata_read_failed") from None
                    remaining -= count
                    unchanged_file(fd, before)
            except OSError:
                yield None, "metadata_unavailable"
                continue
            except ReportError as exc:
                if str(exc) in {"path_changed", "metadata_read_failed"}:
                    raise
                yield None, str(exc)
                continue
            if not ended and count < before.st_size:
                if count < limit:
                    raise ReportError("path_changed")
                if limit < min(before.st_size, MAX_HEADER_BYTES):
                    raise ReportError("metadata_budget")
                yield None, "header_limit"
                continue
            identity = metadata_identity(raw)
            if identity is None:
                yield None, "identity_invalid"
            elif not directory_matches(folder, identity):
                yield None, "directory_identity_mismatch"
            else:
                yield identity, None


def report(lock_path: Path, expected: str, site_packages: Path) -> dict:
    declared = lock_inventory(lock_path, expected)
    try:
        entries = list(metadata_records(site_packages))
    except OSError:
        raise ReportError("site_packages_unavailable") from None
    occurrences = Counter(identity[0] for identity, _ in entries if identity is not None)
    records = []
    witnessed = set()
    for identity, reason in entries:
        if identity is None:
            records.append({"name": None, "version": None, "status": "malformed", "reason": reason})
            continue
        name, version = identity
        if occurrences[name] > 1:
            status = "duplicate"
        elif name not in declared:
            status = "unlisted"
        elif version not in declared[name] or (name in PINS and version != PINS[name]):
            status = "mismatch"
        else:
            status = "matched"
            witnessed.add(name)
        records.append({"name": name, "version": version, "status": status})
    records.sort(key=lambda row: (row["name"] is None, row["name"] or "",
                                  row["version"] or "", row.get("reason", "")))
    return {"ok": True, "schema_version": 1, "profile": PROFILE,
            "lock_path": LOCK_NAME, "lock_sha256": expected, "packages": records,
            "missing_required_witnesses": sorted(set(REQUIRED) - witnessed)}


class Parser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise ReportError("arguments_invalid")


def json_line(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def main(argv: list[str] | None = None) -> int:
    parser = Parser(prog="report_installed_knowledge.py", description=__doc__,
                    epilog=WINDOWS_PATH_HELP, allow_abbrev=False)
    parser.add_argument("--lock", required=True, help="absolute path to accepted knowledge lock bytes")
    parser.add_argument("--expected-sha256", required=True, help="trusted lowercase SHA-256 of the lock")
    parser.add_argument("--site-packages", required=True, help="absolute path to the qualified installed profile")
    try:
        args = parser.parse_args(argv)
        if not re.fullmatch(r"[0-9a-f]{64}", args.expected_sha256):
            raise ReportError("hash_invalid")
        result = report(explicit_path(args.lock), args.expected_sha256, explicit_path(args.site_packages))
        encoded = json_line(result)
        if len(encoded) > MAX_OUTPUT_BYTES:
            raise ReportError("output_limit")
    except (OSError, ValueError) as exc:
        error = str(exc) if isinstance(exc, ReportError) else "input_invalid"
        sys.stdout.buffer.write(json_line({"error": error, "ok": False}))
        return 2
    sys.stdout.buffer.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

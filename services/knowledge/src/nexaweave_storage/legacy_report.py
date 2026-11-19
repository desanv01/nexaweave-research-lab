"""Exact-byte custody for one completed, nested legacy report layout."""
import hashlib
import json
import os
from pathlib import Path
import stat

from .store import Conflict, NotFound, StorageError, _transaction
from .validation import InvalidProject, display_id, principal_id, uuid_value

META_LIMIT = 65536
MARKDOWN_LIMIT = 2 * 1024 * 1024
_FIELDS = {"report_id", "simulation_id", "graph_id", "simulation_requirement",
           "status", "outline", "markdown_content", "created_at", "completed_at", "error"}


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _regular_bytes(directory, name, limit):
    def regular(info):
        return (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and not getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)

    def identity(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    target = directory / name
    try:
        before = target.lstat()
        if not regular(before) or not 0 < before.st_size <= limit:
            raise InvalidProject()
        with target.open("rb") as source:
            opened = os.fstat(source.fileno())
            if not regular(opened) or identity(opened) != identity(before):
                raise InvalidProject()
            data = source.read(limit + 1)
            after = os.fstat(source.fileno())
        named = target.lstat()
        if (len(data) != before.st_size or not regular(named) or not regular(after)
                or identity(before) != identity(after) or identity(before) != identity(named)):
            raise InvalidProject()
        return data
    except OSError:
        raise InvalidProject() from None


def read_original_pair(input_directory):
    """Read the two fixed names from a stable operator-controlled directory."""
    directory = Path(input_directory)
    try:
        if not directory.is_absolute() or directory.resolve(strict=True) != directory:
            raise InvalidProject()
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise InvalidProject()
    except (OSError, RuntimeError):
        raise InvalidProject() from None
    return (_regular_bytes(directory, "meta.json", META_LIMIT),
            _regular_bytes(directory, "full_report.md", MARKDOWN_LIMIT))


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise InvalidProject()
        value[key] = item
    return value


def _invalid_constant(_value):
    raise InvalidProject()


def _validate_pair(legacy_report_id, meta, markdown):
    if (type(meta) is not bytes or not 0 < len(meta) <= META_LIMIT
            or type(markdown) is not bytes or not 0 < len(markdown) <= MARKDOWN_LIMIT):
        raise InvalidProject()
    try:
        record = json.loads(meta.decode("utf-8"), object_pairs_hook=_pairs,
                            parse_constant=_invalid_constant)
        text = markdown.decode("utf-8")
        if (type(record) is not dict or set(record) != _FIELDS
                or record["report_id"] != legacy_report_id or record["status"] != "completed"
                or any(type(record[key]) is not str for key in _FIELDS - {"error", "outline"})
                or record["error"] is not None or not text.strip()):
            raise InvalidProject()
        # The original text-mode writer can translate newlines on Windows.
        embedded = record["markdown_content"]
        if embedded and embedded.replace("\r\n", "\n") != text.replace("\r\n", "\n"):
            raise InvalidProject()
        outline = record["outline"]
        if outline is not None:
            if (type(outline) is not dict or set(outline) != {"title", "summary", "sections"}
                    or type(outline["title"]) is not str or type(outline["summary"]) is not str
                    or type(outline["sections"]) is not list
                    or any(type(section) is not dict or set(section) != {"title", "content"}
                           or any(type(value) is not str for value in section.values())
                           for section in outline["sections"])):
                raise InvalidProject()
    except (ValueError, RecursionError, UnicodeError):
        raise InvalidProject() from None


_COLUMNS = "r.meta_bytes,r.meta_sha256,r.markdown_bytes,r.markdown_sha256"


def _verified(row):
    meta, meta_hash, markdown, markdown_hash = row
    if (type(meta) is not bytes or not 0 < len(meta) <= META_LIMIT
            or type(markdown) is not bytes or not 0 < len(markdown) <= MARKDOWN_LIMIT
            or _digest(meta) != meta_hash or _digest(markdown) != markdown_hash):
        raise StorageError()
    return meta, meta_hash, markdown, markdown_hash


class LegacyReportStore:
    def __init__(self, connection_factory):
        self._factory = connection_factory

    def register(self, principal, workspace_id, project_id, legacy_report_id, meta, markdown):
        principal = principal_id(principal)
        workspace_id, project_id = uuid_value(workspace_id), uuid_value(project_id)
        display_id(legacy_report_id)
        _validate_pair(legacy_report_id, meta, markdown)
        expected = (meta, _digest(meta), markdown, _digest(markdown))
        with _transaction(self._factory) as conn:
            if conn.execute(
                "SELECT 1 FROM mf_app.projects WHERE principal=%s AND workspace_id=%s "
                "AND project_id=%s FOR UPDATE", (principal, workspace_id, project_id),
            ).fetchone() is None:
                raise NotFound()
            conn.execute(
                "INSERT INTO mf_app.legacy_reports "
                "(project_id,legacy_report_id,meta_bytes,meta_sha256,markdown_bytes,markdown_sha256) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (project_id,legacy_report_id) DO NOTHING",
                (project_id, legacy_report_id, *expected),
            )
            actual = conn.execute(
                f"SELECT {_COLUMNS} FROM mf_app.legacy_reports r "
                "WHERE r.project_id=%s AND r.legacy_report_id=%s FOR UPDATE",
                (project_id, legacy_report_id),
            ).fetchone()
            if actual is None:
                raise StorageError()
            if _verified(actual) != expected:
                raise Conflict()
        return {"schema_version": 1, "status": "registered", "legacy_report_id": legacy_report_id,
                "meta_sha256": expected[1], "markdown_sha256": expected[3]}

    def read(self, principal, workspace_id, project_id, legacy_report_id):
        principal = principal_id(principal)
        workspace_id, project_id = uuid_value(workspace_id), uuid_value(project_id)
        display_id(legacy_report_id)
        with _transaction(self._factory) as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM mf_app.legacy_reports r "
                "JOIN mf_app.projects p ON p.project_id=r.project_id "
                "WHERE p.principal=%s AND p.workspace_id=%s AND p.project_id=%s AND r.legacy_report_id=%s",
                (principal, workspace_id, project_id, legacy_report_id),
            ).fetchone()
            if row is None:
                raise NotFound()
            _, _, markdown, markdown_hash = _verified(row)
        return markdown, markdown_hash

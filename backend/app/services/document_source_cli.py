"""One trusted local DOCX-to-owned-source operation, without app startup."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
from uuid import uuid5

MAX_REPLY = 10 * 1024 * 1024


class DocumentSourceError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise DocumentSourceError("invalid_binding")


def _load_extractor():
    source = Path(__file__).resolve().parents[1] / "utils" / "docx_extraction.py"
    name = "_mirofish_document_source_extractor"
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise DocumentSourceError("parser_unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _binding(argv):
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("operation", choices=("ingest-docx",))
    for name in ("principal", "project-id", "source-revision", "source-name",
                 "document-path", "expected-document-sha256"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    # Fixed repository storage package; no caller or wire path enters sys.path.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "services" / "knowledge" / "src"))
    from mirofish_storage.validation import principal_id, uuid_value
    try:
        args.principal = principal_id(args.principal)
        args.project_id = uuid_value(args.project_id)
        args.source_revision = uuid_value(args.source_revision)
        name = args.source_name
        if not name or len(name) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise ValueError()
        name.encode("utf-8")
        path = args.document_path
        if (not path or len(path) > 4096 or not os.path.isabs(path)
                or os.path.normpath(path) != path or path.startswith(("\\\\", "//"))
                or any(ord(c) < 32 or ord(c) == 127 for c in path)
                or Path(path).suffix.lower() != ".docx"
                or ":" in os.path.splitdrive(path)[1]
                or any(part in (".", "..") for part in Path(path).parts)):
            raise ValueError()
        path.encode("utf-8")
        if not re.fullmatch(r"[0-9a-f]{64}", args.expected_document_sha256):
            raise ValueError()
    except ValueError:
        raise DocumentSourceError("invalid_binding") from None
    return args


def _safe_stat(value, directory=False):
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if ((flag and getattr(value, "st_file_attributes", 0) & flag)
            or not (stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode))):
        raise DocumentSourceError("invalid_source")


def _identity(value):
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _read_document(path, maximum):
    target = Path(path)
    descriptors = []
    try:
        ancestors = tuple(reversed(target.parents))
        prior = tuple(item.lstat() for item in ancestors)
        for item in prior:
            _safe_stat(item, directory=True)
        before = target.lstat()
        _safe_stat(before)
        if before.st_size > maximum:
            raise DocumentSourceError("limit_exceeded")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
            # Descriptor traversal makes POSIX ancestor replacement unable to
            # redirect the admitted read through a symbolic link.
            directory = os.open(target.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(directory)
            for component in target.parts[1:-1]:
                directory = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                descriptors.append(directory)
            descriptor = os.open(target.name, flags, dir_fd=directory)
        else:
            descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as source:
            opened = os.fstat(source.fileno())
            _safe_stat(opened)
            if _identity(opened) != _identity(before):
                raise DocumentSourceError("invalid_source")
            data = source.read(maximum + 1)
            after = os.fstat(source.fileno())
            if _identity(after) != _identity(opened) or after.st_ctime_ns != opened.st_ctime_ns:
                raise DocumentSourceError("invalid_source")
        if len(data) > maximum:
            raise DocumentSourceError("limit_exceeded")
        final_path = target.lstat()
        _safe_stat(final_path)
        if _identity(final_path) != _identity(before) or final_path.st_ctime_ns != before.st_ctime_ns:
            raise DocumentSourceError("invalid_source")
        for item, old in zip(ancestors, prior):
            current = item.lstat()
            _safe_stat(current, directory=True)
            if (current.st_dev, current.st_ino) != (old.st_dev, old.st_ino):
                raise DocumentSourceError("invalid_source")
        return data
    except OSError:
        raise DocumentSourceError("invalid_source") from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _ingest(args):
    import psycopg
    from mirofish_storage import Conflict, NotFound, ProjectStore, SourceStore
    dsn = os.environ.get("MIROFISH_APPSTORE_DSN")
    if not dsn:
        raise DocumentSourceError("authority_unavailable")
    def connect():
        return psycopg.connect(dsn, connect_timeout=3)
    try:
        ProjectStore(connect).get(args.principal, args.project_id)
    except NotFound:
        raise DocumentSourceError("document_source_denied") from None
    # No document filesystem inspection occurs before the actual authority gate.
    extractor = _load_extractor()
    data = _read_document(args.document_path, extractor.MAX_INPUT)
    if hashlib.sha256(data).hexdigest() != args.expected_document_sha256:
        raise DocumentSourceError("document_sha256_mismatch")
    try:
        extracted = extractor.extract_docx(data, max_text_chars=1024 * 1024)
    except extractor.DocxError as error:
        raise DocumentSourceError(error.code) from None
    if not extracted.text.strip():
        raise DocumentSourceError("no_extractable_text")
    if len(extracted.text.encode("utf-8")) > 1024 * 1024:
        raise DocumentSourceError("limit_exceeded")
    declarations, blocks = [], []
    for ordinal, block in enumerate(extracted.blocks):
        item = asdict(block)
        item["ordinal"] = ordinal
        item["empty"] = block.start == block.end
        if not item["empty"]:
            if len(declarations) >= 100 or len(extracted.text[block.start:block.end].encode("utf-8")) > 32768:
                raise DocumentSourceError("limit_exceeded")
            identity = "docx-main-body-v1:" + _canonical(item).decode("utf-8")
            evidence = str(uuid5(args.source_revision, identity))
            declarations.append({"evidence_id": evidence, "start": block.start, "end": block.end})
            item["evidence_id"] = evidence
        else:
            item["evidence_id"] = None
        blocks.append(item)
    store = SourceStore(connect)
    try:
        record = store.ingest_text(args.principal, args.project_id, args.source_revision,
                                  args.source_name, extracted.text, declarations)
    except Conflict:
        raise DocumentSourceError("source_conflict") from None
    except NotFound:
        raise DocumentSourceError("document_source_denied") from None
    passages = []
    for passage in record.passages:
        resolved = store.resolve_evidence(args.principal, args.project_id, passage.evidence_id)
        value = asdict(resolved)
        for key in ("project_id", "source_revision", "evidence_id"):
            value[key] = str(value[key])
        value["source_recorded_at"] = resolved.source_recorded_at.isoformat()
        passages.append(value)
    return {"schema_version": 1, "project_id": str(record.project_id),
            "source_revision": str(record.source_revision), "source_name": record.name,
            "text_sha256": record.text_sha256, "byte_length": record.byte_length,
            "codepoint_length": record.codepoint_length, "offset_unit": "unicode_codepoint",
            "passages": passages, "blocks": blocks,
            "document_sha256": args.expected_document_sha256, "input_hash_verified": True,
            "binary_retained": False, "original_document_verified": False,
            "binary_persistently_bound": False, "scope": list(extracted.coverage),
            "excluded_parts": list(extracted.excluded_parts), "deleted_revision_text_included": False,
            "field_instruction_text_included": False, "graph_ingestion_executed": False,
            "ocr_performed": False, "page_layout": "unknown", "semantic_quality": "unknown"}


def main(argv=None):
    try:
        result = _ingest(_binding(argv))
        reply, status = {"ok": True, "result": result}, 0
        encoded = _canonical(reply)
        if len(encoded) > MAX_REPLY:
            raise DocumentSourceError("limit_exceeded")
    except DocumentSourceError as error:
        encoded, status = _canonical({"ok": False, "error": error.code}), 2
    except Exception:
        encoded, status = _canonical({"ok": False, "error": "authority_unavailable"}), 2
    sys.stdout.buffer.write(encoded + b"\n")
    sys.stdout.buffer.flush()
    return status


if __name__ == "__main__":
    raise SystemExit(main())

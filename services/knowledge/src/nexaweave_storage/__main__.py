"""Explicit local metadata migration, import, and export commands."""

from __future__ import annotations
from nexaweave_configuration import environment

import argparse
import json
import os
from pathlib import Path
import stat
import sys

import psycopg

from .store import ProjectStore, StoreError, migrate
from .source import SourceStore, _passages_input, _source_input
from .validation import (InvalidProject, canonical_payload, display_id,
                         principal_id, strict_json, uuid_value)


def _input(path: str):
    target = Path(path)
    try:
        if not stat.S_ISREG(target.lstat().st_mode):
            raise InvalidProject()
        with target.open("rb") as source:
            raw = source.read(1024 * 1024 + 16385)
    except OSError:
        raise InvalidProject() from None
    return strict_json(raw)


def _regular_bytes(path: str, maximum: int) -> bytes:
    target = Path(path)
    try:
        if not stat.S_ISREG(target.lstat().st_mode):
            raise InvalidProject()
        with target.open("rb") as source:
            raw = source.read(maximum + 1)
    except OSError:
        raise InvalidProject() from None
    if len(raw) > maximum:
        raise InvalidProject()
    return raw


def _output(value: dict, path: str | None) -> None:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":")) + "\n"
    if path is None:
        sys.stdout.write(encoded)
    else:
        with open(path, "x", encoding="utf-8") as target:
            target.write(encoded)


def _source_data(record) -> dict:
    return {"schema_version": 1, "project_id": str(record.project_id),
            "source_revision": str(record.source_revision), "name": record.name,
            "text_sha256": record.text_sha256, "byte_length": record.byte_length,
            "codepoint_length": record.codepoint_length,
            "recorded_at": record.recorded_at.isoformat(),
            "passages": [{"evidence_id": str(item.evidence_id), "start": item.start,
                          "end": item.end, "page": item.page,
                          "excerpt": item.excerpt, "excerpt_sha256": item.excerpt_sha256}
                         for item in record.passages],
            "original_document_verified": False}


def _citation_data(item) -> dict:
    return {"schema_version": 1, "principal": item.principal,
            "project_id": str(item.project_id),
            "source_revision": str(item.source_revision),
            "evidence_id": str(item.evidence_id), "source_name": item.source_name,
            "source_sha256": item.source_sha256,
            "source_byte_length": item.source_byte_length,
            "source_codepoint_length": item.source_codepoint_length,
            "source_recorded_at": item.source_recorded_at.isoformat(),
            "start": item.start, "end": item.end, "offset_unit": item.offset_unit,
            "excerpt": item.excerpt, "excerpt_sha256": item.excerpt_sha256,
            "declared_page": item.declared_page,
            "original_document_verified": False}


def _ingestion_data(plan) -> dict:
    return {"schema_version": 1, "principal": plan.principal,
            "display_graph_id": plan.display_graph_id,
            "scope": plan.scope.model_dump(mode="json"),
            "source": plan.source.model_dump(mode="json"),
            "ontology": plan.ontology.model_dump(mode="json"),
            "request_fingerprint": plan.request_fingerprint,
            "source_byte_length": plan.source_byte_length,
            "source_codepoint_length": plan.source_codepoint_length,
            "source_provenance": plan.source_provenance,
            "ingestion_executed": False}


def _export_data(record):
    return {
        "schema_version": 1,
        "principal": record.principal,
        "workspace_id": str(record.workspace_id),
        "project_id": str(record.project_id),
        "display_id": record.display_id,
        "revision": record.revision,
        "snapshot": record.snapshot,
        "evidence": list(record.evidence),
        "digest": record.digest,
        "binary_migration": False,
    }


def _import_payload(value, args):
    if type(value) is not dict:
        raise InvalidProject()
    if "schema_version" not in value:
        snapshot, evidence, _ = canonical_payload(value, [], args.display_id)
        return snapshot, evidence
    expected = {"schema_version", "principal", "workspace_id", "project_id",
                "display_id", "revision", "snapshot", "evidence", "digest",
                "binary_migration"}
    if (set(value) != expected or type(value["schema_version"]) is not int
            or value["schema_version"] != 1 or type(value["revision"]) is not int
            or value["revision"] != 1 or value["binary_migration"] is not False
            or value["principal"] != args.principal
            or value["workspace_id"] != args.workspace_id
            or value["project_id"] != args.project_id
            or value["display_id"] != args.display_id):
        raise InvalidProject()
    snapshot, evidence, digest = canonical_payload(value["snapshot"], value["evidence"], args.display_id)
    if type(value["digest"]) is not str or value["digest"] != digest:
        raise InvalidProject()
    return snapshot, evidence


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="explicitly install or verify mf_app schema")
    imp = commands.add_parser("import-project", help="import one legacy snapshot or revision-one export")
    exp = commands.add_parser("export-project", help="export one selected metadata revision")
    for command in (imp, exp):
        command.add_argument("--principal", required=True)
        command.add_argument("--workspace-id", required=True)
        command.add_argument("--project-id", required=True)
        command.add_argument("--display-id", required=True)
    imp.add_argument("--input", required=True)
    exp.add_argument("--revision", type=int, default=None,
                     help="selected history revision; default current")
    exp.add_argument("--output", help="new file only; default stdout")
    src = commands.add_parser("import-source", help="retain explicit UTF-8 extracted text")
    cite = commands.add_parser("resolve-evidence", help="resolve an exact retained-text passage")
    for command in (src, cite):
        command.add_argument("--principal", required=True)
        command.add_argument("--project-id", required=True)
        command.add_argument("--output", help="new file only; default stdout")
    src.add_argument("--source-revision", required=True)
    src.add_argument("--name", required=True)
    src.add_argument("--input", required=True)
    src.add_argument("--passages", help="JSON list of declared codepoint ranges")
    cite.add_argument("--evidence-id", required=True)
    plan = commands.add_parser("export-ingestion", help="offline plan from retained owned source")
    plan.add_argument("--principal", required=True)
    plan.add_argument("--display-graph-id", required=True)
    plan.add_argument("--source-revision", required=True)
    plan.add_argument("--operation-id", required=True)
    plan.add_argument("--ontology", required=True, help="bounded local ontology JSON file")
    plan.add_argument("--output", help="new file only; default stdout")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command not in ("migrate", "export-ingestion"):
            principal_id(args.principal)
            uuid_value(args.project_id)
        if args.command == "export-ingestion":
            from nexaweave_knowledge.bindings import _identity
            from nexaweave_knowledge.contracts import OntologySpec
            from nexaweave_knowledge.source_bridge import BridgeError, SourceIngestionBridge
            try:
                _identity(args.principal, args.display_graph_id)
            except ValueError:
                raise InvalidProject() from None
            uuid_value(args.source_revision)
            uuid_value(args.operation_id)
            ontology_raw = _regular_bytes(args.ontology, 65536)
            strict_json(ontology_raw)
            try:
                ontology = OntologySpec.model_validate_json(ontology_raw)
            except (ValueError, TypeError):
                raise InvalidProject() from None
        if args.command in ("import-project", "export-project"):
            uuid_value(args.workspace_id)
            display_id(args.display_id)
        if args.command == "import-project":
            snapshot, evidence = _import_payload(_input(args.input), args)
        if args.command == "import-source":
            revision = uuid_value(args.source_revision)
            try:
                retained_text = _regular_bytes(args.input, 1024 * 1024).decode("utf-8")
            except UnicodeError:
                raise InvalidProject() from None
            declared = [] if args.passages is None else strict_json(_regular_bytes(args.passages, 65536))
            _source_input(args.name, retained_text)
            _passages_input(declared, uuid_value(args.project_id), revision, retained_text)
        if args.command == "resolve-evidence":
            uuid_value(args.evidence_id)
        dsn = environment.get("NEXAWEAVE_APPSTORE_DSN")
        if not dsn:
            print("missing_dsn", file=sys.stderr)
            return 2
        def connect():
            return psycopg.connect(dsn, connect_timeout=5)
        if args.command == "migrate":
            with connect() as conn:
                migrate(conn)
        elif args.command == "import-project":
            ProjectStore(connect).create(args.principal, args.workspace_id, args.project_id,
                                         args.display_id, snapshot, evidence)
        elif args.command == "export-project":
            record = ProjectStore(connect).get(args.principal, args.project_id, args.revision)
            if record.workspace_id != uuid_value(args.workspace_id) or record.display_id != args.display_id:
                raise InvalidProject()
            _output(_export_data(record), args.output)
        elif args.command == "import-source":
            record = SourceStore(connect).ingest_text(args.principal, args.project_id,
                args.source_revision, args.name, retained_text, declared)
            _output(_source_data(record), args.output)
        elif args.command == "resolve-evidence":
            citation = SourceStore(connect).resolve_evidence(args.principal,
                args.project_id, args.evidence_id)
            _output(_citation_data(citation), args.output)
        else:
            try:
                plan = SourceIngestionBridge(connect).plan(args.principal, args.display_graph_id,
                    args.source_revision, args.operation_id, ontology)
            except BridgeError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            _output(_ingestion_data(plan), args.output)
        return 0
    except (InvalidProject, StoreError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (OSError, psycopg.Error):
        print("storage_error", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

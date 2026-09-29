"""Explicit local metadata migration, import, and export commands."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys

import psycopg

from .store import ProjectStore, StoreError, migrate
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
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command != "migrate":
            principal_id(args.principal)
            uuid_value(args.workspace_id)
            uuid_value(args.project_id)
            display_id(args.display_id)
        if args.command == "import-project":
            snapshot, evidence = _import_payload(_input(args.input), args)
        dsn = os.environ.get("MIROFISH_APPSTORE_DSN")
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
        else:
            record = ProjectStore(connect).get(args.principal, args.project_id, args.revision)
            if record.workspace_id != uuid_value(args.workspace_id) or record.display_id != args.display_id:
                raise InvalidProject()
            data = json.dumps(_export_data(record), sort_keys=True, ensure_ascii=False,
                              separators=(",", ":")) + "\n"
            if args.output is None:
                sys.stdout.write(data)
            else:
                with open(args.output, "x", encoding="utf-8") as target:
                    target.write(data)
        return 0
    except (InvalidProject, StoreError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (OSError, psycopg.Error):
        print("storage_error", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

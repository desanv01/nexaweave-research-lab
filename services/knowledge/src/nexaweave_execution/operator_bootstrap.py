"""Explicit local operator commands for installing catalogs and binding source scope."""

import argparse
import json
import os
import sys

import psycopg
from psycopg.conninfo import conninfo_to_dict

from nexaweave_configuration import environment
from nexaweave_knowledge.bindings import ScopeBindingStore
from nexaweave_knowledge.contracts import KnowledgeScope, Layer
from nexaweave_knowledge.operations import migrate as migrate_knowledge
from nexaweave_storage.store import ProjectStore, migrate as migrate_app
from nexaweave_storage.validation import principal_id, uuid_value

from .budget import migrate as migrate_budget
from .followup_store import migrate as migrate_followup
from .native_launch_store import migrate as migrate_native_launch
from .native_run_store import migrate_native_runs
from .preparation_store import migrate as migrate_preparation
from .report_store import migrate as migrate_report


_DRIVERS = (
    ("mf_app", migrate_app),
    ("mf_knowledge", migrate_knowledge),
    ("mf_execution", migrate_budget),
    ("mf_preparation", migrate_preparation),
    ("mf_native_execution", migrate_native_runs),
    ("mf_native_launch", migrate_native_launch),
    ("mf_report", migrate_report),
    ("mf_followup", migrate_followup),
)
_DSN_FIELDS = {"host", "port", "dbname", "user", "password", "connect_timeout", "application_name",
               "sslmode", "sslrootcert", "sslcert", "sslkey"}


class Refused(ValueError):
    """An operator prerequisite or persisted identity did not match."""


def _connection_factory(database, role):
    if any(key.upper().startswith("PG") for key in os.environ):
        raise Refused("ambient_postgres_settings")
    dsn = environment.get("NEXAWEAVE_APPSTORE_DSN")
    if not dsn or len(dsn) > 16384:
        raise Refused("explicit_dsn_required")
    settings = conninfo_to_dict(dsn)
    port = settings.get("port", "")
    if (set(settings) - _DSN_FIELDS or settings.get("dbname") != database
            or settings.get("user") != role or settings.get("host") not in {"127.0.0.1", "::1"}
            or not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535
            or not settings.get("password")):
        raise Refused("explicit_owned_local_database_required")
    settings.update(connect_timeout=5, options="-c statement_timeout=10000 -c lock_timeout=5000 "
                    "-c idle_in_transaction_session_timeout=15000")

    def connect():
        conn = psycopg.connect(**settings, autocommit=True)
        try:
            identity = conn.execute(
                "SELECT current_database(), session_user, current_user, pg_catalog.pg_get_userbyid(datdba) "
                "FROM pg_catalog.pg_database WHERE datname=current_database()"
            ).fetchone()
            if identity != (database, role, role, role):
                raise Refused("database_owner_mismatch")
        except BaseException:
            conn.close()
            raise
        return conn

    return connect


def _migrate(connect):
    with connect() as conn:
        for schema, driver in _DRIVERS:
            if schema == "mf_native_execution" and conn.execute(
                    "SELECT pg_catalog.to_regclass('mf_app.project_revisions') IS NOT NULL"
            ).fetchone() != (True,):
                raise Refused("project_revision_authority_missing")
            driver(conn)
    return {"schema_version": 1, "status": "ready", "catalogs": [schema for schema, _ in _DRIVERS]}


def _bind_source(connect, args):
    principal = principal_id(args.principal)
    workspace, project, graph = map(uuid_value, (args.workspace_id, args.project_id, args.graph_id))
    record = ProjectStore(connect).get(principal, project)
    if (record.principal, record.workspace_id, record.project_id) != (principal, workspace, project):
        raise Refused("project_authority_mismatch")
    scope = KnowledgeScope(workspace_id=workspace, project_id=project, graph_id=graph, layer=Layer.source)
    store = ScopeBindingStore(connect)
    bound = store.bind(principal, args.display_graph_id, scope)
    if store.resolve(principal, args.display_graph_id) != bound:
        raise Refused("binding_readback_mismatch")
    return {"schema_version": 1, "status": "bound", "principal": bound.principal,
            "display_graph_id": bound.display_graph_id, "scope": bound.scope.model_dump(mode="json")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    migrate = commands.add_parser("migrate", help="install or verify the eight catalogs")
    bind = commands.add_parser("bind-source", help="bind an owned project's source-layer scope")
    for command in (migrate, bind):
        command.add_argument("--database", required=True)
        command.add_argument("--owner-role", required=True)
    for name in ("principal", "workspace-id", "project-id", "graph-id", "display-graph-id"):
        bind.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    try:
        connect = _connection_factory(args.database, args.owner_role)
        receipt = _migrate(connect) if args.command == "migrate" else _bind_source(connect, args)
    except Refused as error:
        print(f"operator_bootstrap_refused:{error}", file=sys.stderr)
        return 2
    except Exception:
        print("operator_bootstrap_refused", file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

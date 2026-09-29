"""Opt-in tests against only the disposable loopback PostgreSQL fixture."""

import os
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from mirofish_storage import Conflict, MigrationMismatch, NotFound, ProjectStore, migrate
from mirofish_storage.__main__ import main as cli_main
from test_project_store import snapshot

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def factory():
    if os.getenv("PROJECT_STORE_POSTGRES_INTEGRATION") != "1":
        pytest.skip("disposable PostgreSQL integration disabled")
    dsn = os.getenv("PROJECT_STORE_POSTGRES_TEST_DSN")
    if not dsn or any(os.getenv(key) for key in ("PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE")):
        pytest.fail("approved fixture DSN required")
    settings = conninfo_to_dict(dsn)
    if (settings.get("host") != "127.0.0.1" or settings.get("port") != "15432"
            or settings.get("dbname") != "mirofish_operations_test"
            or settings.get("user") != "mirofish_fixture" or not settings.get("password")
            or set(settings) - {"host", "port", "dbname", "user", "password", "connect_timeout"}):
        pytest.fail("not the approved disposable fixture")
    def connect():
        return psycopg.connect(dsn, connect_timeout=3)
    with connect() as connection:
        migrate(connection)
        migrate(connection)
    return connect


def test_restart_history_isolation_and_identity(factory):
    store = ProjectStore(factory)
    workspace, project = uuid4(), uuid4()
    first = store.create("owner", workspace, project, "proj_1", snapshot())
    external = first.snapshot
    external["name"] = "local mutation"
    assert first.snapshot["name"] == "Name"
    assert first.revision == 1 and store.create("owner", workspace, project, "proj_1", snapshot()) == first
    assert ProjectStore(factory).get("owner", project) == first
    with pytest.raises(NotFound):
        store.get("other", project)
    with pytest.raises(NotFound):
        store.history("other", project)
    changed = snapshot()
    changed["name"] = "Changed"
    second = store.update("owner", project, 1, changed)
    assert second.revision == 2 and store.history("owner", project) == [first, second]
    assert store.list_projects("owner", workspace) == [second]
    assert store.list_projects("other", workspace) == []
    assert store.create("owner", workspace, project, "proj_1", snapshot()) == first
    with pytest.raises(Conflict):
        store.create("owner", workspace, project, "proj_1", changed)
    with pytest.raises(Conflict):
        store.create("owner", workspace, uuid4(), "proj_1", snapshot())
    with pytest.raises(Conflict):
        store.update("owner", project, 1, changed)
    assert store.history("owner", project) == [first, second]


def test_concurrent_cas_one_winner(factory):
    store = ProjectStore(factory)
    project = uuid4()
    store.create("race", uuid4(), project, "proj_1", snapshot())
    barrier = Barrier(2)
    def update(name):
        value = snapshot()
        value["name"] = name
        barrier.wait(timeout=5)
        return ProjectStore(factory).update("race", project, 1, value)
    winners, conflicts = [], 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(update, name) for name in ("A", "B")]
        for future in futures:
            try:
                winners.append(future.result(timeout=10))
            except Conflict:
                conflicts += 1
    assert len(winners) == conflicts == 1
    assert len(store.history("race", project)) == 2


def test_schema_constraints_and_drift(factory):
    with factory() as connection:
        assert connection.execute("SELECT count(*) FROM pg_indexes WHERE schemaname='mf_app' AND indexname='projects_owner_workspace_list'").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM pg_constraint WHERE conname='project_revisions_project_id_fkey' AND connamespace='mf_app'::regnamespace").fetchone()[0] == 1
        assert connection.execute("SELECT condeferrable AND condeferred FROM pg_constraint WHERE conname='projects_current_revision_fkey' AND connamespace='mf_app'::regnamespace").fetchone()[0]
    project = uuid4()
    ProjectStore(factory).create("head", uuid4(), project, "proj_1", snapshot())
    with factory() as connection:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with connection.transaction():
                connection.execute("UPDATE mf_app.projects SET current_revision=999 WHERE project_id=%s", (project,))
                connection.execute("SET CONSTRAINTS mf_app.projects_current_revision_fkey IMMEDIATE")
    assert ProjectStore(factory).get("head", project).revision == 1
    with factory() as connection:
        with connection.transaction():
            connection.execute("CREATE TABLE mf_app.unexpected_table (id integer)")
            with pytest.raises(MigrationMismatch):
                migrate(connection)
            connection.execute("DROP TABLE mf_app.unexpected_table")


def test_cli_import_update_export_and_fixed_failures(factory, tmp_path, monkeypatch, capsys):
    workspace, project = uuid4(), uuid4()
    value = snapshot()
    value["files"] = [{"filename": "uploaded.pdf", "size": 17}]
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setenv("MIROFISH_APPSTORE_DSN", os.environ["PROJECT_STORE_POSTGRES_TEST_DSN"])
    common = ["--principal", "cli owner", "--workspace-id", str(workspace),
              "--project-id", str(project), "--display-id", "proj_1"]
    assert cli_main(["import-project", *common, "--input", str(source)]) == 0
    assert capsys.readouterr().err == ""
    changed = snapshot()
    changed["files"] = value["files"]
    changed["name"] = "Updated"
    ProjectStore(factory).update("cli owner", project, 1, changed)
    assert cli_main(["export-project", *common]) == 0
    current = json.loads(capsys.readouterr().out)
    assert current["revision"] == 2 and current["snapshot"]["name"] == "Updated"
    first_path = tmp_path / "first.json"
    assert cli_main(["export-project", *common, "--revision", "1", "--output", str(first_path)]) == 0
    assert json.loads(first_path.read_text(encoding="utf-8"))["revision"] == 1
    assert cli_main(["import-project", *common, "--input", str(first_path)]) == 0
    assert ProjectStore(factory).get("cli owner", project).revision == 2
    original = first_path.read_bytes()
    assert cli_main(["export-project", *common, "--output", str(first_path)]) == 2
    assert first_path.read_bytes() == original
    assert capsys.readouterr().err == "storage_error\n"
    wrong_scope = list(common)
    wrong_scope[3] = str(uuid4())
    assert cli_main(["export-project", *wrong_scope]) == 2
    wrong_owner = list(common)
    wrong_owner[1] = "other"
    assert cli_main(["export-project", *wrong_owner]) == 2
    errors = capsys.readouterr().err
    assert "MIROFISH_APPSTORE_DSN" not in errors
    assert os.environ["PROJECT_STORE_POSTGRES_TEST_DSN"] not in errors
    assert str(source) not in errors

"""Guarded PostgreSQL and native cohort fixtures shared by this suite."""

from dataclasses import replace
import os
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture(scope="session")
def connection_factory():
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    from nexaweave_execution.native_run_store import migrate_native_runs
    from nexaweave_storage import migrate

    if os.getenv("PROJECT_STORE_POSTGRES_INTEGRATION") != "1":
        pytest.skip("disposable PostgreSQL integration disabled")
    dsn = os.getenv("PROJECT_STORE_POSTGRES_TEST_DSN")
    pg_keys = ("PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER",
               "PGPASSWORD", "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE")
    if not dsn or any(os.getenv(key) for key in pg_keys):
        pytest.fail("approved fixture DSN required")
    settings = conninfo_to_dict(dsn)
    if (settings.get("host") != "127.0.0.1"
            or settings.get("port") != "15432"
            or settings.get("dbname") != "mirofish_operations_test"
            or settings.get("user") != "mirofish_fixture"
            or not settings.get("password")
            or set(settings) - {"host", "port", "dbname", "user", "password", "connect_timeout"}):
        pytest.fail("not the approved disposable fixture")

    def connect():
        return psycopg.connect(dsn, connect_timeout=3)

    with connect() as connection:
        migrate(connection)
        migrate_native_runs(connection)
    return connect


@pytest.fixture(scope="session")
def cohort_fixture(connection_factory, tmp_path_factory):
    from nexaweave_execution.native_run_store import NativeRunStore
    from test_native_experiments import build_cohort, hashes

    cohort, runs, root = build_cohort(connection_factory, tmp_path_factory)
    store = NativeRunStore(connection_factory)
    pinned_rows = tuple(store.get("owner", member.request.run_id) for member in cohort.members)
    pinned_files = hashes(root)
    # Consumers create their own mutable authority rows, manifests and hosts.
    # Shared inputs are terminal and must remain unchanged for the entire suite.
    yield cohort, tuple(runs), root
    assert hashes(root) == pinned_files
    assert tuple(store.get("owner", member.request.run_id) for member in cohort.members) == pinned_rows


@pytest.fixture
def experiment_python():
    """Require Main's explicit installed runtime; never use the test runner."""
    value = os.environ.get('NEXAWEAVE_EXPERIMENT_TEST_PYTHON')
    try:
        if (type(value) is not str or not 1 <= len(value) <= 4096 or '\x00' in value
                or not Path(value).is_absolute() or not Path(value).is_file()
                or not os.access(value, os.X_OK)):
            raise ValueError
    except (ValueError, TypeError, OSError):
        pytest.fail('NEXAWEAVE_EXPERIMENT_TEST_PYTHON must explicitly name an absolute installed knowledge interpreter executable', pytrace=False)
    return value


@pytest.fixture
def protected_fixture(experiment_python, connection_factory, cohort_fixture, tmp_path, monkeypatch):
    from app.services.native_experiment_contracts import ExperimentCohort, ExperimentMember, canonical, sha
    from app.services.native_experiment_http_facade import ExperimentSettings, NativeExperimentFacade
    from nexaweave_execution.native_run_contracts import NativeChildIdentity, NativeRunReceipt
    from nexaweave_execution.native_run_store import NativeRunStore
    from psycopg.conninfo import conninfo_to_dict
    from types import SimpleNamespace
    from test_native_experiments import install_offline_boundary

    install_offline_boundary(monkeypatch)
    base, runs, _ = cohort_fixture
    store = NativeRunStore(connection_factory)
    extras = []
    for index, state in enumerate(('declared', 'uncertain', 'cancelled')):
        request = replace(base.members[0].request, run_id=uuid4(),
            simulation_id='http-'+state+'-'+uuid4().hex, seed=(-(2**63), 2**63-1, 2**53+1)[index],
            platforms=('twitter',) if state == 'declared' else base.members[0].request.platforms)
        store.register(request)
        if state == 'declared':
            store.request_cancel('owner', request.run_id)
        else:
            owner = uuid4()
            claimed = store.claim_start('owner', request.run_id, owner, 240)
            if state == 'uncertain':
                store.mark_uncertain('owner', request.run_id, claimed.attempt_id, owner)
            else:
                # Authoritative synthetic control-plane fixture, not a claim of
                # a real native cancellation. No process is launched/adopted.
                child = NativeChildIdentity(uuid4(), 2147483000, 'a'*64)
                store.attach('owner', request.run_id, claimed.attempt_id, owner, child, 240)
                store.request_cancel('owner', request.run_id)
                receipt = NativeRunReceipt(request.run_id, claimed.attempt_id, child.instance_id,
                    request.fingerprint, 'cancelled', 'b'*64)
                store.settle('owner', request.run_id, claimed.attempt_id, owner, receipt)
        extras.append(ExperimentMember('http-'+state, 'Observed '+state, 'declared-case', request))
    cohort = ExperimentCohort('owner', base.members+tuple(extras))
    manifest = tmp_path/'cohort.json'
    raw = canonical(cohort.wire())
    manifest.write_bytes(raw)
    pg = conninfo_to_dict(os.environ['PROJECT_STORE_POSTGRES_TEST_DSN'])
    environment = {f'KNOWLEDGE_PG_{k}': pg[v] for k, v in (
        ('HOST', 'host'), ('PORT', 'port'), ('DATABASE', 'dbname'), ('USER', 'user'), ('PASSWORD', 'password'))}
    scope = {'schema_version': 1, 'workspace_id': str(uuid4()),
        'project_id': str(cohort.members[0].request.project_id), 'graph_id': str(uuid4()),
        'run_id': None, 'branch_id': None, 'layer': 'source'}
    token = '0123456789abcdef'*4
    settings = SimpleNamespace(python=experiment_python, principal='owner', scope=scope,
        child_environment=environment, token=token, display_graph_id='http-fixture', bootstrap='unused')
    monkeypatch.setenv('KNOWLEDGE_EXPERIMENT_MANIFEST', str(manifest))
    monkeypatch.setenv('KNOWLEDGE_EXPERIMENT_MANIFEST_SHA256', sha(raw))
    binding = ExperimentSettings.capture(settings)
    assert binding is not None
    facade = NativeExperimentFacade(settings, binding=binding)
    return settings, facade, cohort, runs, manifest, raw

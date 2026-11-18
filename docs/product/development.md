# Develop NexaWeave from source

These commands support source inspection, guarded unit checks and a frontend build. They are not a qualified service launch or deployment recipe. Use synthetic data, keep credentials out of the repository, and review [current status](status.md) before interpreting results.

## Toolchain

Use Python 3.12 and Node 24.14.1 or newer. The lean Python profile is pinned in tools/ci-unit-requirements.txt and resolves its transitive dependencies from a package index. It omits the full OASIS/CAMEL engine stack and is suitable for guarded source tests, not a live simulation engine. Use a disposable Python environment.

## Python source checks

From the repository root:

    python -m pip install -r tools/ci-unit-requirements.txt
    python -m pip install --no-deps services/knowledge
    python tools/check_baseline_manifest.py
    python tools/run_unit_tests.py

The installed distribution is `nexaweave-knowledge`; its canonical modules are `nexaweave_knowledge`, `nexaweave_storage` and `nexaweave_execution`. New configuration uses `NEXAWEAVE_` names. Existing import and configuration inputs remain explicit aliases, while conflicting old and new values fail closed. See [compatibility](compatibility.md) before working with existing data.

The manifest command checks archived source bytes and exceptions. The unit launcher uses dummy provider keys, disables .env loading, and blocks non-loopback connections in its guarded test processes while allowing local fixtures. Installing dependencies may contact a package registry. A passing run does not establish native engine, provider, browser or deployment qualification. Main owns execution and interpretation of project acceptance checks.

## Local operator bootstrap and protected startup (W8.1)

Use the locked backend and separate knowledge environments with Python 3.12.13
and uv 0.11.25. Install the knowledge package non-editably in its own
environment. Set `KNOWLEDGE_PYTHON` and `BACKEND_PYTHON` to the absolute
interpreter paths for the two environments on your platform.

```sh
uv sync --locked --project backend --python 3.12.13
uv sync --locked --project services/knowledge --python 3.12.13 --extra source-pdf
uv pip install --python "$KNOWLEDGE_PYTHON" --no-deps --reinstall services/knowledge
```

Prepare an empty, explicitly owned PostgreSQL database on
`127.0.0.1` or `::1`. The login role must own the database. Supply
`NEXAWEAVE_APPSTORE_DSN` privately with the explicit host, port, database, role,
and password. Clear ambient `PG*` variables before running the commands. Set
the example shell variables below to the same approved database and principal,
the initial project and workspace UUIDs, and a distinct source graph UUID and
display graph ID.

```sh
"$KNOWLEDGE_PYTHON" -I -m nexaweave_execution.operator_bootstrap migrate \
  --database "$DB_NAME" --owner-role "$DB_OWNER"
"$KNOWLEDGE_PYTHON" -I -m nexaweave_storage import-project \
  --principal "$PRINCIPAL" --workspace-id "$WORKSPACE_ID" --project-id "$PROJECT_ID" \
  --display-id "$PROJECT_DISPLAY_ID" --input "$PROJECT_SNAPSHOT_FILE"
"$KNOWLEDGE_PYTHON" -I -m nexaweave_execution.operator_bootstrap bind-source \
  --database "$DB_NAME" --owner-role "$DB_OWNER" \
  --principal "$PRINCIPAL" --workspace-id "$WORKSPACE_ID" --project-id "$PROJECT_ID" \
  --graph-id "$GRAPH_ID" --display-graph-id "$DISPLAY_GRAPH_ID"
```

Migration reports `status: ready` and all eight verified catalogs. Each driver
owns its transaction. If a later catalog refuses, earlier committed catalogs
remain available for the same checked migration command to verify on retry.
Import creates the project from a valid legacy snapshot or a matching
revision-one export. Binding reports `status: bound` and the canonical source
scope. An identical binding returns the same result on repeat. A conflicting
or tombstoned binding refuses. Keep the project display ID separate from the
graph display ID.

Export the protected launcher settings below so the backend process and its
children inherit them. Disable both dotenv loaders before starting:

```sh
export PYTHON_DOTENV_DISABLED=1
export FLASK_SKIP_DOTENV=1
```

For the protected launcher, set `NEXAWEAVE_APP_MODE=research_local`,
`FLASK_DEBUG=0`, `FLASK_HOST=127.0.0.1`, and the explicitly allowed origins in
`NEXAWEAVE_ALLOWED_ORIGINS`. Set `KNOWLEDGE_BOOTSTRAP_SCRIPT` to the installed
`nexaweave_knowledge/read_bootstrap.py` in the knowledge interpreter's
`site-packages`. Supply `KNOWLEDGE_READ_TOKEN` privately and set
`KNOWLEDGE_PRINCIPAL`, `KNOWLEDGE_DISPLAY_GRAPH_ID`, and
`KNOWLEDGE_BOUND_SCOPE_JSON` from the binding receipt. Point
`KNOWLEDGE_PG_HOST`, `KNOWLEDGE_PG_PORT`, `KNOWLEDGE_PG_DATABASE`,
`KNOWLEDGE_PG_USER`, and `KNOWLEDGE_PG_PASSWORD` at the same owned store.
Supply explicit `KNOWLEDGE_NEO4J_URI`, `KNOWLEDGE_NEO4J_USER`, and
`KNOWLEDGE_NEO4J_PASSWORD` for the owned Neo4j service. Start with
`"$BACKEND_PYTHON" backend/run.py`. The protected default listener is
`127.0.0.1:5001`. Check `/health` for `research_local` and an authenticated
`/api/source/library/<display_graph_id>` for the owner's source metadata.

The guarded readiness case uses one disposable PostgreSQL fixture and the real
launcher with a fixture-owned loopback readiness and shutdown hook. It checks
catalog receipts, owner metadata across restart, and owned cleanup. Full
Neo4j connectivity, Temporal workers, provider access, and the complete
research journey have separate prerequisites and acceptance checks.

## Opt-in PostgreSQL logical restore exercise (W8.2)

Main/Codex alone qualifies and runs this selected exercise; authoring is not a
verification result. Existing original-PDF `--unit` and `--postgres` modes are
unchanged. From the repository root, with the prerequisites below already met:

    <QUALIFIED_PYTHON> tools/run_source_binary_tests.py --backup-restore

The runner selects one test, requires one pass with zero skips, and checks that
storage/knowledge packages load from the interpreter's installed site-packages.
Use a qualified installed knowledge distribution with the source-pdf/PyMuPDF
profile, pytest, pytest-asyncio and psycopg. No package installation, Docker launch,
HTTP host, provider call or tool download is performed by this mode.

### Operator prerequisites (not established by the runner)

- Windows with qualified nested Job/pipe support. Other operating systems fail
  closed: the shared process helper does not prove descendant ownership there.
- Exclusive use of the existing disposable loopback fixture at
  `127.0.0.1:15432`, maintenance database `mirofish_operations_test`, role
  `mirofish_fixture`, PostgreSQL 18. The role must already have create/drop
  rights; new exercise databases are created with that owner. Missing permission is a failure, not
  authority to change roles, broaden fixture admission or reuse persistent data.
- Qualified absolute paths to matching PostgreSQL 18 `pg_dump.exe` and
  `pg_restore.exe`; establish their versions, dependencies, trusted directories,
  identities, native egress and process behavior before execution. Executable
  SHA-256 checks do not qualify DLLs or prevent a writable-path replacement race.
- A preprovisioned private temporary root outside the checkout, with Windows
  inherited ACLs restricting access to the operator and approved system
  principals. Qualify the directory/ancestor ACLs and reparse-point custody
  before allowing credential writes. TemporaryDirectory alone is not ACL proof.
- Qualify the temporary-volume and PostgreSQL server resource envelope, including
  quotas/free-space policy for WAL, server logs and native temporary files.
  The archive sink cap below is **not** a whole-exercise runtime disk quota.

Supply these environment inputs through the qualified private operator process,
not a committed file, shell history containing passwords, or public diagnostics:

| Input | Required value |
| --- | --- |
| `PROJECT_STORE_POSTGRES_BACKUP_RESTORE` | `1` (explicit exercise opt-in) |
| `PROJECT_STORE_TEST_PASSWORD` | `<DISPOSABLE_FIXTURE_PASSWORD>` |
| `PROJECT_STORE_BACKUP_RESTORE_PRIVATE_ROOT` | `<ABSOLUTE_QUALIFIED_PRIVATE_ROOT>` |
| `PROJECT_STORE_PG_DUMP` | `<ABSOLUTE_QUALIFIED_PG18_BIN>/pg_dump.exe` |
| `PROJECT_STORE_PG_DUMP_SHA256` | `<64_LOWERCASE_HEX_DIGITS>` |
| `PROJECT_STORE_PG_RESTORE` | `<ABSOLUTE_QUALIFIED_PG18_BIN>/pg_restore.exe` |
| `PROJECT_STORE_PG_RESTORE_SHA256` | `<64_LOWERCASE_HEX_DIGITS>` |
| `PROJECT_STORE_BACKUP_RESTORE_RESOURCES_QUALIFIED` | `1`, only after the preceding ownership, tool, credential and resource qualification |

The final flag is an operator attestation, not an automatically established
security property. Do not set it to bypass a missing enforceable resource bound.
All inherited `PG*` variables are refused, even empty ones. The runner constructs
the fixed fixture DSN and integration opt-in itself. Native tools receive a
minimal environment with an exercise-created private passfile, never the DSN or
password in arguments. Passfile escaping is applied; passwords with CR/LF/NUL or
over 1024 characters are refused. The passfile must not be retained or published.

### Lifecycle, enforced limits and cleanup

The test creates distinct run-UUID source/target databases from `template0`,
records names/OIDs, migrates only the source, and commits one project revision
(integer 1), one real synthetic PDF, its separate source-revision UUID and page
passages. The project evidence manifest references that source and first passage;
its object key is inert metadata, not external-file custody. No target migration
or seed can repair missing restored content.

`pg_dump` uses custom format on binary stdout, no owner/ACL export, and a 5-second
lock wait. An incremental Python sink writes at most 32 MiB, checks remaining
allowance **before every write**, and stops the owned tool on overflow. This is
not a post-run size check. Each diagnostic stream is counted to 1 MiB and
discarded; no unbounded native log or communicate buffer is retained. Restore
uses `--exit-on-error --single-transaction --no-owner --no-privileges`; no
`--clean`, `--create`, parallelism or retry into a partial target is used.

Native tool calls retain 60-second owned deadlines and 3-second connection
timeouts; `pg_dump` also uses `--lock-wait-timeout=5s`. No native `PGOPTIONS`
statement/lock timeout is supplied or claimed: PostgreSQL 18.6 clients reset
those settings. The test has a 480-second work budget checked at connection/tool
boundaries; the outer child wait is 600 seconds. Each native/outer owner gets
the shared helper's 3-second cleanup budget. Separate management SQL uses
30-second statements/5-second lock waits, reduced to 5/2 seconds for cleanup.
Store operations retain their existing 5/2/10-second transaction profiles;
migration retains its 10/3/15-second profile. A 64-MiB source-size check before
dump is admission only, not a runtime PostgreSQL disk cap. No CPU/memory or total
disk quota is implemented by the process owner. Native pipe denial or missing
external bounds must be reported, not replaced with unbounded file redirection.

Every connection returned to the real stores verifies database name, role,
recorded OID and the admitted server endpoint (which may expose server port
5432 behind host port 15432). Readback checks current/explicit project revision,
history/digest/manifest, source/passage/evidence binding, exact original bytes,
SHA-256 and wrong-owner refusal. A separate source readback confirms unchanged
data. Receipts contain only synthetic identities, counts, hashes and fixed
failure codes; tool errors and PDF bytes are not printed.

Finally, ordinary cleanup closes owned processes/connections, checks recorded
name/OID/owner, and drops only successfully identified run-created databases.
It separately attempts run-private file cleanup and reports cleanup failures
independently of the primary error. No forced session termination, persistent
fixture schema cleanup, role changes or volume deletion is allowed. Unknown
CREATE outcomes are reported, not adopted by name/prefix. On an outer kill,
process crash or host loss, Main must reconcile the emitted database-attempt
and database-created receipts; this is not a crash-proof cleanup service. Do not
blindly delete matching names or retain passfiles/archives as public evidence.

A pass establishes one bounded logical restore and store-level isolation, not
PITR, interrupted-migration recovery, physical backup, production RPO/RTO,
protected HTTP/browser behavior, or complete W8.2 disaster recovery.

## Frontend build

From the repository root:

    cd frontend
    npm ci --ignore-scripts
    npm run build

The build checks the Vue artifact; it does not exercise a browser journey. If running Vite for a separately authorized local investigation, use BROWSER=none and avoid automatic URL opening. Do not treat the inherited root development scripts or Docker setup as a supported full launch.

## Model and runtime boundaries

Graphiti and self-hosted Neo4j Community form the knowledge direction. PostgreSQL and Temporal provide durable authority and orchestration. Generation is configurable for official DeepSeek deepseek-flash, with embeddings separately configured. Paid calls require actual local credentials and a concrete total spending cap. There is no qualified fully local, no-egress profile, full release sequence or public app deployment yet.

For scoped work and review expectations, see [contributing](../../CONTRIBUTING.md). The older [import-stage development notes](../development.md) remain a historical source record, not the current status page.

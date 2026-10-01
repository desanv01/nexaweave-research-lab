# U04c source import — UNVERIFIED handoff

## Main-requested correction 1 — bounded pytest case IDs

Main reported the first sanitized pure execution encountered Windows pytest
setup/teardown errors before the oversize admission body: the implicit ID for
the 12MiB byte parameter inflated `PYTEST_CURRENT_TEST` beyond Windows limits.
Worker added explicit IDs `malformed-json`, `duplicate-key`, `nonfinite`,
`invalid-utf8`, `artifact-over-cap` only to that raw-input parametrization in
`test_research_import.py`. Exact input bytes, cap and no-connection assertions
remain unchanged; no skips or production changes. Source reading of the other
parametrizations in this file found only short strings, UUIDs, digests, small
integers and booleans; no other oversized literal IDs needed correction.

This correction changed only that test file and this handoff. Operations were
`Get-Content` of those two assigned files and `apply_patch` authoring; no worker
execution/checks/Git/status/hash/network/DB occurred. Main reported stable-source
review/eleven hashes and accepted-base fast-forward preserved all eleven; worker
does not independently verify that report. Candidate remains UNVERIFIED, stable
and idle pending Main review/execution.

Assigned checkout: `C:\Users\Dv\Desktop\MiroFish\_implementation_worktrees\u01`.
Assigned branch: `task/u04c-owned-source-import`; dependency base supplied by Main:
`d8647ad311936a53613da49b43e1305b56af8932`. Worker did not inspect Git state.
Main subsequently reported PR73 accepted/merged as
`a2a6a844f8d6f153c25730d1bb7f468f63eb0242`; that report is dependency information,
not worker verification or U04c acceptance. Main will synchronize exact revisions.

## Authored paths

1. `services/knowledge/src/mirofish_storage/migrations/0003_research_imports.sql`
2. `services/knowledge/src/mirofish_storage/research_import.py`
3. `services/knowledge/src/mirofish_storage/research_import_cli.py`
4. `services/knowledge/tests/test_research_import.py`
5. `services/knowledge/tests/test_research_import_postgres.py`
6. `docs/architecture/owned-research-source-import.md`
7. `coordination/handoffs/U04c-source-import.md`
8. `services/knowledge/src/mirofish_storage/store.py` — SQL3 registry tuple only.

## Candidate behavior

Pure admission validates/copies v1 artifact bytes against required trusted whole
SHA256 and bounded inert provenance before opening a connection. Typed UUID5
remapping isolates target/artifact/kind/original identities. One owned transaction
locks persisted target principal/current revision, inserts selected sources,
exact passages and receipt, and closes/rolls back on failure. Repeat verifies
receipt lineage/integrity, source timestamps/content and passage ordinals/content
before returning the original receipt without writes. Independently advanced
targets require their current revision while keeping original import time/revision.
Target project snapshot/evidence/history and original retained sources are untouched.

SQL3 has target FK, composite retained target-revision FK and target/artifact primary key, bounded provenance/hash/type
checks and no redundant index. Main's missing-key/SQL UNKNOWN review note was
addressed with explicit `?& ARRAY[...]` and actual safe-savepoint missing/null/
wrong-type/version constraint-denial test source. Main's subsequent retained
target-revision lineage note was addressed with the composite FK and actual
safe-savepoint invalid retained-revision denial source. SQL1/SQL2 were not authored.
CLI reuses existing bounded local-file reader and strict parsing primitives,
denies ambient PG settings and DSN service/passfile controls, validates before
database dependency construction, and emits fixed safe summaries/errors.

Pure regressions cover deterministic mapping/copies/inert provenance, trusted
inputs, malformed/extra/digest/UUID/offset/Unicode/NUL/caps, no early connection,
and CLI privacy/authority controls. Actual PG sources cover exact Unicode/source
resolution/provenance, stable repeats, distinct targets/history, current authority,
late real driver failure rollback, collisions, tampering, concurrent imports,
SQL required-field constraints and fresh installed `-I` CLI file->PG->SourceStore.
No test results or counts are claimed. Main owns inherited migration expectations,
new migration qualification source, runner/CI/install/provenance registration.

## Limits and operations

UNVERIFIED: no imports, tests, lint/type checks, builds, install, runtime, DB,
migrations, browser/audit, status/hash/Git, network/provider calls, messages to
other chats, delegation, automations or release operations were performed.
Tools used only PowerShell `Get-Content`/`rg --files` source/coordination reads and
`apply_patch` file authoring. Root/current records were read for ownership; all
source reads and writes stayed in assigned u01. No other worker checkout accessed.

No binary/graph restoration, workflow readiness adoption, publisher authentication,
semantic support, HTTP endpoint, whole-project/full C41/U04/all44 acceptance,
paid calls or public deployment. PostgreSQL JSONB textual-size constraint may be
stricter than the canonical-byte cap; a SQL denial rolls back the whole import.
Socket guard in authored CLI test is not native libpq egress proof. Receipt
integrity does not defend against a privileged DB actor recomputing all digests.

Latest requested worker settings GPT-6.1 Sol/medium, Fast ON; Fast application
unavailable/unverified. Main owns review/verification/integration/acceptance.
Stable handoff; worker idle pending Main corrections. No implicit next packet.

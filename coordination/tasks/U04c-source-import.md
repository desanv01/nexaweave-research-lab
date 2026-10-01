# U04c — atomic owned import of selected retained-source bundles

Main planning packet; NOT a worker assignment until copied into an explicitly released checkout and dispatched to a separate named MiroFish project chat. Dependency base is reviewed draftPR73 exact d8647ad311936a53613da49b43e1305b56af8932; its Linux48 qualification passed but full acceptance is pending. Bounded implementation may proceed independently in the released checkout; import acceptance requires accepted PR73 and Main review of the exact combined revisions. No implicit dependency acceptance. Main owns all review/execution/install/checks/Git/CI/acceptance; implementation worker authors only.

## Product behavior

Import the validated v1 selected-source artifact into one explicitly chosen EXISTING persisted-owned project, with an expected current project revision. Import only its selected retained text and exact passage records. Do not overwrite/create the target project, change its snapshot/evidence/current revision or copy old workflow readiness. Preserve original project/snapshot/evidence/source/passages identity and original recorded times as inert private import provenance. A receipt distinguishes original provenance from newly retained/imported timestamps.

The artifact never grants principal/workspace/project authority. Trusted local CLI/operator supplies principal, target project and expected revision outside artifact bytes. Import has no HTTP endpoint, provider, model, Graphiti, graph binding, binary restoration, extraction, URL/path/workflow dispatch, semantic truth or publisher-authentication claim. No implicit migration.

## Atomic transaction and identity

Validate trusted expected artifact SHA256, strict v1 envelope, all current caps/digests/UUIDs/types/offsets/metadata and copied inputs before opening a connection. Require explicit expected whole-artifact hash for import; this pins caller-chosen bytes, not publisher trust. Use deterministic UUID5 IDs namespaced by target project, kind, whole-artifact hash and original canonical IDs for each imported source/passage. Preserve exact Unicode text/name/excerpts/offsets/page/hashes/order; new source recorded_at reflects retention/import time, while original timestamps remain in provenance. Original snapshot/evidence metadata stays inert in receipt, not live target metadata or binary presence.

One owned PostgreSQL connection and one transaction: bounded statement/lock/idle timeouts; lock target project row with persisted principal and expected current_revision; missing/other owner denied, stale revision conflicted. Insert selected source/passages and immutable import receipt atomically. No nested store-owned connections or independently committed source inserts. Failure after any insertion rolls back all new source/passages/receipt and closes connection. Never overwrite existing records or adopt source/evidence IDs from artifact.

Idempotence keyed by owned target project plus artifact hash. A repeat first resolves current persisted owner/revision, verifies exact receipt lineage and all persisted imported source/passage data against validated expectations, then returns the original receipt/timestamp without writes. Changed/tampered persisted content fails closed; wrong owner cannot enumerate receipt. A second target gets distinct IDs; parent/original sources and target snapshot/history remain untouched.

## Sequential migration

New fixed SQL3 import-receipt table under mf_app with target project FK, unique target/artifact identity, origin project/revision and whole/payload hashes, aware imported timestamp and bounded structured provenance JSON/digest. Cap canonical provenance UTF8 at2MiB and deny NUL/invalid Unicode anywhere in copied PostgreSQL JSON metadata before connection. SQL checks fixed field types/hash syntax and bounded JSON size; no arbitrary executable SQL. No redundant index on existing unique key. Preserve SQL1/SQL2 bytes/checksums/catalog behavior. Add only version3 tuple to store.migrate registry; no other existing store changes. Main updates inherited migration tests and qualifies fresh/v1/v2 upgrade, repeat migrate, mismatch and transactional rollback. Workers do not run migrations or checks.

## CLI/files/privacy

New installed module, one bounded stdin JSON request with operation import, principal, target_project_id, expected_revision, input absolute local path and required expected_sha256. Reuse reviewed file admission/strict parsing principles and trusted export CLI DSN restrictions; reject extra fields/ambient PG/service/passfile/user DSN. Read/validate file before connection. Fixed safe JSON summary/errors and exit0/2; no text/path/principal/DSN/raw exception echo; no optional filesystem receipt write creating postcommit ambiguity. Immutable database receipt is authoritative. Existing export/inspect CLI behavior unchanged.

Keep original private text and metadata warnings; imported snapshot paths/object keys/workflow IDs never resolve. False original-binary/graph-restored/publisher-authenticated flags. This is owned selected-source import with inert provenance, not whole-project restore, full report/event/metric/checkpoint portability or full C41/U04/all44 acceptance.

## Bounded files and authored regression source

Worker allowed: new migrations/0003_research_imports.sql; new research_import.py; new research_import_cli.py; new test_research_import.py; new test_research_import_postgres.py; new docs/architecture/owned-research-source-import.md; coordination/handoffs/U04c-source-import.md; store.py ONLY version3 registry tuple. Main supplies task, runner/CI/dependency/provenance and inherited-test registration.

Pure cases: deterministic typed remap, isolation across targets/artifact hash, all strict caps/trusted digest/extra field/malformed JSON/Unicode, inert malicious metadata, no connection on validation failure. Actual guarded PG cases: owned explicit target import, exact retained Unicode passages/resolution/hashes/declared pages and original provenance; preserved target snapshot/digest/history; stable idempotent repeat; distinct second target; owner/missing/stale denial before writes; rollback after injected late write failure; collisions/malformed/tamper denied; all connections closed. Fresh -I noneditable CLI actual file -> PG -> SourceStore read and digest, no provider imports; inspection/file payload never dispatches code/URL. Python socket guard is not native libpq egress proof; use only explicit approved literal disposable fixture DSN. Main executes every check.

No tests/imports/lint/typechecks/build/install/runtime/status/hash/Git/network/DB/browser/audit, messaging/delegation/automation, commits/pushes/PRs/merges or release by worker. Return stable UNVERIFIED paths, exact limitations and operations used; idle pending Main correction. Worker GPT6.1Sol/medium; FastON requested, unavailable/unverified unless tool evidence establishes it. Main requested FastOFF; no global default changes.

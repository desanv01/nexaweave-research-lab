# Knowledge operation ledger (U03a foundation)

This package records knowledge write identity and one outstanding writer per canonical `KnowledgeScope`. It is a synchronous PostgreSQL boundary in the isolated knowledge runtime. It does not call Graphiti, authorize users, reserve paid model usage, or reconcile uncertain writes.

## Migration and privileges

Migration `0001_knowledge_operations.sql` is applied only by an explicit call to `migrate(connection)` against an empty, disposable `mf_knowledge` schema. The migration uses a transaction-scoped advisory lock and stores the SQL file SHA256 beside version 1. It also stores a catalog fingerprint of all owned tables, ordered columns and types, constraints, and index definitions. A rerun compares both checksums; an existing schema without the record is rejected. There is no implicit DDL at import or Ledger construction and no downgrade or purge API. Main owns the disposable fixture and production rollout procedure.

Use separate database roles: a migration owner with schema/DDL rights and a runtime role limited to `USAGE` on `mf_knowledge` plus the required `SELECT`, `INSERT`, and `UPDATE` on its four data tables and `DELETE` only on `scope_admissions`. Runtime needs no schema creation, migration-table mutation, role creation, or unrestricted database rights. Provision roles outside this migration after reviewing the deployment. Do not embed credentials in migration files.

`Ledger` receives a factory that returns a fresh Psycopg3 connection for each call. Configure a finite connection timeout in that factory. The ledger sets local statement, lock, and idle-in-transaction timeouts, uses short transactions, closes its connection, and emits only stable public error text. No external provider wait belongs inside a transaction.

## State and admission

The caller supplies an already authorized, validated scope. The derived `group_id` is a partition key, not authorization. `admit` records a UUID operation and the exact canonical request fingerprint. The helper `request_fingerprint(scope, source, ontology)` uses the same scope/source/ontology JSON fields, sorting, encoding, and SHA256 as the existing provider helper. A repeated operation with a changed fingerprint conflicts.

`claim` accepts `pending` or `failed_no_effect`, creates a fresh UUID4 attempt and a scope admission in one transaction, and returns the token only to that claimant. An existing admission blocks both the same operation and a different operation in that scope. The token guards ledger updates; it cannot fence a stale worker's Neo4j writes. `heartbeat`, `complete`, `mark_uncertain`, and `fail_no_effect` require the current token and admission. A completion validates the deterministic episode UUID, scope, fingerprint, bounded evidence UUIDs, and a 64 KiB maximum receipt before writing it. Duplicate completion with the same receipt/token returns the stored record; a different receipt conflicts.

`mark_uncertain` retains the admission. Time or heartbeat age never releases it. Generic `complete` cannot release an uncertain admission; a future reconciliation API needs proof of provider effects before acknowledging it. `fail_no_effect` releases only a running attempt after an explicit no-effect classification; it cannot turn uncertain work into a retry. A pending operation can be cancelled, but a running operation cannot. Tombstoning blocks new admission and claim while an existing matching running attempt may still record completion or uncertainty. There is deliberately no generic reset, unlock, purge, or automatic replay method. Reconciliation needs its own reviewed API and evidence of provider effects and old-worker termination.

Operation states and attempts remain queryable through fresh Ledger instances. Receipts contain only identifiers and a fingerprint; callers must keep prompts, source content, model payloads, secrets, and provider credentials outside this ledger. PostgreSQL and Neo4j are separate commit domains, so this foundation does not promise distributed exactly-once effects.

## Tests and current limit

The candidate uses Psycopg/binary3.3.6 in the isolated knowledge lock and the
official PostgreSQL18.6-bookworm image pinned by digest in the test Compose file.
[Psycopg package metadata](https://pypi.org/project/psycopg/3.3.6/) declares
LGPL-3.0-only; binary dependencies and notices must remain in the release SBOM/
redistribution review. This does not change the inherited application license.
Main's launcher constructs only the disposable loopback DSN and refuses skipped
or empty requested database qualification. The fixture role is for disposable
CI only, not a production least-privilege configuration.

`tests/test_operations.py` covers pure validation and fingerprint equivalence. `tests/test_operations_postgres.py` is opt-in with `KNOWLEDGE_POSTGRES_INTEGRATION=1` and `KNOWLEDGE_POSTGRES_TEST_DSN` constrained to host `127.0.0.1`, port 15432, user `mirofish_fixture`, and database `mirofish_operations_test`. If integration is explicitly enabled, a missing or mismatched DSN fails the fixture. Fresh Ledger instance visibility is checked; fresh OS-process recovery is not yet a test in this packet. Main runs the tests against its disposable fixture and owns further integration, migration deployment, usage accounting, and provider cutover.

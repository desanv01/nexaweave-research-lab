# Knowledge operation boundary — Main design for U03

Status: implementation contract; not implemented or qualified by this document.
Evidence base: accepted U01a Graphiti spike and current provider source.

## Why persistence must precede the cutover

The spike stores an episode and a completion marker in Neo4j, but its check then
write sequence is not a concurrent admission lock. The Flask integration must
not expose it as a multi-worker idempotent service. Use the approved early
PostgreSQL foundation for operation identity and admission before routing
ordinary application mutations through it. Keep Graphiti dependencies isolated
from the native simulation environment; no requirement for Supabase hosting.

## Authority and initial records

- Scope mapping owns a stable application scope UUID, complete canonical scope
  fields, unique group_id, schema revision and tombstone state. Canonical UUID
  mapping for legacy project IDs is persisted, never guessed from an ID's shape.
- Knowledge operation owns scope + operation UUID (unique pair), canonical
  request fingerprint, source/ontology revisions, state, attempt identity,
  admission/heartbeat times, provider episode identity, bounded result receipt,
  and a stable error classification. Different input under the same identity is
  a conflict, not an update. Never place model keys or raw prompts in the ledger.
- A scope write-admission record prevents overlapping extraction/mutations in
  one scope, including different operation IDs. The initial safe concurrency is
  one in-flight write per scope. Scope partitions are not authorization.
- Usage attempts reserve a bounded configured allowance before paid dispatch;
  an interrupted or missing provider response is an unknown charge, not zero.
  Full organization quotas/accounting remain U05; no paid calls until Main has
  the user's provider configuration and explicit test cap.

SQL implementation must use UUID/timestamptz and constrained states, unique
operation keys, foreign keys/indexes, parameterized queries, explicit bounded
connections/timeouts and separate migration/runtime privileges. No production
table migration is authorized by this design alone. Version/checksum migrations
must reject incompatible schema drift rather than hide it with IF NOT EXISTS.

## State transitions and external effects

1. Admit validated request and immutable fingerprint in a short transaction.
2. Acquire operation and scope admission atomically; record attempt token before
   any remote dispatch. Recheck tombstones and write conflict in that transaction.
3. Commit, then call Graphiti outside the PostgreSQL transaction. Holding row
   locks across an LLM request is not the recovery mechanism.
4. Commit a completion receipt only after the provider identity/scope/marker and
   evidence references validate, guarded by the current attempt token.
5. If a timeout, worker death or lost response makes dispatch uncertain, retain
   an uncertain state and quarantine that scope's write admission. Do not turn
   an expired heartbeat into permission for a replacement writer.
6. Reconciliation inspects the matching provider completion marker and result
   evidence. A proven completed operation may be acknowledged without replaying
   extraction. A partial episode is not success. Absence of a marker alone does
   not prove that no dispatch is still running.
7. Only proven no-effect failure may be retried automatically. Partial writes
   require an explicit recovery decision and old-writer termination evidence.
   Cancellation after dispatch records intent/uncertainty, not rollback.

Attempt tokens guard PostgreSQL updates, not Neo4j writes made by a stale
process. We cannot promise distributed exactly-once or fencing of Graphiti's
internal writes. The initial quarantine policy deliberately trades availability
for protection against concurrent uncertain replay. Temporal later coordinates
these operations without weakening their rules.

## Runtime boundary and incremental integration

Use a separately installed knowledge worker/runtime, accessed only through
versioned application DTOs. A supervised private internal transport must impose
authentication, request/response byte caps, deadlines and allowlisted methods;
no arbitrary Cypher, filesystem paths or user-chosen provider endpoint dispatch.
The final transport implementation receives its own packet before coding.

First implement and qualify the ledger/concurrent admission with a disposable
real PostgreSQL instance. Then integrate it around the existing ingest method,
extend scoped entity/edge paging, history and lifecycle operations, and adapt
graph building, entity/profile readers, investigative tools and memory updates.
Preserve all four research tools, reconciliation barriers and partial failures.
No normal import path may instantiate Zep; no fallback to cloud or silent
keyword-only research. Remove Config.validate's Zep prerequisite only together
with a working explicit provider path, not by hiding the startup error.

## Main acceptance fixtures

Race identical/different requests and distinct operations in one scope; simulate
death before dispatch, after dispatch, after Neo4j completion and before the
PostgreSQL acknowledgment; verify wrong-scope receipts, stale attempt tokens,
tombstones, cancellation and schema drift. Inspect database state, not mocks
alone. Confirm no transaction spans the fake model wait. Repeat in a fresh
process and show ambiguous work is not automatically replayed.

Design references: [PostgreSQL row locks](https://www.postgresql.org/docs/current/explicit-locking.html),
[constraints](https://www.postgresql.org/docs/current/ddl-constraints.html),
and [Psycopg concurrency](https://www.psycopg.org/psycopg3/docs/advanced/async.html).
Exact dependency/image versions will be pinned and tested during implementation.

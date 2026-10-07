# Durable graph-bound preparation

U07c adds explicit plan/start/status routes to the protected `research_local`
application. A plan resolves the trusted display binding, PostgreSQL project and
retained source, reads one bounded graph projection and freezes source text,
projection, options and UUID-sorted actor selection in `mf_preparation`. It does
not call a model. The graph node/edge scan is non-atomic. The seed belongs to the
future native run; it does not randomize selection or promise deterministic LLMs.

The version-one API admits 1–100 actors, seed 0–4294967295, Twitter, Reddit or both,
and **1–24 rounds**, matching the accepted native run contract. Start reauthorizes
the project revision/source, reserves the operation ceiling against the existing
shared `mf_execution` account lock, and CAS-queues one attempt. The synchronous
HTTP method only schedules identifier/digest data through the injected scheduler.
The PostgreSQL queue fence and Temporal reject-duplicate workflow ID prevent
duplicate generation after repeated clicks, response loss or host restart. Status
uses the same operation ID and plan digest and reads historical authoritative
state without reading artifact files or initializing any provider.

The plan identity SHA-256 uses canonical ASCII JSON: recursively sorted object
keys, compact comma/colon separators, `ensure_ascii=True`, and finite values only.
Its exact fields are schema_version, display_graph_id, scope, project_revision,
operation_id, source, options, actors and projection_sha256. Mutable status,
authorization and receipt fields are excluded. Producer correspondence requires
nonblank actor names of at most 1024 Unicode codepoints and source names of at
most 256; strings admit neither NUL nor lone surrogates. Authorization ceilings
are canonical positive decimal strings at most 2^63-1; enabled model capability
requires a ceiling. Result checking recomputes the plan identity hash.

## Explicit operator composition

Ordinary Flask construction registers a lazy `KnowledgePreparationFacade` with no
host. Requests return `preparation_unavailable` until the operator explicitly
composes a trusted host; no environment flag silently initializes a provider.
The public routes share the existing loopback bearer and origin boundary. They
reject query/extra/duplicate/nonfinite/encoded/chunked input, require strict UTF-8
JSON at most 64 KiB and revalidate injected facade replies at most 256 KiB.
The trusted facade caps simultaneous calls at four. Public replies contain source
references, actors, options and status, never source text,
graph attributes, prompts, credentials, model responses or private paths.
Operational model_calls_disabled and budget_denied replies use HTTP 409 so the
private client retains the reviewed plan/recovery identity. Unauthorized remains
401 and origin_denied remains 403.

The host runtime needs the BACKEND `native-store` and `native-temporal` extras,
the installed knowledge distribution's `execution` extra, and the existing
Flask/backend generator requirements. It uses the installed knowledge package, without dynamic
source-module loading or another knowledge stack. Main owns installation and
qualification; this packet adds no dependencies or automatic installation.

The operator explicitly runs `nexaweave_execution.preparation_store.migrate(conn)`
after the accepted application, knowledge and budget migrations. It owns a separate
namespace, SQL checksum, advisory lock and catalog drift checks. It does not alter
the accepted `mf_execution` migration/catalog. `BudgetLedger.reserve_prepared`
uses the SAME reservation table/account totals as ingestion, with a domain-separated
fingerprint. Settlement writes a DISTINCT tagged `PreparedBudgetReceipt`, preserving
legacy ingestion `CompletionReceipt` decoding. Ceilings are conservative admission,
not measured provider bills. Started uncertainty retains the entire ceiling.

Construct `DurablePreparationHost` with trusted `ReadHostSettings`, a fixed PG
connection factory, the accepted bound `KnowledgeReadFacade`, an existing absolute
private artifact root, shared account ID and positive operation ceiling, a current
authorization callback, and a trusted chat client factory/model metadata. The
authorization callback defaults absent/disabled. The client must honor the wrapper's
finite `timeout`/`max_tokens`, have internal SDK transport retries disabled, and
close promptly. The host retains at most four active/unclosed transports and denies
additional creation while those slots remain occupied. The wrapper shares call/token/deadline bounds across inherited
generator workers/retries; calls are limited to `3 * actors + 4`, each to 4096
output tokens and at most 15 seconds, operation deadline at most 600 seconds.
These injected transport guarantees are a trusted host obligation; Python cannot
preempt an arbitrary non-cooperative SDK function. No live provider was qualified
by source authoring or scripted fixtures.

Construct `TemporalPreparationHost` using an already connected trusted client,
fixed task queue and host. Enter its `worker()` async context in an operator-owned
event loop. Set the durable host scheduler to `temporal.scheduler_for(owned_loop)`
before exposing it via `KnowledgePreparationFacade(settings, host=host)` and
`create_app(preparation_facade=facade)`. HTTP threads submit to that existing loop;
the adapter creates no loop/thread/detached job and must not be called from the
loop's own thread. The worker owns and drains its executor. Failed transport close
is retained on the trusted host; `drain_cleanup()` retries closure without model
dispatch and returns false until proven closed. Keep that host alive if worker
shutdown reports `preparation_uncertain`; cleanup failure never publishes READY.
Activity retry is
exactly one attempt, start-to-close 660 seconds, schedule-to-close 720 seconds,
heartbeat timeout 45 seconds, workflow lifetime 780 seconds. Activity heartbeat
context is carried into the inherited profile worker callbacks. History receives
only dispatch IDs/digest and terminal IDs/artifact digest, with fixed errors.

## Publication and native handoff

The activity claims the queued PG attempt once, reauthorizes before filesystem
access and first provider call, and invokes actual strict neutral
`OasisProfileGenerator` and `SimulationConfigGenerator` through `SimulationManager`.
The narrowly added frozen projection/selected UUID dependency preserves complete
incident facts and neighbors and performs no fresh graph reads at start. A trusted
deterministic `sim_<operation UUID hex>` ID is accepted only for neutral preparation;
legacy callers retain their prior default behavior.

Generation writes a private `_attempts/<operation>/<attempt>/sim_<operation>` tree.
The host verifies state/config/grounding, platform correspondence and profile
count/order, bounded native regular-file guards and manifest. Profile names must
match each frozen actor in order. Twitter admits exactly the inherited ordered
five-column header (user_id, name, username, user_char, description), rejecting
duplicate/foreign/reordered columns and wrong row widths. When both platforms are
enabled, the host reconstructs each Twitter row from the corresponding Reddit
username/bio/persona using the actual inherited `twitter_loader_row`, including
its bio/persona joining and CR/LF normalization, and requires exact correspondence.
It creates a NEW
publication directory exclusively, copies complete files, flushes them and marks
inputs read-only, verifies exact bytes again, settles the tagged budget receipt,
then CAS-publishes PG READY. Publication is NOT a distributed filesystem/database
atomic transaction; partial/orphan directories or lost acknowledgments never
grant launch and cannot be overwritten. Attempts and orphan publications are
retained for diagnosis; no cleanup deletes unowned paths. Failures after started
dispatch become uncertain and hold budget. A crashed/lost activity can remain
queued/preparing with its attempt fence rather than falsely claiming cancellation,
release or retry; explicit operator reconciliation is a later capability.

`bind_native` is trusted Python composition only. It reauthorizes owned READY and
the exact current project revision BEFORE file existence/reads, derives the fixed
server simulation path, validates the complete receipt and bytes, and returns an
accepted `NativeRunRequest` plus `NativeOwnedSessionFactory` using trusted runtime
SHA and picklable model factory. Those can feed existing `NativePreparedHost` or
`TemporalPreparedHost`; their native lifecycle/admission contracts are unchanged.
HTTP cannot supply paths, runtime/model factories or launch a run. Prepared exports
are not described as executed simulations. Native live launch/feed/reports remain
subsequent work.

## Qualification fixtures authored for Main

`backend/tests/test_durable_preparation.py` exercises actual inherited scripted
generation through an explicitly nondurable in-memory activity seam, artifact
formats/grounding/native binder, duplicate/lost scheduling replies, strict generator
failures, pre-file reauthorization and bounded client limits. Additional fixtures
mutate actor names/order, duplicate/foreign/reordered Twitter headers, row width
and cross-platform username/bio/persona mappings before artifact admission, and
exercise the inherited CR/LF normalization through actual scripted generation.
`backend/tests/test_preparation_api.py` exercises actual Flask boundaries, strict
DTOs (including injected replies), no-host behavior and cold optional imports.

`services/knowledge/tests/test_preparation_store.py` uses the existing approved
disposable PG guard `PROJECT_STORE_POSTGRES_INTEGRATION=1` and
`PROJECT_STORE_POSTGRES_TEST_DSN`, real project/source/binding/preparation/budget
stores and actual scripted inherited generation. It covers duplicate races,
restart/ownership, shared ingestion/preparation cap contention, crash fences,
stale/tombstoned authorization, binder-before-files and migration drift. Its graph
byte seam is scripted; it does not qualify live Neo4j or model quality.
`test_temporal_preparation.py` includes pure identifier/one-attempt checks and a
`postgres` + `preparation_temporal` combined fixture guarded by
`TEMPORAL_EXECUTION_INTEGRATION=1` and exactly
`TEMPORAL_TEST_ADDRESS=127.0.0.1:17233`. It runs the actual Temporal worker, PG
authority, inherited scripted generators, history replay, duplicate/restart fences
and executor cleanup, under finite waits. Main's preparation runner must include
backend and service test import paths; workers execute no fixtures.

All new behavior is UNVERIFIED until Main runs qualification and reviews exact
source and hosted logs. No model-quality, native-launch, install, full U07 or full
44-capability acceptance follows from these authored fixtures.

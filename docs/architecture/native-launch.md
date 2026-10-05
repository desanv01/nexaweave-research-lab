# Connected native launch

Status: authored, UNVERIFIED. Main owns installed-package, PG, Temporal, native
engine, browser and hosted acceptance. This document is not a qualification
receipt, provider-price guarantee, model-quality claim or full-phase claim.

The protected `research_local` routes are explicit POSTs to
`/api/native-launch/{plan,start,status,cancel}/<display_graph_id>`. The existing
bearer/origin boundary applies before the route. Every request rejects duplicate
JSON keys, nonfinite JSON, unknown fields, queries, encodings and a missing or
oversized length. Bodies are limited to 4096 bytes; exact public DTOs to 65536
bytes with the existing 128-byte envelope allowance. Cold hosts return
`native_launch_unavailable` and construct no provider or native engine.

Plan admits only a canonical launch UUID and an independently owned READY
preparation operation/digest. Its frozen identity contains exactly
`schema_version,display_graph_id,scope,preparation,request,limits,ceiling_microusd,model_label`.
Sorted compact ASCII JSON defines the SHA256, including Unicode escaping. The
request is the accepted NativeRunRequest, not an alternate API or manifest
adoption mechanism. It preserves the exact simulation/artifact/project revision,
platforms, seed and rounds from the preparation. Public DTOs expose no path,
credential, source text, process identity or provider endpoint.

Up to 100 immutable unstarted reviews per principal/preparation are retained in
the separate `mf_native_launch` schema. A disabled review captures null ceiling;
operator configuration may be followed by a NEW explicit launch UUID/review.
Neither changing configuration nor repeating a review mutates a frozen plan.
The private frozen configuration stores only account identity and a hash of the
picklable bounded model parameters, never their credential-bearing pickle.
Review does not register a native run or spend the native filesystem marker.

Start reauthorizes source/project/READY authority, reserves a native budget
purpose, then CASes one permanent dispatch claim. A partial unique own-schema
index enforces one claimed row per principal/preparation. Queue admission locks
the current project and preparation records. Model/budget/config denial before
queue does not spend that artifact claim. Exact duplicate starts recover; they
do not reschedule. Scheduling uncertainty retains the claim, full ceiling and
immutable run identity. No retry or unrelated-run adoption discovers a lost
acknowledgement. A late valid activity may still resolve that same fenced row.

The trusted host uses the accepted TemporalNativeHost, NativePreparedHost,
NativeOwnedSessionFactory, NativeProcessDriver, NativeRunCoordinator and
NativeRunSupervisor. Its permitted supervisor subclass adds ledger settlement;
it preserves the original owner thread, one-shot start and retryable retained
cleanup. The trusted Temporal factory reauthorizes before inspecting files and
again at the coordinator's prelaunch policy boundary. PG, Temporal and files
remain separately guarded authorities; this is not a distributed transaction.

Native reservations share the ingestion/preparation account lock and totals.
The accepted `mf_execution` SQL/catalog remains unchanged. That table requires
the original `mf1_…` scope group; native purpose therefore uses a domain-specific
episode UUID (`mirofish:native-budget:v1:<scope-group>:<run-uuid>`) and
`native_run_budget_v1` fingerprint/receipt. Existing ingestion and preparation
scope/episode IDs remain identical. Opposite-purpose receipt injection fails.
Only the exact terminal PG NativeRunReceipt can settle a native reservation.
Completed/failed/cancelled execution accounts the entire ceiling. Started
uncertainty holds it; cancellation or caller timeout never releases it. Release
requires a row-lock transition permanently closing a still-unclaimed review
before reservation release, or an already-authoritative undispatched closed
review. A stale GET of planned is not release proof: queue may race that read.
`close_undispatched` serializes with queue and returns release permission only
for cancelled/intent=true/dispatch_claimed=false/budget_attempt_id=null. Once
closed the review cannot queue, even if another Start retained a stale planned
DTO. Queued or uncertain rows never grant release permission. Accounted ceilings
are admission amounts, not billed usage.

BoundedNativeModelFactory carries picklable parameters and creates CAMEL models
only in the owned child. A single lock/counter/deadline is shared by both
platforms and synchronous/asynchronous run paths. Inputs (messages plus tool
schema and response format), maximum calls, output tokens and run duration are
bounded. Returned CAMEL backends must expose finite `_timeout`, exact-zero
`_max_retries`, finite `max_tokens`, and nonstreaming configuration. Scripted
mode rejects SDK clients, API keys and endpoints. Local mode accepts literal
127.0.0.1/::1 HTTP(S) endpoints only, without userinfo/query/fragment. Responses
are checked after transport return, including measured tokens and bounded bytes.
Paid/provider mode remains disabled because captured provider pricing and a
justified spend policy are absent. Python cannot preempt an arbitrary factory or
transport; the trusted operator must supply cooperative finite construction,
transport and cleanup. The accepted native owned-process timeout/cleanup remains
an independent lifecycle boundary.

Composition is explicit: instantiate DurableNativeLaunchHost with the accepted
preparation host and the SAME account, then construct TemporalNativeHost with
`supervisor_factory=launch_host.supervisor_factory`, and attach that real host
with an operator-owned synchronous bridge to its already-running event loop.
The bridge must use finite waits, avoid that loop's own thread, and retain lost
scheduling acknowledgement without cancelling/resubmitting the workflow. This
module creates no background loop, polling job, client, worker or schedule.

Status and cancel require the exact retained launch UUID/digest. Cancellation
records intent and routes it to the actual native owner when possible. A
cancelled terminal receipt qualifies observed cancellation; intent does not.
Cleanup is unknown unless the actual retained Temporal local registry supplies
its booleans. Failed close retains that same owner for explicit retry. Native
completion is owned evidence, not a narrative report or prediction.

Authored fixtures include strict Flask/schema negatives, newly inherited file
generation, configuration changes, lost replies, shared sync/async bounds,
transport policy negatives, failed-close owner retention, installed real-PG
review/claim races, all-three-purpose cap races, receipt isolation, actual
Temporal/PG spawned-gate recovery and the actual both-platform OASIS child using
fresh U07c PG/Temporal-produced rich preparation. The engine fixture checks
native DB/actions, receipt correspondence and unchanged prepared input bytes.
Selected PG/Temporal fixtures fail missing explicit disposable services; they
do not silently skip. Main must execute and review these authored assertions.

Correction1 adds deterministic same-ID queue-error versus winning Start fixtures,
the opposite locked-close versus stale queue ordering, and distinct-review
winner/loser release checks. Stored rows require exact private frozen/config keys,
canonical account identity/factory digest and coherent state/claim/budget attempt,
workflow/receipt/cancellation/error fields. Unclaimed rows can only be planned
without cancellation or cancelled with intent; claimed terminal rows need an
exact terminal native receipt. Malformed rows fail closed before DTO/factory use.
The own, not-yet-installed launch SQL mirrors these lifecycle constraints.

Correction2 keeps the spawned offline gate factory's defining test module
lightweight: its module imports only stdlib/pytest, with Temporal, native,
preparation and guarded PG fixtures imported lazily in the parent test/functions.
The gate remains a real owned spawned child with a typed request fingerprint and
observed file evidence; the separately authored OASIS case is unchanged. The
original five-second driver handshake, ten-second start marker and gate lifetime
remain unchanged. Missing markers report only bounded cached supervisor/native
state, fixed safe error codes, owner/cleanup booleans and available numeric
process/reader observations; no authority refresh, retry, path/credential/endpoint
or exception text is added. A cold import fixture detects eager heavy dependency
regressions. This fixture correction requires Main execution to establish cause.

# Bounded retained-edge evidence research

U06a adds one explicit local read operation. `EvidenceResearchService` receives a
trusted principal, PostgreSQL connection factory, and Neo4j driver factory. It
does not initialize Graphiti clients or call extraction, embeddings, rerankers,
search, or writes. The factories must provide bounded connection establishment
and query operations; the CLI reuses accepted `ReadSettings` validation and its
three-second connection settings. PostgreSQL stores retain their accepted
statement/lock timeouts.

## Local request and construction

Run `python -m nexaweave_knowledge.research_cli` with one UTF-8 JSON document on
stdin. Example:

```json
{"display_graph_ids":["source_graph","simulation_graph"],"text":"employment evidence","top_k":10,"valid_at":"2025-01-01T00:00:00Z","recorded_before":"2025-02-01T00:00:00Z"}
```

Only `schema_version` (1), `display_graph_ids`, `text`, `top_k`, `valid_at`, and
`recorded_before` are accepted. IDs are display identities, never authorization
tokens. Request limits are 1–5 unique display IDs, 2,000 query codepoints, top_k
1–100, and 16KiB serialized stdin. Duplicate keys, extra fields, booleans/floats
for integers, nonfinite JSON numbers, malformed UTF-8, naive timestamps, and
model-copy validation bypasses are rejected. Trusted configuration is read only
when CLI main is called. No principal, scope, secret, URL, source path, model, or
Cypher enters through the request.

The CLI uses existing `KNOWLEDGE_*` settings required by `ReadSettings`, including
`KNOWLEDGE_PRINCIPAL`, `KNOWLEDGE_DISPLAY_GRAPH_ID`, and
`KNOWLEDGE_BOUND_SCOPE_JSON`. Its trusted bound scope anchors the workspace,
project and graph for the operation; requests can select persisted bindings for
other layers/runs/branches within that identity. The trusted display ID remains
a validated setting, while selected IDs come from the request. The CLI owns one
operation and emits one JSON result or fixed JSON error, with no listener.

## Authority and read admission

Resolve every selected `ScopeBindingStore` record for the trusted principal.
Validate strict canonical scopes and identical workspace/project/graph identity;
retain each exact layer/run/branch scope separately. Reject aliases resolving to
the same scope instead of counting the same page twice. `ProjectStore.get`
confirms principal/project ownership and the actual workspace before graph access,
including graphs with no evidence. Acquire every accepted `Ledger.read_scope`
guard in stable group order, then re-resolve all bindings under those guards.
An active/uncertain write admission, tombstone, foreign binding, mismatched
identity or ownership error fails before constructing the graph driver. Group
IDs are query parameters derived from scopes, never a grant of authority.

All PostgreSQL binding, project, guard, and evidence work runs on a worker thread,
keeping the host asyncio loop responsive. All guards remain held through graph
reads, retained citation resolution, result construction and driver cleanup.
The service permits one in-flight operation per instance and two worker operations
per process, including threads still cleaning up after a timeout/cancellation.
Factories are synchronous and trusted. No request can change their settings.

## Enumeration and ranking limits

Use accepted `_DirectPageProvider.page`/`page_graph`: fixed parameterized Neo4j
edge enumeration, strict group/endpoints, completed episode markers and evidence
provenance. Five pages of 100 edges per exact scope bound scanning at 500;
there are at most five scopes. Deduplication uses exact scope/kind/provider ID.
No current node summaries are used, including current requests. Scope results
report page count, scanned, eligible, excluded, unknown, returned and whether a
continuation cursor remained. A truncated scan is not full graph coverage.

Rank retained eligible edges by the number of distinct query tokens also in the
fact text/relation name. Tokens are Unicode alphanumeric runs, case-folded,
without underscores. Ties use scope group, kind and provider ID ascending. This
is **lexical token overlap**, with no semantic quality or relevance confidence
claim; scripts without spaces can produce long tokens, and zero-overlap facts
remain eligible. `top_k` is global across the explicitly selected scopes.
For a recording cutoff, eligibility includes full retained provenance admission
before ranking. Excluded/unknown provenance does not consume top_k or contribute
returned citation counts; another eligible scanned edge can fill its place.

There is a 30-second response deadline measured before off-thread work starts.
Every PostgreSQL boundary checks the remaining deadline; each async page is
limited by remaining time. A running synchronous database call cannot be
interrupted by Python cancellation: after deadline the host returns a fixed
error, while the shielded worker retains guards until its bounded call finishes
and closes resources. Driver close is attempted on every path after successful
construction, with a three-second cleanup await. The CLI's event-loop shutdown
may wait for this cleanup; the 30-second budget is the operation response budget,
not a guarantee of process teardown at exactly 30 seconds. A failing or hanging
trusted factory cannot be made safe by untrusted JSON. Main should qualify
deadline and cleanup behavior against the accepted runtime.

Results are capped at 2MiB UTF-8 JSON, including citations. Interim selected
fact/citation size is also checked; oversize output fails with `result_too_large`
instead of silently omitting passages. Graph pages retain their accepted 1MiB
page cap. Provider/DB exceptions are replaced with `research_unavailable`;
configuration, invalid request, deadline and busy errors use fixed codes.

## Exact temporal semantics and limitations

This operation filters **currently retained edge records**, not an immutable
bitemporal journal. It cannot reconstruct deleted edges, prior overwritten fact
text, earlier episode/evidence associations, or previous invalidation values.
Graphiti mutation can make historical completeness unknowable. No result is a
claim to reconstruct everything known in the past; `historical_semantics` is
`retained_edges_not_bitemporal_reconstruction`. Historical records require a
known aware created_at. All supplied timestamps must be aware; naive values
are unavailable. Edge expiry with a naive timestamp can fail accepted page
validation and therefore safely fail the complete request.

With recorded_before, require edge created_at <= cutoff and nonempty episode and
retained evidence provenance. A fixed parameterized exact-scope Episodic lookup
uses only the fact's episode UUIDs, returns UUID/group/created_at, and has an
explicit limit of referenced UUID count plus one (at most 101). Require every
referenced episode to exist in that exact scope with aware created_at <= cutoff.
Later episodes are excluded; missing/naive recording timestamps or records are
unknown. Every linked retained source must resolve for the same principal/project
with an aware source_recorded_at <= cutoff. A future source excludes the entire
fact; missing evidence makes it unknown. No such fact's text, episode IDs or
evidence IDs is returned. Future provenance takes precedence over unknown when
both are observed. Scope eligible/excluded/unknown counts include these decisions.
The operation validates all temporally eligible scanned provenance before top_k,
not only evidence on an initially selected set. Only source status/metadata is
cached during historical admission; excerpts are resolved for the final admitted
selection. If retained evidence changes between admission and final resolution,
the request fails safely. Per-edge provenance is bounded to 100 episode/evidence
links; all lookups remain within the total operation deadline and held guards.

The accepted page reader independently requires nonempty episode references,
exact-scope episode existence and completed markers before returning a page.
An empty episode list or already missing/foreign episode can therefore fail the
whole request with `research_unavailable` before the host can report individual
unknown counts. No fake episode reference is inserted to bypass that contract.
Missing episode records observed at the later recording lookup and empty
retained evidence links are counted unknown. Corrupt retained evidence or invalid
provider rows also keep their fixed whole-request failure behavior.

With valid_at, require known
valid_at <= query time and invalid_at > query time, using a half-open interval.
Null invalid_at means no retained assertion end. Graphiti expired_at is treated
as the recording timestamp for invalidation: when it is later than
recorded_before, hide both invalid_at and expired_at and evaluate the retained
edge as not yet invalidated. If invalid_at exists but its recording timestamp is
missing, exclude as unknown for a recording snapshot. This does not restore an
older overwritten fact. A recording-only query uses current valid time and
excludes edges already expired by the recording cutoff. A current query excludes
currently expired edges and currently invalid assertions. A valid-time-only
query tests the retained assertion interval and can therefore include an edge
invalidated later; it has no recording-time claim. Future creation times are
excluded. Missing valid_at is unknown for valid-time queries; current queries
allow an undated assertion if other checks pass. Future-created or future-valid
edges cannot enter current results.

## Retained citations and measurable quality

Resolve each selected evidence UUID through accepted
`SourceStore.resolve_evidence` using the same trusted principal/project. That
store checks source revision, full retained UTF-8 hash, lengths, exact codepoint
offsets, excerpt equality and excerpt hash against retained data. The research
host additionally validates returned identity, revision metadata consistency,
aware source_recorded_at, offsets and excerpt hash. A future source revision
relative to recorded_before excludes its entire historical fact before ranking;
the fact text and episode/evidence IDs are suppressed along with source details.
A missing or foreign evidence link makes that historical fact unknown. Current
queries without a recording cutoff can still return explicit unavailable UUIDs.
Corruption or inconsistent resolver output fails with a fixed safe error.
Graph rows have evidence links, not an independent declaration of an expected
source revision/hash; the resolved retained store supplies those exact values.

Linked/resolved/unavailable citation counts describe returned fact-link
occurrences, not unique documents or ingestion success. Source revision coverage
unions returned passage codepoint intervals within each exact retained revision,
then divides by retained source codepoint length. Multibyte emoji and multilingual
text are counted as Python Unicode codepoints, not bytes or graphemes. Repeated
and overlapping passages do not inflate coverage. This is **retrieved passage
coverage**, not full ingestion, document understanding, evidence truth or model
quality. Only returned resolved revisions are listed.

Every fact retains its layer classification, full scope including run/branch,
episodes, evidence UUIDs, visible timestamps, overlap counts and resolved
citations. Source claims and simulation observations are separate result arrays;
assumption/inference/analysis claims retain explicit classes in another array.
No layer substitutes for source evidence. Candidate competing claims require
the same exact scope, subject ID and named predicate, with differing target or
literal fact text. Candidates point to returned provider IDs and resolved
evidence IDs (citations live on those facts). They are candidates for human
review, with no truth judgment or semantic contradiction inference. Cross-layer
claims remain separate and no automatic cross-layer contradiction is asserted.

## Qualification status

U06a worker authors source tests and guarded integration fixtures only. Main owns
execution, runners, opt-ins, credentials, review and acceptance. Integration
source reuses the accepted PostgreSQL loopback DSN guard/migrations and requires
the existing Neo4j opt-in and non-default fixture password. Synthetic local
Neo4j rows exercise the same production page/service path against retained
PostgreSQL passages without provider model calls. This packet does not qualify
UI flows, live-model quality, the whole U06 phase, or all 44 capabilities.

The separate U06 worker also authored a real subprocess JSON CLI regression
using the same retained graph fixture. It runs the actual module through runpy
after installing the accepted loopback socket guard, with a scrubbed environment,
only approved fixture database settings, and a 60-second process timeout. An
aware recording/valid-time request checks both explicit layers, run/branch scope,
exact retained revisions/hashes/codepoint excerpts and overlapping passage
coverage. A denied display request must exit with the fixed unavailable error.
The child passively observes actual Graphiti application construction/search/write
methods, LLM/embedder/reranker/OpenAI client constructors and call namespaces,
and PostgreSQL/Neo4j close calls across its main and worker threads. Imported
recipe/configuration DTO construction is permitted. It fails on an external socket
attempt, model/search construction, missing successful-operation close calls,
unexpected stderr or process status. This observation does not replace production
service/settings and is an in-process safeguard, not an OS network sandbox or
proof of every possible model route. Main owns execution and qualification of
this additional source; the worker has not run it.

Child qualification diagnostics expose fixed categories and integer expected/
actual exit statuses only. Distinct child codes identify a socket guard violation
(91), model/search observation (92), missing driver close (93), missing PG close
(94), both missing closes (95), or absent expected CLI exit (96). Timeout and OS
launch errors have separate fixed messages. Nonempty stderr remains a failure;
the diagnostic reports only whether a DeprecationWarning marker was present.
No warning text, raw stderr/stdout or configuration enters these failure messages.
No deprecation filter is applied without an identified accepted import warning.

Main identified minimal synthetic-schema Neo4j unknown-property notifications
in the successful child. The test child alone filters the exact warning record
from `neo4j.notifications` with the driver's `NotificationPrinter` argument,
parsed GQL status `01N52`, and raw/parsed classification `UNRECOGNIZED`. It records
that allowed notification in its passive observation set. Other notifications,
log records, warnings and errors retain their normal stderr behavior and fail
the existing assertion. No production driver/configuration/query/logging setting
changes; this fixture filter does not claim production stderr is empty.

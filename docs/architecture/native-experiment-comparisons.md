# Owned recorded experiment comparisons (U10a)

This read-only foundation compares existing runs authorized by the accepted
`NativeRunStore` and pinned recordings admitted by `NativeRecording`. It does
not dispatch simulations. Case/member labels are host declarations; they cannot
establish controlled interventions, equivalent populations or provider behavior.

## Trusted binding and selector request

The host constructs frozen `ExperimentCohort(principal, tuple(members))` with
1–16 unique member IDs and run IDs in one principal/project/revision. Each
`ExperimentMember` has a member ID, descriptive member/case labels and its exact
validated `NativeRunRequest`. A completed member must also have a trusted
`RecordingPin(absolute_bundle_path, manifest_sha256, RecordingAnchors(...))`.
Graph and branch anchors are supplied by the host; wire requests cannot select
them. Recording anchors must match the request's project/revision/run/simulation.
Path syntax is checked without opening recording files during binding creation.

The only request fields are:

```json
{"version":1,"title":"Offline comparison","member_ids":["case-a","case-b"]}
```

Order follows `member_ids`. The request is at most 8192 bytes and requires a
nonempty unique exact selection, version integer 1 and bounded title. Unknown
fields/IDs, duplicate keys, booleans in integer slots, nonfinite JSON and
malformed data fail safely. No request selects paths, principals, SQL, raw
metrics/receipts, model/provider settings or credentials.

## Authority and recording admission

All selected actual records are read through `get(trusted_principal, run_id)`
before opening any recording. The full stored request and fingerprint must
match the binding. Missing, foreign and conflicting authority fail the entire
operation. Completed/failed/cancelled records require matching terminal receipt,
attempt and child instance identity. Nonterminal receipts are rejected.

Only completed records contribute recording metrics. Failed/cancelled members
retain their explicit disposition; declared/starting/running become pending and
uncertain remains uncertain. Their metrics are unavailable, never synthetic zero.
Each member exposes the authoritative `cancel_requested` boolean, also covered
by its record digest. Cancellation intent does not establish a cancelled outcome:
a declared/starting/running member with requested cancellation remains pending,
without an invented child or terminal receipt. The separate `cancellation_intent`
object counts requested cancellations and explicitly marks its count as overlapping
the exclusive disposition accounting. It must not be summed into that accounting.
No register, start, cancel, reconcile, budget or Temporal operations are called.

The comparator admits one actual recording at a time and uses public
`describe()` and `read(metrics)` APIs. `describe()` returns detached metadata
from admitted bytes: recording revision, anchors/platforms, runtime SHA and
versions, and exact config/grounding/enabled profile SHA256 digests. It contains
no content or paths. Caller mutation and subsequent filesystem edits cannot
alter an already admitted view; fresh admission still rejects altered bytes.
The comparator validates metadata against trusted request/pin anchors and
retains only small metadata and numbers. The existing metrics API internally
reads its minimum one-row page, which is discarded without export.

After each completed recording read, the actual store record is fetched again.
Any change to identity, state, receipt or other stored record fields fails the
whole comparison. Separate authorized reads do not constitute an atomic cohort
snapshot. Incomplete member states describe their preflight observation time.

## Numeric output and limits

Metrics are logged action total/by type and counts for the fixed native table
allowlist. Missing tables are unavailable; an existing empty table is zero.
Absent action types in an available completed action log count as zero when
aggregating the observed union of action types. No action vocabulary is invented
for a cohort without completed observations. No row previews or excerpts appear.

For each case/platform, output includes eligible member count, successful and
non-successful counts, distinct declared/successful seed counts, and sample
count, missing count, minimum, maximum, arithmetic mean, median and population
standard deviation per metric. Missing count includes incomplete/failed members
and completed members lacking the metric; unsupported platforms are excluded
from that platform's eligible denominator. Members and accounting make these
coverage causes explicit. Distributions are descriptive available completed
observations, not estimated causal effects.

The pairwise comparability matrix gives both values and equality for trusted
seed, max rounds, runtime SHA, platforms, revision, prepared artifact manifest
SHA, admitted fixed artifact digests and runtime versions. Unavailable recording
metadata gives `equal: null`. Equal digests establish byte equality only.

Bounds: 16 members, existing recording quotas (160 MiB aggregate artifact bytes,
20,000 events per platform, fixed table/VM/row bounds), at most 128 observed
action types per platform, 120 pairs, and 512 KiB whole-result limit. The default
cooperative deadline is 60 seconds, host configurable within (0, 120]. It is
checked before/after synchronous store/recording work and before publication.
It does not interrupt an individual native call; existing PostgreSQL statement
timeouts, CLI connect timeout and SQLite VM bound still apply. No retries,
executors or background work are introduced. Oversized/corrupt reads and failed
members with inconsistent authority fail the whole result without clipping.

Canonical cohort manifest, record and result SHA256 digests support review;
they are not signatures. `result_digest` covers canonical result excluding that
field. Cohort digest covers the canonical validated binding manifest, distinct
from the CLI's exact-byte input SHA pin.

## Saved-script local CLI

Trusted argv supplies `--principal`, `--manifest` and `--manifest-sha256`.
The host manifest is the JSON form of `cohort.wire()`:

```json
{"version":1,"members":[{"member_id":"case-a","member_label":"A","case_label":"Baseline","request":{"schema_version":1,"principal":"owner","project_id":"00000000-0000-0000-0000-000000000001","project_revision":1,"simulation_id":"simulation-a","run_id":"00000000-0000-0000-0000-000000000002","artifact_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","runtime_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","platforms":["twitter","reddit"],"seed":7,"max_rounds":1},"recording":null}]}
```

`recording: null` is allowed for incomplete/failed runs; completion requires a
pin object with `bundle`, `revision` and exact `anchors` fields. These trusted
host paths/requests are never accepted from stdin. The manifest must be an
absolute canonical regular file without linked/reparse ancestors, at most
64 KiB, with an exact expected SHA256. Descriptor/path identities are compared
around the bounded no-follow read. Windows cross-API identity excludes ctime
because its path/fstat meanings differ, while full same-API checks are retained.

Launch the saved `backend/app/services/native_experiment_cli.py` using the
approved environment and trusted `NEXAWEAVE_APPSTORE_DSN`. It bootstraps only the
fixed sibling modules and repository knowledge storage package; it avoids
Flask/engine/provider initialization. The store uses actual psycopg connections
and existing database conventions; no migration is implicit. Supply one bounded
JSON request on stdin, then EOF. The CLI emits exactly one stdout JSON line
(`ok/result` or fixed `ok/error`) and exit status 0 or 2. It has no output path,
listener or deployment mechanism. Raw exceptions, SQL, DSN and paths are never
included in errors. Its read-only PG calls do not prove OS-wide isolation.

## Open gates

Possible initial log duplicates, post-log interviews, unavailable exact
event-to-row links and unavailable historical state remain recording caveats.
Causal attribution and provider quality are false; actual provider spend is
unknown (`null`) and shared budget enforcement/ensemble launch are unsupported.
Live checkpoints, branch execution, interventions, bridges, sensitivity quality,
UI integration and whole U10/all44 acceptance remain open. Offline fixture
runtime pins and distinct seeds do not establish real provider quality.

All qualification is Main-owned. Authored tests include actual offline native
owned close/capture, genuine failed child, independent log/SQLite expectations,
real PostgreSQL ownership and a fresh saved CLI with import/socket/resource
observations. Worker source is unverified until Main runs and reviews it.
Cancellation regressions include synthetic declared/running intent with no
recording admission, and a real PostgreSQL register/request_cancel setup for a
fresh declared run in the existing owned project. Comparison only reads that
run, preserves its saved state and supplies no receipt or successful metric.

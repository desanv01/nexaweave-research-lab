# Protected owned experiment observations

U10b adds two authenticated `research_local` routes over the accepted read-only
native comparator. It does not start, register, cancel, reconcile, resume, or
capture runs. The graphiti read-only and legacy applications omit these routes.
Health omits experiment capabilities because registration alone does not prove
that the optional operator binding is usable.

`GET /api/experiments/catalog` accepts no query or body. `POST
/api/experiments/compare` accepts exactly `{"version":1,"title":"…",
"member_ids":["…"]}`, at most 8192 UTF-8 bytes, with an explicit JSON MIME type
and matching positive Content-Length. Titles are literal labels, at most 160
UTF-8 bytes; selectors contain 1–16 unique canonical member identifiers.
Duplicate JSON keys, nonfinite numbers, extra keys, encodings, query selectors,
and invalid methods fail before facade creation. Inherited bearer and browser
origin checks precede route admission, including actual preflight rules.
Experiment responses, including denials, carry `Cache-Control: no-store` and
`X-Content-Type-Options: nosniff`. Admission errors use `invalid_request`/400;
optional-configuration, authority, recording, timeout, and reply-integrity errors
use the fixed `experiment_unavailable`/503 code. Private causes are discarded.

The operator supplies `KNOWLEDGE_EXPERIMENT_MANIFEST` and
`KNOWLEDGE_EXPERIMENT_MANIFEST_SHA256`. Literal settings are captured once at
application creation without opening the manifest. Missing, partial, or invalid
optional bindings disable experiments alone. A valid binding creates no child.
An admitted operation lazily uses the already installed knowledge interpreter
and fixed sibling `native_experiment_http_child.py`. The facade freezes a tuple
of principal, configured project UUID, manifest path/hash and PostgreSQL
components. Token, Neo4j, provider/model/proxy variables and credential fallbacks
are excluded. The parent DTO/facade/transport are stdlib-only.

The narrow `KnowledgeProcessClient` subclass retains private-CWD, private pipes,
owned-tree cleanup, one-shot framing, no retry, and a finite 90-second deadline.
The facade admits one operation at a time. Shared transport code is unchanged.
The child loads fixed sibling source only, never app/Flask/native engines or
providers, and uses installed execution/storage packages. It admits bounded,
duplicate-free framed input before manifest or PostgreSQL access. Every call
re-admits the regular, no-link/reparse, identity-stable, 64-KiB hash-pinned
manifest using accepted `read_manifest`/`cohort_from_manifest`. Every member
must belong to the configured principal and project, and the cohort must have
one exact project revision. PostgreSQL connections use explicit components,
finite connection/statement/lock limits, and read-only transactions.

Catalog preflights **all** cohort records through accepted `_authorized` before
publishing any observations and opens no recording. Comparisons first perform
that complete catalog preflight, then reuse `NativeExperimentComparator` with
its unchanged 60-second cooperative bound. Selected run authority precedes
recording access; successful recordings retain accepted pin/anchor checks and
after-read record consistency checks. Catalog/comparison state disagreement
during a concurrent transition fails the entire response. This does not make
the cohort an atomic snapshot.

Public catalogs contain version, project UUID/revision, cohort manifest digest,
ordered members and a projection digest. Members expose only member/case labels,
run UUID, observed state, overlapping cancellation intent, seed, maximum rounds,
and platforms. Comparison members additionally retain disposition, verified
artifact/runtime/request/record digests, recording metadata and anchors, and
available metrics. Neither route publishes private principal, manifest/bundle
paths, DSN, source/profile/event contents or arbitrary extras. Recording anchors
are retained provenance; no graph association is inferred from NativeRunRequest.

All public seeds, including both sides of the comparability matrix, are exact
canonical signed decimal strings spanning the native signed64 domain. Other
integers remain bounded and exactly representable. Missing metrics remain null;
present empty native tables contribute zero. Descriptive distributions exclude
unavailable values. Cancellation intent overlaps the five disposition counts.

`native_result_digest` retains the accepted comparator digest after validating
its exact canonical native bytes. `public_projection_digest` is separately
computed after conversion to seed strings. Parent validation reconstructs the
native representation and checks both digests, configured project, title,
request UUID, pinned manifest identity, ordered selection, catalog/member
identities, strict keysets, bounds and flags. It recomputes disposition counts,
cancellation overlap, distinct seeds, every distribution and matrix pair from
validated member observations. Rehashing a corrupt aggregate does not bypass
validation. The private reply includes a catalog beside comparison for exact
cohort joining; HTTP returns only the requested public projection.

Retained limitations: non-atomic cohort observations; descriptive completed
available observations only; labels do not prove controlled intervention;
digests are not signatures or semantic-equivalence proof; possible initial log
duplicates and post-log interview traces; no exact event-row links, historical
or causal truth, provider-quality assessment, ensemble launch or shared-budget
enforcement. Actual provider spend is unknown/null. No whole U10/all44, public
deployment or paid-provider acceptance follows from this bounded API.

The new contract/API/process tests are source-authored mock evidence only until
Main runs them. Native integration sources reuse accepted offline actual-run
and PostgreSQL fixtures with the actual fixed child and a Flask test client.
They do not qualify a separate lean socket HTTP runtime; Main supplies that
proof separately. `NEXAWEAVE_EXPERIMENT_TEST_PYTHON` must explicitly name an
absolute installed knowledge interpreter executable. Missing or invalid values
fail the fixture clearly, with no skip, test-runner interpreter fallback,
source-package injection, or installation. The child remains isolated with
`-I`; parent `PYTHONPATH` does not supply installed child packages. Main owns
runtime and CI wiring. These sources compare actual retained recording metrics,
unchanged source/record observations, mixed states, signed64 extremes and
negative manifest/run/bundle/PG bindings. The cancelled control-plane fixture
is explicitly synthetic, not proof of actual native cancellation behavior.
Main owns runtime setup, verification, dependency/CI changes and acceptance.

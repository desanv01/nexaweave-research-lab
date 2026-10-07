# Authenticated loopback evidence API

U11a exposes accepted model-free research and dossier assembly through the
existing `graphiti_readonly` Flask host. It is an API foundation, not full U11
or frontend acceptance. Legacy application mode keeps its existing behavior.

`POST /api/graph/research/<graph_id>` accepts the U06 research request: optional
integer `schema_version: 1`, one to five unique `display_graph_ids`, nonblank
`text` (at most 2,000 codepoints), optional integer `top_k` (1–100, default 10),
and optional aware ISO timestamps `valid_at` and `recorded_before`.

`POST /api/graph/dossier/<graph_id>` accepts the U09 dossier request: optional
integer `schema_version: 1`, nonblank `title` (at most 256 codepoints), the same
selection/cutoffs, and one to six ordered `sections`, each with nonblank
`heading` (256), `query` (2,000), and optional integer `top_k` (1–100).

The route must equal the configured trusted anchor display ID, and that exact
anchor must occur in the request selection. Additional display IDs are resolved
and authorized by the actual child research service using persisted ownership,
project, graph, layer, run and branch bindings. IDs alone confer no authority.
No principal, scope, path, provider, model, SQL, metrics or claims selector is
accepted. Query-string selectors are rejected as well.

The existing bearer token, exact browser Origin allowlist, method boundary and
route-specific preflight apply before request parsing. Preflight allows POST
and the existing Authorization/Content-Type headers. Requests require plain
`application/json`, a positive known Content-Length, no transfer/content
encoding, and at most 16 KiB research / 32 KiB dossier bytes. Duplicate keys,
nonfinite numbers, excessive nesting, extra fields, bool-as-int values, invalid
selection/text and naive cutoffs fail before creating a child. Public errors
contain only `success: false` and an error `code`.

Successful responses are compact JSON `{"success":true,"data":...}` containing
the accepted service DTO without clipped records. Research payloads are limited
to 2 MiB; dossiers to 4 MiB; each envelope has an additional explicit 1,024-byte
allowance. Invalid requests return 400, authentication 401, denied Origin 403,
foreign route anchor 404, unsupported method 405, busy 409, excessive result
413, deadline/transport/evidence availability failure 503, malformed correlated
reply 502, and unexpected application failure a fixed 500. Persisted selection
denials deliberately use the service's opaque availability failure, avoiding
ownership/existence disclosure. Raw exceptions and connection secrets are not
returned or logged by the new boundary.

An app creates its evidence facade lazily after valid admission. A single shared
nonblocking semaphore admits at most two owned child operations across both
routes. It stays held through the synchronous client's cleanup. There is no
wait queue or new executor; graph/population requests do not construct evidence
clients. Injected evidence facades are a pure-test seam only.

The stdlib-only backend client subclasses the accepted one-shot transport.
Protected request, reply and response-limit hooks preserve all ordinary graph
defaults, including method/error allowlists, graph caps, binary framing,
duplicate/nonfinite/depth checks, request UUID correlation, EOF, child exit,
deadline and owned cleanup. The new profile has only research/dossier methods
and its own fixed error allowlist. Each root envelope has exactly version 1,
canonical request UUID, method, trusted anchor scope and payload.

The executable comes exclusively from trusted `ReadHostSettings`. The bootstrap
is the fixed `evidence_bootstrap.py` sibling of installed `read_bootstrap.py`
under `site-packages/nexaweave_knowledge`; no request path is used. The bootstrap
also rejects package/read-runtime/dispatcher modules resolved outside that
installed directory. Main must install a non-editable package before testing
or running this API. `-I -u`, binary private pipes, a private temporary directory,
scrubbed startup environment and the existing owned timeout/cleanup remain in
use. Only the accepted principal, anchor binding and PostgreSQL/Neo4j keys cross
the profile environment boundary; no provider credentials or proxy hooks do.

The isolated dispatcher independently validates the complete root/request,
principal, scope and anchor. It invokes the actual `EvidenceResearchService`
and `EvidenceDossierService` with trusted connection/driver factories. It
validates result DTOs and request/response selection provenance, then bounds
encoded result and envelope bytes before frame allocation/write. It adds no
mutation operation, Graphiti constructor, model call, semantic judge or fallback.
Ordinary graph dispatch and stdio are unchanged.

Historical reads retain `retained_edges_not_bitemporal_reconstruction`; ranking
remains lexical. Dossiers keep `model_generated: false`, `semantic_judge_used:
false`, `claim_support_status: not_reviewed`, ordered query traces, citation
union coverage and non-atomic-query consistency. Citation integrity is not a
semantic support judgment, and simulation claims are not real-world predictions.

Main qualification source includes pure route/profile/dispatcher tests and a
real loopback HTTP backend child using
`NEXAWEAVE_WORKBENCH_BACKEND_PYTHON`, `KNOWLEDGE_PYTHON` and
`KNOWLEDGE_BOOTSTRAP_SCRIPT`. The latter uses retained actual PostgreSQL/Neo4j
fixtures and the default real facade/private child. Passive backend profiling
observes fresh installed launches, environment keys and owned cleanup. The
backend socket guard is process-local. Separate fresh `-I` installed-bootstrap
probes reuse the accepted passive socket/model/driver/PG-close instrumentation
for both operations; the repository path is admitted only to import the test
guard and then removed before installed application loading. These probes are
different child processes from the HTTP-owned launches, and neither observation
establishes OS-wide provider/network isolation.
Actual service/store queries, exact citations and retained data re-admission
are verified by test assertions when Main executes them. Existing graph
transport and cold-start regressions remain required gates.

No worker execution or verification is claimed. No frontend, localization,
accessibility, paid/provider-quality, semantic or public deployment acceptance
is implied. Vue/Flask/OASIS/CAMEL, Graphiti/self-hosted Neo4j Community, future
configurable providers and inherited notices remain the project constraints.

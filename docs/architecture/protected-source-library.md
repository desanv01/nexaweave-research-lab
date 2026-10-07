# Protected retained-source library (U04d candidate)

Status: implementation candidate, UNVERIFIED. Main owns review, installed-process
and actual HTTP/PostgreSQL qualification, inherited gates and acceptance.

`research_local` is an explicit application mode on the existing lazy factory.
It adds source library and retention capabilities to the protected graph read
host. `legacy` and `graphiti_readonly` do not register source routes or advertise
source retention. The existing bearer token, configured principal, exact origin
allowlist, display graph and canonical scope remain the authority inputs. An
injected source facade does not bypass settings parsing or the HTTP boundary.

For normal local startup, configure the existing trusted read-host settings
(installed interpreter/bootstrap, token, principal, display graph, scope and
connection settings), set `NEXAWEAVE_APP_MODE=research_local` and use the backend's
existing `python run.py` entrypoint. Both protected modes select
`Config.validate_readonly()` and default to `127.0.0.1:5001`, threaded with debug
disabled by validation. An explicitly configured host must remain loopback;
debug-enabled configuration is rejected before Flask starts. Legacy startup
keeps its existing validator and `0.0.0.0` default. Settings validation still
checks the existing graph-read configuration; source children receive only
their PG/principal/display/scope subset. No server is started by worker actions.

The source endpoints are:

- `GET /api/source/library/<graph_id>`: latest twenty metadata rows, with
  `has_more` from a bounded twenty-one-row read and `window_limit: 20`.
  This is a window, not a complete pagination API.
- `GET /api/source/item/<graph_id>/<source_revision>`: exact retained text,
  source hash, byte/codepoint lengths, time and ordered passage declarations.
- `POST /api/source/retain/<graph_id>`: exact JSON keys `schema_version`,
  `source_revision`, `source_name`, `format`, `content`, `input_sha256`.

All source responses, including authentication, preflight, method and route
errors under `/api/source/`, carry `Cache-Control: no-store` and
`X-Content-Type-Options: nosniff`. Fixed sanitized error codes carry no database,
filesystem, credential or parser diagnostic details. Query arguments, GET
payloads, compressed or chunked requests, missing POST length, duplicate JSON
keys, excessive nesting and unsupported media are rejected. POST requires
`application/json`; the raw body and each private request/result have a 4 MiB
cap, with 1024 bytes reserved for the protocol/HTTP envelope. Serialization can
make an otherwise size-valid text request exceed the raw cap; admission fails
rather than clipping it.

Text content is exact UTF-8, nonblank and at most 1 MiB. It rejects invalid
Unicode, NUL, DEL and C0 controls except TAB/LF/CR. Source names are nonblank,
at most 256 codepoints and reject control characters. Revision UUIDs use their
canonical lowercase spelling. The supplied lowercase SHA-256 pins the UTF-8
text bytes, or decoded DOCX bytes; it expresses caller-chosen integrity and
does not establish publisher trust. DOCX uses strict canonical base64, bounded
before decoding, and at most 2 MiB decoded input.

These nonblank/control restrictions apply to new retention requests. GET and
list preserve the accepted SourceStore eligibility for existing records:
nonempty names/text, valid UTF-8, no NUL, names up to 256 codepoints and text up
to 1 MiB. Whitespace-only text and names with other control characters remain
readable as escaped JSON data. GET preserves stored passage ordinal order,
including reversed or overlapping declarations; each passage independently
requires exact bounds, UTF-8 excerpt limit, matching hash and a unique evidence
ID. It does not impose monotonic or non-overlap rules on independently declared
legacy evidence. New retention still requires the full exact ordered generated
declarations and typed identities, with no invented page.

Before DOCX parsing, a fresh private `context` request resolves the persisted
principal/display binding and checks the exact configured scope, source layer,
absence of run/branch, non-tombstoned scope, and persisted project owner and
workspace. The parent then reuses the existing strict stdlib OOXML main-body
extractor. It does not fetch relationships, open external files, evaluate field
instructions or perform OCR. Main-body paragraphs and table cells are covered;
headers, footers, footnotes and endnotes are excluded. Empty blocks remain
visible as transient extraction metadata but produce no passage. A second fresh
child resolves authority again during retention, including immediately before
the source transaction.

Text passage chunks cover the complete exact text contiguously, split only
between Unicode codepoints, with at most 32768 UTF-8 bytes per excerpt and one
hundred passages. Their UUID5 name is
`retained-text-v1:<ordinal>:<start>:<end>` within the source revision UUID.
DOCX passage IDs use the reviewed CLI's `docx-main-body-v1:` prefix and sorted
compact canonical block JSON, including ordinal and empty status. Offsets always
count Unicode codepoints. No page is invented. Oversized blocks or more than one
hundred nonempty blocks are denied before insertion.

The child uses the accepted `ScopeBindingStore`, `ProjectStore` and `SourceStore`.
No store or migration source is changed. Retention is append-only and does not
advance project revision, snapshot, history or evidence; it does not perform
graph ingestion or operation admission. The store's own source/passage
transaction provides atomicity and collision/idempotence checks. Binding,
project and source operations are separate transactions: this path makes no
atomic revocation guarantee across them or any graph/database boundary.

POST returns source metadata and ordered passage IDs, offsets and excerpt hashes
without repeating full text or excerpts. It includes transient extraction and
input digest flags; neither the original digest nor DOCX blocks are claimed to
have been persistently recorded. GET returns retained extracted text, not an
original DOCX reconstruction. `binary_retained` and `graph_ingestion_executed`
are always false. Original binary proof, graph readiness, layout and semantic
quality are not established by this operation.

The parent client/facade use stdlib dependencies and the accepted
`KnowledgeProcessClient` lifecycle through a source-specific profile. An
app-shared semaphore permits at most two source requests; one sixty-second
budget covers context, parsing and retention. Each child receives exactly one
bounded length-prefixed frame with exact version/request ID/method/scope/payload.
Methods are `context`, `list`, `get`, `retain`; replies are correlated and
strictly checked, including injected clients. HTTP responses are rechecked even
when a facade is explicitly injected. Exact get-text/hash/passage joins occur in
the real facade. For an injected facade's DOCX retention reply, the HTTP boundary
can check structural block/ID/hash shapes but lacks original extracted text;
this injection seam is for explicit Main tests, not a replacement live authority.

The fixed installed `source_bootstrap.py` sibling launches through `-I` with no
source-path or environment fallback. It validates installed knowledge and
storage module paths before processing. Only trusted PostgreSQL connection,
principal, display and scope settings cross the child boundary; tokens, Neo4j
and provider/API settings do not. PostgreSQL uses explicit connection keywords
and a three-second connection timeout, rejecting ambient service, passfile,
options and hostaddr configuration. The knowledge package's existing provider
export is lazy so binding/contracts imports do not import the provider module;
its public `GraphitiKnowledgeProvider` name and `__all__` remain available.

The source child does not import the read runtime/provider or construct Neo4j,
Graphiti or model clients. This is a process boundary, not an OS sandbox or a
native-libpq egress policy. Transport loss after a child starts can mean retention
completed; it yields a fixed `outcome_unknown` response and does not prove
rollback. There is no automatic retry or regenerated source revision. A caller
can reconcile the same revision through GET and use the same exact idempotent
request deliberately. Busy admission is safe before spawn. No paid calls or
public deployment are part of this packet.

Authored regression sources cover pure HTTP/validation denials with injected
clients, actual disposable PostgreSQL ownership/collision/idempotence/closure
and snapshot-preservation cases, checksum tampering, DOCX-compatible block IDs,
and cold installed imports. They have not been executed by the worker. Main
supplies and executes actual Flask-to-fresh-installed-child-to-owned-PG cases,
socket observations, concurrency/deadline cleanup, packaging and inherited
compatibility qualification.

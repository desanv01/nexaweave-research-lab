# U11c retained-source workbench — implementation candidate

This packet extends the lazy `/research` route with Sources. U04d is a backend
implementation dependency under Main qualification, not an accepted dependency
by virtue of this frontend. This document records source design, not execution,
browser accessibility qualification, full-phase acceptance, or any all44 gate.

## Connection and transport

`createWorkbenchClient` remains the single owner of the memory-only bearer token,
literal loopback origin and configured display graph. SourceLibrary receives
three fixed methods and connection/busy/reset signals, never token or origin
authority. The existing graph connect request is unchanged. Sources make no
requests on mount, connection, file selection, preparation, receipt or inspection
completion. The user explicitly loads the latest20 window, submits a prepared
attempt, or inspects a revision. No optional health request was introduced.

The shared transport adds only authenticated GET library, GET item/canonical
revision and POST retain under `/api/source` for the configured graph. It uses
the existing abort controller, generation fence and finite deadline. Requests
omit credentials, disallow redirects, disable caching and omit referrer. Source
response streams are capped at 4 MiB + 1024 wrapper bytes; graph/research/dossier
caps remain unchanged. Duplicate JSON keys (including escaped equivalents),
excessive nesting, unknown DTO fields, malformed types and unexpected envelopes
are rejected. A non-JSON HTTP401 clears the same shared connection. Source404
does not revoke graph authorization; non-JSON404 is a fixed source-unavailable
error and never displays HTML or server details.

The source-only fixed error allowlist also recognizes `source_denied`; the
component maps it to EN/ZH/MS denial copy. Arbitrary error codes and additional
server error fields remain invalid. Source denial does not itself revoke the
shared graph connection; HTTP401 still clears the entire connection.

The source DTO validator is separate from transport. Source input is snapshotted
before asynchronous verification so later caller changes cannot change the sent
identity or receipt correlation. GET revision/project hints are bounded UUIDs;
project hints check consistency only and never establish server authorization.

## Exact source identity and preparation

Paste input or one File with `.txt`, `.md`, or `.docx` extension is explicit.
Only File.name is displayed; separators/fake paths are rejected. Text file limits
are checked before reading; DOCX is limited to 2 MiB and text to 1 MiB. Fatal UTF8
decoding with `ignoreBOM:true` preserves the BOM as U+FEFF. Paste input, names
and content are not trimmed or normalized. New names reject every Unicode Cc
control, including C1 U+0080–U+009F. Blank input, unsupported controls,
NUL, unpaired surrogates and over-limit names/content are rejected. UTF8 width is
bounded before encoding paste input. DOCX uses canonical base64 and a digest of
the original input bytes; the browser does not perform DOCX extraction.

WebCrypto prepares SHA256 and one random UUID for each explicitly prepared
attempt. Editing draft fields invalidates its preparation. A separate button
submits the reviewed revision/name/format/input digest. The shared client
rechecks the input digest before POST. The component consumes the preparation
once and clears draft text/File references at submission. There is no automatic
POST retry, repeat submission button for an old preparation, or persisted draft.

## Window, inspection and receipts

The library is an explicit latest20 window, not complete pagination. It presents
window limit, has_more, local load time and a persistent refresh/staleness note.
Empty is shown only after an actual successful list response. Metadata has
canonical revision/project/hash identity, aware recorded time, UTF8 byte length
and Unicode codepoint length. Lists reject duplicate revisions and mixed project
identity. Retained metadata names must contain at least one codepoint; whitespace
and C1 controls remain eligible in legacy names (NUL/invalid Unicode are denied).
Valid legacy names/text are permitted on reads; new-upload control or nonblank
restrictions are not imposed on retained legacy reads.

Before displaying GET content the shared client independently verifies full
UTF8 SHA256, byte length, codepoint length and every declared excerpt digest.
Each verified excerpt is at most32768 UTF8 bytes; declared pages are null or
positive integers no larger than2147483647.
Passages preserve stored declaration order, including overlapping/reversed spans.
Excerpts use Array.from(text) codepoint slicing. Zero passages is a valid read.
The component uses Vue text interpolation/pre elements, with no HTML, Markdown,
linkification, image or download rendering. Native buttons select passages;
inspection and excerpt panels receive focus, Escape closes them and returns
focus to the corresponding originating control.

A successful POST presents the actual metadata/passage/extraction receipt.
Text receipts also verify returned text/excerpt hashes against submitted content.
Generated text receipt declarations must be nonempty, contiguous in declaration
order from0 through the full retained codepoint length, each no larger than32768
UTF8 bytes, with null pages. These whole-text/null-page requirements apply only
to text POST receipts, never to legacy GET passage order/coverage.
DOCX receipts validate transient main-body block order, bounds, empty markers,
typed passage identity and correlation; extracted text hashes require a separate
explicit GET. Transient coverage includes main-body paragraphs/table cells and
excludes headers/footers/footnotes/endnotes, deleted revision text and field
instructions. Input digest/blocks are not persisted; original-document and binary
binding are not verified; no OCR is performed; page layout/semantic quality are
unknown. All receipt/inspection views state binary_retained:false and
graph_ingestion_executed:false. Retention never triggers graph ingestion,
research, a model, or provider operation and never claims searchable evidence.

## Uncertainty and clearing

At submission the component saves only attempted revision/name/format/input
digest in current route memory. A completed receipt resolves uncertainty. Any
failed submitted request conservatively leaves the attempt uncertain, including
deadline, transport failure, outcome_unknown, cancellation and unreadable receipt:
retention may already have happened. The UI offers explicit GET of that revision;
not-found is not a rollback guarantee. Successful GET must match revision,
available project/retained hash, source name and the submitted text digest for a
text attempt. Until reconciled, preparing another upload is disabled. Discarding
draft content does not erase an unresolved reconciliation identity. Disconnect
clears both identity and protected state.

Parent operations coordinate source/research busy/cancel ownership. Source
operations preserve prior evidence results; ordinary research behavior remains.
Disconnect, authorization failure, reconnection reset and unmount clear source
drafts, File references, preparations, selection, text, receipt, window, attempted
identity and pending state. Component generation fences cover asynchronous local
preparation and rendering; the shared client fences network and hash work. No
late completion may repopulate disconnected source state.

## Local design and remaining qualification

Human-selected installed UI/UX Pro Max guidance was read, including static
quick-reference/pro-rules; its search runtime was not executed under the worker
source-only boundary. This extends the existing workbench design: semantic color
tokens/system fonts, native labeled controls, visible focus, minimum44px controls,
wrapping hashes/text, one-column mobile layout/two-column library above768px and
reduced-motion rules. EN/ZH/MS source copy declares the same keys. No hosted12ui
call, design charge, new dependency, CDN, telemetry or storage was introduced.

Regression sources use mock transport and actual compiled Vue templates. They
are not live backend, browser, focus/contrast measurement or accessibility proof.
Main must execute Node/inherited tests and build, review exact sources/revisions,
qualify U04d and the actual browser/API/private installed PostgreSQL journey,
including320–1440px layouts, focus/keyboard/touch behavior, limits, uncertainty and
401/disconnect clearing. Git, CI, ledger/issue upkeep and acceptance remain Main’s.

# Retained textual PDF source — U13b source candidate

Status: source implementation complete for the bounded thirteen-path packet;
all authored work remains UNVERIFIED. Main alone owns lock refresh, profile
installation, checks, actual child/PG/HTTP execution, CI, Git and acceptance.
The prior partial handoff and latest pause are historical checkpoints. Main
assignment correction 1 explicitly added the protected source facade path.

## Protected flow

The research_local JSON retention endpoint accepts explicit format `pdf` with
the same six upload keys as text/DOCX: schema_version, source_revision,
source_name, format, content and input_sha256. Native-free Flask admission and
fixed child admission independently require canonical ASCII base64, at most
2 MiB decoded bytes, exact lowercase SHA256, a PDF marker in the first 1024
bytes and an ending EOF marker. Paths, passwords, plugins, process settings,
credentials and extra fields are rejected. The 4 MiB source envelope remains.

The existing facade semaphore and shared 60-second deadline cover the existing
context authorization call followed by exactly one `retain_pdf` child request.
There is no application retry. The fixed installed source_bootstrap child
revalidates admission, resolves persisted project/source authority, lazily
imports the PDF module/native library, parses, reserves receipt space, then
re-resolves authority immediately before SourceStore.ingest_text. The existing
owned process lifetime/tree cleanup mechanism is unchanged. Native parsing
never runs in Flask. No graph/provider/model call is made by retention.

The optional source-pdf extra pins PyMuPDF==1.26.7. The PDF module imports
PyMuPDF only inside extract_pdf. Text, DOCX, context/list/get and cold source
startup do not require a PDF runtime import. A missing optional profile returns
fixed source_unavailable, never empty text or an unsupported success fallback.

## Exact extracted text and bounds

The extractor rejects encryption/password-required documents, native-repaired
documents, empty/no extractable nonblank text, malformed/truncated input,
unsupported control text and quota overflow. Native error text, paths and
passwords are not emitted in response DTOs. A document is closed on success or
failure; failed native cleanup prevents a successful extraction result.

At most 100 pages are admitted before traversal. Every page.get_text() string
is preserved exactly. Every page, including empty and whitespace-only pages,
is joined in page order with two newline separators. Offsets count Unicode
codepoints in that exact retained string; original page numbers are 1-based.
Nonblank pages yield one declared passage each. Empty/whitespace-only pages
have summary entries and retain their position but yield no empty passage.

Each page is bounded by the existing 32768-byte UTF-8 passage quota; the joined
text is bounded by 1 MiB UTF-8 including separators. Input/output quotas and
owned child lifetime do not claim a hard native allocation or memory sandbox.
UUID5 evidence identities use the canonical source revision and the exact
validated page summary (page, start, end, empty, excerpt_sha256) prefixed by
pdf-page-text-v1:. The immutable SourceStore transaction preserves existing
revision collision/ownership rules and derives persisted excerpt hashes.

## Receipt and retained evidence

PDF retention returns the unchanged common source metadata/passages schema
plus extraction. The extraction has exactly these keys:

- format, input_hash_verified, input_sha256;
- input_digest_persisted, blocks_persisted, original_document_verified,
  binary_persistently_bound, ocr_performed;
- page_layout, semantic_quality, coverage;
- page_text, pages, page_count, empty_page_count, declared_passage_count.

format is pdf, coverage is [page_text], and page_text is an ordered list of
exact extracted strings. Each pages entry has exactly page/start/end/empty/
excerpt_sha256. All counts/types/offsets/page ordering/hashes/UUID5 declarations
and total UTF-8/codepoint lengths are revalidated by the stdlib backend against
reconstructed retained text, both at the child reply and public HTTP boundary.
Receipt room is reserved before mutation. Corrupt or late mutation replies
report outcome_unknown without retry.

input_hash_verified is true only after actual decoded-byte admission.
input_digest_persisted, blocks_persisted, original_document_verified,
binary_persistently_bound, ocr_performed, binary_retained and
graph_ingestion_executed remain false. page_layout and semantic_quality remain
unknown. The PDF binary/digest/page summary is transient and is not persisted.
The existing get/list DTO key sets remain unchanged after retention/restart;
source text and declared page/evidence references are persisted in the existing
schema. Later reads verify retained text/excerpt hashes, not the original PDF.

Existing text/DOCX receipt key sets, parsing paths, authentication, origin,
preflight, no-store/nosniff and default/readonly mode behavior are preserved.
No frontend PDF preparation or file UI is part of this packet. Extracted text
order is not original visual layout. No OCR, rendering, table reconstruction,
semantic quality, binary re-verification, complete long-document support,
whole-phase/C01/C40/all44 or public/paid acceptance is claimed.

## Source-authored qualification

Dedicated PDF tests require the optional profile without importorskip. Sources
cover actual Unicode/empty/image-only/encrypted/malformed/page and text quotas,
exact offsets/hashes/IDs, incremental limits, native cleanup and missing profile.
Strict wire/receipt and cold-start tests cover rejection before parser/authority,
SDK/native-free cold imports, authority recheck and missing profile denial.
Backend injected-client tests are contract tests, not actual parser/runtime
qualification. Existing PG/HTTP files add actual owned-store and fixed installed
child cases for immutable PDF retention, owner denial, collision, restart and
exact page/evidence/excerpt resolution; existing observer/cleanup assertions
remain, with backend/native and cold-context guards strengthened.

None were executed by the worker. Main must require source-pdf in its dedicated
runner/CI, qualify exact installed module/profile provenance and all actual
fixture cases, preserve inherited text/DOCX/cold contracts and integrate accepted
PR78 dependencies separately. No lock/runner/CI/schema/core process changes were
made in this packet.

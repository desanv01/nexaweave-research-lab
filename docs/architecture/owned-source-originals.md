# Owned original PDF retention (U07i)

This additive research-local V2 operation stores exact admitted PDF bytes beside
the existing retained Unicode text and passage declarations. V1 source routes,
receipts, text/DOCX/PDF extraction and selected-source bundles keep their
existing shapes. A V1 revision has no inferred original. It cannot be upgraded
by replay; a caller must create a new canonical source revision.

## Boundary

`POST /api/source/retain-original/<graph_id>` admits exactly the V2 PDF payload:
`schema_version=2`, `source_revision`, `source_name`, `format=pdf`, canonical
base64 `content`, and SHA-256 of the decoded input. Source-local owner/scope
authorization precedes PDF import/extraction and repeats before the atomic
mutation and publication. The existing 2 MiB binary, 1 MiB retained text,
100-page, 32 KiB page passage, 100-evidence and 4 MiB JSON limits remain.

`SourceStore.ingest_pdf` inserts source revision, passages and binary row in
one PostgreSQL transaction. A replay locks and validates the exact prior
source, passage sequence, bytes and digest. A V1 text-only source with the same
revision conflicts; no backfill is attempted. The additive migration
`0004_source_binaries.sql` is checksummed by the existing `mf_app` catalog
migrator. Migration failure rolls back the whole transaction. Old migration
bytes are unchanged. Restore from a verified pre-migration snapshot to roll
back a database that contains binary rows; do not drop retained originals.

`GET /api/source/original-metadata/<graph_id>/<source_revision>` returns a
versioned source/binary metadata result. `GET /api/source/original/<graph_id>/<source_revision>`
adds canonical base64 of exact bytes. Each owner-bound store read recomputes
length and SHA-256, and the child, backend and browser recheck binding and
byte integrity. Both are no-store, nosniff, fixed-route JSON responses with
no caller path, principal or project grant. Missing originals on old V1
revisions return `not_found`; corruption returns `source_unavailable`.
The legacy V1 list/get DTO retains its fixed `binary_retained=false` field even
for a V2 source revision; it describes that V1 response, not the separate
original-byte operation. The explicit V2 metadata lookup is authoritative for
original availability after reconnect. The Sources text inspector explains this
in EN/ZH/MS and points to explicit V2 lookup rather than concluding from a V1
GET that no original exists for that revision.

The V2 retention receipt is the V1 PDF page/passage layout plus an exact
`binary` metadata object, `schema_version=2` and `binary_retained=true`.
`input_digest_persisted`, `original_document_verified` and
`binary_persistently_bound` become true only after the transaction's exact
readback. `graph_ingestion_executed`, `ocr_performed` and `blocks_persisted`
stay false; `page_layout` and `semantic_quality` stay unknown. PDF page text
extraction is not OCR, visual layout reconstruction, or semantic review.

The Sources component offers a distinct original-PDF retention action and an
explicit revision lookup after reconnect. It validates bytes/size/digest before
creating an `application/pdf` Blob, never stores tokens/originals locally, and
revokes object URLs on download and reset. Parent workbench transport/wiring
belongs to Main. This feature does not modify report, follow-up, budget,
Temporal, or selected-source bundle V1 contracts.

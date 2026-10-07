# Bounded DOCX main-body source ingestion

U13a candidate implementation is **UNVERIFIED**. Main owns source review, all
execution, fixture qualification, CI, Git integration and acceptance. This slice
does not establish completion of C40, U13, OCR, operational recovery or all 44
requirements.

`FileParser` admits `.docx` alongside the existing PDF/MD/MARKDOWN/TXT formats.
The existing parser worker and its five-field limit wire contract are unchanged.
The DOCX adapter loads only its fixed `docx_extraction.py` sibling with an import
spec, so the saved `parser_worker.py` can still load the parser directly under
`python -I` without importing Flask, app configuration or an SDK. Upload admission,
Home's existing accept/filter lists and the two format labels include DOCX.
No screen, layout, style or provider generation is introduced.

## Extraction profile

`extract_docx` accepts admitted immutable bytes and returns a frozen `Extraction`
containing a text string, a tuple of frozen blocks and fixed coverage/exclusion
tuples. It never extracts files to disk, opens a URL, fetches a relationship,
evaluates a field or renders Word pages. It uses only standard-library ZIP/XML
handling. The fixed XML parts are `[Content_Types].xml`, `_rels/.rels` and
`word/document.xml`; the normal nonmacro main content type and one internal
officeDocument relationship to `word/document.xml` are required. Both transitional
and strict WordprocessingML main namespaces are supported.

Input is at most 50 MiB or the caller's tighter byte cap. A central-directory
preflight bounds entry allocation before `ZipFile`: at most 2,000 entries and
64 MiB declared uncompressed data, with at most 8 MiB for each parsed XML part.
Every entry is read in bounded chunks through EOF for CRC verification, including
unparsed parts. Only the three fixed XML parts are retained. Names must be
canonical relative slash paths without duplicate names, empty/dot/traversal
components, backslashes, colon, percent, query/fragment characters or controls.
Directory/special entries, encryption, unsupported compression, embedded-object
parts and macro/ActiveX profiles fail. Stored and deflated compression are admitted.
Spanned/alternate central-directory profiles are unsupported. There is no executor.

XML is strictly UTF-8 (optional UTF-8 BOM); other encoding declarations, invalid
controls, DOCTYPE/entity declarations, malformed syntax and unsupported roots
fail. Incremental pull events bound depth to 32 and nodes to 100,000 per XML part.
XML's own line-ending/entity normalization applies before text extraction; there
is no additional trimming or Unicode normalization. The result is bounded by the
caller's tighter text cap and the fixed 5,000,000-codepoint ceiling, with at most
5,000 blocks. Failures are classified as `malformed_document`,
`unsupported_document`, `limit_exceeded` or `invalid_source`, without content/path
details. Limits fail instead of clipping text.

Main-body paragraphs and table rows/cells retain document order. Body paragraphs
are separated by two LF characters. A body table starts after two LF characters
when earlier blocks exist. Table cells use TAB separators, rows use LF, and
paragraphs within a cell use LF. No trailing paragraph/row separator is appended;
an empty final cell can therefore leave a trailing TAB. Explicit inline tabs and
text-wrapping breaks emit TAB and LF. Literal Unicode, emoji and XML-preserved
spaces survive. Every body paragraph and table cell has a block, including empty
ones. Block ranges exclude the separator preceding that block; a cell range
includes its internal paragraph separators. Offsets use Python Unicode codepoints,
including one codepoint for a non-BMP emoji, and directly slice the returned text.
Table/row/cell indexes are zero-based in declared order. `gridSpan` (positive,
at most 9,999) and `vMerge` (`restart` or `continue`) retain declaration metadata.
Merged cells are not expanded or copied into a rectangular grid. Empty cells have
equal start/end offsets. Table semantics and layout remain unknown.

Ordinary text, inserted/move-to text, literal hyperlink labels and retained field
results are included. Inline deleted/move-from text, field instructions and field
control nodes are excluded. Unknown content wrappers, nested tables, altChunk,
drawings/textboxes, embedded objects, ambiguous alternate content, hidden-run
flags, legacy horizontal merging, property-change histories and deleted table
row/cell or paragraph-mark profiles fail explicitly. Page/column breaks also fail
as unsupported layout profiles. This deliberately narrow profile does not silently
report partial text as a complete supported extraction.

Headers, footers, footnotes and endnotes are excluded; their ZIP bytes still
receive size/name/CRC admission but their XML is not interpreted. Main-body
reference markers requiring an unsupported inline profile can fail. Coverage
is explicitly `main_body_paragraphs` and `main_body_table_cells`, never full
document text. No page numbers, Word rendering fidelity, OCR, signatures, input
authenticity or semantic quality are inferred.

## Owned retention CLI

The saved `backend/app/services/document_source_cli.py` supports one trusted local
operation, with no listener, model call, arbitrary writer or wire-supplied authority:

```text
python -I backend/app/services/document_source_cli.py ingest-docx
  --principal <trusted-principal> --project-id <canonical-uuid>
  --source-revision <canonical-uuid> --source-name <bounded-name>
  --document-path <absolute-canonical-docx-path>
  --expected-document-sha256 <lowercase-64-hex>
```

`NEXAWEAVE_APPSTORE_DSN` supplies the local configured PostgreSQL connection. The
storage package is bootstrapped from a fixed repository-relative location and the
extractor from its fixed utils sibling; the app, engine and providers are not
loaded. The trusted launcher remains responsible for restricting local principal
and DSN configuration. The CLI is not an HTTP authorization surface. No migration
occurs implicitly.

Scalar identifiers, source name, hash and lexical path form are validated first.
An actual `ProjectStore.get(principal, project)` succeeds before document filesystem
inspection/read. Foreign/missing ownership receives the fixed whole-operation
`document_source_denied`. After this gate the CLI rejects symlink/reparse ancestors
and nonregular documents, compares path/open identity, reads admitted bytes once,
checks metadata stability and verifies SHA-256 before parsing those same bytes.
POSIX supports descriptor-based no-follow traversal. Windows uses ancestor reparse
checks plus path/open/path identity checks; these are not a guarantee against every
concurrent hostile Windows namespace mutation. This is a trusted local file
operation, not an adversarial file server. Windows lstat/fstat ctime values are not
cross-compared; changes within the same stat API retain ctime checks.

Source-compatible retention limits are at most 1 MiB UTF-8 text, 100 nonempty
blocks and 32 KiB UTF-8 for every excerpt. Nonempty means `start < end`, not trimmed
content; an entirely whitespace result is rejected. All blocks remain in output
metadata, but empty ranges have no evidence record. No block is clipped or silently
omitted. Evidence UUIDs are deterministic UUIDv5 values using the source revision
and a versioned canonical block identity/range/ordinal. The actual `SourceStore`
retains extracted text and declarations and enforces authority, idempotence and
conflict atomicity. Existing identical text/name/passages are idempotent; conflicts
fail as `source_conflict` without overwriting the retained source. Actual resolved
passages are then returned by `SourceStore.resolve_evidence`.

One bounded JSON line reports success or a fixed safe error; exit statuses are
0/2. No raw ZIP/XML, SQL, DSN, path or exception detail is emitted. The result
includes source IDs, digest/lengths, actual resolved passages, block metadata,
input hash verification and coverage. `binary_retained=false`,
`binary_persistently_bound=false` and `original_document_verified=false` are
explicit: the current source schema stores text/passages, not original DOCX bytes
or their persistent binary binding. An input hash checks this operation's bytes;
it is not rendering/authenticity/signature/semantic proof. Changing excluded parts
without changing retained text need not cause a source conflict. Both
`graph_ingestion_executed` and `ocr_performed` are false; page/layout and semantic
quality remain unknown.

## Authored qualification source

Pure source covers package/XML safety, exact limits, namespaces, Unicode/ranges,
ordering, revision handling, merge metadata, blank cells and safe adapter errors.
Parser-process additions launch the actual fixed child on DOCX bytes. Upload
additions exercise actual parsing before the explicitly offline ontology fixture.
The new PostgreSQL integration source reuses `test_source_store_postgres.factory`
and `ProjectStore` snapshots, launches fresh saved CLIs, observes actual PG closes,
guards provider/app imports and loopback sockets, checks real resolved passages,
idempotence/conflicts, foreign ownership before absent documents, source limits,
and unchanged parent bytes/project snapshots. Directory symlink creation can be
unavailable on Windows and is explicitly marked as a host privilege skip.
These tests are authored only; no execution or acceptance is claimed here.

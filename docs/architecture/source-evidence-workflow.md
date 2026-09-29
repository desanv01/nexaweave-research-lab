# Retained source text and passage evidence

This is an explicit, local metadata workflow for an existing owner-scoped project. It retains an extracted UTF-8 text file as one immutable source revision and derives exact passage excerpts from caller-declared Unicode codepoint ranges. It does not read PDFs, copy binary uploads, extract text, prove a page mapping, or establish that a statement is true.

## Copy-paste workflow

Set `MIROFISH_APPSTORE_DSN` locally to the application PostgreSQL DSN, then explicitly migrate the owned `mf_app` schema. The DSN is never a CLI argument. The project must already exist; for a new project first import a complete inherited `Project.to_dict` JSON snapshot with `import-project`.

```text
python -m mirofish_storage migrate
python -m mirofish_storage import-project --principal owner --workspace-id 11111111-1111-1111-1111-111111111111 --project-id 22222222-2222-2222-2222-222222222222 --display-id proj_1 --input project.json
```

For a text file containing `A😀猫\r\n`, the emoji and Chinese character occupy codepoint indices 1 and 2. A passage declaration file can contain:

```json
[{"evidence_id":"33333333-3333-3333-3333-333333333333","start":1,"end":3,"page":9}]
```

The `page` value is optional caller-declared metadata. It has not been checked against an original document. Import and then resolve the passage:

```text
python -m mirofish_storage import-source --principal owner --project-id 22222222-2222-2222-2222-222222222222 --source-revision 44444444-4444-4444-4444-444444444444 --name extracted-text --input extracted.txt --passages passages.json
python -m mirofish_storage resolve-evidence --principal owner --project-id 22222222-2222-2222-2222-222222222222 --evidence-id 33333333-3333-3333-3333-333333333333
```

Both commands emit JSON to stdout by default. `--output new-file.json` creates a new file exclusively and refuses an existing leaf. The CLI reads only the explicitly named bounded regular input and passages files. It does not follow any path inside their contents. `import-source` may omit `--passages` to retain source text without citations.

## Data and integrity contract

`SourceStore.ingest_text(principal, project_id, source_revision, name, text, passages=())` validates and copies input before opening a connection. The source name is 1–256 Unicode characters; text is nonempty UTF-8 up to 1 MiB, with no NUL or surrogate. BOM and newline bytes are retained without normalization. The SHA-256 is calculated from `text.encode('utf-8')`. Byte length and Python Unicode codepoint length are stored separately. Each passage has exactly `evidence_id`, `start`, `end`, and optional `page`. Half-open `[start,end)` offsets are Unicode codepoint indices, not UTF-8 bytes or UTF-16 units. The excerpt and SHA-256 of its UTF-8 bytes are derived internally; callers cannot submit them. Each excerpt is at most 32 KiB UTF-8. Up to 100 passages may be attached to a source revision.

The source revision UUID is globally bound to one project. An identical retry returns the same source and passages; any changed name, text, or passage declaration conflicts. Evidence IDs are globally unique across sources. The source and all passages insert in one short transaction. No file or provider calls occur in that transaction. `get_source`, `list_sources` (maximum 100, ordered by server `recorded_at` then UUID), and `resolve_evidence` require the owning principal and project UUID. Reads recheck retained text, hashes, offsets, excerpts, and passage hashes; corruption produces a fixed storage error. Returned records are frozen and contain immutable strings and tuples.

Migration 0002 adds `mf_app.source_revisions` and `mf_app.passage_evidence` with primary keys, project/source composite foreign keys, check constraints, and scoped listing indexes. Explicit `migrate` checks every known SQL checksum and the current head catalog shape before applying pending DDL under the existing dedicated advisory lock. Fresh installation and v1 upgrade preserve project rows. Unknown or drifted schemas are rejected without repair.

U04a project revision evidence manifests remain declarations, not verified citations. A resolvable passage in this store has its own source revision and evidence UUID and points only to retained extracted text. The existing knowledge `SourceEnvelope` may hash just a submitted passage, so its content hash can differ from this store's full-source text hash. A future bridge must map these identities explicitly. No automatic project-revision update or provider ingestion occurs here.

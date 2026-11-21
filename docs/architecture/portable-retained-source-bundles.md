# Portable retained-source bundles — bounded U04b

This private data artifact supports a selected immutable PostgreSQL project
revision and up to 16 explicitly requested retained source revisions. The U14
committed-source ZIP is a separate artifact.

`nexaweave_storage.research_bundle.export_bundle` resolves persisted authority
through `ProjectStore.get(principal, project_id, revision)` and
`SourceStore.get_source(principal, project_id, source_revision)` for every selected
source. Revision is mandatory and positive. Sources need only belong to the
project; the current schema does not bind each retained source to a particular
project snapshot revision. The explicit selection describes that distinction.
Selection order is canonical UUID order; passage order is stored order.

## Version 1 data format

The exact envelope fields are `schema_version: 1`,
`kind: "mirofish_retained_sources"`, `payload`, and `payload_sha256`.
The exact payload fields are `scope: "selected_sources_only"`, `project`, and
`sources`. Project fields are workspace/project canonical UUIDs, display ID,
positive revision, exact snapshot, evidence metadata, and the existing canonical
project digest. Snapshot/evidence validators are reused without relaxation.

Each source includes canonical project/source UUIDs, name, exact Unicode text,
UTF-8 SHA256 and byte length, Unicode codepoint length, aware ISO recorded time,
and ordered passages. Each passage includes canonical evidence/source/project
UUIDs, start/end codepoint offsets, nullable declared page, exact excerpt and
excerpt SHA256. No principal, DSN, credentials, export timestamp, random identity
or machine field is added. Identical stored records produce identical bytes.

Canonical encoding uses UTF-8 JSON with sorted keys, compact separators,
unescaped Unicode, and no nonfinite constants. SHA256 covers canonical payload
bytes. Existing project digest covers its canonical snapshot/evidence pair.
Inspection accepts ordinary JSON whitespace/key order while rejecting duplicate
keys; an optional trusted whole-artifact digest pins the exact bytes too.

Inspection independently checks every digest, declared length, exact codepoint
slice, project/source join, UUID, timestamp, field set and type. Duplicate retained
source IDs and retained passage evidence IDs across all sources are rejected.
Project evidence remains exact inert reference metadata: it may legitimately
reference an included retained passage ID. Its own duplicate rules and canonical
digest remain those of the existing project validators. References do not imply
original binary presence. Inspection rejects invalid Unicode, JSON
constants, excessive nesting and unsupported values. Limits are 12 MiB artifact,
8 MiB aggregate retained UTF-8 text, 1 MiB per source, 16 sources, 100 passages per
source and 32768 bytes per excerpt. Project metadata retains its existing caps.
Export counts actual retained UTF-8 bytes after each selected source read and
fails immediately above 8 MiB, before reading another source or assembling the
oversized artifact. Final validation still enforces the encoded 12 MiB cap.

## Version 2 linked evidence and version 3 original PDF

Version 2 selects exactly one owned retained source and adds an `evidence_link`
with its source revision, evidence ID, text SHA-256 and excerpt SHA-256. The
selected project revision must refer to exactly one passage from that source.

Version 3 adds one `original_pdf` field alongside the version 2 payload fields.
The exporter reads the stored original and its bound source through
`SourceStore.get_original` using the same project and principal as the project
revision lookup. The object carries `source_revision`, `media_type` set to
`application/pdf`, `byte_length`, `sha256`, and `content_base64`. The latter
is a list of standard padded base64 strings. Each non-final string is exactly
1 MiB and the final string holds the remaining encoded bytes. Inspection
checks canonical encoding, revision binding, the PDF header and EOF marker,
exact decoded length and SHA-256. An original is limited to 2 MiB and the
existing 12 MiB encoded artifact cap applies. The payload digest covers both
the evidence link and original.

The installed `nexaweave_storage.research_import_cli` takes a trusted local input
file, its exact SHA-256, and an explicit principal, target project ID and
current target revision. A first v2 or v3 import requires a distinct owned target
project with an empty retained-source library and no prior import receipts.
The target project is created independently before import. The transaction
remaps retained source and passage IDs under the target project, writes the
retained text and passages, and in v3 writes the original PDF under the remapped
source revision. The receipt commits with those rows. Replay checks current
target ownership and revision, then verifies the receipt, remapped rows and,
for v3, the exact stored original before returning the same receipt. The target
owner reads the restored PDF through the ordinary `SourceStore.get_original`
entry point. The import records the origin project and evidence as provenance.

## Trusted local CLI

Run the installed module `python -m nexaweave_storage.research_bundle_cli` with one
UTF-8 JSON request on stdin, capped at 32768 bytes. Main owns execution and
installation. The following export request defaults to version 1. Pass
`"schema_version": 2` or `"schema_version": 3` and a single source revision to
opt in to the corresponding newer format.

```json
{"operation":"export","principal":"owned-local-principal","project_id":"00000000-0000-0000-0000-000000000001","revision":1,"source_revisions":["00000000-0000-0000-0000-000000000002"],"output":"C:\\trusted\\new-bundle.json"}
```

Inspect request fields are `operation: "inspect"`, `input` absolute local path,
and optional lowercase 64-hex `expected_sha256`. Inspect needs no DSN or store
connection. Export uses only trusted local `NEXAWEAVE_APPSTORE_DSN`, rejects libpq
ambient PG variables and service/passfile connection settings, and disables
default password-file discovery. It never migrates a database.

Paths must be absolute normalized local paths without UNC forms, control
characters, parent components or alternate stream syntax. Input must be a
regular file; ancestor directories, input/output files, symlinks and Windows
reparse points are checked. Input reads are capped. Output uses exclusive create,
never overwrite, after all ownership reads and artifact validation. Failure
cleanup attempts to remove only the identity created by this call, and refuses
cleanup if ancestors or the file identity changed. These checks do not provide a
hostile concurrent-filesystem sandbox; trusted local filesystem use is required.

Read stability compares descriptor metadata before/after using `fstat`, and path
metadata before/final using `lstat`, including ctime changes within each API.
The final path/descriptor join compares device/inode, size and mtime. It does
not compare ctime across those APIs, because CPython on Windows may report
different ctime semantics for unchanged path and descriptor observations.
Regular-file, symlink/reparse, ancestor identity and capped-read checks remain.

One fixed JSON reply is written: `{"ok":true,"result":...}` or
`{"ok":false,"error":...}`, exit 0 or 2. Error vocabulary is `invalid_request`,
`invalid_bundle`, `invalid_path`, `bundle_denied`, `authority_unavailable`.
Summaries include identity/revision, counts, total retained text bytes, artifact
and payload hashes, a privacy warning and binary presence and publisher
authentication flags. Version 3 import reports original binary restoration
after transaction verification. CLI replies omit source text, original bytes,
paths, display names, principal, DSN and raw exceptions.

## Privacy and remaining scope

Export includes private retained text and potentially sensitive original
snapshot metadata. A v3 artifact also carries the unredacted original PDF.
Treat the whole artifact as private. Snapshot paths, workflow IDs and graph IDs
remain inert strings. The trusted artifact hash identifies the exact bytes
chosen for import. Publisher authentication would require separate evidence.

Graph data, Temporal history, reports, events, metrics and checkpoints are
outside the retained-source bundle scope.

Authored tests cover synthetic pure artifacts and the approved disposable
PostgreSQL factory, including fresh isolated installed-module subprocesses.
Regressions preserve legitimate project metadata references to retained passage
IDs, deny same-owner foreign-project source selection without creating output,
and observe closed authority connections. Pure source-read counting covers the
early aggregate cap; it is not described as database qualification. The inspect
child denies psycopg connection factories and connection construction as well
as the native `pq.PGconn` connect/connect-start entry points used by libpq-backed
connection generators. Python socket interception remains an additional guard.
Controlled stat regressions admit stable differing path/descriptor ctimes while
rejecting a ctime-only change within either API. Raw ASCII JSON containing escaped
lone surrogates reaches both decode and inspection rejection directly, without
first failing artifact encoding in the test authoring helper.
The v3 PostgreSQL witness covers exact binary and passage restoration in a
distinct owner's project, authorized replay, and rejection when the restored
PDF has been replaced with a different file and a matching stored digest.

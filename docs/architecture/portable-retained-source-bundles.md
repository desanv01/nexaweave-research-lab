# Portable retained-source bundles — bounded U04b

Implementation is **UNVERIFIED** pending Main review, installation and checks.
This private data artifact supports a selected immutable PostgreSQL project
revision and 1–16 explicitly requested retained source revisions. It is separate
from the U14 committed-source ZIP. It does not establish full C41/U04 acceptance.

`mirofish_storage.research_bundle.export_bundle` resolves persisted authority
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

## Trusted local CLI

Run the installed module `python -m mirofish_storage.research_bundle_cli` with one
UTF-8 JSON request on stdin, capped at 32768 bytes. Main owns execution and
installation. Export request fields:

```json
{"operation":"export","principal":"owned-local-principal","project_id":"00000000-0000-0000-0000-000000000001","revision":1,"source_revisions":["00000000-0000-0000-0000-000000000002"],"output":"C:\\trusted\\new-bundle.json"}
```

Inspect request fields are `operation: "inspect"`, `input` absolute local path,
and optional lowercase 64-hex `expected_sha256`. Inspect needs no DSN or store
connection. Export uses only trusted local `MIROFISH_APPSTORE_DSN`, rejects libpq
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
and payload hashes, a privacy warning and explicit false binary/authenticity
claims. They never echo source text, paths, display names, principal, DSN or raw
exceptions.

## Privacy and remaining scope

Export includes private retained text and potentially sensitive original snapshot
metadata. It is not a secret scan. Snapshot paths, workflow IDs and graph IDs
remain inert strings and are never resolved, rendered, extracted or dispatched.
Evidence object keys do not prove original binary presence. Hashes identify
integrity, not publisher authenticity or signatures.

No durable import, identity remapping, original binary restoration, graph restore,
Temporal replay, model/provider calls or public publication is implemented.
Reports, events, metrics and checkpoints remain future bundle work. This packet
does not change Vue/Flask/OASIS/CAMEL, Graphiti/self-hosted Neo4j, initial
configurable DeepSeek selection, inherited licenses, notices or all 44 criteria.

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
These sources are not execution evidence. Main must install the exact candidate,
run tests under approved guards, review filesystem/connection behavior, integrate
runner/CI registration and decide exact-revision acceptance.

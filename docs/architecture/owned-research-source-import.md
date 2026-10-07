# Owned selected-source import

The installed `nexaweave_storage.research_import_cli` command imports a v1
retained-source bundle into an explicitly chosen **existing** owned PostgreSQL
project. The operator supplies principal, target UUID, expected current revision,
absolute local input path and trusted whole-file SHA256 independently of bundle
bytes. That hash pins the operator's chosen bytes; it does not authenticate a
publisher or establish semantic truth.

```json
{"operation":"import","principal":"local-owner","target_project_id":"00000000-0000-0000-0000-000000000011","expected_revision":2,"input":"/absolute/private/bundle.json","expected_sha256":"<64 lowercase hexadecimal characters>"}
```

Supply one bounded stdin JSON request to
`python -I -m nexaweave_storage.research_import_cli`. The trusted local environment provides
`NEXAWEAVE_APPSTORE_DSN`; request fields cannot supply a DSN. All ambient `PG*`
variables and DSN service/servicefile/passfile options are rejected; default
password-file discovery is disabled and connection timeout is three seconds.
The existing reviewed local-file reader rejects links/reparse points, checks
ancestor identities and file changes, and enforces the 12MiB artifact cap. These
checks do not establish a hostile-filesystem sandbox. Request cap is 32KiB.

Strict bundle validation, trusted digest, canonical UUIDs, Unicode, metadata,
source/excerpt hashes, codepoint offsets, selected-source limits and copying all
finish before connection construction. Import additionally rejects NUL in every
copied metadata value/key because PostgreSQL JSONB cannot store it. Canonical
provenance UTF8 and its spaced JSON representation are capped at 2MiB; SQL also
caps PostgreSQL's own JSONB text representation. The source text cap is 1MiB each,
8MiB aggregate, with at most 16 sources and 100 passages per source. Existing
bundle validator restrictions remain in force.

Source IDs use UUID5 with the target project UUID as namespace and the name
`mirofish-retained-import-v1:source:<artifact-sha256>:<original-source-uuid>`.
Passages use the same format with kind `passage` and the original evidence UUID.
Kinds, whole artifact hash and target isolate identities; an artifact with
different bytes, even equivalent whitespace, is a distinct import. Original
identities are never adopted and an existing mapped-ID collision is denied.

One factory-owned connection and transaction use fixed five-second statement,
two-second lock and ten-second idle-in-transaction timeouts. The target row is
locked using its persisted principal before checking expected current revision
or accessing any receipt. Missing and other-owner targets are indistinguishable;
stale revisions conflict. Inserts use fixed parameterized SQL, never nested
SourceStore calls or independently committed transactions. Sources, ordered
passages and receipt commit together; exceptions roll them back and close the
connection. Unique collisions fail without overwrite or adoption.

The SQL3 receipt's primary key is target project plus artifact hash. A composite
foreign key pins its target revision to an actual retained project revision. It stores
origin project/revision, payload hash, target revision at import, UTC import time
and bounded structured provenance. Provenance includes the entire original
project snapshot/evidence metadata plus original source IDs/names/hashes/lengths,
recorded times and ordered original passage identities/offsets/pages/hashes,
alongside mapped IDs. Text and excerpts reside in the new retained records rather
than being duplicated in provenance. `provenance_sha256` binds a canonical
envelope containing this provenance, target project, whole/payload hashes,
revision at import and imported timestamp. It is not a publisher signature or
protection against a database administrator recomputing a digest.

Repeat import first checks current persisted ownership and expected revision.
It locks the receipt and imported source/passage rows, verifies exact lineage,
the receipt integrity envelope, all source fields and import timestamps, and every
passage's exact ordinal/content. It returns the original receipt without inserts.
If the target independently advances, the caller must supply the new current
revision; the original receipt still records its first import revision/time.
Changed, missing or extra persisted passages, altered source data or receipt
metadata fail closed. The original source's recorded time remains inert
provenance; new source `recorded_at` equals the receipt's import time.

Migration is explicit through the existing migration function. SQL1/SQL2 remain
unchanged; store.py adds only the SQL3 registry entry. Import never migrates,
creates/overwrites projects, changes their snapshot/evidence/current revision or
history, restores binaries/graphs, grants artifact-defined authority, resolves
metadata paths/object keys/URLs, or dispatches workflow/provider/model operations.
Private original text and snapshot metadata are not secret-scanned. CLI success
contains counts, hashes, timestamp and false binary/graph/publisher flags;
errors are fixed codes with exit 2, without text/path/principal/DSN/raw exceptions.
Exit 0 means a successful transaction/verified repeat. There is no optional
filesystem receipt write; the immutable database receipt is authoritative.

Worker-authored pure, actual guarded PostgreSQL and fresh isolated installed CLI
test sources require Main execution. A Python socket observer is not a native
libpq egress proof; actual tests use only the explicit approved literal disposable
fixture DSN. Main owns fresh/v1/v2 migration, repeat/catalog/checksum/rollback
qualification and inherited test registrations. This bounded behavior is not
whole-project restore, reports/events/metrics/checkpoints portability, full C41,
U04 or all-44 acceptance. No provider calls or public deployment are authorized.

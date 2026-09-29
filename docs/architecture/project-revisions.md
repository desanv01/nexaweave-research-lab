# Project metadata revisions, first slice

`mirofish_storage` is an isolated Python package in the existing knowledge distribution. It has no import-time database activity and does not import Flask, Graphiti, or `mirofish_knowledge`. The host authenticates a printable ASCII principal (1–128 characters, internal spaces allowed, not all spaces) and supplies workspace and project UUIDs. The display project ID is an ASCII alphanumeric, underscore, or hyphen token (1–128 characters). These are persisted separately using case-sensitive C collation. The store does not authenticate the principal.

## Snapshot v1

The exact top-level allowlist from inherited `Project.to_dict` is: `project_id`, `name`, `status`, `created_at`, `updated_at`, `files`, `total_text_length`, `ontology`, `analysis_summary`, `graph_id`, `graph_build_task_id`, `zep_batch_id`, `zep_batch_operation_id`, `simulation_requirement`, `chunk_size`, `chunk_overlap`, and `error`. All are required, preserving values without defaults or key removal. Status is one of `created`, `ontology_generated`, `graph_building`, `graph_completed`, `failed`. `project_id` must match the supplied display ID. The current graph upload route persists file entries with exactly `filename` and `size`; the older documented `filename`, `path`, `size` shape and the full `save_file_to_project` return shape (`original_filename`, `saved_filename`, `path`, `size`) are also accepted. Each shape is preserved without adding fields. Ontology is a JSON object or null. Optional metadata strings may be null. Text fields are capped at 64 KiB, file names at 1 KiB, and collections at 1,000 entries; numeric fields are bounded nonnegative integers. Combined canonical snapshot and evidence JSON is capped at 1 MiB UTF-8, with a maximum nesting depth of 32. Duplicate JSON keys, nonfinite numbers, invalid Unicode, non-JSON Python objects, and unknown top-level fields are rejected.

Legacy `files[].path` values are **unresolved inert metadata**. Import never opens or copies those paths, reads uploads, or proves that binary files have migrated. `zep_*` fields are retained as inert strings; they do not authorize provider operations. The evidence manifest contains up to 100 declared references, with exact keys `evidence_id`, `source_revision`, `sha256`, `object_key`, and `byte_length`. Storage keys are relative opaque names with no dot segments, URL syntax, backslash, or controls. Each evidence ID is unique in its revision. References are not blob inspection or source-span verification.

## Database behavior

Explicit `migrate(connection)` installs `mf_app` version 1 under a dedicated transaction advisory lock. It records the migration file SHA-256 and a catalog-shape SHA-256, then rejects unknown or drifted schemas. It never drops or repairs objects. Runtime methods do not require migration-owner rights. `projects` binds immutable principal/workspace/project/display identity and the current revision. A deferred composite foreign key requires that its head point to a revision of the same project at transaction commit. `project_revisions` holds versioned canonical JSON, digest, and UTC timestamp. The digest is SHA-256 of compact sorted-key UTF-8 JSON containing `snapshot` and `evidence`; reads check it. The store inserts revision rows and updates only the project current-revision pointer. A row lock and exact expected revision make concurrent updates single-winner. Create retries with identical identity and revision-one content are idempotent. Owner-scoped missing reads use `not_found`; identity or stale-write collisions use `conflict`. Listing and history have deterministic ordering and a 200-row hard limit.

## Local explicit command

Set `MIROFISH_APPSTORE_DSN` in the local environment. The CLI has no DSN argument and emits fixed error codes without database diagnostics.

```text
python -m mirofish_storage migrate
python -m mirofish_storage import-project --principal OWNER --workspace-id UUID --project-id UUID --display-id proj_1 --input project.json
python -m mirofish_storage export-project --principal OWNER --workspace-id UUID --project-id UUID --display-id proj_1 --revision 1 --output new-export.json
```

Import accepts a bounded regular JSON file containing either a legacy snapshot v1 or a versioned export envelope. Versioned import accepts **revision 1 only**, requires exact identity arguments and a matching digest, and does not reconstruct multi-revision histories. Export defaults to the current revision, may select a history revision explicitly, writes to stdout by default, or creates a new output file exclusively. An export of revision 2 or later is not currently importable. The envelope declares `binary_migration: false`. Commands do not follow paths embedded in JSON and never alter original metadata files.

This slice does not include binary upload migration, source-span extraction, organization roles, app route cutover, backup tooling, or production migration. Its tests use a disposable loopback PostgreSQL database; no live provider is involved.

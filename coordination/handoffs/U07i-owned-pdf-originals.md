# U07i static source handoff — UNVERIFIED

Worker checkout `u00`, branch `task/u07i-owned-pdf-originals`, starting at
accepted PR96 base `f14c332c448cb8177ce4b8f43184043440da1a4d`.
This handoff records static authoring only. No test, import, parser, build,
PostgreSQL, browser, Git, provider or network operation was run by the worker.

Changed only assigned document paths: additive migration/catalog; `SourceStore`
atomic PDF/original reads; fixed child admission and V2 receipt; strict backend
codec/facade/API; pure frontend codec and Sources component/locales; focused
storage/child/HTTP/frontend fixtures; architecture record. Accepted V1 paths
and old migration SQL were left intact. No U07h or Main-owned parent path was
edited.

The exact 23-path scope is the frozen `NEXT-DOCUMENT-PATHS.json` entries:
`services/knowledge/src/nexaweave_storage/migrations/0004_source_binaries.sql`,
`store.py`, `source.py`; `services/knowledge/src/nexaweave_knowledge/source_library.py`;
`backend/app/services/knowledge_source_client.py`, `knowledge_source_facade.py`,
`backend/app/source_library_api.py`; `services/knowledge/tests/test_source_store.py`,
`test_source_store_postgres.py`, `test_source_library.py`,
`test_source_library_postgres.py`, `test_source_library_http_integration.py`,
`test_source_binary.py`, `test_source_binary_postgres.py`;
`backend/tests/test_source_library_api.py`; `frontend/src/api/sourceLibrary.js`,
`frontend/src/components/workbench/SourceLibrary.vue`, `frontend/src/i18n/workbench.js`,
`frontend/tests/source-library-client.test.mjs`, `source-library-render.test.mjs`,
`source-original.component.test.js`; `docs/architecture/owned-source-originals.md`
and this handoff. All were authored except the existing `test_source_store.py`,
which was read and left unchanged because its V1 assertions still apply; the
new pure binary tests live in `test_source_binary.py`. The table name frozen for Main's independent migration checks
is **`mf_app.source_binaries`**. The legacy V1 text inspector now labels its
`binary_retained=false` DTO as a text/passage view that cannot establish
original availability; EN/ZH/MS copy points to explicit V2 metadata lookup.
V1 wire shapes and old receipts remain unchanged, and only verified V2
results display original-byte availability.

Main integration interface:

- `sourceOriginalPayload(method,payload)` supports exactly
  `sourceRetainOriginal`, `sourceOriginalMetadata`, `sourceOriginalRead`.
  It returns a JSON body string for the V2 POST and a strict
  `{source_revision}` identity for GETs.
- `verifyOriginalPdfInput(payload)` asynchronously verifies admitted canonical
  PDF bytes/hash before POST.
- `validateSourceOriginalResult(method,value,payload)` asynchronously verifies
  exact V2 receipt/metadata/read fields, source binding and PDF byte digest.
- `SourceLibrary.vue` calls `methods.retainOriginal(payload,{signal})`,
  `methods.originalMetadata({source_revision},{signal})`, and
  `methods.originalRead({source_revision},{signal})`. Main maps these to
  `retainOriginalPdf/getOriginalMetadata/downloadOriginalPdf` and the frozen
  routes. The component uses the optional signal for its owned read; Main
  should forward it to its transport cancellation boundary.

Authored, **unverified** cases cover V3→V4 migration/idempotence/drift,
exact binary replay/restart/owner denial, V1 no-original, corruption,
passage-collision no-orphan, actual fixed-child HTTP read after restart,
four injected transaction abort boundaries (source insert, passage insert,
binary insert and readback),
codec malformed bytes, locale copy, and component download/URL revocation.
The assigned frontend client fixture additionally covers Main's integrated
fixed original GET routes/no bodies, exact digest-verified original content,
preflight wrong-hash refusal before fetch, caller-aborted pending read and
late-result fencing. The component fixture asserts owned `AbortSignal` delivery.
After Main's first frontend qualification reported two failures within this
worker's paths, the render fixture was updated to compile/map the actual
`ConnectedFollowup.vue` sibling in the combined candidate. The original
component fixture now tracks real WebCrypto digest promises with a bounded
drain, and includes a digest-delayed reset case that asserts the revision,
metadata and download action clear without creating a Blob or clicking a
link. The component aborts owned original operations on revision edits/reset,
disconnect and unmount, then rechecks generation, connection, signal and
controller ownership after asynchronous V2 validation. The read captures
expected binary metadata before validation and checks it again before Blob
creation. These corrections are static and remain **unverified** pending
Main's combined frontend rerun.
Those `createWorkbenchClient` cases target Main's disjoint parent wrapper
integration and therefore require the combined candidate; this worker did not
edit or execute `frontend/src/api/workbench.js` in `u00`.
Main must run the new guarded `run_source_binary_tests.py` unit/postgres modes,
review full selected logs and zero skips, then perform quiet headless browser
and hosted PR/push/postmerge gates under its normal accepted-base workflow.

Limitations remain explicit: no OCR/table layout reconstruction, no semantic
support review, no binary addition to selected-source bundle V1, no paid call,
no public deployment, and no full C40/C41/all44 acceptance. Future direct
human pause immediately controls.

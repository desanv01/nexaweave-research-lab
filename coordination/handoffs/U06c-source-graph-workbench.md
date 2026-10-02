# U06c source-to-graph workbench — correction 2 UNVERIFIED stable handoff

## Current bounded correction 2 — 2026-10-03

Main reported correction 1's 75 Node passes/no skips, passing 7.09-second build and actual closed browser fixture against accepted backend491, local PG/Neo4j and canned SDK responses. Those checks apply to the prior candidate. Main's browser finding was a shared parent banner that encouraged submitting again after graph-ingestion default-off403 or uncertain503. This correction remains **UNVERIFIED** and requires Main's new qualification; no prior result is claimed for the changed candidate.

Exactly four authorized paths changed for correction 2:

1. `frontend/src/views/ResearchWorkbench.vue` — ingestion methods now identify plan/execute/status within the existing parent operation wrapper. A computed safe banner selects request-specific recovery guidance for ingestion errors and cancellation. Authentication/origin denial still uses the inherited auth guidance and clearing path. Disconnect clears the banner context; subsequent source/research/dossier operations select their unchanged existing feedback. The single client, generation, busy/cancel behavior and child execution latch remain intact.
2. `frontend/src/i18n/workbench.js` — complete EN/ZH/MS parent ingestion banner copy: planning feedback, submitted execution status-only reconciliation, and unavailable-status guidance. No server details or retry-execution invitation is displayed by these messages.
3. `frontend/tests/workbench-render.test.mjs` — additive actual shared-route/SFC/client regression source with valid source/plan fingerprints. It exercises planning503, execution403 default-off and execution503 unknown, localized banners without request side effects, duplicate-execution blocking, explicit status404/uncertain recovery, preserved research-read feedback, and ingestion401 protected-state clearing. Existing assertions remain; nested banner copy key parity is also asserted.
4. `coordination/handoffs/U06c-source-graph-workbench.md` — this current correction receipt.

The other eight worker paths were not edited for this correction. No dependency, backend, task, ledger, runtime or CI edits. No imports/tests/checks/build/browser/runtime/DB/network/Git/delegation/messages/schedule operations were executed by this worker. Only assigned source reads and source/test/handoff authoring occurred. Prior architecture documentation still describes correction 1 and the core workflow; this current section records the bounded parent-banner fix.

Correction 2 source is complete and stable, **UNVERIFIED**. Main alone reviews and runs qualification. Exclusive u00 ownership is retained; worker remains idle pending a bounded Main correction or direct human pause.

## Preserved correction 1 source handoff

Assigned u00 `task/u06c-source-graph-workbench`, supplied accepted base `6f8eb1bb66b5a9f2c8a5e6eb385981a74c5ad7a4`. Frozen backend reference `b89a10de49add9392c5fce8178188cc312851b21`; Main reports PR78 fully accepted at merge `49172568b32d6c88e9e3b9823db8bf335b5b2320` with all 24 gates/logs reviewed. Frontend correction 1 remains unverified; Main alone integrates and qualifies it. The appended Main initial-scope/fingerprint clarification and both typed golden vectors remain applied.

The human's latest direct resume superseded the immediately acknowledged pause. Main reports current partial bytes captured in the root resume-partials-2026-10-02 checkpoint before redispatch; no pre-pause source hash-equivalence claim is made. This handoff supersedes the stale first handoff and partial correction checkpoint.

Correction 1 is now source-complete: the inherited status count remains exactly one within `.connection [role="status"]`, with a separate exactly-one ingestion assertion. Strict name/description validation now shares helper rules with field feedback. Every entity, edge, attribute and pair input has a stable unique ID; validation generates localized inline errors and `aria-invalid`/`aria-describedby` associations. Only an explicit invalid Plan activates and focuses the linked summary. Entered values remain, errors update after activation without focus jumps, links focus their affected controls, and broken references after entity rename/removal are identified rather than silently reassigned. Summary/link focus styling and wrapping are authored. New actual-SFC regression sources cover invalid/duplicate/reserved names, duplicate/reserved attributes, rename/removal pair errors, duplicate pairs, Unicode 500-codepoint bounds, localized links/focus, zero requests until corrected, successful corrected Plan and reset clearing. No dependency or authorization/execution-latch changes.

Exactly twelve assigned edit paths:

1. `frontend/src/api/sourceIngestion.js` — new strict ontology/payload/DTO/receipt/correlation checks; full-source canonical fingerprint and deterministic group/episode.
2. `frontend/src/api/workbench.js` — ingestion plan/execute/status in the single private client, bounded response stream, cloned request context, inspected-source revalidation, frozen connection scope and auth clearing.
3. `frontend/src/views/ResearchWorkbench.vue` — parent operation/reset wiring and real ingestion component, inspected-source event association.
4. `frontend/src/components/workbench/SourceLibrary.vue` — additive explicit inspection/clearing event; inherited retention/inspection behavior retained.
5. `frontend/src/components/workbench/SourceIngestion.vue` — new editable ontology, explicit plan/review/one execute, retained attempt, manual/known status recovery, separate graph/admission states, safe late-response/reset handling.
6. `frontend/src/i18n/workbench.js` — complete EN/ZH/MS ingestion copy, nested graph/admission states and fixed error feedback.
7. `frontend/tests/source-ingestion-client.test.mjs` — new contract/golden/malformed/transport/denial/uncertainty sources.
8. `frontend/tests/source-ingestion-render.test.mjs` — new real SFC flow/edit/reset/late-response/manual/localization/focus/style sources.
9. `frontend/tests/workbench-render.test.mjs` — additive real ingestion module wiring and cold-state assertions; inherited assertions preserved.
10. `frontend/tests/source-library-render.test.mjs` — additive real route module wiring and inspection event/clearing assertions; inherited assertions preserved.
11. `docs/architecture/source-ingestion-workbench.md` — behavior, trust boundary, recovery/privacy and honest limits.
12. `coordination/handoffs/U06c-source-graph-workbench.md` — this handoff.

No tests, imports, Python, checks, builds, browser QA, audits, runtime/server/DB/network/Git/status/hash/commit/push/PR/merge/release operations were executed. No workers delegated, other chats messaged, automations altered, backend copied or out-of-bound source edited. Read-only PowerShell source reads and apply_patch source edits only. An initial direct .NET file write to the assigned workbench path was denied; it made no file change, and the authorized source edit was applied using apply_patch. No sandbox escalation was requested.

Main must execute inherited and new Node sources, build and genuine browser/HTTP/store/synthetic model flow and inspect exact candidate paths/revisions against the accepted backend. Main's first pre-correction run reported 70 passed/1 global status-count failure/no skips and a passing 7.11-second build with inherited warnings. Those are historical first-candidate results and do not qualify this completed correction. This handoff is not a passing test/build assertion or acceptance receipt.

Limits: initial workspace/internal graph scope is authenticated-server asserted (not independently browser-owned); manual status proves no source association; large ceilings above JS safe integer fail closed; actual usage/model call count remains unknown. Local ontology field errors are specific and localized; remote malformed/rejected replies still use fixed safe messages. Control/style test sources do not establish actual responsive/keyboard/screen-reader/contrast browser acceptance. Starter ontology is editable, not semantic quality acceptance. No paid model calls, secrets/cap configuration, public deployment or whole-phase completion authority inferred.

Correction 1 is complete for this bounded source packet, UNVERIFIED. Worker remains stable/idle with exclusive u00 ownership unchanged, preserving all candidate bytes until a specific bounded Main correction or direct human pause.

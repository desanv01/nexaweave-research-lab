# U07b source-only handoff — UNVERIFIED

## Correction 2 — stable source, UNVERIFIED; latest behavior

This section supersedes the original immediate-download lifecycle described
below. Main reported correction1's preserved12 paths,124 relevant Node tests
passing with no skips and a7.51-second build; real PG/Neo browser preview of12
actors, pagination, inert full inspector, zero/maxseed, invalidseed and
EN/ZH/MS responsive checks passed. Main also reported both export responses200
and old requested feedback, with no actual saved file/event observed in IAB or
isolated Chrome. The existing immediate dossier control likewise produced no
saved file in IAB. This is not proof of an asynchronous-download product defect.
These are Main-reported results, not worker verification and not acceptance of
this latest source revision.

Correction2 changes seven of the original nine exclusively owned paths:

1. `frontend/src/components/workbench/PopulationWorkbench.vue`
2. `frontend/src/i18n/populationWorkbench.js`
3. `frontend/src/views/ResearchWorkbench.vue`
4. `frontend/tests/population-workbench-client.test.mjs`
5. `frontend/tests/population-workbench-render.test.mjs`
6. `docs/architecture/population-workbench.md`
7. `coordination/handoffs/U07b-graph-population-workbench.md`

Explicit Prepare Twitter CSV / Prepare Reddit JSON calls retain the same
graph-bound endpoints, submitted preview options, deadline, body limits,
byte-preserving mappings and MIME/UTF8/row admission. After admission, the panel
owns one bounded Blob URL and exposes an actual visible native Save Twitter CSV
/ Save Reddit JSON link with its fixed download name. There is no automatic
click or download after a network response. Explicit immediate native Save is
the only download initiation; it makes no backend call. Prepared feedback is
localized EN/ZH/MS and forwarded through the existing parent live status region.
Requested feedback appears only after Save and never asserts a saved file.

The visible link has native link/keyboard semantics, a44px target and visible
focus. Disabled/busy activation prevents default. The prepared URL remains
available while waiting for Save. The existing1000ms cleanup timer starts after
Save activation. Replacement preparation/options edit/reset/disconnect/unmount
revoke the owned URL and remove the link. All original auth/reset/stale guards
and correction1's local invalid-reply normalization remain in place.

Client source fixtures now explicitly establish that export preparation returns
bytes without allocating a browser URL or initiating Save. Actual compiled-SFC
fixtures now require no click on Prepare, a visible fixed-name link, explicit
Save with no extra callback request, exact Blob bytes, one replacement URL,
delayed-response suppression, invalid-output rejection with no link, localized
prepared/Save text without refetch, disabled activation prevention, retention
beyond1000ms while waiting, and1000ms revocation after Save. Existing negative
assertions and request deadlines were preserved. Test-hook native activation
records intent/default allowance, not a real saved browser file.

Correction commands were only source reads/searches (`Get-Content` with
`Select-Object`, `rg -n`) and authorized source edits (`tools.apply_patch`). One
documentation patch failed because its source context did not match; a focused
source read supplied the actual context and the corrected patch was applied.
No tests, imports, builds, execution, Git, browser or provider calls were run.
Main owns both existing render harness paths and all qualification, including
actual prepared-link/save browser behavior and downloaded bytes/files. No new
backend/dependency/provider/storage call or unrelated product fix was authored.

Correction2 source is stable, UNVERIFIED. The worker stops tools/edits and returns
idle with exclusive ownership retained until Main's explicit release/transfer.

## Correction 1 — stable source, UNVERIFIED

Main reported preserving the original11 exact paths and running113 relevant
Node tests:111 passed,2 failed, no skips. These are Main-reported results; this
worker did not execute or independently verify them. One failure was local
malformed UTF8 feedback: Node's fatal TextDecoder error has
`ERR_ENCODING_INVALID_ENCODED_DATA`, which previously mapped to generic failed.

This correction touches only
`frontend/src/components/workbench/PopulationWorkbench.vue` and this handoff.
The component now normalizes its local MIME/byte-shape, fatal UTF8 decoding and
export-row admission exceptions to fixed `invalid_reply`. The awaited method
callback remains outside that normalization block, preserving its
unauthorized/cancelled/unavailable and other transport codes. No fixture,
assertion or deadline was changed. Main owns the separate missing population
SFC import-map entry in existing `frontend/tests/source-library-render.test.mjs`,
in addition to the already corrected workbench-render harness.

Correction commands were source reads only using `Get-Content` with
`Select-Object`, followed by `tools.apply_patch` for these two authorized paths.
No tests/imports/execution/Git occurred. The correction is stable and
UNVERIFIED; the worker returns idle and retains exclusive checkout ownership
until Main explicitly releases/transfers it.

Stable authored candidate in exclusive checkout
`C:/Users/Dv/Desktop/MiroFish/_implementation_worktrees/u02`, assigned branch
`task/u07b-graph-population-workbench`, accepted transfer base
`6521b38afcada360de244ce27a5284d604c9a31c`. These identifiers come from Main's
packet; this worker did not run Git to reverify them. Main remains sole
orchestrator/reviewer/checker/runtime/Git/acceptance owner. Latest direct-human
worker model correction is GPT-6.1 Sol/MEDIUM; Fast remains requested-unverified.

## Touched paths — exactly the nine authorized paths

1. `frontend/src/api/populationWorkbench.js` — new complete preview/options/native export admission and bounded CSV parser.
2. `frontend/src/components/workbench/PopulationWorkbench.vue` — new explicit selection, preview, pagination, source inspector and native download panel.
3. `frontend/src/i18n/populationWorkbench.js` — new full EN/ZH/MS surface copy and fixed error mapping.
4. `frontend/tests/population-workbench-client.test.mjs` — new source fixtures, not executed.
5. `frontend/tests/population-workbench-render.test.mjs` — new actual SFC/compiler-sfc/jsdom interaction fixtures, not executed.
6. `frontend/src/api/workbench.js` — narrow private population route integration, private admitted-preview/options record, exact byte capture and shared cancellation/auth/body deadline handling.
7. `frontend/src/views/ResearchWorkbench.vue` — narrow panel/callback/type-label/reset wiring; population feedback uses the existing route live status region.
8. `docs/architecture/population-workbench.md` — behavior, producer formats, security/reset contracts and unqualified limits.
9. `coordination/handoffs/U07b-graph-population-workbench.md` — this handoff.

## Implemented source behavior

- Explicit graph-bound preview and export POSTs through the existing private
  client; no mount/change/locale/poll fetch, implicit retries or provider calls.
- Canonical typed labels excluding Entity/Node; up to50 selectable unique types;
  empty selection preserves backend all-custom-types behavior. Strict integer
  form and DTO limits preserve zero/max seed without rounding/coercion.
- Complete exact preview shape/flags/date/count/profile identities, nullable
  trait fields, profile/source memberships, directions/incident joins,
  references, bounded JSON attributes and full text admission. Accepted data is
  deeply detached. Shared incident edges must match across selected endpoints.
- Ten-row pages and an accessible named inspector separating generated traits
  from original source summary/attributes/facts and episode/evidence references.
  Plain text only, no activated URLs or clipped source text.
- Exact inherited Twitter five-column and Reddit truthy optional-field mappings;
  bounded quotedCSV parser; ordered/matching export rows required before Blob.
  Original response byte chunks survive unchanged into fixed-name downloads.
- Private admission retains exact preview options; edited/mutated/replaced
  options or preview cannot be used to export. Parent/component/client
  generations reject stale replies; reset/disconnect/cancel/replacement/unmount
  clear protected state and download resources. Malformed401 clears connection.
- Fixed localized feedback, explicit requested-not-saved download wording,
  synthetic/no-model/no-simulation/non-atomic/re-read limitations. EN/ZH/MS
  controls use existing tokens/system fonts and native semantics/44px targets.

No dependency, backend, router, CI, architecture, provider, notice or license
changes were authored. Existing Vue/Flask/Graphiti/Neo/PostgreSQL/OASIS foundations
remain the source of behavior. This packet does not implement model persona
enrichment, scenario configuration, automatic launch or native simulation.

## Actual source references read

Root current `AGENTS.md` and the complete Root U07b packet; assigned checkout
`AGENTS.md` and `docs/plan/MASTER-PLAN.md`; `workbench.js`, `sourceLibrary.js`,
`sourceIngestion.js`, `experimentComparison.js`, `dossierExport.js`;
`ResearchWorkbench.vue`, existing workbench component/test compile/mount patterns;
`knowledge_read_app.py` population routes/options; actual
`backend/app/services/knowledge_population.py`, `knowledge_reader.py` and
`oasis_profile_generator.py` profile/Reddit/Twitter mappings/rule branches;
existing backend population fixtures; research-workbench/provider-neutral
architecture documents. Local UI/UX Pro Max `SKILL.md` and static pro-rules were
read; its scripts were not executed. No hosted12ui or remote design was used.

## Commands actually executed

All terminal commands were source reads/searches only: `Get-Content -Raw
-LiteralPath <file>`, `Get-Content <file> | Select-Object -Skip <n> -First <n>`,
`Get-Item -LiteralPath <file> | Select-Object FullName,Length`, `rg --files
<source-directories> | rg <path-pattern>` and `rg -n <source-pattern> <files>`.
Independent source reads were batched through `functions.exec` and
`Promise.allSettled`; source edits used `tools.apply_patch`. Two initial reads
used nonexistent `backend/services/...` paths from the packet's abbreviated
description, then `rg --files backend` located and reads used the actual
`backend/app/services/...` paths. The large initial source/plan output was
truncated by tool output limits; relevant producer/transport/format spans were
read separately before authoring.

No source was imported or executed by the worker. No tests, lint, builds, checks,
audits, installs, Git commands, browser/server/native/DB/container commands,
network/provider tools, subagents, new chats or schedules were run. No
verification or acceptance claim is made.

## Concrete Main follow-up and limits

Main acknowledged ownership of the narrow import-map update in existing
`frontend/tests/workbench-render.test.mjs`: compile
`PopulationWorkbench.vue` and map its parent-relative import to that compiled
SFC URL. Without this Main harness adjustment, the old Node data-URL compiler
will try to import a `.vue` file directly. The worker did not write that path.
The new render fixture contains the complete new parent import map itself.

Main should review all nine source paths and run the newly authored client/render
fixtures plus affected existing regressions and the locked frontend build.
Actual Flask/PG/Neo browser journey, both download byte/row proof, native loader
qualification, EN/ZH/MS browser keyboard/focus, 320/768/1440 layout and zoom are
UNVERIFIED. jsdom source assertions do not establish real layout/accessibility.

The initial admitted graph supplies displayed type labels, while preview/export
read fresh bounded graph projections. Empty type selection still allows all
current custom types. Matching native export rows cannot establish identical
grounding or atomic reads; UI and architecture copy state that limit. Flask
provides nosniff on exports, but existing CORS does not expose that header to
cross-origin JS; admission uses the browser-visible expected content type and
never trusts Content-Disposition. The worker added no CORS/backend change.

No paid/public/fullphase/all44/native-runtime acceptance is claimed. Stable
source authoring is complete; this worker stops tools/edits and retains exclusive
idle checkout ownership until Main explicitly releases/transfers it.

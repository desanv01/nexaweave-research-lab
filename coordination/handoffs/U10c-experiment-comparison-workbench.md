# U10c stable source handoff — 2026-10-04

Source authoring complete for the connected experiment observation workbench in exclusively assigned u02 at Main-supplied accepted PR82 base. NO checks executed; Main qualification pending. No imports, Python, Node, lint/build/tests, audit/hash/status/Git, installation/runtime/DB/network/browser/provider/model calls, delegation, other-chat messages or schedules were executed. Ordinary source read/write tools only.

## Ten touched paths

1. `frontend/src/api/experimentComparison.js` — new strict public DTO and ordered immutable request admission.
2. `frontend/src/components/workbench/ExperimentComparison.vue` — actual connected manual catalog/selection/comparison SFC, progressive observation/provenance details and responsive cards.
3. `frontend/src/api/workbench.js` — narrow experiment methods in existing private request transport, fixed paths, bounded caps and guarded unavailable/auth handling.
4. `frontend/src/views/ResearchWorkbench.vue` — method-only props to additive experiment section through shared busy/reset/auth lifecycle.
5. `frontend/src/i18n/workbench.js` — additive complete EN/ZH/MS experiment copy.
6. `frontend/tests/experiment-comparison-client.test.mjs` — new request/projection/bounds/state/seed/cancellation/auth test source.
7. `frontend/tests/experiment-comparison-render.test.mjs` — new actual compiled SFC/localization/inert text/selection/reset/error source.
8. `frontend/tests/workbench-render.test.mjs` — real nested SFC compilation hook and additive route no-auto-request/auth/disconnect/reload/cancel assertions; existing assertions retained.
9. `docs/architecture/experiment-comparison-workbench.md` — behavior, transport/admission and evidence boundaries.
10. `coordination/handoffs/U10c-experiment-comparison-workbench.md` — this handoff.

## Decisions and limitations

- Only explicit fixed GET catalog and fixed POST compare; no graph suffix, run orchestration or model request. Parent retains the single private connection. Components receive no token/origin/path/config.
- Selection is sent in catalog order, snapshots detach before awaits, title/member edits invalidate prior results, and refresh clears old membership before admission. Shared cancellation and reset generations suppress late results. Optional unavailable failure supports explicit manual recovery only. HTTP401/403 clear protected route state.
- Strict accepted public projection shape/identity/count/availability/capability checks. Signed64 seeds are canonical bounded strings, never Number. Null is unavailable, known zero remains zero. Server statistics stay server values; no Python serializer/native comparator/browser digest verification was invented.
- Conservative snapshot matching includes catalog state and cancellation intent. Since the accepted server compares against a fresh catalog, an intervening run transition may cause the UI to reject a server-valid newer response. This intentionally fails closed and instructs explicit catalog refresh; Main should review this UX/contract decision. No out-of-scope backend edit.
- Responsive member cards/native details/44px actions/polite progress/visible focus and EN/ZH/MS use existing tokens/system fonts under supplied local UI/UX Pro Max guidance. No hosted design call, new dependency, asset/router redesign or protected persistent storage.
- Mixed five-state/cancel overlap and large seeds are test fixtures only. Node/build/browser/PG qualification, all44 acceptance and actual accessibility remain pending. Backend-only passing evidence was not rerun or claimed here.
- Initial broad source filename lookup encountered preserved inaccessible cache directories; no contents were read, altered or deleted and no complete inventory is claimed. The registry path from the assignment was located under `_upgrade_plan/migration-2026-09-30/WORKER-REGISTRY.md` and read there.

Main can preserve these source bytes, review and run change-relevant frontend checks plus the bounded actual accepted-backend/browser fixture. No new assignment inferred. U10c is now idle, retaining exclusive ownership of these ten paths; no further tools or source work until an explicit bounded correction assignment or direct human steering. Future direct human pause supersedes immediately.

## Correction1 stable source handoff — 2026-10-04

Main reported its first Node execution as 87 passed / 3 failed / no skips in 6.566sec, preserving `_upgrade_plan/u10c-node-first-helper.log` and the original source bytes. This is Main-reported evidence, not a worker execution or a passing qualification claim. All owned check trees/private CWD were reported closed. The first failure evidence remains preserved.

Applied only the explicitly authorized four correction paths:

1. `frontend/tests/source-library-render.test.mjs` — additionally authorized eleventh worker path. Compiles the actual ExperimentComparison SFC and maps its URL in the ResearchWorkbench compile harness, retaining all existing source-library assertions.
2. `frontend/tests/experiment-comparison-render.test.mjs` — awaits the existing settle helper between the separate member1/member0 checkbox events so Vue commits each user selection. The exact submitted `['m0', 'm1']`, large-seed, null-metric and all other substantive assertions remain unchanged.
3. `frontend/tests/workbench-render.test.mjs` — scopes the cancellation feedback lookup to `.experiment-comparison .feedback`, retaining cancellation text, signal-abort and no-late-population assertions.
4. `coordination/handoffs/U10c-experiment-comparison-workbench.md` — this appended correction record.

No production/API/component/view/i18n/architecture source changed in correction1. No assertions were dropped or weakened; no timeouts changed. Total worker ownership is now eleven paths, plus Main's separately owned task (twelve total packet paths). NO checks executed by this worker; Main qualification/rerun pending. U10c is stable and idle again, retaining exclusive assigned ownership until an explicit bounded correction or direct human steering. Future direct human pause stops immediately.

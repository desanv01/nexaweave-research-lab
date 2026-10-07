# U07g backend source handoff — UNVERIFIED

Worker chat `01a1118f-48ba-74b1-bad6-c32b47040620`, GPT-6.1 Sol/medium, Fast requested-unverified. Accepted input base supplied by Main: `c7485e24f78740c7f575823dc8e797b63bea86ef`. Issue95. Main explicitly registered and activated exactly26 backend paths in u02/task/u07g-connected-native-reports. Current root authority, contract, packet, transfer and wire clarification1 were statically read. Old workers were not revived. Source/test authoring and static reads only; no imports, AST/parser, execution, tests, build, lint, installs, Git, network/provider/browser/runtime, skill scripts, schedules or other-chat messages were performed by this worker.

## Exact writes

1. backend/app/services/connected_report_client.py
2. backend/app/services/connected_report_context.py
3. backend/app/services/durable_report_host.py
4. backend/app/services/report_models.py
5. backend/app/services/report_process.py
6. backend/app/services/connected_report_tools.py
7. backend/app/services/report_dependencies.py
8. backend/app/services/report_agent.py
9. backend/app/services/connected_report_facade.py
10. backend/app/connected_report_api.py
11. backend/app/__init__.py
12. backend/app/knowledge_read_app.py
13. backend/app/services/native_observation_reader.py
14. backend/tests/test_connected_report_client.py
15. backend/tests/test_connected_report_host.py
16. backend/tests/test_connected_report_api.py
17. backend/tests/test_connected_report_dependencies.py
18. backend/engine_tests/test_connected_report_process.py
19. services/knowledge/src/mirofish_execution/report_contracts.py
20. services/knowledge/src/mirofish_execution/report_store.py
21. services/knowledge/src/mirofish_execution/temporal_report.py
22. services/knowledge/src/mirofish_execution/migrations/report_0001.sql
23. services/knowledge/src/mirofish_execution/migrations/report_0001_down.sql
24. services/knowledge/tests/test_report_store.py
25. docs/architecture/connected-native-reports.md
26. coordination/handoffs/U07g-connected-native-reports.md

## Main integration interfaces

- `register_connected_report_routes(app, settings, connected_report_facade=None)` and `ConnectedReportFacade(settings, host=...)`; six methods are plan/start/status/cancel/read/download. `known_report` is a durable metadata-only method used to validate downloads from injected facades without status recovery. Frozen `kind`, `section_index`, artifact response and unbracketed reference-key wording matches clarification1.
- `DurableReportHost(settings, connection_factory, read_facade, native_launch_host, artifact_root, account_id=None, ceiling_microusd=None, authorize=None, model_factory=None, model_label=None, limits=None, scheduler=None)`.
- `BoundedReportModelFactory(transport_factory, model_name, limits)`; transport factory is spawn-pickleable, explicit, cooperative and returns an OpenAI-shaped SDK object with integer max_retries0 and close(). No implicit/default paid transport. Every compatibility and nested request crosses the same RequestBoundary.
- `TemporalReportHost(client=..., task_queue=..., trusted_host=..., allow_dispatch=...)`, owned `worker()` and `scheduler_for(owned_loop)`. Main supplies the existing loop/client and bounded lifecycle. Persisted workflow is exact workflow_id/run_id. Dispatch is exact schema_version/report_id/plan_sha256/attempt_id.
- `ReportStore` and explicit `migrate(connection)`/`rollback(connection)` own mf_report only. Queue admission uses accepted BudgetLedger account/totals helpers and the unchanged shared budget tables inside one transaction. Domain-separated report accounting holds/settles full ceilings without manufacturing an ingestion/preparation/native receipt; report completion proof stays in mf_report.
- Completed `ReportStore.finish(...)` requires the trusted `verify_output` callback: full output hash/reference/lease checks finish inside the publication/accounting transaction before commit. Failed proof rolls back both publication and settlement. No post-commit lease failure can create a false success through that callback.
- `ScriptedReportTransportFactory(mode='ok', sections=2, trace_path=None)` in the engine fixture is a reusable local, cooperative SDK-shaped factory for Main's combined fixture. The engine module and service fixture helper import backend/tests/test_connected_report_client lazily: Main should put backend/tests on its explicit fixture path, consistent with existing qualification helpers. No default runner or CI selection was modified by the worker.

## Authored cases — all UNVERIFIED

- Cold scalar/UTF8/duplicate/depth/size admission; exact fields, UUID/hash, native windows, boolean integers, malformed envelopes; immutable cross-reply identities; completed receipt/manifest/cleanup and read/download digest/base64 refusal.
- Late source passage/native-record/native-manifest corruption; native tool exact admitted ranges, distinct evidence channel, bounded refusal; invalid/invented/partial reference refusal.
- Actual SDK counter/timeout/token/non-streaming capture, first-possible-request ordering, provider-compatibility failure fence, zero-request oversize input and unconfigured factory.
- Model-disabled immutable read/download, complete output verification including unselected files, every noncompleted state refusal before files, scheduler lost reply with permanent same-ID fence, exact repeated CRLF native log lexemes and file lease rechecks.
- Actual spawned inherited outline/ReACT/tools/assembly, two sections, EN/ZH/MS, parent manager/CWD/locale isolation, invalid references/failed sections/transport failure partial retention, cold late-record corruption, cancellation after first possible request, separate operation roots. No engine execution claimed.
- Actual guarded PostgreSQL same-ID queue race, permanent activity claim, cross-owner denial, distinct report shared-capacity race, known no-call release versus possible-request retention, restart expiry uncertainty, context hash corruption and schema/catalog drift. Native journal seeds here are explicitly synthetic SQL authority fixtures; Main's actual combined native-body fixture is separate.

## Review boundaries

No unassigned paths or old fixtures/assertions/bounds were changed. Legacy report callers keep original tools/defaults/loops; connected additions are explicit. Existing observation page parsing/bounds/results are delegated to the same implementation; context is the new capability. Original retrieval evidence and restore validation are unchanged. Partial report outputs remain retained. Immutable read/download paths perform neither runtime polling nor journal/budget mutation.

Main must inspect actual worker bytes, install/prove the new execution-package files, run meaningful changed-behavior checks, qualify actual owned runtime/PGTemporal/browser behavior and preserve every failure. Exact source/PR/push/postmerge full logs and match-head acceptance remain Main-only. No tests, runtime, provider quality, billed cost, issue95 completion or full44 acceptance are claimed. There is no outstanding path/DTO clarification request; source awaits Main review/corrections.

Stable source handoff: worker idle/tools off after final delivery; exclusive26 retained until explicit Main correction or release. Any future direct human pause stops immediately.

## Bounded correction1 — UNVERIFIED

Main preserved the initial backend26/UI11/Main9 bytes in `u07g-combined1` and supplied the static compatibility finding `U07G-MAIN-REVIEW-FINDINGS1.md`. Explicit correction1 authorization was restricted to the same26 paths. This correction writes only `backend/app/services/report_agent.py`, `backend/tests/test_connected_report_dependencies.py` and this handoff.

Added class defaults `connected_context=None` and `connected_instruction=''` beside the existing legacy `neutral_mode=False` default. Legacy ReportAgent objects constructed through `__new__` can therefore use unchanged tool dispatch without requiring connected constructor state. The new lazy-import fixture exercises the real legacy quick-search dispatch with an injected local tool, checks its exact original result/arguments and original advertised tool set, and refuses implicit model construction. No old fixtures, assertions, tools, loops, bounds or other product files were changed for correction1.

Correction1 is source-only and UNVERIFIED: no imports/execution, parser/AST, tests, Git, runtime or provider work was performed. Main owns exact-byte review and qualification. No outstanding interface question. Worker returns stable idle/tools off; exclusive26 remains retained until explicit Main correction or release.

## Bounded correction2 — UNVERIFIED

Main explicitly authorized finding2 point1 in `U07G-MAIN-REVIEW-FINDINGS2.md` after correction1 was stable. The original snapshot/finding remains Main-owned and preserved. This correction writes only `services/knowledge/src/mirofish_execution/report_store.py`, `services/knowledge/tests/test_report_store.py` and this handoff, within the same26.

`ReportStore.cancel` now sets `report_cancelled` only when its locked pre-update state is `planned`, alongside that existing planned-to-cancelled transition. All other states preserve their prior state and error code; receipt/manifest, attempt/dispatch flags and shared budget logic are untouched. A new explicit PostgreSQL fixture checks the actual planned cancellation's public safe code, known cleanup, immutable frozen plan, absence of owner/dispatch/request/receipt/reservation, same-ID repeated cancellation, permanently refused queue and unchanged account status. No existing test or assertion was weakened.

Correction2 is source-only and UNVERIFIED; no execution/checks, imports, AST/parser, tests, Git or runtime occurred. No UI paths, limits or broader interfaces changed. No outstanding clarification. Worker stable idle/tools off, exclusive26 retained for Main review/correction/release.

## Bounded correction3 — UNVERIFIED

Main explicitly authorized the same26 correction after preserving `u07g-unit-initial.log` and `u07g-download-diagnostic.log`. The worker statically read the relevant failure/diagnostic and source; no diagnostic or check was executed. Main reported initial49 collected/47 passed/1 failed/2 Windows setup-teardown errors/zero skips, owned trees closed and original46 source unchanged. These are preserved Main results, not worker qualification or acceptance.

Exact correction3 writes:

- `backend/tests/test_connected_report_api.py`: short explicit IDs for all four existing malformed raw bodies, including the unchanged32769-byte oversize input. Values, body, assertions and bounds unchanged.
- `backend/tests/test_connected_report_host.py`: add the exact `dto['plan_sha256']` attribute to the existing file-host fixture row. All read-only spies, content/hash assertions and product lease behavior unchanged.
- `backend/app/services/connected_report_context.py`: use the same bracket-adjacency guarded marker expression for matching and stripping. An inner pair inside triple/nested brackets can no longer be admitted and stripped as a valid marker. Existing broken/invented marker and native-required checks remain.
- `backend/tests/test_connected_report_dependencies.py`: add short-ID source/native triple, asymmetric nested-opening/nested-closing and quadruple marker negatives, both alone and beside an actual valid native marker. This exercises both marker matching and leftover-marker refusal without altering prior assertions.
- This handoff.

Correction3 remains source-only UNVERIFIED: no imports/execution, AST/parser, tests/build, Git, network/provider/browser/runtime, skill scripts or other-chat messages. No Main/UI paths or contract limits changed. No outstanding interface question. Stable idle/tools off; exclusive26 retained until Main correction or release.

## Bounded correction4 — UNVERIFIED

Main explicitly authorized correction4 after its actual combined journey reached a two-platform native receipt and exposed a legitimate projected-field null during report planning. Main's earlier correction3 qualifications and original failure logs remain Main-owned evidence, not acceptance claimed here.

Exact correction4 writes only `backend/app/services/connected_report_tools.py`, `backend/tests/test_connected_report_dependencies.py` and this handoff. Lexical corpus assembly converts only `None` fields to empty strings. Supplied text, identifiers, source graph order, scope checks, lexical scores and deterministic score/ID ranking are preserved. Non-text malformed values are not stringified or coerced; existing graph input authority and the legacy tools/reader are unchanged.

The new lazy-import fixture uses an actual KnowledgeReadFacade → KnowledgeGraphReader path over an explicit local ByteClient. It proves actual projected node `fact=None` and edge `summary=None`, supplied text preservation, exact node/edge/both score and tie ordering, limit selection, repeated deterministic results, denied mismatched scope, exactly two projection reads and byte-identical graph/wire facts after selection. No semantic transport or model is supplied.

Correction4 is source-only UNVERIFIED; the worker performed only static reads and source/test authoring, with no imports/execution, AST/parser, tests, Git, runtime/provider/browser/network, skill scripts or other-chat messages. No unassigned paths, Main fixture, contract or limits changed. No outstanding clarification. Stable idle/tools off, exclusive26 retained until Main correction or release.

### Correction4 additional rollback fixture — UNVERIFIED

Main separately authorized missing rollback/down-migration coverage inside the same26. The only additional fixture write is `services/knowledge/tests/test_report_store.py`; this handoff records it. No migration or product file was changed for rollback coverage, and all prior PG assertions remain intact.

Three short-ID actual guarded PostgreSQL cases isolate queued, generating and uncertain journal states inside `connection.transaction(force_rollback=True)`. They assert the specific active row alone triggers `busy`, with unchanged report catalog/checksum/row hashes and accepted preparation/native-run/native-launch/shared-budget catalog signatures and table counts. A fourth quiescent case performs actual rollback and up migration inside a forced-rollback transaction, asserts only mf_report disappears, then asserts the rebuilt owned catalog and migration checksum match exactly while accepted schema signatures/counts remain unchanged. After each forced rollback the fixture compares the complete preexisting report row-hash witness and schema/migration witnesses, so historical rows and schemas are restored. Synthetic state rows are explicitly journal guard data, not native/model execution evidence.

All additional cases remain UNVERIFIED source authoring. No runtime, imports/execution, tests/parser/Git or permanent data deletion was performed by the worker. No outstanding fixture/interface question. Stable idle/tools off, exclusive26 retained.

## Bounded correction5 — exclusive27, UNVERIFIED

Main explicitly amended the same worker's ownership from26 to27 by adding only `services/knowledge/src/mirofish_execution/budget.py`, after verifying its accepted c748 bytes and absence of overlapping owners. `U07G-BUDGET-AMENDMENT1.md` and Main's subsequent exact internal wire pin supersede the earlier budget.py write prohibition and the initial settlement design. The worker statically read the amendment/current paths/STATE/packet and preserved private journey trace: actual PostgreSQL correctly refused a settled reservation with null receipt. No failed row was redispatched, and no original failure evidence was modified.

Exact correction5 writes:

1. `services/knowledge/src/mirofish_execution/report_contracts.py`
2. `services/knowledge/src/mirofish_execution/budget.py` — newly authorized27th path
3. `services/knowledge/src/mirofish_execution/report_store.py`
4. `backend/tests/test_connected_report_client.py`
5. `backend/tests/test_connected_report_dependencies.py`
6. `services/knowledge/tests/test_report_store.py`
7. `docs/architecture/connected-native-reports.md`
8. This handoff.

`ReportBudgetReceipt.from_wire/json_value` uses exactly `kind`, `operation_id`, `attempt_id`, `fingerprint`, `plan_sha256`, `report_receipt`, `report_receipt_sha256`. Kind is `connected_report_budget_v1`; the existing report budget fingerprint is preserved. `budget_episode(group, reportUUID)` uses the pinned `mirofish:connected-report-budget:v1:` UUIDv5/NAMESPACE_URL string; `report_budget_episode` is its internal alias. Available properties include operation/attempt/fingerprint/plan/digest and a detached JSON `report_receipt`. The actual existing completed public report receipt field set is preserved without adding public HTTP/result fields. Immutable scalar proof internals prevent alias mutation and strictly validate version/UUID/hash/language/integrity/unreviewed-status/digest/operation/plan bindings.

The accepted budget extension adds only the new receipt type/import/parser branch and report-purpose guards/error mapping. Original ingestion/preparation/native parser branches, APIs, domain fingerprints/episode mappings, algorithms and all shared SQL/migrations/catalog/constraints remain unchanged. ReportStore queue binds the exact frozen scope and report-purpose episode. Finish checks scope/attempt/fingerprint/episode/empty evidence/ceiling, validates the actual completed journal proof, stores its typed budget receipt and reads it through the shared parser before final file verification and commit. Any invalid proof or verifier failure rolls back journal and settlement together. Existing unknown/no-call/failure/cancellation reservation outcomes remain conservative; there is no null settled receipt, false success or fake receipt from another domain.

Added UNVERIFIED pure cases cover canonical receipt roundtrip, detached exports/frozen assignment, pinned distinct mapping, sixteen bounded proof mutations including rehashed malformed nested proof, actual parser operation/attempt/fingerprint/evidence mismatch, report receipt in source/native episodes and source/preparation/native receipt in the report episode. Added dedicated guarded PG cases cover actual typed report settlement against the same12 cap with a competing shared reservation, exact journal/receipt/context/manifest/digest/attempt matching, idempotence/cap refusal, four invalid receipt/attempt/file-proof atomic rollback cases, refusal through all existing source/preparation/native reserve/settle APIs, and three transaction-local foreign-receipt injection/refusal/restoration cases. The PG artifact helper is explicitly journal/file-proof fixture data, not an inherited-agent or actual-native qualification claim; Main's separate combined fixture owns that actual journey. All original12 PG assertions and prior failure evidence remain untouched.

Main must independently review/install/qualify the amended execution package, budget regressions and actual combined journey before acceptance. Correction5 is source/test authoring only and UNVERIFIED: no imports/execution, checks, AST/parser/tests, Git, runtime/network/provider/browser, skill scripts or other-chat messages. No changes outside27, no shared schema/constraint edits and no limits expansion. No outstanding interface clarification. Stable idle/tools off; exclusive27 retained until explicit Main correction or release.

### Correction5 concurrent isolation continuation — UNVERIFIED

Main explicitly requested one additional actual concurrent engine fixture within the same27 after the budget handoff. Exact continuation writes only `backend/engine_tests/test_connected_report_process.py` and this handoff. Product code, per-process limits, cleanup bounds and all existing sequential/engine assertions are unchanged.

The authored case runs two distinct ReportProcess report UUID/private roots under `ThreadPoolExecutor(max_workers=2)`, with distinct frozen native record hashes/context digests and operation-private SDK traces. A first-request barrier bounded by the existing10s handshake proves both actual children are alive at request admission; one joint future deadline uses the existing run ceiling plus20s cleanup without restarting clocks. Assertions cover both inherited completions, exact own seven-file manifests/references/native evidence/meta IDs, first request exactly once, known cleanup, parent CWD/manager root/locale and frozen bytes unchanged, no cross-output root/report/log state, independent SDK PID/counters/close/locale and differing report prose. This is a standalone child isolation fixture, not durable/native acceptance.

Source-only UNVERIFIED; no fixture or process was executed by this worker. No new skip, timer expansion, Main path or product change. Stable idle/tools off, exclusive27 retained.

## Bounded correction6 — UNVERIFIED

Main explicitly authorized `U07G-ADMISSION-AMENDMENT2.md` within the same27. The worker statically read that amendment, current STATE, `u07g-admission-diagnostic1/RESULT.json`, `u07g-browser1/BROWSER-INITIAL-FAILURE.json` and relevant process source. Preserved diagnostic/browser identities were not redispatched or mutated. Their recorded no-SDK uncertain outcomes remain failure evidence, not acceptance.

Exact correction6 writes only:

1. `backend/app/services/report_process.py`
2. `backend/engine_tests/test_connected_report_process.py`
3. `docs/architecture/connected-native-reports.md`
4. This handoff.

Private protocol now sends exact action-bound `('checking', 'go')` immediately upon READY and `('checking', 'admitted')` immediately upon FIRST. Child validates that acknowledgement within the unchanged10 seconds, then waits with bounded cancellation polls for exact `('go',)`/`('admitted',)` under the same absolute whole-run deadline. Parent performs unchanged full synchronous reauthorization after acknowledgement, repeats cancellation/deadline/checkpoint validation before grant and before/after first durable request admission, and fences duplicate FIRST or completion before first admission. SDK/transport construction stays after GO; actual requests stay after durable ADMITTED. Missing, malformed, foreign, duplicate, out-of-order and closed controls refuse work. No new executor/background task, public wire change, authority cache/skip or reader/SDK/cleanup timer expansion was introduced.

Authored18 additional actual engine cases are UNVERIFIED: control0 versus slow11 at both authorization stages on a captured120-second ceiling with construction/request snapshots; context denial/unauthorized before GO or ADMITTED; wrong-action/early-grant/duplicate/closed controls at both phases over the real child/owned cleanup; missing checking acknowledgement with late grant (fixed10-second refusal, honest timeout/uncertain if its reply is lost); cancellation event while waiting at either phase; and a shortened2-second absolute-deadline case requiring the authorization callback actually entered. A separate new trace factory observes model construction without changing prior scripted fixtures. Existing ten child cases, concurrent first-request barrier and every prior assertion remain unchanged. Initial READY10, control acknowledgement10, captured run<=600 (new success cases120), SDK<=15 and cleanup20 are preserved.

No execution, imports, AST/parser, checks/tests, Git, provider/network/browser/runtime, skill scripts or other-chat messages were performed. No unassigned paths, Main qualification changes or limits expansion. Main owns stable-byte review and changed-process/real-reader browser qualification under its original bounds. No outstanding private interface question. Stable idle/tools off; exclusive27 retained until explicit Main correction or release.

## Bounded heartbeat correction3 — UNVERIFIED

Main's `U07G-HEARTBEAT-AMENDMENT3.md` expands this worker's exclusive u02 scope from27 to30. Retained Browser4 Temporal history proves a15-second activity heartbeat timeout, not full report acceptance or the exact individual blocking call. The original failed identity is never redispatched. Source/test authoring only; Main owns source review, execution, actual Temporal/browser qualification and integration.

Exact authored writes for this correction:

1. `backend/app/services/durable_report_host.py`
2. `backend/app/services/connected_report_context.py`
3. `backend/app/services/knowledge_transport.py`
4. `backend/tests/test_connected_report_host.py`
5. `backend/tests/test_knowledge_reader.py`
6. `backend/tests/test_owned_process.py`
7. This handoff.

The trusted report activity now supplies one same-thread tick to initial, GO, FIRST and both publication reauthorizations. The tick rejects the original absolute deadline, sends the activity heartbeat and rechecks durable/activity cancellation and current model authorization. `freeze_context` still performs both complete fresh graph projections, native/source/file/context comparisons and retained lease checks. Its private knowledge transport sends the tick during response and process-exit waits in <=1-second slices within the original transport deadline; no-callback callers retain the previous wait behavior. The transport's private `KnowledgeCooperativeAbort(BaseException)` survives the unchanged stdlib reader's `except Exception` sanitization; context maps only fixed cancellation/deadline/uncertainty codes to `ReportError`. The transport always runs owned cleanup before propagating abort, and cleanup failure overrides it with uncertain transport failure. No new worker thread/executor, public wire, provider call, authority cache or timer increase was introduced.

Authored UNVERIFIED regressions cover an actual waiting knowledge child with repeated same-calling-thread ticks, private cancellation/deadline/heartbeat-failure abort, owner cleanup and client reuse, cleanup failure overriding abort to uncertainty, reader passthrough with normal error sanitization intact, private context translation to safe report cancellation, all five generation authorization stages receiving ticks, and changed-context refusal. Main must review and run these, then add its owned exact Temporal15-second aggregate-authorization regression and new-identity browser qualification under original limits. One synchronous OS spawn or DB call cannot itself emit a same-thread heartbeat while blocked; Main's retained private trace should confirm the timed-out stage and any further concrete failure. No tests, imports, AST/parser, lint, build, Git, runtime, network, browser or skill scripts were run by this worker. Stable idle/tools off; exclusive30 retained pending Main review.

### Correction7 selected transport-fixture repair — UNVERIFIED worker edit

Main reviewed the prior source50, reported reportunit79 pass/zero skips, and preserved an actual PostgreSQL/Temporal heartbeat regression pass with four owned reader waits totaling over17 seconds under the unchanged15-second activity heartbeat. Main's selected transport-reader run exposed three abort-mode fixture failures: the second reuse iteration inherited the first iteration's tick count and aborted before spawn, leaving `owner.process=None`. After Main closed the runtime, this worker changed only `backend/tests/test_owned_process.py` and this handoff. The fixture now clears per-call ticks and wait-start time at the top of each reuse iteration. Abort requires at least five same-thread ticks and at least one second since the post-spawn tick, so both reuse calls exercise a running owned child and response wait before their existing process-poll, private-directory, thread and lock cleanup assertions. Original timeout6 and child delay2.2 stay unchanged. The source-only repair is UNVERIFIED by this worker; Main must review and rerun the selected cases. Production code, other tests, failed report identities and all bounds are unchanged. No checks, execution, Git, network or browser use by this worker; stable idle/tools off, exclusive30 retained.

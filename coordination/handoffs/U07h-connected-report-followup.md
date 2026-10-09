# U07h backend handoff — static authored source

Exclusive checkout: `u02`, branch `task/u07h-grounded-report-followup`, accepted parent `f14c332c448cb8177ce4b8f43184043440da1a4d`. Scope is the frozen `NEXT-BACKEND-PATHS.json` 23 paths only. No tests, imports, parsers, builds, runtimes, DB operations, Git or network calls were executed by this worker. All authored cases are **UNVERIFIED**; Main owns checks, runtime, Git, CI and acceptance.

Source: pure strict follow-up DTO/history/budget proof; additive checksummed `mf_followup` migration and one-transaction shared-account admission/publication; fresh completed-parent reauthorization, private inherited chat child, two-tool trace, six immutable turn files, external current publication proof; cold seven-route HTTP facade/API; one-shot Temporal activity; narrow connected chat instruction/trace addition. Existing report/knowledge/native SQL and source behavior are unchanged outside the assigned `budget.py` union and `report_agent.py` connected-chat branch.

Exact worker-authored paths (22 of the exclusive 23; assigned `backend/app/services/report_models.py` is reused without an edit):

- `backend/app/services/connected_followup_client.py`
- `backend/app/services/connected_followup_context.py`
- `backend/app/services/connected_followup_facade.py`
- `backend/app/services/durable_followup_host.py`
- `backend/app/services/followup_process.py`
- `backend/app/connected_followup_api.py`
- `backend/app/services/report_agent.py`
- `backend/tests/test_connected_followup_client.py`
- `backend/tests/test_connected_followup_host.py`
- `backend/tests/test_connected_followup_api.py`
- `backend/tests/test_connected_followup_dependencies.py`
- `backend/engine_tests/test_connected_followup_process.py`
- `services/knowledge/src/nexaweave_execution/followup_contracts.py`
- `services/knowledge/src/nexaweave_execution/followup_store.py`
- `services/knowledge/src/nexaweave_execution/temporal_followup.py`
- `services/knowledge/src/nexaweave_execution/migrations/followup_0001.sql`
- `services/knowledge/src/nexaweave_execution/migrations/followup_0001_down.sql`
- `services/knowledge/src/nexaweave_execution/budget.py`
- `services/knowledge/tests/test_followup_store.py`
- `services/knowledge/tests/test_followup_budget.py`
- `docs/architecture/connected-report-followup.md`
- `coordination/handoffs/U07h-connected-report-followup.md`

Main integration seam: call `register_connected_followup_routes(app, settings, followup_facade=connected_followup_facade)` from its assigned factory path. Configure `DurableFollowupHost` with the accepted `DurableReportHost`, same protected settings/account and bounded `BoundedReportModelFactory`; attach `TemporalFollowupHost.scheduler_for(owned_loop)` to that host before enabling model authorization. Run the additive migration explicitly on the guarded disposable PostgreSQL. Missing optional components leave only follow-up operations unavailable; route registration and ordinary metadata remain cold.

Main corrected its integration fixture to use the accepted `reports._output_root(parent_row)` location. The worker did not edit that Main-owned test.

Authored static tests: strict DTO/chain/publication negatives, cold API, disabled protected reads, parent-copy immutability, guarded PostgreSQL same-ID race/history/shared-account allowance, typed fifth receipt, and an actual spawned inherited-chat scripted transport with two executed tools and failure/corruption cases. Main runner selects these files; record exact outcomes and repair concrete failures without weakening accepted bounds or reusing failed identities. Review `U07H-PUBLICATION-AMENDMENT3.md` for the current-pair null/external-proof rule.

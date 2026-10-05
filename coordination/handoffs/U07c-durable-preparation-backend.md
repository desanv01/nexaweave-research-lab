# U07c backend — stable UNVERIFIED handoff

Source-only worker handoff, 2026-10-05. Exclusive authoring root:
`C:/Users/Dv/Desktop/MiroFish/_implementation_worktrees/u02`.
Main supplied branch `task/u07c-durable-preparation` and accepted base
`be004ce23bf5425ab28d9540428bea2759a6c4dc`; the worker did not execute Git or
independently verify those revisions. Main owns review, runtime, qualification,
integration and issue87 acceptance. No other writer's paths were authored.

Main's explicit common-contract correction from 1–10000 rounds to **1–24 rounds**
is applied. The accepted native contracts were not relaxed. Worker source and
fixtures are ready for Main inspection, not claimed tested or accepted.

Main's subsequent cross-interface correction is applied within the same twenty
paths: preparation API model_calls_disabled/budget_denied now return HTTP 409,
while unauthorized401/origin_denied403 remain unchanged. Actual Flask denial and
same-plan status-recovery fixtures were added. Architecture documentation now
correctly identifies BACKEND native-store/native-temporal extras and the installed
knowledge distribution's execution extra, removes the stray Public fragment, and
records the existing canonical ASCII plan identity/producer limits. No dependency
file was changed. This correction remains UNVERIFIED; no fixture was executed.

Main's scoped correction2 is also applied, within these same twenty paths:
`_artifacts` now requires exact row-name correspondence to frozen actors/order,
parses the exact inherited ordered five-column Twitter CSV header without
DictReader duplicate-key ambiguity, rejects wrong row widths, and compares both
platforms by reconstructing Twitter rows from corresponding Reddit records with
the actual inherited `OasisProfileGenerator.twitter_loader_row` normalization.
Additive fixtures mutate names/order, duplicate/foreign/reordered headers, width,
username/bio/persona mapping before publication, requiring no receipt, no settlement
and no final directory. An actual scripted generation fixture covers CR/LF
normalization. WorkflowFailureError was already present in the source at this
correction's static read; its proper temporalio.client import is now consolidated
with Client. Architecture docs describe these guards. No existing assertion or
deadline was relaxed, no dependency/extra path was changed, and all additions
remain UNVERIFIED. Worker performed static reads and assigned patches only,
executed no fixtures, and returns to idle exclusive ownership after this handoff.

Scoped correction3 follows Main's preserved installed-package unit failure:
Main reported 66 passed / 34 failed / 2 errors / zero skips; those are Main's
results on correction2, not a worker execution or a correction3 pass. The offline
fixture now constructs KnowledgeScope through the accepted strict JSON decoder,
preserving UUID/Layer/schema validation. Static inspection found the identical
decoder mismatch in DurablePreparationHost: settings.scope is the accepted JSON
DTO, but strict Python-object validation requires typed UUID/Layer objects. That
constructor now uses model_validate_json on finite ASCII JSON too, with no schema
relaxation or public-shape change. No other assigned fixture had the same strict
scope construction call in the static search.

The oversized-body API parametrization now has concise fixed ids while retaining
the identical 65538-byte payload and rejection assertions. Closed-payload fixtures
send explicit ensure_ascii=True JSON bytes, allowing the lone-surrogate case to
reach the actual endpoint instead of failing in Werkzeug client serialization;
400/no-facade assertions remain unchanged. No test limit/deadline/skip/assertion
was relaxed. Main owns Windows nodeid/provenance accounting. These changed bytes
are explicitly UNVERIFIED; worker performed static reads/assigned patches only,
ran no source/import/check/test/runtime, and stops tools/edits again while retaining
exclusive ownership pending Main's review/recheck or bounded correction.

## Exact authored paths

1. `services/knowledge/src/mirofish_execution/preparation_contracts.py`
2. `services/knowledge/src/mirofish_execution/preparation_store.py`
3. `services/knowledge/src/mirofish_execution/migrations/preparations_0001.sql`
4. `services/knowledge/src/mirofish_execution/budget.py`
5. `services/knowledge/src/mirofish_execution/temporal_preparation_workflow.py`
6. `services/knowledge/src/mirofish_execution/temporal_preparation_host.py`
7. `backend/app/services/durable_preparation_host.py`
8. `backend/app/services/preparation_client.py`
9. `backend/app/services/knowledge_preparation_facade.py`
10. `backend/app/preparation_api.py`
11. `backend/app/knowledge_read_app.py`
12. `backend/app/__init__.py`
13. `backend/app/services/preparation_dependencies.py`
14. `backend/app/services/simulation_manager.py`
15. `backend/tests/test_durable_preparation.py`
16. `backend/tests/test_preparation_api.py`
17. `services/knowledge/tests/test_preparation_store.py`
18. `services/knowledge/tests/test_temporal_preparation.py`
19. `docs/architecture/durable-preparation.md`
20. `coordination/handoffs/U07c-durable-preparation-backend.md`

Main-only `tools/run_preparation_tests.py`, CI wiring, task/ledger/reviews and Root
records were not touched by this worker.

## Implemented contracts and seams

Protected research_local POST plan/start/status routes use strict shared-v1 JSON
and result validation, bearer/origin protection inherited from the application,
64 KiB request / 256 KiB result bounds, duplicate/nonfinite/extra/query/encoded/
chunked rejection, fixed errors and no-store. Result checking applies to injected
facades too. The lazy default facade creates no optional model/PG/Temporal/native
runtime and returns preparation_unavailable without explicit trusted composition.
Its trusted execute calls have a four-call concurrency bound.

The explicit trusted host resolves ScopeBindingStore/ProjectStore/SourceStore,
freezes detached complete graph/source/options/UUID-sorted actors in a PG plan,
hashes canonical identity/projection, and recovers an existing identical request
without fresh graph reads. Start reauthorizes exact project revision/source and
reserves shared account budget before one PG queue CAS. The queue also locks the
actual project revision row. Repeated/lost-ack starts read the existing attempt,
never schedule a second one. Temporal uses an identifier-only reject-duplicate
workflow ID and activity retry maximum_attempts=1. Its explicit operator-owned
worker context drains its executor; HTTP only submits to an already owned loop.

Budget admission reuses the accepted account lock, reservation table and totals.
Prepared admission uses a domain-separated fingerprint and empty evidence tuple;
settlement writes a DISTINCT tagged PreparedBudgetReceipt. Legacy CompletionReceipt
decoding and the accepted mf_execution SQL/catalog remain unchanged. Preparation
has its OWN mf_preparation SQL/checksum/catalog-drift migration.

Activity generation invokes the real inherited strict neutral profile/config
generators through SimulationManager. New dependency parameters accept an already
validated frozen graph and fixed selected UUID order while retaining complete
incident facts/neighbors. A trusted deterministic sim_<operation UUID hex> ID is
added only for neutral callers; legacy defaults are preserved. The shared wrapper
limits call count/output tokens/prompt bytes/transport timeout/deadline, and records
budget+PG dispatch authority before the first provider request.

Private attempts precede exclusive new publication. Complete state/config/
grounding/platform/profile count/order/username correspondence, bounded native
regular-file guards and manifest are checked; final inputs are flushed/read-only
and rechecked before tagged settlement and READY CAS. Partial/orphan/failed/
uncertain results do not grant launch. All private attempts/artifacts are retained;
no unowned paths are deleted. Unproven transport close retains the client/host,
denies READY and exposes trusted drain_cleanup() retry; at most four active or
unclosed transport slots are admitted.

Trusted bind_native reauthorizes READY/current revision before filesystem access,
derives the fixed server simulation directory, validates the exact receipt/bytes,
runtime scalar binding and picklable model factory, and returns the accepted
NativeRunRequest/NativeOwnedSessionFactory. No HTTP path/factory/credential/run
override exists and no native simulation is launched by this packet.

## Fixtures for Main to execute

Pure files: backend/test_durable_preparation, backend/test_preparation_api, and the
unmarked cases in services/test_temporal_preparation. The durable host's pure seam
is EXPLICITLY nondurable; it exercises real scripted inherited individual AND
organization generation for Twitter/Reddit/both, exact artifacts and native binder,
frozen grounding, duplicate/lost ack recovery, strict generation failures, stale
authorization before files, corrupt publications, lost READY CAS, cancellation,
call/deadline bounds, unproven client cleanup and fixed API/cold import boundaries.
The cold-import fixture authors a finite subprocess for Main; worker ran none.

`test_preparation_store.py` is marked postgres and imports the existing guarded
disposable factory. Set PROJECT_STORE_POSTGRES_INTEGRATION=1 and the existing
approved PROJECT_STORE_POSTGRES_TEST_DSN. It uses real project/source/binding/
preparation/budget stores and actual scripted inherited generation, plus a clearly
scripted graph byte seam. It covers migration drift/legacy preservation, shared
ingestion/preparation account-cap contention, parallel duplicate queue admission,
restart/ownership, lost scheduling replies, crashed claims, corrupt frozen inputs,
stale/tombstoned authorization and native binder-before-files. It does not claim
live Neo4j qualification.

The combined test in `test_temporal_preparation.py` has BOTH postgres and
preparation_temporal markers. Set TEMPORAL_EXECUTION_INTEGRATION=1 and exactly
TEMPORAL_TEST_ADDRESS=127.0.0.1:17233, plus the PG guards above. It runs the actual
Temporal worker + PG stores + inherited scripted generators, checks successful and
failed preparation, shared settled/uncertain ceilings, duplicate/restart fences,
identifier-only history, deterministic replay and worker/transport cleanup using
finite waits. No paid provider or native launch is required. Main's runner must
provide backend/tests and services/knowledge/tests import paths, installed updated
knowledge distribution/extras, and registration of the preparation_temporal marker
without editing the worker's unassigned pyproject path.

## Actual worker actions and remaining risks

Actions were static file reads/searches (PowerShell Get-Content/rg/Select-Object/
Select-String) and apply_patch writes to the twenty assigned paths. Read authorities
were Root AGENTS and both U07c packets. Read source included accepted source bridge,
PG source/project/binding/budget migrations, graph facade/projection/population,
native owned factory/run/prepared host, Temporal modules, inherited generators,
manager and relevant existing test fixtures. Missing guessed filenames and the
existing inaccessible pytest-of-unknown directory were observed during static
search; no cache was changed or deletion attempted.

NO source/import execution, compiler/parser check, tests, lint/build, installation,
provider/paid/network/Git, browser/container/runtime/native engine, skill script,
agent/chat/schedule creation or messaging another chat was performed. There are
no worker verification results or invented passes. Main must review all bytes and
run qualification; failures must be retained and resolved without assertion or
deadline waivers.

Trusted transport methods must honor the supplied finite timeout, disabled SDK
retries and prompt close. Python cannot preempt an arbitrary non-cooperative
injected SDK method; this is an explicit operator obligation, not proved by fake
clients. Started crashes/lost activity ownership can retain queued/preparing fences
and held ceilings; automatic reconciliation/retry/cancellation is not implemented.
Publication is exclusive immutable file input plus PG CAS, NOT a distributed
filesystem/PG atomic transaction. Main still owns actual real-PG/Neo/Temporal browser
qualification, hosted gates, install/runtime guarantees and acceptance. Native live
launch/feed/report, zoom limitation, other workstreams and full44 remain open.

After this stable handoff the worker stops tools/edits and retains idle exclusive
ownership of these twenty paths until Main explicitly releases or issues a bounded
correction. No implicit task revival or overlapping writer is authorized.

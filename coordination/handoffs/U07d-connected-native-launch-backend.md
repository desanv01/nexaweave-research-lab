# U07d backend stable correction3 handoff — UNVERIFIED

Base supplied by Main: accepted PR88
`0e8676117c63c6ff9d2146055c4832aa3fb439a8`.
Checkout: `C:/Users/Dv/Desktop/MiroFish/_implementation_worktrees/u02`.
This is source/fixture authoring only, not an execution or acceptance receipt.

Correction3 authoring baseline supplied by Main: committed
`0289ce8b43f91c30c9a6bf27d92fd628993beb2d`, draftPR90. I statically read the
retained U07D-PR-PYTHON-FAILED-FULL.log: generic Linux1292passed/7FAILED/16
inherited skips45.58sec. Four Start fixtures lack the real optional Temporal
host SDK in that environment, the CAMEL boundary fixture lacks camel, and the
supervisor/gate-diagnostic fixtures lack the dedicated knowledge fixture path.
The original failure remains intact. Main separately reported correction2
fixture2cases passed7.24sec, actual Temporal2cases passed58.11sec and fresh
preparation→both-platform OASIS1case passed159.38sec with cleanup closed. Those
are Main's execution receipts, not worker-executed checks or final acceptance.

Exactly these SEVEN test bodies were moved unchanged from
backend/tests/test_durable_native_launch.py into
services/knowledge/tests/test_native_launch_budget.py:

- test_ready_binding_one_shot_cancellation_and_configuration_freeze
- test_lost_ack_recovery_cannot_reschedule_or_release
- test_disabled_review_does_not_spend_artifact_and_new_review_can_start
- test_shared_sync_async_call_bound_and_picklable_parameters
- test_budget_adapter_keeps_failed_close_owner_retained_until_same_owner_retry
- test_same_id_queue_failure_close_proof_cannot_release_concurrent_queued_winner
- test_gate_failure_diagnostics_do_not_refresh_authority_or_leak_exception_text

Names, assertion text, bodies/helper behavior and bounds are preserved. The
dedicated file imports existing non-test helpers/types; no test_* aliases are
left in the generic backend module. The moved7 remain unmarked unit cases,
alongside the unchanged original pure budget fixture; its original2 postgres
cases are unchanged. There is no module-level postgres marker to deselect moved
cases. All43 pure backend fixtures, including cold import, and32API fixtures
remain for generic Linux; Main's installed native --unit is intended still to
execute all98 including moved7 with zero selected skips. No test/assertion was
removed, weakened or replaced, and no production stub/fallback was introduced.
Static rg text inspection confirmed the seven definitions occur only in the
dedicated file and the remaining backend test definitions remain in place;
no collection/import/parser/check execution was performed.

Correction3 edits ONLY those2 fixture files, docs/architecture/native-launch.md
and this handoff inside the same20. Shared scripted/model/host helpers used by
engine and browser are retained. Product/core/engine/browser/CI/Main runners,
dependencies and installed environments are unchanged. No tests, imports,
builds, Git, network/provider calls or runtime were executed. The classified
source remains UNVERIFIED pending Main's complete native/generic qualification.
Stable correction3: stop tools/edits and retain idle exclusive ownership.

Main-reported correction1 evidence is retained: exact-installed96unit/no skips
passed66.46sec; PGTemporal26passed/2FAILED/no skips492.30sec under unchanged600.
I statically read the actual original integration tests.log and the failing test
source. Both failures show native_run_uncertain before the ten-second gate
marker; the logs do NOT establish the precise child spawn failure. I did not
execute that evidence or convert either failure into a pass. Main reported
owned test/Temporal trees/private CWD closed, PG stopped/data retained.

Correction2 is fixture-only: the spawn-pickled ConnectedGateFactory defining
module now imports only standard library/pytest at module load. Heavy Temporal,
native, source/preparation/backend fixtures moved into parent test functions,
gate_constructor and a lazy guarded PG factory. Minimal typed gate session and
factory are defined directly in the same assigned test file; they preserve the
fingerprint, real NativeProcessDriver/NativeRunCoordinator/BudgetedNativeSupervisor
path and observed file/evidence semantics. All existing gate assertions and
original5sec handshake/10sec marker/40sec gate/600sec total limits are retained.
The actual connected OASIS engine fixture and production/core sources are
unchanged. No uncertainty is masked, no marker forced, no deadline increased.

Missing-marker diagnostics add at most four cached supervisor/driver/process
snapshots: fixed safe phase/error/native state, cancellation/cleanup/owner
booleans, available PID/exitcode/aliveness and reader flags. They perform no DB
refresh, driver operation, wait/join or retry; closed/unavailable process handles
remain unknown. No exception text, path, executable, source, credential or
endpoint is serialized. Authored pure fixtures check lightweight cold module
import and diagnostic safe failure behavior. Correction2 edits only
test_temporal_connected_launch.py, test_durable_native_launch.py, native-launch.md
and this handoff within the same20 paths. The import hypothesis/fix and these
new fixtures remain UNVERIFIED until Main's new bounded qualification.

Implemented protected launch plan/start/status/cancel, strict frozen identity,
separate own PG launch schema, max100 unstarted immutable reviews and permanent
one-artifact dispatch claim per Main's review-claim amendment. Added third native
budget purpose under the existing account lock/totals, exact PG terminal receipt
settlement, preserved lost-ack fences, existing Temporal/owned-native composition,
and a spawn-picklable shared CAMEL call/input/output/time boundary. Main's returned
backend timeout/retry/endpoint policy correction is included. Paid mode is off.

Main preserved the original20 handoff before this bounded correction1. The
queue-error path no longer uses a planned GET as release proof. Both release
paths now call the own store's locked close_undispatched transition, which
permanently closes only an unclaimed planned review before releasing money.
If the concurrent same-ID queue won, the permanent claim/attempt prevents release;
if close won, stale same-ID Start cannot later queue. Queued/uncertain/lost-ack
rows remain fenced and funded. No core SQL/catalog was changed; constraints were
tightened only in the new, not-yet-installed mf_native_launch migration.

The _record boundary now validates exact private frozen/config keys, bounded
finite JSON, canonical account identity/factory digest, exact typed row IDs and
booleans, state/dispatch_claimed/budget_attempt correspondence, claimed-only
workflow/native receipt, terminal receipt requirement, unclaimed cancelled
intent and safe error correspondence. A malformed row cannot become a usable
DTO or trusted factory binding. These checks remain UNVERIFIED static authoring.

Correction1 meaningful authored fixtures: deterministic same-ID failing queue
versus successful Start (pure and real PG); real PG locked close versus a
previous planned read's stale queue; distinct-review Start race checking funded
queued winner and only permanently closed/released loser; malformed private
configuration/lifecycle row negatives and valid closed/uncertain row cases.
Existing transport/shared sync+async policy negatives are retained unchanged.
Correction1 edits only native_launch_store.py, durable_native_launch_host.py,
native_launch_0001.sql, test_durable_native_launch.py, test_native_launch_store.py,
native-launch.md and this handoff, within the same assigned20 paths.

Static reads: latest root AGENTS.md and CONTINUATION.md; full backend packet,
launch draft, frozen details and review-claim amendment; accepted preparation
host/client/facade/API/store/contracts and generator fixtures; native request,
store/coordinator/supervisor/process driver/owned binding/prepared host/Temporal
contracts/host/activity/workflow sources and native fixtures; budget source and
original migration; KnowledgeScope/source/project fixtures; Flask app factories.
Read operations used Get-Content, rg and static Select-Object only. Writes used
apply_patch only, in these20 assigned paths:

1. backend/app/native_launch_api.py
2. backend/app/services/native_launch_client.py
3. backend/app/services/native_launch_facade.py
4. backend/app/services/durable_native_launch_host.py
5. backend/app/services/native_launch_models.py
6. backend/app/__init__.py
7. backend/app/knowledge_read_app.py
8. backend/tests/test_native_launch_api.py
9. backend/tests/test_durable_native_launch.py
10. backend/engine_tests/test_connected_preparation_native_launch.py
11. services/knowledge/src/mirofish_execution/native_launch_contracts.py
12. services/knowledge/src/mirofish_execution/native_launch_store.py
13. services/knowledge/src/mirofish_execution/budget.py
14. services/knowledge/src/mirofish_execution/budgeted_native_supervisor.py
15. services/knowledge/src/mirofish_execution/migrations/native_launch_0001.sql
16. services/knowledge/tests/test_native_launch_store.py
17. services/knowledge/tests/test_native_launch_budget.py
18. services/knowledge/tests/test_temporal_connected_launch.py
19. docs/architecture/native-launch.md
20. coordination/handoffs/U07d-connected-native-launch-backend.md

Authored unit fixtures have no execution marker; pure native budget
receipt/fingerprint semantics are included. Real PG cases carry postgres;
actual Temporal cases carry postgres/native_launch_temporal; actual connected
OASIS carries postgres/native_launch_engine. Selected cases require the explicit
disposable PG/Temporal environment and do not importorskip or call pytest.skip.
Reused existing fixture primitives do not replace the connected engine inputs:
that case generates fresh rich persona/config through the accepted U07c worker
and launches its own resulting artifacts via actual native adapters.

Specific static compatibility resolution: accepted budget SQL only allows
`mf1_[0-9a-f]{64}` group IDs, so an initial native textual group tag would have
violated that constraint. Final authoring retains original group format and uses
only a distinct native episode UUID/fingerprint/receipt. No previous SQL/checksum,
scope/ingestion/preparation episode mapping, package dependency or native bound
was changed. The own schema index implements Main's dispatch-claim amendment.

Limits to review: no imports, parsers, tests, builds, installs, Git, network,
browser, provider calls or runtime were executed here. CAMEL attribute/interface
compatibility, inherited rich fixture generation and all PG/Temporal/native
assertions are therefore UNVERIFIED. Main must supply/qualify the finite owned
Temporal bridge, actual backend transport and package provenance. Arbitrary
trusted factories/cooperative transports cannot be preempted by Python. No paid
price/spend, model-quality, browser zoom, report, fullU07/full44 or cleanup pass
is claimed. No public app deployment was performed.

All20 paths remain exclusive to this worker. After this stable handoff I stop
tools/edits and retain idle ownership until Main explicitly releases or requests
a bounded correction. Future direct human pause controls immediately.

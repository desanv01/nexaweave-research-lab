# U06b connected source graph — stable UNVERIFIED handoff

Worker assignment: exclusively `C:\Users\Dv\Desktop\MiroFish\_implementation_worktrees\u01`,
prepared by Main at accepted PR75 merge
`b9c6496ca20058c2aedd95991b62f6865dd8859b`, branch
`task/u06b-connected-source-graph`. The worker did not independently execute
Git/status/hash verification of that supplied base. Prior U04c remains retired
from all u01 writes. This handoff does not accept postmerge gates or a phase.

## Authored paths (exact assigned twelve)

1. `backend/app/__init__.py`: optional injected ingestion facade propagation.
2. `backend/app/knowledge_read_app.py`: research_local-only lazy registration.
3. `backend/app/source_ingestion_api.py`: fixed plan/execute/status routes,
   inherited auth/origin/preflight, strict bodies, configured source-only scope,
   default-off even with injection, safe errors, no-store/nosniff, DTO revalidation.
4. `backend/app/services/knowledge_ingestion_client.py`: stdlib-only bounded
   fixed installed sibling child, method/scope/frame checks, trusted execute
   whitelist and strict public DTO/complete receipt correlation.
5. `backend/app/services/knowledge_ingestion_facade.py`: lazy trusted environment
   snapshot, explicit host authorization, two finite slots, exactly one call.
6. `services/knowledge/src/mirofish_knowledge/source_ingestion_host.py`: existing
   persisted authority/bridge plan, operation status, actual BudgetedIngestion
   dispatch and lazy real Graphiti adapter after durable start. No fake-production
   executor; explicit constructor-only test seam. Fixed budget read uses the
   existing BudgetLedger private `_account`/`_row` typed parsers and transaction
   helper, because no public reservation getter exists; no core path was edited.
7. `services/knowledge/src/mirofish_knowledge/source_ingestion_bootstrap.py`:
   installed package-origin checks, provider-free cold startup, one bounded frame.
8. `backend/tests/test_source_ingestion_api.py`: authored pure HTTP/facade/cold
   tests, injection and receipt attacks, default-off and lost reply/no retry.
9. `services/knowledge/tests/test_source_ingestion_host.py`: authored pure
   strict config/plan/scope/frame/lazy provider/status uncertainty cases.
10. `services/knowledge/tests/test_source_ingestion_host_postgres.py`: authored
    real disposable PG with deterministic fake provider, settled restart,
    concurrency, ownership/account/cap/tombstone, plan conflict, uncertainty,
    start response loss and settlement failure/held ceiling cases.
11. `docs/architecture/connected-source-graph.md`: operator setup, DTOs,
    admission ceilings vs unknown actual bills, cold/default-off paths and limits.
12. This handoff.

## Behavior and material limits

Planning neither reserves money nor admits writes. Execution requires explicit
host enabled/model authorized/account/positive ceiling and configured model/
embedding/Neo4j settings. It reuses persisted accounts and both ledgers; requests
cannot alter those settings. Already settled duplicates validate receipt and
return without provider construction. Started/busy/cancelled/uncertain states
do not redispatch or release money. Actual provider is lazily imported only after
budget start and authorized coordinator dispatch; Graphiti/CAMEL/model SDKs are
absent from Flask startup and plan/status paths.

The existing32768-codepoint bridge eligibility is retained and exposed. No
long-document completeness, original-binary retention, semantic extraction
quality, actual billing, proven rollback or whole-capability acceptance is
claimed. Execute success requires both proven graph completion and settled
budget. Status distinguishes completed graph/uncertain money, and started money/
not-admitted graph. `model_calls_made` is unknown/null for operation results;
actual usage is always unknown/null. False completion flags do not prove no
effect. Independent authorization reads do not promise atomic revocation.

Status owns configured scope/project and account; it cannot perform fresh source
revision authorization for an operation without a supplied revision because the
existing knowledge/budget operation records contain no source-revision mapping.
It reports only typed operation/receipt identities, not retained-source metadata.
Plan/execute freshly authorize the exact retained revision through the bridge.
Adding source-to-operation persistence would require an unassigned ledger/core
schema path; it was not silently added. Main should assess this documented
boundary against the packet's status scope/project requirement before acceptance.

The Flask structural ontology precheck is stdlib-only; the installed host applies
actual strict OntologySpec with canonical/default field comparison. Provider
configuration follows the actual adapter, including its local_only policy and
finite call/token/concurrency budgets. Ceiling amounts are conservative admission
amounts and do not constitute guarantees about provider billing.

## Execution and acceptance

**UNVERIFIED.** The worker executed only source/coordination reads and writes to
the assigned twelve paths. No Python/import/test/lint/type/build/runtime/browser/
DB/network/Git/status/hash/audit/paid/public calls, delegation, worker messaging
or schedule changes were performed. No test pass, installed bootstrap execution,
HTTP/store/Neo4j compatibility or hosted acceptance is claimed. Settings remain
GPT-6.1 Sol/medium as assigned; Fast ON unavailable to set/verify.

Main owns all review, patch manifest changes, pure/inherited checks, actual
fresh-installed-child/HTTP/PG/Neo4j/fake-SDK qualification, CI/Git/integration and
acceptance. No runner/dependency/lock/core SQL/coordinator/provider/plan/registry
path was modified. No unassigned source boundary was edited. Worker is idle on
this stable handoff pending a specific bounded correction packet; a future
direct human pause takes precedence immediately.

## Correction1 — mandatory production adapter initialization (UNVERIFIED)

Main found the original production branch constructed the real adapter but
omitted its required `await initialize()`. Its driver therefore remained absent;
minimal factory fakes concealed this failure. Main reported backend187/pure13
passes but explicitly did not qualify that real lifecycle. The prior handoff and
test claims above remain historical unverified candidate records, not proof of
working production ingestion.

Correction1 source authorship is stable in exactly the five permitted paths:

- `services/knowledge/src/mirofish_knowledge/source_ingestion_host.py`
- `services/knowledge/tests/test_source_ingestion_host.py`
- `services/knowledge/tests/test_source_ingestion_host_postgres.py`
- `docs/architecture/connected-source-graph.md`
- This handoff

LazyGraphitiProvider now serializes first initialization, constructs the actual
adapter in the production branch, awaits initialization before publishing it,
and returns the same ready adapter for subsequent uses. Initialization remains
inside the coordinator's admitted ingest call after budget start, with the
existing finite coordinator/child deadlines. Lifecycle-aware explicit factory
fixtures initialize too; minimal existing deterministic fakes remain supported
only by the constructor seam, never as a production fallback.

Failed/cancelled initialization marks the wrapper terminal, attempts allocated
adapter cleanup, preserves the original initialization exception if cleanup
also fails, and never invokes ingest/proof or constructs a retry adapter.
Closing is terminal and discards the ready adapter before cleanup; a closed or
failed wrapper cannot be reused. The real adapter already performs its own
initialization-failure cleanup and has idempotent close. Existing coordinator
quarantine/budget-held-ceiling/no-automatic-retry behavior is preserved.

New pure test sources select the actual production construction branch with a
pinned fake module/class requiring initialization, assert ordering and one
initialization, forbid premature proof, verify cleanup and no reuse, and cover
ordinary/cancellation/cleanup-failure cases. An explicit lifecycle factory case
adds concurrent first-use coverage. New real PG test sources use a pinned
production-branch lifecycle adapter to read actual started/running durable rows
at initialization, and preserve/prove held ceiling with no dispatch or retry
after initialization failure; successful settled duplicates initialize no
second adapter. All prior assertions were retained.

**Still UNVERIFIED.** Only packet/source reads and the five assigned source/doc/
handoff edits were performed. No imports/tests/checks/builds/runtime/browser/DB/
network/Git/status/hash/audit/delegation/messages/schedules or paid calls occurred.
No production SDK, HTTP, installed child, PostgreSQL or Neo4j lifecycle pass is
claimed. Main owns all execution, review, manifests, CI/Git and acceptance. Worker
is idle pending a specific bounded correction assignment; later human pause
supersedes immediately. GPT6.1Sol/medium as assigned; Fast ON unverified.

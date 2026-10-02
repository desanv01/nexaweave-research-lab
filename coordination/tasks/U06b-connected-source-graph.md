# U06b — protected retained-source graph operations

PLANNING ONLY until Main assigns a separately named MiroFish project worker and
an exact accepted base in the current registry. No ownership is granted here.
Human resumed full implementation; this advances the approved source/graph
workflow and C04/C25/C29/C30/C31/C38 without claiming full capability acceptance.

## Architecture and concrete behavior

Extend the existing explicit research_local host with authenticated source-to-
graph planning, execution and operation-status endpoints. Reuse the existing
SourceIngestionBridge, BudgetedIngestion, BudgetLedger and knowledge Ledger;
do not invent another coordinator, budget store, provider contract or migration.
The result must connect a retained revision, a human-supplied strict OntologySpec
snapshot, one stable operation UUID and the configured graph/scope into the
actual Graphiti coordinator path. The UI will be a later separate U11 packet.

Default execution stays disabled. Planning/status are non-chargeable; fresh
source/project/binding authorization precedes them. Executing requires explicit
host opt-in, existing operator-created account for the configured principal and
project, a positive host-configured operation ceiling in USD microunits, explicit
model-call authorization, and configured provider credentials/endpoints. No
request may create an account, raise a cap, supply principal/scope/account/
ceiling/endpoint/credentials/script/interpreter or switch a provider. Missing or
invalid execution authorization must deny before provider construction/calls.
Budget amounts are admitted conservative ceilings, never actual provider bills
or a guarantee of exact charges; no implicit default authorizes model spending.

Suggested routes: POST /api/source/ingestion/plan/<graph_id>, POST
/api/source/ingestion/execute/<graph_id>, GET
/api/source/ingestion/operation/<graph_id>/<operation_id>. Keep inherited routes
and graphiti_readonly/legacy behavior unchanged. Fixed request keys for plan/
execute: schema_version, source_revision, operation_id, ontology. Validate all
UUIDs/types, duplicate keys/nonfinite JSON, strict OntologySpec/version/sizes,
configured graph/source-only scope, project/workspace/principal and tombstones.
No run/branch scope or request-selected SQL/Cypher. Plan returns bounded review
identities/fingerprint/evidence IDs/source lengths/ontology revision/operation
and deterministic episode identity, honest spending/ingestion flags, no source
content or secrets. Preserve the bridge's current32768-codepoint eligibility
limit explicitly; do not mislabel this slice long-document completeness.

Use a dedicated fixed sibling installed bootstrap under -I and a dedicated
bounded transport facade. Keep Flask's minimal host/provider-free startup and
plan/status cold paths free of Graphiti/CAMEL/model SDK initialization. The child
uses fresh persisted authorization, the existing account's actual principal/
project/cap, durable reserve/start/settle/quarantine semantics and the existing
ingestion coordinator/completion proof. Lazily construct the actual Graphiti
provider only after the budgeted execution path has granted dispatch. Credentials
cross only explicit trusted-host whitelist for execute; never echo them, URLs
with secrets, raw errors, source text or provider payloads in public DTO/logs.
Initial official DeepSeek deepseek-flash and separate explicit embeddings remain
configurable; preserve local_only policy and disabled Neo4j telemetry behavior.

Plan must not admit a graph write or start a money reservation. Execute uses the
same canonical plan/fingerprint and stable operation ID, fresh checks and
BudgetedIngestion.ingest. Completed idempotent receipt is validated; busy/
conflict/cancelled/uncertain states are explicit. Do not automatically retry
after child launch/lost response, release held ceilings after ambiguous writes,
or report timeout as proven rollback. Status GET checks owned configured scope
and project and reports typed existing ledger state/receipt safely, without
triggering a provider or graph/model mutation. Independent authorization reads
do not promise atomic revocation. Preserve inherited finite operation limits.

Use existing bearer/origin/preflight/no-store/nosniff boundaries even with an
explicitly injected Main facade. Public output and injected DTOs must be
revalidated, including scope/operation/episode/fingerprint identities, complete
receipt/evidence correlation and strict booleans. Fixed sanitized error codes,
bounded request/reply sizes and no raw traceback. Plan/status do not spend.

## Bounded worker paths — source authoring only

1. backend/app/__init__.py — optional injected ingestion facade propagation only.
2. backend/app/knowledge_read_app.py — research_local-only lazy registration.
3. backend/app/source_ingestion_api.py — new fixed protected routes/DTO checks.
4. backend/app/services/knowledge_ingestion_client.py — new bounded fixed child.
5. backend/app/services/knowledge_ingestion_facade.py — trusted settings/DTOs.
6. services/knowledge/src/mirofish_knowledge/source_ingestion_host.py — new owned
   plan/status and actual budgeted executor with lazy provider.
7. services/knowledge/src/mirofish_knowledge/source_ingestion_bootstrap.py — new
   installed stdlib entrypoint/cold isolation, fixed frames/allowlist.
8. backend/tests/test_source_ingestion_api.py — authored route/facade/cold tests.
9. services/knowledge/tests/test_source_ingestion_host.py — authored pure host,
   configuration/receipt/provider-denial/uncertainty tests.
10. services/knowledge/tests/test_source_ingestion_host_postgres.py — authored
    real PG/ledger integration with explicit deterministic fake provider, no
    real models; ownership, concurrency, completed/no-redispatch, changed plan,
    unknown response/start/settle and honest held ceilings/status.
11. docs/architecture/connected-source-graph.md — setup/API/limits, actual vs
    ceilings, default off, no original binary/semantic/fullphase claims.
12. coordination/handoffs/U06b-connected-source-graph.md — stable UNVERIFIED.

Main owns task packet, patch-manifest updates for original source, runner/CI,
all execution, actual HTTP/fresh installed child/PG/Neo/fake SDK assertions,
review/commits/push/PR/merge/acceptance and exact logs. Existing pure bridge/
budget/coordinator/provider suites remain intact; no assertion/deadline waiver,
dependency/lock/native/SQL/core coordinator/provider/plan/registry changes.
If a necessary behavior cannot fit these paths, report the precise boundary
before editing an unassigned path. No silent fake-production executor.

## Worker protocol and Main qualification

Use GPT-6.1 Sol/medium; Fast ON requested but cannot be set/verified. Only assigned
isolated checkout, no other writers. Worker may read source/write these paths
and authored tests, but must not execute imports/tests/checks/build/runtime/DB/
browser/network/Git/status/hash/audits/delegation/messages/automations. Return
stable unverified handoff and idle. A future direct human pause supersedes.

Main will prove no provider creation/calls on unauthorized/disabled/plan/status
and negative scope/budget/account cases; actual fresh child and HTTP/PG behavior;
real scoped Neo4j writes with explicitly injected deterministic fake SDK only
where fixture methodology is declared; ambiguity/no duplicate/held ceilings;
all inherited relevant routes/providers/budget/ledger/ingestion gates and eight
exact hosted jobs/logs. This can qualify connected dispatch/control behavior,
not semantic Graphiti extraction/model quality or whole C04/C29/C31/C38. Live
DeepSeek/embedding acceptance needs configured local secrets and a concrete human
spend cap first; no paid call, public deployment or fabricated acceptance.

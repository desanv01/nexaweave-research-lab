# Protected retained-source graph workflow

This bounded U06b candidate connects retained text to the existing authenticated
`research_local` host. It is worker-authored and UNVERIFIED until Main completes
review and execution. It does not accept full C04/C25/C29/C30/C31/C38 or any whole
phase. Vue, Flask, OASIS/CAMEL and Graphiti with self-hosted Neo4j remain the stack.

## Setup and explicit execution authorization

Use the existing isolated installed knowledge interpreter and `ReadHostSettings`
configuration. The read bootstrap must be the trusted installed
`site-packages/nexaweave_knowledge/read_bootstrap.py`; the ingestion client selects
only its fixed sibling `source_ingestion_bootstrap.py`, launched through the
existing owned transport with `-I`. There is no request-selected script,
interpreter, environment, endpoint, account, principal, project or ceiling.

Install the existing storage, knowledge and execution migrations explicitly.
Create the project, retain its extracted source text, bind the operator principal
and display graph to a source-only scope, and create the existing `BudgetLedger`
account through the operator setup flow. HTTP requests cannot create an account
or change its cap. The account's persisted principal, project and cap are read;
the reservation transaction repeats admission under the account lock.

Execution defaults OFF. The operator must configure all of:

- `KNOWLEDGE_INGESTION_ENABLED=true`
- `KNOWLEDGE_MODEL_CALLS_AUTHORIZED=true`
- `KNOWLEDGE_INGESTION_ACCOUNT_ID`: canonical UUID of that pre-existing account
- `KNOWLEDGE_INGESTION_CEILING_MICROUSD`: explicit positive decimal integer, at
  most `2**63-1`, admitted against the persisted account cap
- Explicit LLM base URL, model and API key under `KNOWLEDGE_LLM_*`; initial
  official DeepSeek configuration is `https://api.deepseek.com` / `deepseek-flash`
- Separate explicit embedding base URL, model, API key and dimension under
  `KNOWLEDGE_EMBEDDING_*`, plus the existing Neo4j URI/user/password

The existing provider's bounded call, token, concurrency, structured-output and
search-recipe settings remain configurable through the explicit execute child
whitelist. `local_only` continues to use the existing local endpoint policy;
there is no fallback to a remote provider in that profile. Graphiti telemetry is
disabled by the existing private environment and adapter.

Credentials pass only the trusted execute whitelist. Plan forwards only the
source authority/PG settings; status additionally forwards a configured account
ID if present. No LLM/embedding/Neo4j credentials are needed by plan or status.
Neither cold path imports the Graphiti adapter, model SDK or CAMEL. Flask remains
the minimal provider-free host. Invalid/disabled authorization, missing/wrong
accounts, mismatched projects and cap denials cannot construct a provider.

Ceilings are conservative admission amounts, **not provider bills**. The host
does not measure actual spend and always returns `actual_usage_microusd: null`.
The operator must choose a defensible ceiling before authorizing real models.
Writing this candidate or configuring fixtures does not authorize paid calls.

## Protected API

All routes inherit the bearer token, configured origins and preflight boundary.
Responses include `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`,
including errors. The routes exist only in `research_local`; inherited read-only
and legacy routes retain their behavior. Request and result bodies are bounded
at 256 KiB, plus 1 KiB private framing/HTTP envelope overhead. One facade allows
two concurrent child calls, each with the inherited owned transport and a finite
120-second operation deadline; the ingestion coordinator has a 60-second provider
deadline. These process-failure boundaries do not constitute a security sandbox.

`POST /api/source/ingestion/plan/<graph_id>` and
`POST /api/source/ingestion/execute/<graph_id>` accept exactly:

```json
{
  "schema_version": 1,
  "source_revision": "canonical UUID",
  "operation_id": "stable canonical UUID",
  "ontology": {
    "schema_version": 1,
    "revision": "canonical UUID",
    "entity_types": [
      {"name": "Person", "description": "A person", "attributes": []}
    ],
    "edge_types": []
  }
}
```

The UUID placeholders above must be replaced with canonical UUID strings. Use
the same operation ID, retained revision and exact ontology snapshot for a
duplicate. Duplicate JSON keys, nonfinite numbers, unknown keys, query options,
compressed/transfer-encoded bodies, invalid schema versions and invalid strict
ontology fields are rejected. Source planning freshly reads persisted ownership,
project/workspace/binding, tombstone and retained revision authorization. Run and
branch scopes are denied. No SQL/Cypher is supplied by a request.

Plan returns scope, operation and deterministic episode identity, request
fingerprint, retained text digest and lengths, ontology revision and evidence
IDs. It returns no source text, source name, secrets or provider payload.
`spending_authorized`, `graph_ingestion_executed` and `model_calls_made` are all
strictly false for planning. Planning neither reserves money nor admits a graph
write, even when execution is enabled on the host.

The existing bridge requires nonempty retained evidence and at most **32,768
Unicode codepoints**. The result explicitly reports that eligibility limit.
Longer retained sources are not ingestible in this slice; retention's larger
input limits do not imply long-document graph completeness. Retained text is not
the original binary; ontology/extraction semantics and model quality are not
qualified by these endpoints.

Execute invokes the existing `BudgetedIngestion` and `SourceIngestionBridge`
fresh-plan comparison, durable reserve/start transitions, knowledge coordinator,
actual lazy `GraphitiKnowledgeProvider`, completion proof and budget settlement.
The real provider is constructed and then `await initialize()` is completed
only when the admitted coordinator invokes ingest, after the durable budget
start and authorized dispatch callback. Initialization builds the adapter's SDK
clients, graph driver and indexes before ingest/proof can use them. The lazy
wrapper publishes only a successfully initialized adapter and initializes it
once. Failed or cancelled initialization attempts resource cleanup, preserves
the original failure and leaves that wrapper terminal; it cannot dispatch,
reinitialize or reuse an uninitialized/closed adapter. The existing coordinator
and budget adapter quarantine uncertainty and retain the ceiling. Successful
adapters are closed when the one-shot host finishes. Test
provider injection exists only as an explicit host-constructor seam; production
has no fake or environment-selected test executor.

Completed duplicates validate the persisted scope/episode/fingerprint/evidence
receipt and return without provider construction or redispatch. Changed plans
conflict. Busy, cancelled and uncertain operations are explicit sanitized errors.
An ambiguous child launch, timeout or lost response is never automatically
retried; no held ceiling is automatically released. A timeout does not prove
rollback. Public errors contain only a fixed code, never a traceback, raw source,
provider diagnostic, credential or endpoint.

`GET /api/source/ingestion/operation/<graph_id>/<operation_id>` accepts no body
or query keys. It freshly authorizes the configured principal, binding,
workspace/project and source-only scope, then reads existing typed knowledge and
configured account reservation records. The endpoint performs no model, graph
mutation or money reservation. Without a configured account it can report the
owned knowledge record; reservation fields are `not_reserved` / null. Unknown
operations return `not_found`.

Status reports separate `state` and `budget_state`, the admitted ceiling if
present, a validated complete receipt if proven, and unknown model-call/actual
spend fields as null. A completed graph with an uncertain settlement remains
`completed` / `uncertain`; it does not become a settled execute success. A started
budget with no admitted graph record remains `not_admitted` / `started`, with
held money and no receipt. False `graph_ingestion_executed` for an unfinished
operation means no validated completion is available, **not proof of no effect**.
Status does not recover a source revision from the knowledge ledger, which does
not store that mapping; execute/plan perform fresh retained-source checks.

Independent persisted reads and fresh checks do not promise atomic revocation.
The caller must preserve the stable operation ID after uncertainty and use an
explicit reconciliation procedure outside this slice. There is no retry,
release, cancel, account administration or reconciliation HTTP endpoint here.

## Qualification remaining with Main

The authored pure tests cover strict framing/configuration, model-free planning,
provider denial and lazy construction, sanitized outputs and correlated receipts.
The authored backend tests cover bearer/origin/default-off injection boundaries,
strict HTTP requests, DTO revalidation, legacy/read-only route absence, cold
startup and no transport retry. The disposable PG sources exercise actual owned
storage/bridge/budget/knowledge rows with explicit deterministic fake providers:
settled duplicate restart, changed plan, owner/account/cap/tombstone denial,
concurrency, provider uncertainty, missing start response and failed settlement.
These are test sources, not executed evidence.

Main must run pure and relevant inherited contracts, fresh installed child,
actual HTTP/PG and scoped Neo4j/fake-SDK control qualification and all eight exact
hosted jobs/logs, updating original-source patch manifests as required. The
candidate does not supply paid DeepSeek/embedding qualification, semantic
Graphiti extraction quality, full-phase acceptance, UI, public deployment or
original-binary retention claims.

Main's correction1 review identified that the original lazy wrapper omitted the
real adapter's mandatory initialization. Previously reported backend187/pure13
passes did not qualify that production lifecycle. Correction1 adds authored
production-construction-branch tests using a pinned lifecycle fake adapter,
requiring initialize before ingest/proof, one initialization across successful
uses, cleanup on failure/cancellation and no reuse after failure/close. Real PG
test sources assert initialization sees already-started budget and running write
admission, then prove held money/no dispatch/no retry on initialization failure.
The worker did not execute these tests. Main still owns actual installed-child,
HTTP/PG/Neo4j/canned-local-SDK qualification and all acceptance.

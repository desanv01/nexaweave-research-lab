# U01a compatibility notes

This is an isolated Python 3.12 package. It does not alter the inherited Flask, Vue, OASIS/CAMEL, Zep modules, manifests, locks or tests. Its source is new adapter code; no upstream file was copied. Graphiti core 0.30.2 is pinned. Neo4j candidate is `neo4j:5.26.31-community@sha256:5eb12ad77fa46ab73e23df9ea1f43f5c0f2a79523435577648e046be042b9b93`; Main must verify resolution and compatibility.

Graphiti source inspected at tag `v0.30.2`: `graphiti_core/graphiti.py`, `nodes.py`, `decorators.py`, `llm_client/openai_generic_client.py`, `llm_client/client.py`, `embedder/openai.py`, `cross_encoder/openai_reranker_client.py`, `search/search_config_recipes.py`, `telemetry/telemetry.py`, and prompt/model modules. The actual telemetry switch is `GRAPHITI_TELEMETRY_ENABLED=false`, set before importing Graphiti. Graphiti's `Graphiti.__init__` silently constructs default OpenAI LLM/embedder/reranker when clients are omitted. This adapter supplies all three. Its `add_episode` resolves a non-default group to a cloned database driver even for Neo4j; `CommunityGraphiti` overrides that method so all groups use Neo4j Community's `neo4j` database. This override needs version-specific regression tests on every Graphiti upgrade.

Initial generation route: configurable OpenAI-compatible base URL/model/key, defaulting to official DeepSeek `https://api.deepseek.com` and `deepseek-flash` only when a key is supplied. The explicit structured-output strategy defaults to `json_object`; the adapter validates parsed JSON against the supplied Pydantic response model. OpenAI SDK automatic retries and Graphiti generic-client retry wrapper are bypassed for bounded calls. Set `KNOWLEDGE_CALL_TIMEOUT_SECONDS`, `KNOWLEDGE_MAX_TOKENS`, `KNOWLEDGE_MAX_COROUTINES`, `KNOWLEDGE_TOTAL_LLM_CALL_BUDGET`, and `KNOWLEDGE_STRUCTURED_OUTPUT_MODE` to override their bounded defaults. Endpoint and Neo4j secrets use masked `SecretStr` fields. The call budget is process-local and not monetary admission control. U03 must bind it to durable usage accounting before paid operations. No model calls were made for this task.

Embedding endpoint, model, key and dimension are independently required. The adapter does not assume DeepSeek embeddings. Cross-encoder search requires its own endpoint/model/key. RRF keeps real BM25 and vector search without an LLM rerank call. The explicit reranker object passed to Graphiti in RRF mode uses the configured LLM route so Graphiti cannot instantiate a hidden default; its `rank` method should not be invoked by the RRF recipe. Deterministic LLM/embedder/reranker implementations exist only in integration fixtures. No Zep import occurs in the package.

The disposable Compose project binds Bolt to `127.0.0.1:17687` and browser HTTP to `127.0.0.1:17474`. The test password must be supplied explicitly and must be non-default; use a throwaway value only. The named volume is disposable test data. Do not point this package at application or production Neo4j data.

## Commands for Main (not run by U01 worker)

From the U01 worktree, Main can generate the lock and install this independent package with its chosen Python 3.12 toolchain, then run:

```powershell
$env:GRAPHITI_TELEMETRY_ENABLED = 'false'
uv sync --project services/knowledge --locked --extra test --python 3.12.13
./services/knowledge/.venv/Scripts/python.exe tools/run_knowledge_tests.py
$env:KNOWLEDGE_TEST_PASSWORD = 'u01-disposable-fixture-only-9274'
docker compose -f infra/compose.knowledge-test.yml up -d --wait
./services/knowledge/.venv/Scripts/python.exe tools/run_knowledge_tests.py --neo4j
docker compose -f infra/compose.knowledge-test.yml down -v
```

On Linux use `.venv/bin/python` instead. Main generated `uv.lock` and installed
the isolated Python3.12.13 runtime. The launcher strips inherited credentials,
sets telemetry off before imports and rejects non-loopback DNS/connections in
the test process. This is not an OS-level network sandbox. The `--neo4j` flag
requires the real local test database; without it, only contract tests run.
The final command removes this Compose project's disposable test volume, not
application data. Main's Windows contract run passed15 tests. Local Docker
stalled creating the test network; real Neo4j qualification proceeds in CI.

Main should run the integration test against the pinned combination, inspect generated nodes/edges and source provenance, verify the direct entity isolation case, and diagnose any fake-client signature or provider API mismatch. The integration fixture does not call external model or embedding APIs. The test deliberately fails when opted in without a running database/password. Main also owns all lint/type checks, dependency/license scans and live model qualification. A live qualification needs explicit keys, price/call cap, embedding dimension, reranker compatibility, and a small synthetic fixture; it must not be confused with the deterministic fixture test.

## Gaps and qualification risks

- The pinned Graphiti version's reference-time semantics can manufacture fact validity from recorded time. C07 is unqualified.
- The deterministic fixture cannot establish DeepSeek JSON quality, live embedding dimensions, reranker logprob compatibility, ranking quality, or costs.
- Neo4j ingest markers are not a durable PostgreSQL operation ledger; concurrent exactly-once submission and automated reconciliation are absent.
- Search result/community provenance and full historical filters are incomplete. Querying with a temporal filter raises explicitly.
- Physical database isolation is not used; grouping and post-filtering need Main's negative integration review. Application authorization remains U03's responsibility.
- The package provides an adapter, not a Flask cutover or operational service.

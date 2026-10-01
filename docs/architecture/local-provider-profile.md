# Opt-in local knowledge provider profile

U12a is a preparatory knowledge-service policy slice. `hybrid` remains the
default: the official DeepSeek generation URL and `deepseek-flash` default are
unchanged, with separately configured embeddings. All inherited capabilities
and the Vue/Flask/OASIS/CAMEL/Graphiti + self-hosted Neo4j Community stack remain
in scope. This slice does not establish complete C39 or whole U12 acceptance.

Set `KNOWLEDGE_OPERATING_PROFILE=local_only` explicitly to require local knowledge
endpoints. Unknown values fail. Generation and embeddings each require an explicit
URL, model and key. A compatible trusted local server may accept an explicit dummy
nonsecret key; the application does not manufacture one or borrow a cloud key.
There is no cloud fallback, install, download or real-model selection in this work.

Example environment values are placeholders, not installed models or credentials:

```dotenv
KNOWLEDGE_OPERATING_PROFILE=local_only
KNOWLEDGE_NEO4J_URI=bolt://127.0.0.1:17687
KNOWLEDGE_NEO4J_USER=neo4j
KNOWLEDGE_NEO4J_PASSWORD=<locally-configured-password>
KNOWLEDGE_LLM_BASE_URL=http://127.0.0.1:18080/v1
KNOWLEDGE_LLM_MODEL=<explicit-local-generation-model>
KNOWLEDGE_LLM_API_KEY=<explicit-local-server-key-or-dummy>
KNOWLEDGE_EMBEDDING_BASE_URL=http://127.0.0.1:18081/v1
KNOWLEDGE_EMBEDDING_MODEL=<explicit-local-embedding-model>
KNOWLEDGE_EMBEDDING_API_KEY=<explicit-local-server-key-or-dummy>
KNOWLEDGE_EMBEDDING_DIMENSION=<actual-local-embedding-dimension>
KNOWLEDGE_SEARCH_RECIPE=hybrid_rrf
KNOWLEDGE_TOTAL_LLM_CALL_BUDGET=32
KNOWLEDGE_CALL_TIMEOUT_SECONDS=30
KNOWLEDGE_MAX_COROUTINES=4
```

RRF uses the existing hybrid search recipe. Graphiti still requires a cross-encoder
client in its client bundle; its explicit fallback route is the configured local
generation URL/model/key. No default OpenAI client is constructed. For
`hybrid_cross_encoder`, add all three `KNOWLEDGE_RERANKER_BASE_URL`,
`KNOWLEDGE_RERANKER_MODEL`, and `KNOWLEDGE_RERANKER_API_KEY` values explicitly.
The reranker is Graphiti's OpenAI-compatible boolean classifier with logprob
responses, not a generic `/rerank` API. The local server must support that protocol.
Generation sets both Graphiti model selectors, including `small_model`, explicitly.
Embedding configuration retains its separate model/endpoint/dimension.

HTTP(S) admission accepts only literal `127.0.0.1` or `[::1]`, an explicit valid
port, and an optional simple fixed path prefix such as `/v1` or `/local/api`.
DNS names, including `localhost`, other 127/8 addresses, alternative numeric
spellings, IPv4-mapped IPv6, userinfo, queries, fragments, whitespace/control
characters, backslashes, percent escapes, empty internal segments and dot segments
are rejected. Paths use ASCII letters, digits, underscores and hyphens.
Transport requests must match scheme/host/effective port and the configured path
prefix by segment; HTTPX's removal of default :80/:443 is accounted for.
Encoded raw request paths are denied. HTTPX may normalize dot segments before
transport, so enforcement applies to the resulting outbound target. An escaped
prefix is rejected before DNS/connect. All 3xx responses are denied and closed,
including redirects to the same local origin.

Each owned HTTPX client uses a narrow guard wrapping an actual AsyncHTTPTransport,
`trust_env=False`, `follow_redirects=False`, and no HTTP transport retries.
The OpenAI clients keep `max_retries=0`; bounded generation retains total call
budget and response-schema validation. Proxy variables are not deleted globally.
Config exposes no custom client or transport injection. Injected Graphiti is denied
for explicit local-only config (and env-selected local-only initialization).

Neo4j local-only admission permits direct `bolt://` with one of the same literal
loopback hosts and an explicit port. It permits no path, DNS, userinfo, routing URI,
query or fragment. This initial profile supports **unencrypted local Bolt only**;
it does not support TLS Bolt or routed clusters. Graphiti telemetry is set false
before Graphiti imports, as in the accepted provider. This does not prove that
every imported package is incapable of network access.

Initialization failures attempt cleanup of every owned HTTP client/transport and
any constructed driver. Cleanup continues after a resource fails and preserves
the original initialization exception. Explicit close is idempotent; a close
error is returned after the remaining resources receive cleanup attempts.
The provider is terminal after close or failed initialization.

The authored tests use fixed synthetic HTTP responses with actual HTTPX/OpenAI
clients. The opt-in fresh-process test initializes actual Graphiti/Neo4j indices
on the retained disposable fixture and executes simple structured generation,
embedding and reranking operations. Test-only socket observation rejects outbound
DNS/connect attempts outside the literal loopback fixture ports. It reads a fixed
`RETURN 1`, adds no group data, and never deletes fixture data or indices. Index
initialization is the existing idempotent provider operation. These are protocol
fixtures and process-local observations, **not real inference, quality, latency,
or host-wide privacy qualification**. No tests have been executed by the worker.

A trusted local server can itself forward requests to the cloud. This policy
constrains the knowledge process's configured requests, not that server's behavior.
It installs no OS firewall/container isolation and does not cover Flask, parsers,
OASIS/CAMEL, Temporal, native processes or host networking. No model runner or local
model was installed/downloaded, and no GTX1650 quality or performance recommendation
is made. Full C39 still needs a declared real local model and end-to-end
parser/native/fallback/telemetry qualification with cloud access denied at the host
boundary. Paid calls still require a concrete cap and local secret configuration;
no public deployment is implied.

Background documentation links supplied in the U12a packet:
[HTTPX environment](https://www.python-httpx.org/environment_variables/),
[HTTPX transports](https://www.python-httpx.org/advanced/transports/),
[Neo4j connection modes](https://neo4j.com/docs/python-manual/current/connect-advanced/).
The worker did not access network documentation or execute provider calls.

Main's fixture configuration disables Neo4j usage reporting with
`NEO4J_dbms_usage__report_enabled=false`. Main's qualification launcher requires
the actual pinned server's `dbms.usage_report.enabled` setting to be false before
running the new Neo4j checks. The retained local fixture's original configuration
is preserved, its actual setting was verified through the real driver, and its
data/volume remain intact. This addresses issue69's reporting default, not all
possible server/host egress. Configure other installations separately. See
[Neo4j reporting setting](https://neo4j.com/docs/operations-manual/current/configuration/configuration-settings/#config_dbms.usage_report.enabled).

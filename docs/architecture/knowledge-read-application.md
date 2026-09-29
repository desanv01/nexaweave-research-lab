# Graphiti read-only application mode (U03i)

`MIROFISH_APP_MODE=graphiti_readonly` selects a separate Flask factory before legacy graph, simulation, report, and runner initialization. The default `legacy` mode is unchanged. This mode serves a graph view and bounded entity context only. It has no generation, mutation, simulation, or report routes and does not fall back to Zep. It needs no LLM, embedding, or reranker key. Bind only to loopback; debug mode and a nonloopback `FLASK_HOST` are rejected at startup.

## Trusted settings

Set these in the server's private environment. The native process passes only `KNOWLEDGE_*` database, graph, principal, display ID, and bound scope settings to the private child. The bearer token is never forwarded.

| Variable | Meaning |
|---|---|
| `MIROFISH_APP_MODE` | `graphiti_readonly` |
| `FLASK_HOST` | `127.0.0.1` (default), `::1`, or `localhost` |
| `MIROFISH_ALLOWED_ORIGINS` | Exact comma-separated HTTP(S) browser origins; CORS is not authentication |
| `KNOWLEDGE_PYTHON` | Absolute executable path to the installed knowledge environment's Python |
| `KNOWLEDGE_BOOTSTRAP_SCRIPT` | Absolute regular file path to that environment's installed `site-packages/mirofish_knowledge/read_bootstrap.py` |
| `KNOWLEDGE_READ_TOKEN` | Private printable bearer token, 32–256 characters, generated with a cryptographic RNG |
| `KNOWLEDGE_PRINCIPAL` | Trusted host principal, not a request field |
| `KNOWLEDGE_DISPLAY_GRAPH_ID` | One bound legacy display ID, ASCII letters/digits/underscore/hyphen |
| `KNOWLEDGE_BOUND_SCOPE_JSON` | Exact full v1 canonical scope JSON, including nullable run/branch IDs |
| `KNOWLEDGE_PG_HOST`, `KNOWLEDGE_PG_PORT`, `KNOWLEDGE_PG_DATABASE`, `KNOWLEDGE_PG_USER`, `KNOWLEDGE_PG_PASSWORD` | PostgreSQL runtime connection; use a limited role |
| `KNOWLEDGE_NEO4J_URI`, `KNOWLEDGE_NEO4J_USER`, `KNOWLEDGE_NEO4J_PASSWORD` | Neo4j read connection and credentials |

Example bound scope shape (replace the identifiers with provisioned canonical UUIDs):

```json
{"schema_version":1,"workspace_id":"00000000-0000-0000-0000-000000000001","project_id":"00000000-0000-0000-0000-000000000002","graph_id":"00000000-0000-0000-0000-000000000003","run_id":null,"branch_id":null,"layer":"source"}
```

Provision explicitly with an operator-controlled Python process before starting Flask. Migration and runtime connections are separate: the migration owner installs schema, then the binding is created once by a trusted operator. This code is illustrative and reads locally configured secrets; it is not an HTTP endpoint.

Install the knowledge package as a regular, non-editable wheel in the isolated interpreter. Its bootstrap path must be the installed file under `site-packages`; a source checkout or editable `uv` install path is intentionally rejected because the child runs with Python `-I` and does not load source directories from `sys.path`.

```python
import json
import os
import psycopg
from mirofish_knowledge.bindings import ScopeBindingStore
from mirofish_knowledge.contracts import KnowledgeScope
from mirofish_knowledge.operations import migrate

with psycopg.connect(os.environ["KNOWLEDGE_MIGRATION_DSN"]) as owner:
    migrate(owner)

def runtime_connection():
    return psycopg.connect(
        host=os.environ["KNOWLEDGE_PG_HOST"],
        port=int(os.environ["KNOWLEDGE_PG_PORT"]),
        dbname=os.environ["KNOWLEDGE_PG_DATABASE"],
        user=os.environ["KNOWLEDGE_PG_USER"],
        password=os.environ["KNOWLEDGE_PG_PASSWORD"],
        connect_timeout=3,
    )

scope = KnowledgeScope.model_validate_json(os.environ["KNOWLEDGE_BOUND_SCOPE_JSON"])
ScopeBindingStore(runtime_connection).bind(
    os.environ["KNOWLEDGE_PRINCIPAL"], os.environ["KNOWLEDGE_DISPLAY_GRAPH_ID"], scope
)
```

With the variables set in the local environment, start `python backend/run.py`. A local client can request:

```powershell
$auth = "Authorization: Bearer $env:KNOWLEDGE_READ_TOKEN"
curl.exe -H $auth http://127.0.0.1:5001/api/graph/data/$env:KNOWLEDGE_DISPLAY_GRAPH_ID
curl.exe -H $auth http://127.0.0.1:5001/api/graph/entities/$env:KNOWLEDGE_DISPLAY_GRAPH_ID?types=Person
curl.exe -H $auth http://127.0.0.1:5001/api/graph/entity/$env:KNOWLEDGE_DISPLAY_GRAPH_ID/00000000-0000-0000-0000-000000000010
```

`GET /health` returns mode and capability names without credentials or endpoints. `GET /api/graph/data/<graph_id>` returns `{"success":true,"data":{graph_id,nodes,edges,node_count,edge_count}}`. `GET /api/graph/entities/<graph_id>` returns the inherited `FilteredEntities.to_dict()` shape: `entities`, `entity_types`, `total_count`, and `filtered_count`; optional `types=Person,Company` and `enrich=false` control collection filtering and edge enrichment. `GET /api/graph/entity/<graph_id>/<entity_uuid>` returns `EntityNode.to_dict()` with `uuid`, `name`, `labels`, `summary`, `attributes`, `related_edges`, and `related_nodes`. Missing entities and unknown display bindings return fixed 404 errors. Missing and wrong bearer tokens return the same 401 response. Other failures use fixed error codes without database or transport details; `busy` is 409 and unknown transport is 503.

Each page is a one-shot installed child process. It allows only `page` commands, checks the exact persisted principal/display/scope binding, obtains a PostgreSQL session shared read lock, commits the scope/admission check, then reads Neo4j with bounded queries. Session-level statement and lock timeouts cover lock acquisition, liveness, and release; the short validation transaction has no provider work. Writers acquire a matching exclusive transaction try-lock before claim or tombstone. The session lock is released after the page has passed validation. No PostgreSQL transaction spans Neo4j work. The graph reader shares bounded limits across page scans; multi-page results remain live reads without a snapshot. A lost PostgreSQL connection discards the result but cannot provide exact distributed fencing. This mode supports one configured binding and does not yet connect generation, simulation, reports, organization roles, or public deployment.

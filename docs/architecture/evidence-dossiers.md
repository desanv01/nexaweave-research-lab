# Model-free retained evidence dossiers (U09a)

`nexaweave_knowledge.evidence_dossier.EvidenceDossierService` connects ordered
research sections to the accepted U06 `EvidenceResearchService`. Trusted host
construction supplies principal, PostgreSQL/Neo4j factories and an optional
exact graph anchor; requests supply persisted display IDs, title, 1–6 ordered
heading/query/top-k sections and optional aware valid/recording cutoffs. Request
data never selects credentials, scope, tools, model, filesystem paths or queries
in database languages. Free text is passed only to the accepted lexical ranking
operation; it is never executed as SQL/Cypher/tool instructions.

The local entry point is `python -m nexaweave_knowledge.dossier_cli`. It reads one
UTF-8 JSON request, at most 32 KiB, and returns one JSON line, at most 4 MiB
including its newline, using the existing `ReadSettings` trusted environment.
Duplicate keys, extra fields, nonfinite JSON, naive timestamps and invalid bounds
fail before configuration access. There is no listener, output-path option,
on-disk report writer or executable export bundle. Fixed errors expose no DSN,
source paths, provider secrets or underlying exception text.

Example request (the trusted caller substitutes existing authorized display IDs):

```json
{
  "schema_version": 1,
  "title": "Retained evidence review",
  "display_graph_ids": ["persisted_source_display", "persisted_simulation_display"],
  "sections": [
    {"heading": "Source and simulation observations", "query": "Alice", "top_k": 10},
    {"heading": "Additional retained evidence", "query": "WORKS_FOR", "top_k": 10}
  ]
}
```

Each section makes exactly one sequential call to the actual accepted research
service. Its owner/binding/ledger/source authorization, conservative historical
admission, direct bounded Neo4j reads and retained PostgreSQL citation resolution
remain in force. U09a does not construct Graphiti search/model clients, embeddings,
cross-encoders or a narrative model. No provider calls are required.

The dossier adds a 60-second cooperative overall deadline and a same-instance
inflight guard. Async research awaits are bounded by remaining overall time;
assembly checks the deadline between records and before returning. Synchronous
serialization/assembly and operating-system scheduling cannot provide hard
preemption. The inherited research worker shields cleanup after cancellation and
retains its existing two-worker admission cap/30-second per-query limit. Dossiers
add no executor, retry or background worker. A timeout never authorizes publishing
a partial dossier. Already-running inherited cleanup can outlive caller timeout;
the dossier does not claim OS-wide termination or closure proof. Cumulative raw
research-response JSON is also capped at 4 MiB before additional sections are
assembled. A large sequence may fail this work budget even if record deduplication
could have reduced the final export; the operation does not omit a requested
query or silently clip its response to fit.

Every query is individually guarded. The sequence is **not an atomic graph
snapshot**. Scope-binding changes between responses, inconsistent duplicate facts
and conflicting immutable evidence fail closed. Different facts may still reflect
different read times; matching records do not prove global snapshot consistency.
The response includes this limitation and records each normalized request digest,
response digest, query/cutoffs, exact per-display scope, scan/excluded/unknown/
truncation counts, lexical rank basis and competing claim candidates. Historical
semantics remain retained-edge filtering, not bitemporal reconstruction.

Claim keys hash full scope (workspace/project/graph/layer/run/branch), edge kind and
retained provider identity. Reference keys also include that scoped claim and
evidence identity. Therefore same provider/evidence identifiers in different
layers/runs/branches produce distinct claim/reference records. Repeated identical
scoped claims reuse records; repeated evidence identities must have identical
retained citation data, and source-revision metadata must agree. Missing citations
are explicit unavailable references; claims without links say `no_evidence_links`.
Retained claim text, predicate, source/target, episode/evidence identities and all
timestamps are preserved. Query-specific ranking is recorded by response digest
and rank basis rather than presented as semantic confidence.

Source claims, simulation observations and other layer claims have separate
section lists. Claims come only from retained returned facts. Same-subject/
predicate candidates remain query-associated review candidates, with no semantic
contradiction or truth judgment. `model_generated=false`,
`semantic_judge_used=false` and `claim_support_status=not_reviewed` are fixed.
Resolved references establish retained reference integrity, not that the passage
semantically supports the assertion. Simulation observations are not real-world
predictions. Source extraction is not truth acceptance.

Numerical summaries distinguish distinct scoped claim-reference links from
summed query link counts. Per-query scan totals count repeated scans; they are
not unique whole-graph counts. Coverage groups citations by authorized project
and retained source revision and unions half-open Unicode codepoint intervals,
deduplicating overlaps across facts, layers and queries. Fractions divide this
union by retained codepoint length; they are **retrieved passage coverage**, never
long-document understanding/ingestion coverage. Truncation and unknown counts
remain explicit. Output overflow fails; no clipping substitutes for completeness.

Markdown is generated from validated request/claim/reference/trace/summary records
in deterministic section order. Untrusted punctuation becomes numeric entities
before Markdown parsing, including HTML, links, autolink punctuation, headings,
fences and image syntax. Controls and format characters are visible U+ markers.
Exact original excerpts remain in structured JSON. Consumers must use a normal
safe Markdown parser without pre-decoding entities into Markdown source; do not
inject structured strings as raw HTML. No raw HTML rendering or JS execution is
part of this mode. Reference IDs are plain text, not active external links.

Input, trace and record SHA256 digests use canonical sorted compact UTF-8 JSON;
the record digest covers ordered sections, sorted claims/references and summary.
They support review/reproducibility, not signing, authenticity or model quality.
Markdown and fixed limitations are export fields outside the record digest.

Pure and actual PG/Neo4j/fresh subprocess test source is included for Main to
execute. Real tests reuse the accepted U06 retained fixture and its loopback-only
child guard/passive model-search/driver/PG-close observations, changing only the
CLI module entry point. Passive observations are process-local, not OS-wide
network or semantic guarantees. Worker execution/qualification has not occurred.

This is a bounded U09 evidence dossier mode. Existing ReportAgent iterative
planning/tools/sections/interviews/follow-up and KnowledgeReportTools barriers are
unchanged. AI narrative quality, semantic support acceptance, interviews/surveys,
full cross-run comparison and whole U09/all 44 capability acceptance remain open.
Vue/Flask/OASIS/CAMEL, Graphiti/self-hosted Neo4j Community, inherited notices and
the future configurable DeepSeek official `deepseek-flash`/separate embeddings
policy remain unchanged. No paid calls or public deployment are implied.

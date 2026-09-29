# Basic graph to OASIS population

The `graphiti_readonly` loopback app can prepare a small, synthetic OASIS population from the accepted bounded knowledge reader. It does not write graph facts, create simulation runs, or call a model. The default legacy `OasisProfileGenerator` remains the enriched path; this workflow invokes its explicit `basic_only=True` rule path and passes a request-local `random.Random(seed)` instance. Read-mode startup and basic generation require no Zep or model key.

## API

Both routes require the read token and existing Origin policy. The URL graph ID must match the host's one configured display graph ID before any work begins.

- `POST /api/graph/population/<graph_id>/preview` returns the usual `{success,data}` envelope. `data` contains `profiles`, source `grounding` keyed by entity UUID, eligible and selected counts, `synthetic_fields`, generator and enrichment markers.
- `POST /api/graph/population/<graph_id>/export` returns an attachment. `platform="twitter"` yields UTF-8 CSV with the inherited OASIS loader columns `user_id,name,username,user_char,description`. `platform="reddit"` yields a UTF-8 JSON array from inherited `to_reddit_format()`.

The JSON body permits exactly `types` (optional unique list of at most 50 identifier labels), `max_agents` (integer 1–100, default 10), and `seed` (integer 0–4294967295, default 0). Export also requires `platform`, exactly `twitter` or `reddit`. The body is limited to 16 KiB. Malformed JSON, duplicate or unknown keys, invalid types, and boolean numbers are rejected before reading. Preview and download bodies are limited to 2 MiB of UTF-8 output. Empty selection returns `empty_selection` (HTTP 422).

## Derivation and provenance

One `graph_data` call scans the bound graph's nodes and edges under the accepted reader's paging, scope, and consistency limits. Nodes with a custom label beyond `Entity`/`Node` are eligible; a requested type must match a label exactly. Eligible nodes sort by UUID, then the first `max_agents` are selected. Their names, first custom type, and summaries pass through the inherited basic profile method. The separate preview grounding manifest preserves source labels, summary, attributes, episode and evidence IDs, and incident edge facts with their episode/evidence IDs. Exported platform files contain simulator profiles, not source evidence; retain the preview if provenance is needed.

Age, gender, country, personality, topics, account handles, and engagement counts are synthetic assumptions. If inherited username generation collides, the population workflow appends a deterministic numeric suffix to keep the selected accounts unique. `enrichment="none"`, `llm_used=false`, and `simulation_executed=false` make the boundary explicit. A seed reproduces profiles for the same graph projection and generation date without changing Python's global random state. `profile_date` and each `created_at` use the host's current local date; output across dates therefore differs. Node and edge scans share one bounded read operation, but the live graph has no global transaction snapshot; `snapshot_consistent=false` states that limit. Profiles must not be interpreted as current or historical facts.

CSV preserves inherited simulator fields, Unicode, quotes, and escaped line breaks. It is simulator data, not a spreadsheet-safe format for untrusted text. Opening it in a spreadsheet can interpret formulas. This route does not provide LLM/search-enriched personas, paid generation, a full simulation preparation flow, or the full application cutover.

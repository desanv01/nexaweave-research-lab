# Approved execution baseline — version 3.0

Execution authorized by the user. The following is the complete approved attachment; its statements about not changing project state refer to its original planning conversation. Follow the execution ledger for current state.

**Locked for this updated plan: Graphiti + self-hosted Neo4j Community Edition will replace Zep Cloud as the primary knowledge system. Paid LLM APIs remain allowed.**

The project remains an attributed, capability-preserving MiroFish upgrade—not a new, reduced simulator built from scratch. Vue, Flask, OASIS/CAMEL, the existing investigative report workflow, and the existing generators remain the starting point.

I reread your attachment and the supporting capability, architecture, GitHub, and worker-protocol documents. Below is the complete consolidated plan with the Zep replacement integrated into the architecture, early phases, acceptance criteria, costs, and deployment modes.

This response updates the plan **here in the conversation**. It does not modify the saved planning files, create a repository, change model settings, or dispatch workers.

# MiroFish Research Lab — consolidated execution plan

## 1. Objective and locked decisions

Build an upgraded MiroFish-derived research platform that preserves its existing end-to-end capabilities and adds stronger evidence tracking, experiment controls, recovery, security, usability, and measurable efficiency.

The defining requirement is:

> Preserve the useful application, replace the mandatory hosted knowledge service with infrastructure we operate, and prove that the resulting workflows remain complete and useful.

### Locked architectural direction

| Area | Decision |
|---|---|
| Development strategy | Reuse and incrementally upgrade MiroFish with attribution |
| Frontend | Retain Vue, Vite, D3, routing, and localization |
| Backend | Retain Flask; introduce typed boundaries and validation |
| Simulation engine | Retain OASIS/CAMEL; instrument and patch where necessary |
| Knowledge framework | **Graphiti** |
| Graph database | **Self-hosted Neo4j Community Edition** |
| Zep Cloud | **Not required for installation, normal operation, or release acceptance** |
| Language models | Paid APIs allowed; provider/model selection remains configurable |
| Application database | Self-hosted PostgreSQL |
| Durable orchestration | Self-hosted Temporal, introduced around stabilized operations |
| Document extraction | Retain PyMuPDF initially under applicable terms |
| Reports and interactions | Preserve existing investigative reports, interviews, surveys, and follow-up chat |
| Development workflow | Main chat plans, reviews, checks, integrates; medium-effort workers implement |
| Completion standard | All 24 inherited capability requirements and all 20 planned upgrades accepted |

Hindsight, Cognee, LightRAG, Mem0, FalkorDB, and other alternatives are **not parallel implementation tracks**. The knowledge-stack decision is settled unless execution reveals a material blocker that requires a new decision.

Locking the choice means committing to implement and qualify it—not declaring its performance or compatibility proven before testing.

## 2. What changes from the attached plan

| Previous plan | Updated plan |
|---|---|
| Zep remains the initial operational provider | Graphiti/Neo4j becomes the first supported operational provider |
| Complete paid Zep baseline precedes modernization | Source-based characterization and provider replacement proceed without requiring Zep payment |
| Local graph replacement is investigated late | Graph integration and qualification become early critical-path work |
| PostgreSQL handles application records | Unchanged; Neo4j separately stores graph artifacts |
| U12 introduces a local graph/model stack | U12 qualifies **fully local inference and no-cloud operation**; the graph is already self-hosted |
| Zep-related charges remain part of normal operation | No mandatory Zep subscription or ingestion charges |
| Provider comparisons assume hosted access | Live Zep comparisons are optional, separately authorized evidence—not a prerequisite |

An important correction follows from this: importing the original source will **not immediately produce a fully working Zep-free application**. There will be an early transition period while the knowledge integration is replaced.

The first complete operational milestone occurs when the inherited workflows work through Graphiti/Neo4j—not merely when the source import succeeds.

## 3. Project identity, workspace, and source provenance

Carry forward the working defaults:

- **Working name:** MiroFish Research Lab.
- **Proposed repository:** `desanv01/mirofish-research-lab`.
- **Initial visibility:** private.
- **Proposed implementation folder:** `C:\Users\Dv\Desktop\MiroFishResearchLab`.
- **Existing reference folder:** `C:\Users\Dv\Desktop\MiroFish`.

“Separate project” means a separate implementation folder and Git repository. It does **not** mean discarding MiroFish or starting a different product. It keeps the reference archive and research material separate from the code being upgraded.

The supplied ZIP remains the fixed source baseline. Its identity will be reverified during U00. Current upstream changes will be evaluated separately and recorded by commit.

Implementation rules:

1. Import verified source rather than copying an entire previously used environment.
2. Exclude caches, installed packages, credentials, uploads, and simulation data.
3. Preserve upstream history where its relationship to the archive can be established.
4. Otherwise record an honest snapshot import with its archive hash.
5. Adopt upstream fixes through reviewed changes.
6. Never treat a moving upstream branch as an immutable baseline.

The main chat must inspect actual repository/workspace state before execution rather than assuming nothing has happened since this plan was written.

## 4. Licensing and attribution

This remains a **derived-project plan**.

Replacing Zep does not remove the license applying to reused MiroFish source. Preserve its license and notices unless separately reviewed permission grants different terms. Noncommercial intent alone is not a substitute for those terms. [MiroFish license](https://github.com/666ghj/MiroFish/blob/main/LICENSE)

The selected knowledge components have separate licenses:

| Component | Licensing basis to record |
|---|---|
| MiroFish source | Applicable inherited terms and any separately verified grant |
| Graphiti | Current repository: Apache-2.0 |
| Neo4j Community Edition | GPLv3 |
| PyMuPDF/MuPDF | Applicable AGPL or commercial terms |
| Other dependencies | Their individual pinned-version licenses and notices |

Sources: [Graphiti license](https://github.com/getzep/graphiti/blob/main/LICENSE), [Neo4j licensing](https://neo4j.com/open-source-project/), [PyMuPDF licensing](https://pymupdf.readthedocs.io/en/latest/about.html).

Release preparation includes:

- Accurate source attribution and change history.
- Dependency inventory and SBOM.
- Required license texts and notices.
- Corresponding-source delivery where applicable.
- Review of redistributed containers, binaries, and patched dependencies.
- No implied upstream endorsement.
- No claim that the entire application is Apache-2.0 merely because Graphiti is.

Any public or commercial distribution needs its actual packaging and permissions reviewed.

## 5. All 24 inherited capabilities remain mandatory

These requirements carry forward. Provider-specific implementation details may change; user-visible functionality cannot silently disappear.

| ID | Required inherited capability |
|---|---|
| C01 | Multi-file PDF, Markdown, and text ingestion |
| C02 | Long-document handling, including important material outside the beginning |
| C03 | Generated typed ontology with validated entities, relationships, and attributes |
| C04 | Graph construction with partial-failure handling and write reconciliation |
| C05 | Hybrid semantic/keyword graph research with evaluated ranking |
| C06 | Entity details, relationships, and multi-step investigation |
| C07 | Current, historical, and superseded claim inspection |
| C08 | Simulation activity incorporated into the research graph |
| C09 | Individual and organizational agent profiles |
| C10 | Automatic scenario, population, and environment setup |
| C11 | Each supported simulation environment running independently |
| C12 | Both environments running concurrently with honest partial-failure reporting |
| C13 | Existing Twitter-like action catalog |
| C14 | Existing Reddit-like action catalog |
| C15 | Platform recommendation and exposure behavior |
| C16 | Heterogeneous activity schedules and time-dependent participation |
| C17 | Agent context and memory with appropriate information boundaries |
| C18 | Interactive graph, profiles, live feeds, logs, and progress |
| C19 | Investigative reports using repeated research tools |
| C20 | Individual and batch agent interviews |
| C21 | Synthetic surveys |
| C22 | Tool-assisted report follow-up chat |
| C23 | English and Chinese user journeys |
| C24 | History, reports, configuration/profile exports, and safe existing downloads |

Each requirement receives:

- Source references.
- Test fixtures and expected behavior.
- Supported operating modes.
- Responsible phase and PR.
- Accepted revision and evidence.
- Known limitations.

Statuses distinguish **source observed**, **implemented but unverified**, and **accepted**.

A successful ten-agent smoke test is useful evidence, but it does not establish complete feature coverage.

## 6. All 20 planned upgrades remain in scope

| ID | Planned improvement |
|---|---|
| C25 | Document-version, passage, event, and report-claim provenance |
| C26 | Distinct source, assumption, simulation, and analysis graph layers |
| C27 | Reviewable, editable, versioned ontologies and populations |
| C28 | Configuration settings with demonstrated execution effects |
| C29 | Central usage accounting, admission limits, and cost controls |
| C30 | Authentication and resource ownership |
| C31 | Durable execution and safe failure reconciliation |
| C32 | Recorded playback and reconstruction of analytical views |
| C33 | Real checkpoint pause/resume |
| C34 | Parent-preserving counterfactual branches |
| C35 | Ensembles and sensitivity analysis |
| C36 | Optional cross-platform information bridge |
| C37 | Evidence-aware comparative reports |
| C38 | Guided and advanced workbench modes |
| C39 | Fully local/private operating profile |
| C40 | DOCX, scanned-PDF OCR, and improved table handling |
| C41 | Portable research bundles |
| C42 | Maintainable interfaces, CI, delivery, and release provenance |
| C43 | Malay user interface |
| C44 | Measured performance improvements |

The newly locked knowledge stack implements the relevant inherited requirements early. It does not replace or excuse these upgrades.

## 7. Target architecture and component responsibilities

The application remains modular. We will not introduce unrelated services merely to make the architecture look more sophisticated.

| Component | Responsibility |
|---|---|
| Vue workbench | Guided workflow, graph exploration, simulation monitoring, reports, interactions |
| Flask API | Request validation, authentication, ownership checks, bounded operations |
| Application services | Projects, sources, populations, experiments, reports, provider coordination |
| PostgreSQL | Application records, versions, ownership, evidence references, operation ledger, usage, audit |
| Knowledge workers using Graphiti | Extraction, graph updates, retrieval, and graph operations |
| Neo4j Community | Persistent entities, relationships, episodes, indexes, and temporal graph artifacts |
| OASIS/CAMEL workers | Native simulation behavior and agent context |
| Native engine storage | Engine-owned social/environment state |
| Temporal | Durable coordination of operations whose retry/recovery behavior is understood |
| Storage abstraction | Documents, exports, recorded artifacts, supported checkpoint bundles |
| Model-provider boundary | Generation, embeddings, structured output, usage and bounded retries |

### Separate runtime compatibility

The archived application requires Python below 3.13 and pins older OASIS/CAMEL dependencies.

Therefore:

- Reproduce a compatible Python 3.11/3.12 baseline.
- Pin a tested Graphiti/Neo4j combination.
- Isolate knowledge-worker dependencies from the simulation runtime where necessary.
- Do not force incompatible dependencies into one environment.
- Do not combine major dependency upgrades with the initial provider cutover.

The exact deployment boundary—an internal worker process or service—is resolved in the early compatibility work. Neither choice requires rewriting Flask.

### Storage ownership

Each field has one authoritative owner.

- PostgreSQL owns application metadata and operation records.
- Neo4j owns the stored knowledge-graph representation.
- OASIS storage owns native engine state.
- Source files and retained extraction artifacts preserve the evidence needed for inspection and supported reconstruction.

A PostgreSQL transaction does not automatically cover Neo4j, SQLite, model calls, or files. Cross-system work needs explicit operation IDs, completion records, and reconciliation.

## 8. The Graphiti/Neo4j replacement contract

Graphiti provides temporal graph construction, custom types, incremental ingestion, provenance, and hybrid retrieval. These are the relevant foundations for replacing the knowledge capabilities MiroFish uses. It is not the whole managed Zep service. [Graphiti documentation](https://github.com/getzep/graphiti)

The application will access it through a provider-neutral `KnowledgeProvider` boundary.

### Required operations

| Operation family | Required behavior |
|---|---|
| Lifecycle | Create, inspect, export, and delete scoped knowledge spaces |
| Ontology | Validate and apply typed entity/relationship definitions |
| Ingestion | Submit documents/events with stable source and operation references |
| Progress | Show queued, running, completed, failed, cancelled, and uncertain operations |
| Reconciliation | Resolve partial or ambiguous effects before retrying |
| Entities | Fetch details, list by type, enumerate with pagination |
| Relationships | Fetch edges, inspect both directions, traverse bounded neighborhoods |
| Retrieval | Hybrid search, evaluated ranking, filters, and traceable results |
| Temporal research | Current and historical queries with explicit time semantics |
| Provenance | Connect results to source episodes, documents, passages, and simulation events |
| Portability | Export supported graph artifacts and reconstruct validated representations |
| Isolation | Enforce project, run, branch, and layer boundaries |
| Deletion | Scoped cleanup with visible completion and recovery handling |

### Foundational contract fields

The main chat will define field-level schemas before implementation. Required concepts include:

- Workspace and project identity.
- Source revision and content hash.
- Logical graph/run/branch scope.
- Ontology revision.
- Operation and ingestion-batch identity.
- Stable application entity/event identifiers.
- Provider-native identifiers and mappings.
- Schema, model, prompt, embedding, and extractor versions.
- Simulation time, source-asserted validity time, and system-recorded time.
- Evidence references.
- Completion cursor and failure classification.

Unknown timestamps stay unknown. A publication date is not automatically the time a statement became true.

### Neo4j Community constraints

The implementation must run on Community Edition without quietly relying on Enterprise-only features.

In particular:

- Do not assume a separate Neo4j database can be created for every tenant or run.
- Use tested logical scopes and provider mappings.
- Treat Graphiti grouping identifiers as data partitions—not authorization.
- Keep Neo4j inaccessible to ordinary application users.
- Enforce access through the application boundary.
- Test the actual Community-compatible backup and restore procedures.

Community Edition is the selected self-hosted product; managed Aura and Enterprise subscriptions are not required dependencies. [Neo4j edition comparison](https://neo4j.com/pricing/)

### Safe ingestion and retries

We must preserve the intent of MiroFish’s reconciliation protections while adapting them to Graphiti.

The implementation will:

1. Persist source identity and an operation record.
2. Record extraction/processing stages.
3. Retain accepted intermediate results where useful for recovery.
4. Apply graph changes with tracked identities.
5. Reconcile completion before acknowledging success.
6. Expose uncertainty rather than blindly repeating mutations.

“Retryable” does not mean “safe to rerun everything.” We will not promise distributed exactly-once execution where the underlying operations cannot provide it.

### No silent Zep fallback

Normal operation must succeed without a Zep key.

There will be:

- No automatic cloud fallback after a Graphiti failure.
- No hidden Zep dependency in report tools or profile generation.
- No requirement to create a Zep account.
- No silent keyword-only fallback presented as equivalent graph research.

A legacy integration may remain isolated for reference or optional comparison. It must not be imported or required by the normal deployment path.

## 9. Preserve and adapt the valuable existing modules

| Existing area | Preserve | Change |
|---|---|---|
| Ontology generator | Typed generation and global document sampling | Translate validated ontology into the new provider contract |
| Graph builder | Batching, progress, reconciliation, completion barriers | Replace Zep calls with Graphiti-backed operations |
| Entity reader | Actor candidates and relationship enrichment | Provider-neutral IDs and scoped entity/edge access |
| Research tools | Quick, broad, historical, decomposed and multi-step investigation | New retrieval implementation and normalized evidence results |
| Graph-memory updater | Simulation activities entering research; final drain/wait | Ordered ingestion, recorded lag, operation identity, separate layers |
| Profile generator | Individuals, organizations, and platform formats | Versioning, editable cohorts, grounding checks |
| Configuration generator | Automatic scenario and environment setup | Validated settings with actual consumers |
| Simulation runners | Both environments, schedules, actions, interview context | Supervision, accounting, event capture, checkpoint support |
| Report agent | Planning, repeated tools, interviews, section assembly, chat | Evidence claims, metrics, contradictions, cross-run analysis |
| Vue components | Connected guided journey and existing views | Smaller typed modules, accessibility, advanced controls |
| Parsing utilities | Existing extraction behavior | Passage references, isolation, later format expansion |

The report agent should continue to investigate. It must not be reduced to a new template merely because the storage provider changed.

## 10. Knowledge, evidence, and temporal reasoning

The knowledge workbench will distinguish:

1. **Source claims:** what a document actually states.
2. **User assumptions:** scenario inputs supplied by the user.
3. **Model inferences:** conclusions generated during extraction or preparation.
4. **Simulation events:** what occurred inside a simulated environment.
5. **Report interpretations:** analytical statements about the available evidence.

A source claim is not automatically true. A simulated post is not evidence that a real person holds that opinion.

### Evidence chain

A report claim should resolve through:

**Claim → research/metric result → graph fact or event → original passage or recorded simulation artifact.**

Graphiti’s episode references are a foundation, not a substitute for our page, section, excerpt, and document-version records.

### Temporal boundaries

The system must distinguish:

- When an event occurred in the simulation.
- When a source says a relationship was valid.
- When the application learned or recorded it.
- When a claim was superseded or invalidated.

Historical queries and interviews must not accidentally use future information.

### Layer and branch separation

Simulation output must not overwrite source evidence as if it were real-world correction.

Run/branch writes occur in isolated scopes. Cross-layer searches are deliberate and return the origin of each result. Derived summaries and entity merges must respect the same boundaries.

### Ingestion completeness

Reports must know how much simulation activity is searchable.

If processing is incomplete, the application must either:

- Wait for the required ingestion barrier; or
- Clearly identify the incomplete coverage.

It cannot claim to have analyzed the entire run when its graph stops several rounds earlier.

## 11. Simulation breadth and upgrades

### Preserve the configured actions

The capability register lists these autonomous actions:

**Twitter-like:** create post, like, repost, follow, abstain, quote.

**Reddit-like:** like/dislike post, create post, create comment, like/dislike comment, search posts, search users, trends, refresh, abstain, follow, mute.

Manual interviews are separate. Installed-library actions are not credited as application features until wired and tested.

### Preserve baseline execution behavior

Characterize before changing:

- Recommendation and feed selection.
- Action ordering and target validation.
- Agent memory.
- Activity schedules and probabilities.
- Network structure and visibility.
- Existing concurrency controls.
- Single-platform and paired-platform operation.

Do not silently replace OASIS scheduling with a different simulation model.

### Planned controls

Add:

- Effective recommendation presets.
- Editable initial networks.
- Heterogeneous activity and timezone settings.
- Organization-specific behavior.
- Scheduled interventions.
- Exposure inspection.
- Action and workload budgets.
- Explicit configuration/model/population versions.

Every exposed setting requires a configuration-to-execution test. Saving a parameter without using it is not implementation.

### Optional cross-platform bridge

The bridge remains new functionality, off by default.

It specifies:

- Direction.
- Delay.
- Actor mapping.
- Content transformation.
- Visibility.
- Deduplication.
- Loop prevention.
- Event lineage.

Turning it off preserves the independent-platform baseline.

## 12. Durability, playback, checkpoints, and branching

These remain four separate capabilities:

| Capability | Required guarantee |
|---|---|
| Recorded playback | Display the stored event sequence faithfully |
| Analytical reconstruction | Rebuild derived metrics/views from recorded artifacts |
| Checkpoint continuation | Restore a supported state in a fresh process and continue |
| Counterfactual branch | Start an isolated child from a parent boundary without mutating the parent |

### Safe orchestration

The existing fresh-start behavior can reset native simulation state. Temporal must not automatically rerun that operation after an uncertain failure.

Initially:

- Track process identity and ownership.
- Use leases and heartbeats.
- Reconcile the actual run state.
- Disable unsafe automatic re-execution.
- Enable continuation only after checkpoint support passes.

Temporal coordinates work; it does not make non-idempotent side effects safe automatically. [Temporal activity guidance](https://docs.temporal.io/activities)

### Checkpoint contents

Depending on the engine, a valid checkpoint may require:

- Native database state.
- Agent memories.
- Population/configuration revisions.
- Scheduler and random state.
- Simulation clock.
- Event cursor.
- Outstanding operations.
- Knowledge-ingestion boundary.
- Graph lineage and retrieval context.
- Runtime and schema versions.

### Graph-aware branching

Copying a simulation database while retaining a writable parent graph is not acceptable.

A branch must have:

- A frozen parent boundary.
- Isolated child state.
- Correctly mapped graph references.
- Explicit intervention metadata.
- Tested parent preservation.

Re-extracting documents with fresh LLM calls is **not exact restoration**. Exact restoration requires saved, validated artifacts or a compatible snapshot—not an assumption that the model will generate the same graph again.

## 13. Reports, interviews, surveys, and exports

Preserve:

- Outline planning.
- Repeated research-tool calls.
- Broad and deep graph investigation.
- Agent interviews.
- Iterative report sections.
- Follow-up chat.

Add:

- Supporting and contradictory evidence.
- Deterministic numerical calculations.
- Structured claim records.
- Research traces.
- Cross-run comparison.
- Clear limitations and uncertainty.
- Safe rendered content.

Citation checks have two parts:

1. The reference exists and is authorized.
2. The evidence actually supports the claim.

An LLM judge can assist review but does not establish correctness by itself.

Interviews must identify whether an answer is:

- Newly generated from a live environment.
- Recorded from an earlier interview.
- Generated from a restored checkpoint.

A later interview is not proof of the agent’s historical internal reasoning.

Exports include reports, evidence, ontology, populations, configuration, events, metrics, and supported checkpoints. Imported bundles are validated and never execute embedded scripts automatically.

## 14. User experience and languages

Keep the five-stage guided journey:

1. Sources and graph.
2. Environment setup.
3. Simulation.
4. Report.
5. Interaction.

The advanced workspace adds detailed controls without forcing every user through them.

Both modes share the same services, data, and experiment manifests.

Required interface behavior includes:

- Interactive graph and entity details.
- Source and simulation layer filters.
- Time/round navigation.
- Independent platform status.
- Dual feeds.
- Ingestion progress and lag.
- Evidence inspection.
- Explicit partial/failure states.
- Accessible list alternatives and keyboard navigation.
- Useful empty states and recovery instructions.

Retain English and Chinese throughout. Add Malay as planned. Interface translation and generated-language quality are assessed separately.

The README presentation reference does not require turning the application into a Windows desktop wrapper.

## 15. Security, privacy, and spending controls

### Before shared deployment

Require:

- Authentication and ownership.
- Scoped graph, document, run, report, and interview access.
- Safe file paths and server-generated storage keys.
- Restricted origins and appropriate CSRF defenses.
- Safe Markdown/HTML rendering.
- Bounded uploads and isolated parsing.
- Private errors and redacted logs.
- Bounded worker permissions and execution.
- Database services inaccessible from the public internet by default.

The original application is a non-public comparison fixture until its exposure risks are addressed.

### Knowledge-specific protection

The model cannot choose arbitrary graph scopes or execute unrestricted database queries.

Application-controlled operations validate:

- Scope.
- Allowed query shape.
- Target identity.
- Traversal depth.
- Result size.
- Time/layer filters.
- Access to evidence.

### Costs

Your acceptance of paid model APIs is retained. The plan does not impose a local-model-only restriction on initial operation.

However, accounting still covers:

- Graphiti extraction.
- Embeddings and reranking where billable.
- OASIS/CAMEL-internal calls.
- Population/config generation.
- Reports and interviews.
- Retries and ambiguous outcomes.

No mandatory Zep invoice remains. Total operating cost still includes model usage and local or rented compute.

### Retention and deletion

Deletion must cover application records, source objects, graph artifacts, native engine state, exports, recorded model artifacts, and relevant backups/history.

Use scoped tombstones and reconciled cleanup. If required evidence is purged, the application must no longer promise exact replay or reconstruction for the affected run.

## 16. Operating modes

| Mode | Knowledge storage | Models | Intended use |
|---|---|---|---|
| **Self-hosted hybrid — primary** | Local Graphiti/Neo4j | Approved paid APIs | First complete supported product |
| **Fully local/private — later qualification** | Local Graphiti/Neo4j | Local generation, embeddings, and any reranking | No-cloud operation |
| **Reference comparison — optional** | Legacy Zep integration in an isolated environment | Explicitly selected providers | Comparative evidence only |

The primary mode is **not fully offline**. Relevant content may be sent to the selected model provider.

U12 must qualify the fully local mode with cloud egress denied. This includes parsing, generation, embeddings, reranking, telemetry, and fallbacks.

The quality and hardware limits of each mode are documented separately.

## 17. Updated 15-phase execution sequence

U00–U14 remain the phase identifiers. The early phases now include the knowledge replacement.

| Phase / proposed branch | Main work | Acceptance gate |
|---|---|---|
| **U00 — `phase/00-baseline-repository`** | Verified source import, attribution, repository structure, README skeleton, CI, locked architecture record | Traceable source and reproducible bootstrap; no secrets/runtime data |
| **U01 — `phase/01-parity-baseline`** | Characterization fixtures, capability mapping, Graphiti/Neo4j compatibility spike, knowledge contracts, early bounded live-model checks | Core ontology/ingestion/search feasibility demonstrated; baseline evidence accurately classified |
| **U02 — `phase/02-security-patches`** | Path/rendering/CORS/error/input fixes, local access protection, internal-service exposure controls | Relevant exploits fixed; safe local integration environment |
| **U03 — `phase/03-service-adapters`** | Complete Graphiti provider integration; replace Zep consumers; model/engine/storage boundaries; checkpoint spike | Full inherited workflow runs on Graphiti/Neo4j without Zep credentials; provider failure/isolation tests pass |
| **U04 — `phase/04-persistence-evidence`** | Full PostgreSQL application migration, ownership, versions, source spans, event/evidence records, portable artifacts | Reversible migration; correct authority, references, and access |
| **U05 — `phase/05-durable-execution`** | Temporal, supervised runners, robust graph-job recovery, admission/accounting, cancellation, progress | No blind resets or untracked duplicate effects; interruption and budget tests pass |
| **U06 — `phase/06-knowledge-research`** | Richer temporal/layered research, long-document coverage, contradiction handling, ranking/evidence improvements | Required research depth and quality gates pass |
| **U07 — `phase/07-simulation-upgrades`** | Full action validation, effective controls, recommendations, network/activity settings, paired execution | Both platforms and all exposed settings have demonstrated behavior |
| **U08 — `phase/08-checkpoints-branches`** | Playback, reconstruction, supported pause/resume, engine and graph-aware branches | Fresh-process restoration and parent-preservation tests pass |
| **U09 — `phase/09-investigative-reports`** | Evidence-aware investigations, metrics, interviews, surveys, chat, richer exports | Report, citation, numerical, and context-boundary gates pass |
| **U10 — `phase/10-experiment-lab`** | Ensembles, interventions, sensitivity analysis, optional bridge, comparisons | Differences are attributable; failed runs and shared budgets handled |
| **U11 — `phase/11-workbench-languages`** | Integrated guided/advanced UX, graph timeline, EN/ZH completion, Malay, accessibility | Full supported user journeys pass |
| **U12 — `phase/12-private-provider-mode`** | Local inference/embedding/reranking profile, disabled cloud fallbacks, privacy qualification | Complete declared local profile works with cloud egress blocked |
| **U13 — `phase/13-ingestion-performance`** | DOCX/OCR/tables, profiling-led optimization, installation/upgrade/restore tools | Input quality, measured performance, and operational recovery pass |
| **U14 — `phase/14-release-qualification`** | Final remediation, packaging, source/notices/SBOM, documentation, release verification | All mandatory capabilities and promised upgrades pass together |

### Early persistence dependency

Graph ingestion needs operation records before U04.

Therefore U01/U03 may introduce a **small foundational PostgreSQL schema** for knowledge operations, scope mappings, and minimal usage records. U04 expands application persistence and migrates legacy records.

This avoids first building unreliable temporary storage and then replacing it immediately.

### Early safety and usage limits

Basic loopback isolation, safe fixtures, bounded requests, and per-run limits apply from the first live integration test. U02 and U05 deepen these controls; they are not permission to ignore them earlier.

### Updated milestones

- **B0 — Characterized source and viable replacement:** import, baseline tests, contracts, and knowledge spike.
- **B1 — Working Zep-free baseline:** complete inherited workflow through Graphiti/Neo4j, with initial hardening.
- **B2 — Durable, evidence-aware platform:** application persistence, ownership, accounting, and supervised execution.
- **B3 — Experiment/research upgrade:** accepted recovery, branches, ensembles, controls, and richer reports.
- **B4 — Qualified full release:** remaining privacy, language, ingestion, performance, and operational gates complete.

## 18. Verification and the meaning of “as good or better”

Three different claims must remain separate:

1. **Feature completeness:** the required workflows function.
2. **Research quality:** extraction, retrieval, and reports meet the chosen rubric.
3. **Comparison with Zep:** measured differences against actual reference outputs or live runs.

### Without paying for Zep

We can establish:

- Source-derived behavioral requirements.
- Deterministic application tests.
- Human-curated document/question/evidence fixtures.
- Actual Graphiti/Neo4j integration results.
- End-to-end simulation/report checks.
- Performance and failure behavior on our own deployment.

We cannot claim a direct live Zep non-inferiority result without the corresponding evidence.

Absence of Zep credentials therefore does **not block development or the Zep-free release**. It limits the comparative claims we can make.

### Required evaluation coverage

| Area | Evidence |
|---|---|
| Ingestion | Mixed documents, long documents, duplicate input, partial failure |
| Ontology | Relevant entity/relationship recovery and validation |
| Retrieval | Ranking, multi-hop evidence, current/historical distinction |
| Provenance | Correct source passage and event references |
| Simulation | Both environments, all configured actions, exposure and memory boundaries |
| Reports | Completeness, usefulness, unsupported claims, numeric accuracy |
| Isolation | Cross-project/run/branch access and leakage attempts |
| Recovery | Worker interruption, lost response, unfinished ingestion, restart |
| Branching | Parent preservation and correct child ancestry |
| Usability | Guided workflow effort and supported-language journeys |
| Efficiency | Runtime, RAM, storage, model usage, graph lag, failures |

Quality thresholds and critical cases are declared before evaluating the final candidate. They are not weakened after poor results.

Benchmark small smoke, standard, and stress workloads separately. Record hardware, active agents, rounds, platforms, model settings, and cache conditions.

MiroFish is the application baseline—not ground truth about human behavior. Simulation variation is not automatically a confidence interval for real-world outcomes.

## 19. Main-chat and worker protocol

This remains the workflow for execution **in the main thread**.

| Role | Requested configuration | Responsibility |
|---|---|---|
| Main chat | GPT-6 Sol/high | Planning, architecture, task assignment, all review/audit/checks, GitHub integration, acceptance |
| Phase workers | GPT-6 Sol/medium | Scoped implementation only |

Workers may inspect relevant original source and dependencies. Their fresh contexts are for focus, not clean-room isolation.

Workers do not:

- Run tests, lint/type checks, verification builds, browser QA, or audits.
- Certify parity or completion.
- Merge, publish, or change acceptance rules.
- Switch the locked architecture independently.

They may write specifically assigned tests; the main chat reviews and executes them.

### Every assignment contains

1. Bounded objective and capability IDs.
2. Accepted base revision and worktree.
3. Source and contracts to inspect.
4. Allowed files, dependencies, and commands.
5. Behavior to preserve and change.
6. Failure, cancellation, migration, and rollback semantics.
7. Attribution requirements.
8. Main-owned acceptance criteria.
9. Required handoff contents.

The worker returns changes as **unverified**, including commands run, migrations, known gaps, and suspected issues.

### Review loop

**Assignment → implementation → unverified handoff → main review/checks → corrections → exact-revision acceptance → merge verification.**

Use one active worker normally, with at most two initially when scopes are independent. Serialize shared schemas, migrations, interfaces, lockfiles, workflows, and release metadata.

Persistent records track the task, worker, base/head revision, PR, findings, evidence, and acceptance. A worker’s summary is never a substitute for verification.

## 20. GitHub delivery and CI/CD

Carry forward phase branches, focused PRs, and continuous integration.

The main chat:

1. Creates the issue/task packet.
2. Assigns the implementation workspace.
3. Inspects the handoff before pushing.
4. Creates logical commits and a draft PR.
5. Reviews Actions results and runs remaining checks.
6. Requests corrections.
7. Accepts the exact revision.
8. Merges and verifies the result.
9. Updates capability status, changelog, and roadmap.
10. Tags qualified milestones.

Every created PR is attached to the main task.

Protected `main` and required checks are configured where the account plan supports them. Chats using the same account are not independent GitHub reviewers; the workflow must not pretend otherwise.

### Updated workflows

| Workflow | Purpose |
|---|---|
| CI | Backend/frontend checks, schemas, deterministic capability fixtures |
| Integration | **Real disposable Neo4j/PostgreSQL services**, Graphiti adapter, engine and browser tests |
| Security | Secrets, dependencies, containers, licenses, access regressions |
| Documentation | Commands, versions, links, localization and status consistency |
| Live evaluation | Reviewed, bounded tests using approved model APIs and the self-hosted graph |
| Release | Exact-version packages, source, checksums, SBOM, notices, draft release |
| Deployment | Promote a tested artifact to an approved environment |
| Upstream review | Report upstream changes without automatically merging |

Ordinary CI uses fake model responses but should exercise real database behavior where relevant. Mocking all graph operations would miss integration failures.

No normal CI job requires Zep credentials. Paid model evaluations remain separate.

Pin actions, minimize permissions, isolate untrusted code, and keep private documents/model payloads out of published artifacts. Deployment accounts, public hosting, and scheduled expensive jobs are not implied by this planning request.

## 21. Repository layout and documentation

Retain the inherited top-level structure initially. Add modules as their phases implement them.

The following are **proposed subdirectories under the implementation repository**, not claims that they already exist:

| Area | Planned location |
|---|---|
| Existing API/services | `backend/app/` |
| Provider and engine adapters | `backend/app/adapters/` |
| Knowledge worker/service boundary | `services/knowledge/`, if runtime isolation is selected |
| Durable workflows | `backend/workflows/` |
| Application migrations | `backend/migrations/` |
| Supervised engine entry points | `backend/scripts/` |
| Backend tests | `backend/tests/` |
| Vue application | `frontend/src/` |
| Frontend tests | `frontend/tests/` |
| Capability and browser tests | `tests/parity/`, `tests/e2e/` |
| Quality fixtures and rubrics | `evals/` |
| Versioned dependency patches | `patches/` |
| Compose, backup and deployment definitions | `infra/` |
| Setup, export and migration utilities | `tools/` |
| Architecture and operations | `docs/` |
| Task packets, handoffs and acceptance | `coordination/` |
| GitHub workflows | `.github/workflows/` |

Dependency modifications belong in recorded patches or pinned packages—not undocumented edits inside installed environments.

### Planning documents to synchronize in the main thread

Before worker dispatch, the main thread should update:

- Architecture/master plan.
- Capability register and mode definitions.
- GitHub/CI specification.
- Worker protocol.
- Evidence and decision records.
- Deployment/cost assumptions.

The existing saved versions still contain Zep-first language. They must not be handed to implementation workers as if they already reflect this conversation.

## 22. README and repository presentation

Keep the presentation contract from your supplied plan:

- Left-aligned title.
- Bold purpose sentence.
- Inline badges.
- Short explanation.
- Honest status callout.
- Separator.
- Captioned application screenshots.
- Native Markdown sections and tables.

Preserve the section order:

1. Screenshots.
2. Why this exists.
3. What it does.
4. How it works.
5. Requirements.
6. Quick start.
7. Command-line reference.
8. Where things live.
9. Project structure.
10. Troubleshooting.
11. Current status.
12. Roadmap.
13. Security.
14. Contributing.
15. License and attribution.

Updated README content must explain:

- Graphiti/Neo4j self-hosting.
- No required Zep account.
- Paid model API configuration.
- Tested Community Edition versions.
- Hybrid versus fully local privacy.
- Persistent volumes and backup/restore.
- Actual accepted capability status.

Do not copy the reference project’s license badge, platform requirements, commands, or screenshots. Do not label generated mockups as verified application captures.

## 23. Release criteria, practical limits, and execution readiness

A complete release requires:

- All 24 inherited capabilities accepted in the primary mode.
- All 20 promised upgrades accepted in their declared modes.
- No required Zep account, key, subscription, or fallback.
- Tested Graphiti/Neo4j compatibility.
- Security and ownership gates.
- Supported checkpoint and branch recovery.
- Evidence and numerical fidelity.
- Complete supported-language journeys.
- Validated imports, exports, upgrades, and restores.
- Accurate source, notices, dependency inventory, and release documentation.
- Exact-revision build and test evidence.

Interim private builds are allowed with clear limitations. They are not called the finished project.

### Remaining implementation decisions

The direction is locked, but these details still need evidence:

- Exact compatible dependency/image versions.
- Internal knowledge-worker deployment boundary.
- Model, embedding, and reranking configuration.
- Hardware capacity and supported workload sizes.
- Identity provider for shared deployments.
- Actual GitHub account/protection availability.
- Community-compatible backup and branch restoration mechanics.
- Quality thresholds and evaluation sample sizes.

These are bounded implementation decisions—not reasons to reopen the entire stack selection.

### Schedule

The Zep replacement is now early engineering work, so the previous Zep-first timeline should not be carried forward unchanged.

Re-estimate after:

- U01 establishes compatibility and the knowledge contract.
- U03 demonstrates the complete Zep-free workflow and checkpoint feasibility.

Use accepted outcomes and observed review throughput—not generated lines of code—to forecast remaining work.

## Final consolidated direction

**MiroFish Research Lab will reuse MiroFish’s application and simulation capabilities, replace its mandatory Zep integration with Graphiti + self-hosted Neo4j Community Edition, allow paid model APIs, and progressively deliver the original 24 capability requirements plus all 20 upgrades.**

The first execution sequence in the main thread should be:

**Synchronize the approved planning documents → establish the verified derived repository → characterize the baseline → prove the selected knowledge stack → integrate and qualify the full Zep-free workflow → proceed through the remaining upgrades.**

No implementation phase is marked passed by this plan, and no project state has been changed in this side conversation.

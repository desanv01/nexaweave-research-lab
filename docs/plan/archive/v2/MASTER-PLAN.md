# MiroFish Research Lab: capability-preserving upgrade plan

Version 2.0, review candidate. Prepared 26 September 2026.

Status: plan for review; implementation has not begun in this task. All acceptance gates below are planned, not passed.

## 1. Objective and the change in direction

Build an upgraded, noncommercial MiroFish-derived application that retains its useful end-to-end behavior and adds stronger research tools, experiment control, reliability, privacy, usability and measurable efficiency.

The earlier plan could produce a narrower custom simulator after extensive infrastructure work. The comparison attachment correctly identifies missing guarantees around Zep-backed research, OASIS behavior, dual-platform execution, interviews, automation, languages and exports. This revision changes both the implementation method and the release contract.

The new approach is direct source reuse and incremental modernization. MiroFish is the starting application; OASIS/CAMEL remain the initial simulation implementation; Zep remains the initial graph/research provider. We do not first replace them with smaller imitations.

Success requires all mandatory capabilities in CAPABILITY-REGISTER.md to pass, plus the specified upgrade gates. A ten-agent run is an integration fixture, never the definition of feature completeness or superiority. Keeping a feature in source is not sufficient: it must work through the supported workflow.

Three comparisons remain distinct: archive baseline versus derived baseline; derived baseline versus upgraded version; hosted-provider mode versus local/private mode. Tests cannot hide losses in one mode behind improvements in another.

## 2. Authorization, provenance and licensing posture

The user authorizes source inspection, adaptation and reuse, reports direct approval from the MiroFish team, and intends no commercial release. This is sufficient task authorization to plan and implement a derived project. Implementation workers may inspect and adapt the original source, tests and prompts within their assignments. The previous prohibition on source access is superseded.

Preserve inherited license files, copyright notices and component attributions. Noncommercial intent does not itself waive AGPL conditions. Until separate written terms establish otherwise, the imported application remains governed by its existing license; do not relabel the whole derivative MIT/Apache or claim exclusive authorship. Record the scope of any special grant when available without publishing private correspondence. Third-party components and hosted-service terms remain separate. This is not a blocker to development under the existing permissions/license. [MiroFish license](https://github.com/666ghj/MiroFish/blob/main/LICENSE)

A modified network deployment must have a corresponding-source delivery path consistent with the applicable terms. An authenticated source download can serve users who lack access to a private development repository. A private GitHub URL inaccessible to those users is not a sufficient substitute. Do not add a noncommercial restriction to an AGPL distribution simply because our own intended use is noncommercial.

Use distinct project branding with a clear derivative attribution. Code permission is not assumed to establish upstream endorsement or unrestricted trademark/logo rights. Preserve credit and choose original project screenshots and identity assets for the new README.

## 3. Baseline and repository location

The frozen comparison baseline is the supplied `MiroFish-main.zip`, SHA-256:

`D3BEF0AFEA92B99626526FFCCE0508414FEB3F9E88C3EDDA1F283CE5F447BF53`

It contains 128 file entries. Six inspected implementation/manifests in the extracted tree were hash-checked against the ZIP and matched. This is not a claim that every file in the previously used extracted environment is untouched; implementation starts from a verified archive/import, excluding generated caches and installations.

Current upstream is a second reference. In U00, resolve and record an exact upstream commit, compare it to the archive, and select fixes through reviewed PRs. Do not substitute moving `main` for the audited archive or infer that upstream's current README proves every feature works.

Recommended project: `MiroFish Research Lab`, repository `desanv01/mirofish-research-lab`, local sibling `C:\Users\Dv\Desktop\MiroFishResearchLab`. All are proposed, not created. A new workspace may be needed for that sibling path. This research directory and its archive remain available for baseline comparison.

Prefer preserving verified upstream history when the archive can be mapped to it. If no exact mapping is established, import the archive honestly as a snapshot with its hash and attribution; do not invent an upstream commit. A separate derived repository, rather than a native public GitHub fork, permits the proposed private development visibility.

Do not commit `.env`, provider keys, user uploads, simulation databases, cache directories, installed packages, raw paid-model transcripts or private permission correspondence. Everything needed to develop, review and reproduce the software goes to GitHub; private research data does not.

## 4. Non-regression policy

Every inherited capability gets a source reference, characterization fixture, delivery phase, active provider mode, expected behavior and acceptance evidence. Status values are planned, source-observed, baseline-verified, implemented-unverified, accepted, blocked or explicitly waived by the user.

No mandatory row can become silently deferred. A change that removes an exposed action, language, export, research tool or environment fails the gate unless an accepted replacement exists. Security patches may deliberately block unsafe input/path behavior without counting that vulnerability as a feature to preserve.

No optional replacement becomes the default until it passes the same relevant tests and a benchmark against the retained path. Feature flags separate engine/provider modes while they are evaluated. Existing formats and user journeys stay usable during migration; compatibility adapters carry old callers until their replacement is accepted.

Preserve the strengths identified in the comparison: long-document sampling, non-idempotent Zep-write reconciliation, graph reader/finalization barriers, local runner concurrency limits, report tool allowlists and defenses against fabricated tool wrappers. Refactoring these protections without regression cases is prohibited.

## 5. What we reuse and what we improve

| Area | Starting point | Upgrade path |
|---|---|---|
| Frontend | Vue 3, Vite, D3, router, English/Chinese UI, five-stage flow | Keep framework and flow; split large components, add TypeScript at boundaries, richer graph/replay views, accessibility and complete localization |
| Backend API | Flask endpoints and domain services | Add typed schemas, authorization and service interfaces; retain Flask unless an evidence-backed ADR justifies a later API migration |
| Graph creation | Ontology generator, graph builder, entity reader, paging/reconciliation | Add richer ontology review, document spans, temporal/evidence layers, provider interface and cancellation |
| Knowledge research | Existing multi-query, temporal, entity/edge, interview and report tools over Zep | Preserve investigations; add explicit provenance, bounded graph expansion, ranking evaluation and contradiction handling |
| Population | Existing individual/organization profiles and automatic setup | Add cohorts, editable parameters, profile versions, confidence/assumption labels and consistency tests |
| Simulation | OASIS/CAMEL, both run scripts and parallel execution, native platform state | Keep action breadth/recommendation/memory; instrument, supervise, add real parameter wiring, persistence and checkpoint support |
| Reporting | Existing outline planning, repeated research calls, interviews, section assembly and report chat | Add typed evidence claims, deterministic metrics, semantic citation assessment and comparative reports |
| Inputs | Existing PyMuPDF/text decoding and multi-file workflow | Retain extraction baseline under applicable terms; add source spans, isolation, better tables and later OCR/DOCX |
| Persistence | Native OASIS state plus application JSON/files | Add PostgreSQL authority for application metadata; retain native simulation storage behind engine adapter until a proven replacement exists |
| Jobs | Existing runner lifecycle and IPC | Supervised workers and Temporal coordination; progressive checkpoint/recovery, no blind replay of unsafe activities |
| Delivery | Source/Docker workflow | GitHub-based branch/PR gates, locked builds, installers/scripts where applicable, release manifests and recovery documentation |

### Runtime baseline

The archive declares Python `>=3.11,<3.13`, `camel-oasis==0.2.5`, `camel-ai==0.2.78` and `zep-cloud==3.25.0`. Reproduce with a compatible Python 3.11/3.12 environment, choosing the exact tested interpreter from the baseline lock/install. Do not force Python 3.13 because the previous plan named it.

Retain the archived Vue/Vite dependency combination for the baseline; then upgrade dependencies in small, separate PRs. Select a supported Node LTS compatible with the installed Vite/toolchain and pin it. Add no mass dependency update alongside a structural refactor.

PyMuPDF is not automatically removed in this derived plan. Record its AGPL/commercial terms independently of the MiroFish permission. A parser switch needs format-quality parity, not just a more convenient license label. OASIS/CAMEL are separate dependencies with their own notices and patch records.

## 6. Architecture after incremental modernization

The target remains a modular application rather than a collection of unnecessary microservices:

```text
Vue research workbench
        |
Flask API + validation + session/authorization boundary
        |
Application services: projects, knowledge, populations, experiments, reports
        |
        +-- PostgreSQL: application records, versions, evidence, usage, audit
        +-- Object storage: documents, exports, model artifacts, checkpoint bundles
        +-- Workflow service: Temporal plus supervised activity workers
        +-- KnowledgeProvider: Zep initially; qualified local provider later
        +-- SimulationEngine: OASIS/CAMEL plus explicit patches/adapters
        +-- ModelProvider: routed generation/embedding clients and cost policy
```

This is a target reached through phases, not an instruction to rewrite the baseline before demonstrating it.

### Data authority

PostgreSQL becomes authoritative for users/workspaces/projects, input revisions, run manifests, experiment metadata, budgets, report claims, task state projections and audit records. Native OASIS storage remains authoritative for its internal social state during the transition. Event/evidence records derived from that state are explicitly projections until the adapter transaction protocol proves stronger guarantees.

Define which system owns each field. Avoid unsynchronized double writes that leave JSON, PostgreSQL, SQLite and Zep each claiming to be the source of truth. Use stable operation IDs, outbox/ingestion cursors and reconciliation for cross-store effects. Do not claim a SQL transaction spans SQLite, object storage and Zep.

Application migration runs in dry-run/copy mode first, validates counts/references, backs up the original, and changes one storage authority at a time. Running experiments retain their engine/runtime version; they are not silently moved onto incompatible storage.

### Interfaces

- `KnowledgeProvider`: ingest, status/reconciliation, ontology, entity/edge fetch, hybrid search/rerank, temporal filters, bounded traversal, source references, export and deletion.
- `SimulationEngine`: create/load, start, status, stop, supported actions/config, events, interviews, checkpoint/export/import and capability discovery.
- `ModelProvider`: generation, structured outputs, streaming where needed, embeddings, usage, errors, capabilities, privacy and bounded retries.
- `StorageProvider`: bounded reads/writes, generated object keys, scoped download, manifest export and retention/deletion.
- `ExperimentService`: independent or paired environments, actor mapping, ensembles, intervention schedules, branch ancestry and comparison.

Interfaces reflect actual supported behavior. An unsupported checkpoint operation returns an explicit capability result; it does not return success after copying only a database file.

### Identity and personal deployment

Support an easy personal/local profile and an authenticated shared profile. Start with the existing local experience behind loopback, protected session/API access and conservative defaults. Shared access uses an established OIDC provider, with Keycloak a self-hosted reference option. Workspace authorization is enforced in the API and tools, not inferred from UUID secrecy.

A shared deployment is unavailable until resource ownership and negative access tests pass. Personal mode still needs protection against hostile browser origins, CSRF where applicable, unsafe uploads and path traversal. No mandatory enterprise deployment ceremony is inserted into every local run.

## 7. Knowledge and research capability

Retain Zep as the initial provider because its hosted extraction, temporal graph maintenance, hybrid search and reranking are substantial functionality. The source contains the integration, not the managed service implementation. Reusing MiroFish does not give us the proprietary service internals or remove provider costs. [Zep search documentation](https://help.getzep.com/v2/searching-the-graph)

Maintain source knowledge, user assumptions, simulated events and derived analysis as distinct logical layers while permitting federated investigation. Every hit reports its layer, project/run/environment, source time and retrieval provenance. A convenient unified graph view must not present simulated posts as real-world evidence.

Temporal records distinguish when a claim was valid from when the system learned/recorded it. Preserve `valid_at`, `invalid_at`, `expired_at` semantics where available; unknown dates remain unknown. Source revisions alone do not supply temporal reasoning.

Add original-document revision/page/section/excerpt references and an extraction manifest. Use the existing global long-text sampling as a baseline, then improve coverage with per-document budgets, section diversity, retrieval and coverage diagnostics. Do not regain capacity by simply dropping the ends of documents.

Preserve the existing investigative tool families: quick search, broad/historical search, question decomposition, entity details and relationships, deeper multi-query investigation and selected-agent interviews. New bounded tools add contradiction search, cross-run comparison, trend/cascade metrics and provenance trace. Validate both scope and result shape.

Keep dynamic simulation-to-graph updates. Add per-run cursors, idempotent event references where the provider supports them, explicit ingestion lag, drained completion barriers and failure visibility. A report cannot imply it searched the full run if graph ingestion stopped early.

The local graph track is mandatory before advertising full offline/private parity. Evaluate a maintained open-source graph/memory engine such as Graphiti and the full retrieval contract; Graphiti is not automatically equivalent to Zep Cloud. Select its backend only after dependency, extraction, ranking and temporal-query tests. PostgreSQL/pgvector remains useful but is not treated as a complete Zep replacement by itself. [Graphiti](https://github.com/getzep/graphiti)

## 8. Simulation breadth and extension

### Preserve the actual configured catalog

The archived parallel runner exposes Twitter-like `CREATE_POST`, `LIKE_POST`, `REPOST`, `FOLLOW`, `DO_NOTHING`, `QUOTE_POST` and Reddit-like `LIKE_POST`, `DISLIKE_POST`, `CREATE_POST`, `CREATE_COMMENT`, `LIKE_COMMENT`, `DISLIKE_COMMENT`, `SEARCH_POSTS`, `SEARCH_USER`, `TREND`, `REFRESH`, `DO_NOTHING`, `FOLLOW`, `MUTE`. Manual interviews are a separate operation. Do not conflate a library's entire catalog with the application's configured catalog.

Characterize recommendation behavior, actor visibility, memory, network structure, action validation, active-hour selection and peak/off-peak multipliers before changing them. Preserve the existing per-environment semaphore of 30 as an observed local limit, then make concurrency a validated run/account budget rather than calling the original unlimited in every respect.

Support each environment alone and both concurrently. Stable logical actor IDs map to each platform's native agent IDs. Every event and metric records its platform. One failed environment produces an honest partial/failed experiment state; the other can be retained for inspection without fabricating a successful pair.

New simulation features include enforced action budgets, meaningful recommendation presets, heterogeneous activity/timezones, actual scheduled interventions, editable initial networks, organization-specific behavior and exposure/memory inspection. Every exposed parameter needs a demonstrated path from configuration to changed execution. Merely generating or saving a setting is insufficient.

Do not silently replace OASIS ordering with the previous proposal's synchronous custom kernel. Preserve baseline semantics in compatibility mode. New synchronous barriers, ranking algorithms or action-selection policies are named/versioned experiment options so their effects can be compared.

### Cross-platform propagation

The inspected parallel runner does not establish explicit information transfer between platforms. A bridge is therefore a new upgrade: opt-in, defined actor/content mapping, direction, delay, visibility and deduplication/loop-prevention rules. Off by default retains baseline semantics. On requires fixtures proving transfer, no accidental infinite propagation and clear event lineage.

## 9. Durability, replay, resume and branching

Temporal is introduced around stabilized services, not as a claim that an opaque subprocess becomes crash-safe. The existing runner deletes its native database when creating a new environment and resets that environment; wrapping this start routine in automatic retries would risk losing state or duplicating paid work.

Uncheckpointed simulation-start activities initially use no automatic re-execution. A durable supervisor tracks process identity, ownership/lease and heartbeats. After uncertainty, it reconciles the process/run state; it never blindly calls reset. Once checkpoint recovery is proven, bounded resume is enabled. Temporal activities still require idempotent effects. [Temporal activity documentation](https://docs.temporal.io/activities)

Define four capabilities separately:

| Capability | Required guarantee |
|---|---|
| Recorded playback | Exact display of the stored event/feed sequence with platform attribution |
| Projection replay | Rebuild our recorded analytical projections and compare canonical hashes |
| Checkpoint continuation | Restore supported engine, memory, random and scheduler state in a new process and continue from a committed boundary |
| Counterfactual branch | Restore the same parent prefix into an isolated child world, apply an explicit change and preserve the parent |

A checkpoint includes native DB state, agent memory, profiles, engine/model config, random states, scheduler/time/activity state, retrieval context, event cursor, outstanding operations and knowledge-layer lineage. Quiesce/flush at a verified boundary; an arbitrary SQLite file copy is not sufficient. Resolve provider graph isolation rather than letting a child mutate the parent's graph.

Perform a checkpoint feasibility spike in U03, then implement/qualify it in U08. If needed, maintain a narrowly patched, version-pinned OASIS dependency with its source/license and patch manifest. Replacing the whole engine is a last resort requiring a new parity-backed decision, not an automatic fallback. If continuation cannot be achieved, the promised checkpoint/branch gate remains blocked; recorded playback is not relabeled as resume.

Fresh LLM calls remain stochastic. Exact replay uses recorded data. Checkpoint continuation and paired seeds support controlled comparisons without promising identical fresh provider outputs. Record model deployment identity when available and explicitly mark unknown versions.

## 10. Report depth, interviews and surveys

Keep full report planning, multi-step searches, iterative section research, graph exploration and interviews from the baseline. Preserve individual/batch interviews, synthetic surveys and follow-up report chat from the beginning; do not defer them until after a reduced report milestone.

Add structured research plans with bounded tools, deterministic statistics, supporting and contradictory citations, uncertainty labels and an evidence inspector. Numerical charts come from stored metric results. Reports remain readable investigations rather than rigid summaries of a few counters.

Citation validation has two gates: references resolve to authorized evidence; evidence actually supports the claim. An LLM judge is an aid, not proof. Unsupported interpretations remain explicitly qualified or are excluded from findings. A document statement is a source claim, not automatically true.

Interviews require a living or correctly restored engine context. Mark whether a response is a new interview, recorded answer or checkpoint-based reconstruction. Time-specific interviews must not see future events. Post-run explanations are new generated statements rather than proof of historical internal reasoning. Prefer bounded action summaries/tool traces over collecting or exposing hidden model reasoning.

Export reports, evidence bundles, ontology, scenario config, population, action logs, metrics and supported checkpoint bundles. Preserve legacy downloads where safe. Import validates format/version/size/ownership and never executes scripts from an uploaded bundle. A source/config export is not automatically a resumable checkpoint.

## 11. User experience and languages

Keep the existing five-stage guided experience: sources/graph -> environment -> simulation -> report -> interaction. Add an advanced workspace for evidence review, populations, schedules, ranking rules, budgets, experiments, comparisons and provider settings.

Guided mode proposes settings and shows a concise run summary, estimated consumption and important assumptions. The user can start with a few meaningful decisions. Advanced mode exposes detail without forcing all users through an annotation workflow. Both modes call the same services and store the same manifests.

Retain English and Chinese, complete currently missing strings, and test both paths including backend messages/exports. Malay is a named additional language track, not a substitute for Chinese parity. Multi-language generation quality is evaluated separately from translated navigation.

The workbench keeps the interactive graph, entity details, dual feeds, live logs/progress, profiles and interview/report views. Add graph-by-round/time filters, source/simulation layer filters, evidence links, synchronized run comparisons, accessible lists, keyboard support, large-list virtualization and explicit partial/failure states. Preserve existing flow during component extraction; do not require a React rewrite.

README/repository presentation follows the user's other repository exactly in structure and visual idiom. It does not prescribe our application screens or require a Windows desktop wrapper.

## 12. Security, privacy and cost controls

Patch confirmed unsafe path construction/deletion, unsafe HTML rendering, unrestricted CORS, default exposure and traceback disclosure before any shared deployment. Introduce centralized authorization, scoped object access, input bounds, storage keys, request limits and process permissions. Retain the old baseline only as a non-exposed comparison fixture.

Add bounded uploads and isolated parsing, source/LLM/tool trust boundaries, scoped retrieval, safe Markdown/link rendering, secrets redaction, privacy/provider disclosures and worker resource limits. The model never grants itself database/network authority.

Centralize model usage across ordinary generation, CAMEL/OASIS calls, interviews, graph extraction where measurable, report planning and embeddings. A wrapper around only the application's `LLMClient` would miss SDK-internal traffic; inspect/instrument every provider path or route through a qualified gateway. Hosted Zep billing may not expose per-call model usage, so report its separately measured/estimated charges honestly.

Reserve bounded budgets transactionally before requests, reconcile actual use, account for retries and ambiguous outcomes, and stop scheduling when limits are reached. Quotas cover both environments, all ensemble children and report/interview follow-up. Provider-side spending limits complement application estimates; cancellation cannot guarantee an already received request stops billing.

Retention/deletion spans object storage, PostgreSQL, native engine snapshots, Zep/local graphs, recorded model payloads, exports and workflow history. Use tombstones and reconciled cleanup. Keep ordinary event history append-only but allow scoped purge under policy; removing required state removes the exact-replay guarantee. Backups expire on a stated schedule and deletion records are reapplied after restore.

Full local mode disables cloud fallbacks for parsing, embeddings, graphs, model generation and telemetry. It must pass an egress-denial test; running just the main model locally is not sufficient.

## 13. Phases, branches and acceptance

Names below are proposed branch names for execution after plan approval. Each phase has one fresh implementation chat by default, split into bounded packets; main owns all reviews/checks and GitHub integration. Larger phases can use approved child task branches, with a final phase integration gate. No phase is silently marked complete from a worker message.

| Phase / branch | Implementation | Main acceptance / milestone |
|---|---|---|
| U00 `phase/00-baseline-repository` | Derived repo, verified source import/provenance, license records, README structure, issue/PR templates, initial CI | Archive identity preserved; no secrets/generated runtime files; reproducible bootstrap and upstream comparison record |
| U01 `phase/01-parity-baseline` | Characterization fixtures, baseline harness, feature register wiring, record/replay test providers | All material archive paths characterized; full paid baseline explicitly passed or blocked, not inferred; B0 |
| U02 `phase/02-security-patches` | Path/rendering/CORS/error/input patches, local session protection, shared-access gating | Reproductions no longer exploit flaws; legitimate baseline flows remain available; B1 |
| U03 `phase/03-service-adapters` | Provider/engine/storage seams, schemas, early checkpoint spike, upgrade isolation | No feature loss; spike identifies full checkpoint state/patch cost and proves tiny restore or records a concrete blocker |
| U04 `phase/04-persistence-evidence` | PostgreSQL application records, ownership, source versions/spans, migration/import/export, recorded event projections | Old data migrates non-destructively; citations/ownership correct; authority map and rollback tested |
| U05 `phase/05-durable-execution` | Temporal coordination, supervised runners, quotas, accounting, cancel/reconcile, progress streaming | Crashes do not trigger blind reset; no duplicate acknowledged effects; full-path budget tests; B2 control-plane upgrade |
| U06 `phase/06-knowledge-research` | Temporal/layered graph search, improved long-document coverage, evidence/contradiction UI, reliable graph updates | Baseline research tools preserved; retrieval and provenance benchmark passes |
| U07 `phase/07-simulation-upgrades` | Full action coverage, real parameter wiring, ranking/activity presets, actor/network controls, paired status | Both platforms and all baseline actions pass; every new setting has an effect test |
| U08 `phase/08-checkpoints-branches` | Engine snapshots/restore, playback/reprojection, round pause/resume, isolated branches | Fresh-process restore and parent-preserving branch tests pass; no playback-as-resume claim |
| U09 `phase/09-investigative-reports` | Existing research/interview flow upgraded with metrics/evidence/contradictions; richer exports/surveys/chat | Blind report evaluation, interview context tests and source/numeric fidelity gates pass |
| U10 `phase/10-experiment-lab` | Ensembles, controlled interventions, sensitivity analysis, opt-in cross-platform bridge, comparison UI | Paired manifests, missing-run handling, bridge lineage and ensemble spending limits; B3 experiment upgrade |
| U11 `phase/11-workbench-languages` | Guided/advanced UX polish, graph timeline, bilingual completion, Malay track, accessibility | Full baseline navigation and EN/ZH journeys pass; real screenshots and measured usability |
| U12 `phase/12-private-provider-mode` | Qualified local graph/model stack, provider interchange, model/privacy presets | Full local path with no cloud egress; research/behavior quality and limitations measured; no fake Zep equivalence |
| U13 `phase/13-ingestion-performance` | DOCX/OCR/table improvements, resource optimization from profiling, install/upgrade/restore tooling | Existing extraction retained; new formats pass fixtures; comparable speed/cost measurements and backup recovery |
| U14 `phase/14-release-qualification` | Main-directed final fixes, packaging, source/SBOM/notices, docs and release workflows | All mandatory parity + promised upgrade gates pass together; B4 qualified release |

P0-P13 from the prior independent-rebuild plan are obsolete phase identifiers. U00-U14 avoids mixing their acceptance records with this strategy.

### U00: repository and presentation

Main creates/initializes the agreed repository after plan review, records origin/visibility, and establishes a minimal governance/bootstrap commit. Import source through a baseline branch/PR; bootstrap is the one documented exception before protections can exist. Capture current upstream by SHA, inspect changes against the ZIP and maintain a selected-change list. Implement the README skeleton and Actions from the start; do not wait until final release to push work.

### U01: establish the full application baseline

Main reruns inherited tests; the earlier reported 130 passing backend tests are historical evidence, not a current result. Build new characterization coverage around graph creation/search, automatic profile/config generation, both simulations, dynamic graph updates, reports with interviews, individual/batch interaction, languages and exports. Capture reproducible fake-provider fixtures and separately run budgeted real-service experiments. Do not advertise parity while real-service gates remain blocked.

### U02-U05: strengthen the existing application

Patch risks in place, then extract small interfaces while preserving call behavior. Move application persistence with reversible migrations and explicit store authority. Introduce supervised/durable coordination only after start/stop/retry semantics are understood. Authentication, quotas and metadata should not force rebuilding the graph or simulation engine. Keep known lifecycle/reconciliation safeguards until their replacements are proven.

### U06-U10: research and experiment improvements

Upgrade graph inquiry and evidence coverage, then enrich working simulation controls. Deliver real checkpoint capability and branches through the early-spiked OASIS integration. Strengthen the existing report investigations and agent interactions. Add multi-run experiments and opt-in propagation only after the state/version foundation can attribute their differences.

### U11-U14: complete user-facing and deployment breadth

UI improvements ship throughout the earlier phases; U11 integrates/polishes the complete workbench and language coverage rather than building the first UI. Local provider parity, advanced parsing, measured optimization and install/restore support follow. Release requires the full matrix, not only feature checkboxes or passing unit tests.

## 14. Milestones and definition of better

- B0: reproducible inherited application; every material feature characterized, with live validation status recorded.
- B1: hardened parity build retaining both environments, graph research, reporting, interactions, languages and exports.
- B2: secure/persistent control plane with practical spending limits, evidence capture and supervised jobs.
- B3: experiment/research improvements including accepted checkpoints, branches, ensembles, interventions and investigative reports.
- B4: complete qualified upgrade, including the promised private mode, ingestion, language/usability and operational support.

The application is usable for private evaluation before B4 where its profile is qualified; releases carry exact limitations. The full project's completion still requires B4. An unavailable service credential or model budget is recorded as a real integration blocker, never converted into a pass based on fixtures.

Mandatory superiority claims are explicit and testable: fewer known exploitable application paths; scoped access; citations tied to source/event records; controlled experiment comparisons; recoverable supported run state; enforced cost admission; richer effective configuration; qualified local/private workflow. Report quality, extraction accuracy, speed and cost require comparative evidence before claiming improvement.

## 15. Benchmark protocol

Main owns the datasets, rubrics, execution and interpretation. Use legally usable sources and synthetic/sanitized fixtures. Run the archived baseline in an isolated local environment; never expose the vulnerable baseline to the public.

| Dimension | Comparison / evidence |
|---|---|
| Inputs | Multi-file PDF/Markdown/text, long document with important end material, conflicting updates, organizations/individuals, EN/ZH |
| Ontology/profile grounding | Relevant entity/relation recovery, source consistency, organization behavior and reproducible frozen profile artifacts |
| Research | Hybrid top-k relevance, entity chains, current/historical results, contradictory evidence and query decomposition |
| Simulation breadth | Every configured action and environment, initial network, recommendation exposure, time/activity settings |
| Reporting | Blind usefulness/completeness/readability scores, unsupported claims, interview integration, numerical fidelity |
| Interactions | Single/batch interviews, surveys, report follow-up and reconstruction/time boundaries |
| Usability | Steps and elapsed active-user effort from multi-file upload to useful report in guided mode |
| Performance | Same model/config/workload/hardware where feasible; wall time, tokens, external charges, RAM, failures and graph lag |
| Resilience | Worker kill, lost response, graph ingestion failure, quota exhaustion, reconnect and partial-platform failure |
| Reproduction | Event playback, projection hash, new-process checkpoint restore and parent-preserving branch |

Use at least three distinct use cases: communication response, narrative spread, stakeholder reaction. Fixed prompts/config and repeated runs separate variance from regressions. Prefer the same model and resource envelope; report mismatches explicitly. Compare cold/warm caches separately. Simulation ranges are not confidence intervals for real people.

Proposed decision rules: 100% mandatory functional cases; zero known release-blocking path/authorization/rendering defects; every displayed numeric report metric matches its recorded computation; every citation resolves to authorized evidence; retrieval/report non-inferiority margin agreed in U01 (for example no more than 5% relative loss on the chosen aggregate score, with critical cases required individually). Predeclare margins and sample sizes; do not change them after seeing results. A small sample is insufficient to claim statistical equivalence.

Test 10-agent smoke, 100-agent standard and larger stress profiles only within approved budgets. Record active agent count, rounds, decisions, context sizes, concurrency and hardware. Do not inherit a million-agent claim from an OASIS research headline.

## 16. GitHub delivery and orchestrator workflow

The main chat runs GPT-6 Sol/high as requested; phase workers use GPT-6 Sol/medium. Main owns all planning/review/audit/check execution and GitHub orchestration. Worker chats implement scoped instructions and return unverified changes. Automatic GitHub Actions is the main-managed verification system, not a worker review role.

After handoff main inspects for scope/secrets, commits logical changes, pushes the phase branch, opens/updates its draft PR, reviews code and CI artifacts, runs necessary local/live checks, sends worker corrections, pushes updates and merges only after exact-revision acceptance. Documentation, capability status and changelog move with the feature. Every created PR is attached to this task.

Default one active worker, up to two on disjoint files/interfaces with sufficient review capacity. Each works in an isolated worktree of this derived repository. Workers may access the inherited source now. They do not receive GitHub write/provider credentials unnecessarily, run verification, merge branches, certify parity or publish releases.

Detailed branch/protection/release design is in GITHUB-DELIVERY.md; packet/handoff/acceptance formats are in ORCHESTRATION-PROTOCOL.md. Main preserves ledger entries containing worker ID, task packet, base/HEAD SHA, PR, CI evidence, findings, corrected revision and accepted merge/tag.

## 17. Repository layout

Retain top-level paths initially to reduce unnecessary churn:

```text
backend/app/                     # inherited app, incrementally modularized
backend/app/adapters/            # engine, graph, model, storage boundaries
backend/app/services/            # inherited and improved domain services
backend/workflows/               # durable orchestration/activity handlers
backend/migrations/              # app-state database migrations
backend/scripts/                 # supervised engine entry points / compatibility
backend/tests/                   # inherited tests plus main-owned regressions
frontend/src/                    # Vue app, split into typed feature modules
frontend/tests/                  # component and route coverage
tests/e2e/                       # main-owned actual browser flow
tests/parity/                    # baseline characterization and capability checks
evals/                           # licensed fixtures, rubrics, benchmark manifests
patches/oasis/                   # versioned upstream patches if required
infra/                           # Compose profiles, images, backup/restore
tools/                           # setup, migration, export, source-bundle utilities
docs/                            # architecture, operations, user docs, evidence
docs/upstream/                   # upstream manifest, patch history, selected changes
coordination/                    # tasks, handoffs, main reviews, phase ledger
screenshots/                     # actual sanitized application captures
.github/workflows/               # CI, integrations, release and optional deploy
README.md / README-ZH.md         # reference presentation, truthful status
ROADMAP.md / CHANGELOG.md        # phase and release history
LICENSE / THIRD_PARTY_NOTICES    # accurate inherited and new notices
```

Do not vendor installed `node_modules`/virtualenvs or managed-service data. If an OASIS patch is needed, use a pinned patch/fork package with source availability and a recorded upstream diff, not undocumented edits inside a local environment.

## 18. Resources, schedule and decisions

Reuse removes much of the previous rewrite burden, but the baseline's real-service operation, native state serialization and local graph parity remain substantial unknowns. Use accepted phases and measured throughput, not generated code volume, as the planning unit.

Indicative ranges with focused participation: repository/baseline/hardening in roughly 2-6 weeks, a materially improved hosted-provider edition in roughly 2-4 months, the full checkpoint/experiment/private-provider scope in roughly 4-8 months or longer. These are low-confidence ranges, not commitments. Re-estimate after U01 and U03's checkpoint spike; no claimed numeric speedup from AI assistance.

Source reuse does not remove model, Zep, CI-minutes, storage or local hardware costs. Ordinary PR checks use fixtures with no paid secrets. Real-service benchmarks are main-triggered with agreed per-run/batch caps. A 100-agent, 20-round, 5-run ensemble has up to 10,000 turn decisions before report/graph work if all agents are active and each turn makes one call.

Proposed choices for review:

| Choice | Recommendation |
|---|---|
| Strategy | Derived upgrade; retain baseline capabilities and reuse complex implementation |
| Name/repository | MiroFish Research Lab / `desanv01/mirofish-research-lab`, initially private |
| Source baseline | Supplied ZIP, plus separately reviewed current-upstream patches |
| Initial stack | Vue/Flask/OASIS/CAMEL/Zep/PyMuPDF; measured upgrades, no framework rewrite requirement |
| License handling | Retain applicable inherited terms/notices; apply any separate grant only within its actual scope |
| Main/worker workflow | Main high; implementors medium; main-owned checks and all GitHub actions |
| Release contract | Full capability parity plus accepted upgrade matrix, no silent omissions |
| README presentation | Exact structural pattern of the user's pinned reference repo, project-specific factual content |
| Git workflow | Phase branches and PRs into protected main; continuous pushes by main; milestone/tagged releases |
| External spending | Provider/CI budget established before paid work; credentials remain outside Git |

After plan acceptance, begin U00 and U01. Do not start by writing a replacement social engine or discarding the existing report/knowledge machinery.

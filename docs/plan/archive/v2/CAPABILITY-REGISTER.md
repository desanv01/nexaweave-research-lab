# Mandatory capability, reuse and improvement register

Version 2.0 review candidate, 26 September 2026. No row is an implementation pass in this turn.

Baseline: the user's 128-file archive. Source observations below come from the attachments, prior archive reports and selected source checks; live provider behavior/performance has not been rerun for this planning task. U01 must distinguish source-present from baseline-verified.

P = material inherited capability; I = promised improvement in this plan. All P rows are release requirements unless the user explicitly approves an omission. All I rows are part of the proposed full upgrade scope; interim releases may disclose them as incomplete, but the complete project cannot be declared finished while they remain open.

Every implemented row receives fixture IDs, supported mode(s), responsible phase/PR, accepted commit, CI/live evidence, limitations and date. Current status for all implementation work: **planned**.

## Capability gates

| ID | Type / requirement | Preserve or build | Main acceptance evidence | Phase |
|---|---|---|---|---|
| C01 | P Multi-file PDF/Markdown/text upload | Retain parser/upload flow | Mixed multi-document input survives through graph and report with document identity | U01/U02/U04 |
| C02 | P Long-document context | Retain global sampling; improve coverage | Relevant entity/claim near beginning, middle and end remains retrievable; document coverage recorded | U01/U06 |
| C03 | P Typed ontology | Reuse ontology generation and normalization | Types/relations/attributes and source-target constraints accepted; invalid outputs rejected | U01/U06 |
| C04 | P Graph build and external-write reconciliation | Retain builder/paging/lifecycle protections | Lost-response and partial-batch fixtures reconcile without blind duplicate writes | U01/U03/U05 |
| C05 | P Hybrid semantic/keyword graph research | Keep Zep path; qualify alternatives | Fixed query set evaluates relevance/ranking and provider failures; no silent degraded fallback | U01/U06/U12 |
| C06 | P Entity details and relationship investigation | Keep entity/edge tools and decomposed research | Multi-hop question retrieves supporting relationships and source context within bounds | U01/U06 |
| C07 | P Current/historical/invalidated claims | Preserve temporal fields and broad searches | As-of/historical query distinguishes superseded from currently asserted information | U01/U06 |
| C08 | P Simulation activity enters research graph | Keep updater and completion barrier | Accepted event batch appears in research; lag/failure/partial ingestion visible | U01/U05/U06 |
| C09 | P Individual and organizational profiles | Reuse generation/format conversion | Both actor types work on both environments with grounded characteristics | U01/U07 |
| C10 | P Automatic setup | Retain suggested profiles/config/seed content | Multi-file upload + natural-language requirement reaches a runnable suggested experiment | U01/U07/U11 |
| C11 | P Each simulation environment | Retain single-environment runners | Twitter-like alone and Reddit-like alone complete actual configured actions | U01/U07 |
| C12 | P Parallel paired environments | Retain parallel runner | Paired actor mapping, independent counters and honest partial failure behavior | U01/U05/U07 |
| C13 | P Twitter-like action catalog | Retain create, like, repost, follow, abstain, quote | Each configured action has state/action-log visibility and invalid-target tests | U01/U07 |
| C14 | P Reddit-like action catalog | Retain post/comment votes, search posts/users, trends, refresh, follow, mute, abstain | Every configured action exercises actual engine state or retrieval behavior | U01/U07 |
| C15 | P Recommendation/exposure behavior | Characterize OASIS policy; retain compatibility mode | Known network/content fixture gives explainable platform-specific exposures | U01/U07 |
| C16 | P Activity/time heterogeneity | Preserve active hours, peak/off-peak and activity probability | Controlled schedules affect selected actors; seed/selection records retained | U01/U07 |
| C17 | P Agent context/memory | Keep CAMEL/OASIS behavior | Prior interactions influence permitted future context; no unauthorized/future information | U01/U03/U08 |
| C18 | P Graph, profiles, live dual feeds and logs | Reuse Vue/D3 components | Real browser flow shows both platforms, graph details and update/progress states | U01/U11 |
| C19 | P Investigative reports | Keep planning, repeated tools, broad/deep research and interviews | Tool trace demonstrates a substantive multi-step question answered without losing depth | U01/U09 |
| C20 | P Individual/batch agent interviews | Retain IPC/environment connection | Selected agent(s) return scoped synthetic answers from living/restored environment | U01/U08/U09 |
| C21 | P Synthetic surveys | Retain batch-question flow | Same questionnaire targets selected cohorts; responses/missing responses export correctly | U01/U09 |
| C22 | P Report follow-up chat | Retain tool-assisted conversation | Follow-up cites permitted graph/run/report evidence and handles missing context | U01/U09 |
| C23 | P English and Chinese | Preserve locale files and complete uncovered strings | Equivalent EN/ZH source-to-report journeys, error messages and download labels | U01/U11 |
| C24 | P History and exports | Retain histories, reports, profiles/config and safe script downloads | Old projects/reports accessible; exported formats remain usable and attributed | U01/U04/U09 |
| C25 | I Source and claim provenance | Add document versions/spans and report claim references | Exact authorized passage opens; normalized offsets remain correct | U04/U06/U09 |
| C26 | I Layered evolving analytical graph | Separate source/assumption/simulation/analysis while federating search | Round/time/layer filters show provenance and preserve research across layers | U06/U11 |
| C27 | I Richer reviewable ontology/population | Editable cohort/organization settings and versioned rules | Edits produce new revisions; no silent rewriting of frozen run inputs | U06/U07 |
| C28 | I Real configuration effects | Wire actual ranking weights, time controls and scheduled events | Parameter -> code consumer -> controlled effect fixture for each exposed setting | U07/U10 |
| C29 | I Central cost/admission controls | Cover LLMClient, CAMEL/OASIS, interviews, reports and external costs | Concurrent calls cannot reuse one remaining allowance; retries/unknown charges visible | U03/U05 |
| C30 | I Authentication and ownership | Local protection + shared identity/workspace model | Negative matrix covers API, binary downloads, graph IDs, interviews, SSE and exports | U02/U04 |
| C31 | I Durable lifecycle | Supervise/reset-safe orchestration and failure reconciliation | API/process interruption preserves acknowledged results and never blindly resets an existing run | U05 |
| C32 | I Recorded playback and reprojection | Capture versioned complete events and relevant artifacts | Playback equals record; rebuilt analytical projection hashes match | U04/U08 |
| C33 | I True checkpoint pause/resume | Engine/native state, memories, scheduler/random states | Destroy process, restore checkpoint, verify state and continue at supported boundary | U03 spike/U08 |
| C34 | I Counterfactual branches | Isolated child engine/knowledge world | Parent hashes unchanged; child prefix and explicit intervention recorded | U08/U10 |
| C35 | I Ensembles/sensitivity | Multi-seed/model/population/config experiments | Matched manifests, missing-run accounting, distributions and shared budgets | U10 |
| C36 | I Cross-platform bridge | Opt-in transfer, delay, actor mapping and lineage | No transfer when off; controlled transfer and loop prevention when on | U10 |
| C37 | I Evidence-aware comparative reports | Metrics, contradictions, section claims, cross-run tools | All displayed numeric claims match metrics; citation support assessed with rubric | U09/U10 |
| C38 | I Guided and advanced workbench | Preserve convenience, add precise control | Guided user effort no material regression; advanced controls act through same services | U11 |
| C39 | I Local/private provider profile | Fully local graph, models and embeddings | Complete feature profile succeeds with cloud egress blocked; parity evaluated separately | U12 |
| C40 | I Extended input support | DOCX, scanned PDF OCR, table-aware passages | Owned fixtures retain structure/source references without degrading existing text PDFs | U13 |
| C41 | I Portable research bundle | Versioned source/config/population/results/evidence and supported checkpoints | Import validates schema/size and recreates supported artifacts without executing content | U04/U09/U13 |
| C42 | I Maintainability and delivery | Typed boundaries, regressions, CI/PRs, release provenance | Clean clone/install/migrate/run/upgrade/restore and exact-revision CI evidence | U00-U14 |
| C43 | I Malay user interface | Additional catalog and documented output-language behavior | Navigation/error/export translation checked; model-language quality separately reported | U11 |
| C44 | I Effective performance improvements | Profiling-led bounded concurrency, caching/batching and UI virtualization | Comparable workload measurements with cost, memory, quality and variance; no fabricated speedup | U13 |

## Actual baseline action mapping

Twitter-like actions in `backend/scripts/run_parallel_simulation.py`: `CREATE_POST`, `LIKE_POST`, `REPOST`, `FOLLOW`, `DO_NOTHING`, `QUOTE_POST`.

Reddit-like actions in that runner: `LIKE_POST`, `DISLIKE_POST`, `CREATE_POST`, `CREATE_COMMENT`, `LIKE_COMMENT`, `DISLIKE_COMMENT`, `SEARCH_POSTS`, `SEARCH_USER`, `TREND`, `REFRESH`, `DO_NOTHING`, `FOLLOW`, `MUTE`.

`INTERVIEW` is manually dispatched rather than offered as an ordinary autonomous action. Other installed-library actions are not credited as application features until wired and tested. Baseline configured actions and new proposed actions must be displayed separately.

## Source reuse map

Paths below are relative to the imported MiroFish root. Original files are reused with attribution, then changed through narrow PRs.

| Source component | Valuable behavior to retain | Planned change / caution |
|---|---|---|
| `backend/app/services/ontology_generator.py` | Typed ontology and global long-document sampling | Keep coverage, add review/configuration; do not enforce old fixed type counts on unrelated new modes without a compatibility decision |
| `backend/app/utils/ontology.py` | Source/target and schema normalization | Reuse and expand validation cases |
| `backend/app/services/graph_builder.py` | Durable IDs, batch/episode reconciliation, processing barriers | Provider interface and persisted state; preserve non-idempotent mutation handling |
| `backend/app/utils/zep.py`, `zep_paging.py`, `zep_lifecycle.py` | Retry classification, paging, reader coordination | Replace process-local limitations with scoped durable coordination, retaining semantics |
| `backend/app/services/zep_entity_reader.py` | Actor candidates, entity/edge enrichment | Stable provider-neutral IDs and provenance |
| `backend/app/services/zep_tools.py` | Hybrid/reranked search, temporal inspection, multi-query tools and interviews | Typed tool results, bounds, layer filters, citation and benchmark data |
| `backend/app/services/oasis_profile_generator.py` | Individual/organization profiles and platform formats | Versioning, editable cohorts, evidence grounding, consistent logical actor mapping |
| `backend/app/services/simulation_config_generator.py` | Automatic scenario/time/seed setup | Keep convenience; expose only effective validated settings, implement intended missing effects |
| `backend/scripts/run_parallel_simulation.py` | Dual environments, action catalogs, schedules, concurrency and retained interview context | Supervision, event capture, deterministic identity, correct reset/restore separation |
| `backend/scripts/run_twitter_simulation.py`, `run_reddit_simulation.py` | Single-platform execution | Keep single mode parity; consolidate duplicated code only after characterization |
| `backend/app/services/simulation_runner.py`, `simulation_manager.py`, `simulation_ipc.py` | Lifecycle, artifacts, interview commands and result collection | Durable records/leases, bounded authenticated IPC, explicit engine capability contract |
| `backend/scripts/action_logger.py` | Actions/events available for monitoring | Versioned event envelope, cursors, result provenance, no secret logging |
| `backend/app/services/zep_graph_memory_updater.py` | Activities -> episodes, final drain/wait | Layered run graph, recorded ingest lag, reconciliation and provider isolation |
| `backend/app/services/report_agent.py` | Outline, tools, iterative sections, report follow-up and wrapper defenses | Evidence model, safe rendering, retained interview depth, bounded logs |
| `backend/app/utils/file_parser.py`, `text_processor.py` | PDF/text extraction and chunking | Preserve quality; isolate parser and add exact document/source references |
| `frontend/src/components/Step1GraphBuild.vue` through `Step5Interaction.vue` | Guided five-stage experience | Smaller typed modules, error/accessibility/security changes, no dropped stages |
| `frontend/src/components/GraphPanel.vue` | Interactive graph and entity details | Layer/time filters, event lineage, virtualization/list alternative |
| `frontend/src/components/HistoryDatabase.vue` and views/router/API wrappers | History/navigation and connected workflows | Ownership-aware history, backward-compatible identifiers and migration paths |
| `frontend/src/i18n/` plus locale resources | English/Chinese support | Full string coverage and added Malay catalog |
| Inherited tests and fixtures | Existing behavior/regression knowledge | Preserve provenance; main reruns and extends rather than claiming historical passes |

## Replacement rule

A proposal to switch Vue, Flask, OASIS, Zep, PyMuPDF or native persistence must name the capability rows it affects, provide a migration and rollback plan, and compare actual behavior/quality. No new framework is itself a product improvement. Retaining a component does not exempt it from supported-version/security maintenance.

## Release interpretation

Full parity means all P rows are accepted in the supported hosted-provider profile. Full private parity requires the relevant same rows accepted in local mode as well. Only a clearly named restricted preview may ship with a narrower profile; it is not the completed project. Main's acceptance record is tied to a commit and environment, not a percentage of vaguely checked boxes.

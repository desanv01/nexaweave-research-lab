# NexaWeave roadmap

The [approved master plan](docs/plan/MASTER-PLAN.md) defines all gates. Source-observed behavior, implementation and accepted evidence are distinct states in the [capability register](docs/plan/CAPABILITY-REGISTER.md) and main ledger.

**Current checkpoint — 2026-10-05:** latest accepted `be004ce23bf5425ab28d9540428bea2759a6c4dc` / [PR86](https://github.com/desanv01/nexaweave-research-lab/pull/86) delivers bounded graph population, grounding and native Save. PR81–86 required gates and full logs were reviewed. Eight broad workstreams remain partially implemented; full end-to-end workflow, all 44 capabilities and release qualification remain open. U07c graph-bound durable preparation is authored and undergoing Main local/browser qualification, not merged or accepted. Public source visibility is not a qualified release or public application deployment.

The table below preserves the **historical U01–U02 planning snapshot** and phase scope. Its early “Planned” labels do not describe today's implementation inventory; the current checkpoint above and Main's exact acceptance records govern. No additional whole-phase completion is inferred from a bounded slice.

| Phase | Scope and gate | Status |
| --- | --- | --- |
| U00 | Traceable source import, attribution, repository delivery and initial CI | Accepted; PR [#2](https://github.com/desanv01/nexaweave-research-lab/pull/2), merge1166188 |
| U01 | Characterization, fixtures, Graphiti/Neo4j compatibility and contract | U01a/U01b accepted PR5/PR6; research characterization and live qualification remain |
| U02 | Local security patches and access isolation | U02a accepted PR7; IPC implementation and other security gates remain open |
| U03 | Complete Graphiti provider cutover; inherited workflow without Zep credentials | Planned |
| U04 | Persistence and evidence authority | Planned |
| U05 | Durable execution, accounting and cancellation | Planned |
| U06 | Temporal, layered graph research and retrieval quality | Planned |
| U07 | Simulation controls and dual-platform breadth | Planned |
| U08 | Playback, checkpoints and parent-preserving branches | Planned |
| U09 | Investigative reports, interviews, surveys and exports | Planned |
| U10 | Ensembles, sensitivity and comparative experiments | Planned |
| U11 | Workbench, English/Chinese/Malay journeys and accessibility | Planned |
| U12 | Qualified fully local private mode | Planned |
| U13 | DOCX/OCR/tables, performance and recovery tools | Planned |
| U14 | All capability gates, notices, packaging and exact-revision release | Planned |

The locked architecture uses Graphiti plus self-hosted Neo4j Community, PostgreSQL application authority and Temporal durable orchestration. Initial generation is configurable for official DeepSeek `deepseek-flash`, separately from embeddings; live model qualification remains open. Paid calls require local credentials and a concrete total spending cap. Zep is not a target prerequisite; its historical use remains documented in the upstream archive. Every completed box requires Main-recorded evidence on an exact revision. NexaWeave branding preserves upstream MiroFish notices and persisted compatibility identifiers.

# Roadmap

The [approved master plan](docs/plan/MASTER-PLAN.md) defines all gates. Source-observed behavior, implementation and accepted evidence are distinct states in the [capability register](docs/plan/CAPABILITY-REGISTER.md) and main ledger.

| Phase | Scope and gate | Status |
| --- | --- | --- |
| U00 | Traceable source import, attribution, repository delivery and initial CI | Accepted; PR [#2](https://github.com/desanv01/mirofish-research-lab/pull/2), merge1166188 |
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

The hybrid target is Graphiti plus self-hosted Neo4j Community with configurable model providers. Initial generation is targeted at the official DeepSeek V4.1 Flash API; live qualification and budget limits remain open. Zep is not a target release prerequisite, although the U00 imported application still requires it. Every completed box will require main-recorded evidence on an exact revision.

# Changelog

All notable accepted project changes will be recorded here. Entries that are still in a draft PR are explicitly unverified.

## Unreleased — U01a compatibility spike accepted

- PR5 / merge `e4eafb2024dbe246e93b9973e0dbd90b09ca9b40`: isolated locked Graphiti0.30.2 / Neo4j5.26.31 Community adapter, scoped contracts, provenance and uncertain-write guards, configurable model routes and bounded calls.
- Real-database CI passed17 tests with synthetic LLM/embedding clients; post-merge CI36215384237 passed. No live paid model qualification or complete application cutover is implied.

## Unreleased — U00 repository delivery accepted

- Imported the 128-file MiroFish archive as an attributed snapshot. Archive SHA-256: `d3bef0afea92b99626526ffcce0508414feb3f9e88c3edda1f283ce5f447bf53`; source commit unknown.
- Preserved both inherited workflows as inert references and added a minimal, credential-free CI workflow for source integrity, inherited unit fixtures and frontend build.
- Added repository documentation and a single recorded test-path adaptation for the quarantined star-history workflow.
- [PR2](https://github.com/desanv01/mirofish-research-lab/pull/2) accepted as merge1166188: Linux167 tests, manifest and frontend build passed, including post-merge CI36214869721. This establishes a delivery baseline, not a runtime or security release.

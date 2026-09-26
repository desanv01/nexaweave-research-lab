# Main-chat / phase-worker protocol for the derived upgrade

Version 3.0, approved Graphiti/Neo4j execution baseline. Supersedes the source-isolation and independent-rewrite rules in `_rebuild_plan/ORCHESTRATION-PROTOCOL.md`.

## Roles

The main chat owns planning, architecture, reverse-engineering analysis, capability definitions, task dispatch, all code review/audit/checks, GitHub integration and acceptance. Requested setting is GPT-6 Sol/high; this document does not change a running model setting.

Each phase or substantial implementation task has a fresh worker chat using `gpt-6-sol` with `medium` reasoning. Workers implement instructions and return work as unverified. They may read the inherited source, tests, source analysis and relevant dependency code within the assignment; the user has explicitly changed that authorization.

Workers do not run tests, lint/type checks, verification builds, browser QA, security/license scans or acceptance audits. They do not claim parity, certify completion, merge, publish, change review gates or independently revise architecture. Reading code and writing the assigned implementation are permitted. Main may delegate writing specifically defined test fixtures; main reviews and executes them.

Automatic GitHub Actions is part of main's verification system. Main creates/pushes commits, controls PRs and reads the results. Workers do not use auto-CI as a reason to run their own QA cycle or claim acceptance. Implementation commands that implicitly invoke check hooks must be specified by main; do not quietly disable checks in order to push.

## Project and worktrees

Create workers in the new derived project's Git worktrees after resolving the actual project ID, repository path and accepted base. Do not create standalone tasks in the research folder by accident. Fresh chats keep context focused; their purpose is not clean-room isolation in this revised plan.

Source access is allowed, but scope remains bounded. No unrelated repositories, credentials or private data are handed to workers. Worker ownership of files prevents collisions; it is not a claim that a worktree provides a filesystem security sandbox.

Default one active worker; at most two initially when contracts and write paths are disjoint and main can promptly review both. Serialize migrations, shared interfaces, lockfiles, workflow definitions and release metadata.

## Persistent records

Store `coordination/ledger.md`, `coordination/decisions.md`, `coordination/tasks/`, `coordination/handoffs/`, `coordination/reviews/`, a machine-readable capability register, benchmark manifests and upstream patch records in the derived repo. Public versions must be sanitized.

Each task record contains phase/capability IDs, packet version, worker ID, base/HEAD SHA, worktree/branch, upstream files involved, PR URL, implementation state, verification evidence, blocking findings, accepted merge SHA and next task. Main resumes from these records after context compaction rather than assuming a previous worker's summary establishes correctness.

States: planned -> ready -> dispatched -> implementing -> handed-off-unverified -> main-review -> accepted. A failed review returns to changes-requested/implementing. Blocked required live-model checks remain blocked and cannot be marked passed from fake-provider results. Missing Zep access is not a blocker; reference comparisons are optional.

## Main dispatch packet

Every packet has:

1. A bounded outcome and capability IDs.
2. Accepted repository base and exact reference/import/patch versions.
3. Files to inspect, including upstream implementation worth retaining.
4. Allowed write scope and dependency/command list.
5. Behavior to preserve, behavior to change and migration/rollback constraints.
6. Input/action/configuration semantics, including failure, cancellation and costs.
7. Required source attribution and patch records for copied/adapted code.
8. Main-owned acceptance criteria and required handoff contents.

Do not ask the worker to invent a simpler substitute for difficult inherited behavior. A local implementation decision is allowed within the contract; dropping a feature or changing engine semantics requires main's decision and amended packet.

## Handoff and review loop

Worker returns changed files, implemented behavior, upstream code reused, dependencies/patches/migrations, commands actually run, suspected issues/incomplete work and the statement that verification was not performed.

Main inspects scope, secrets and provenance before committing/pushing. Main opens/updates the phase PR and attaches it to this task. Main reviews every changed behavior, runs local and CI checks, and tests actual integration. Relevant cases include graph reconciliation, action catalog, recommendations, source spans, paired-platform status, interviews, privacy, cost accounting, reset/restore and old-data migration.

Corrections are sent as narrow packets with reproduction, expected behavior and affected capability IDs. Worker implements; main rechecks affected paths plus necessary combined-state tests. Main does not accept a diluted rubric or delete a failing inherited test without investigating whether it exposes a real regression.

Main accepts only the reviewed HEAD and target combination, merges through the configured checks, and verifies the merged revision. It updates roadmap/status/evidence, closes the milestone and archives the phase chat only when appropriate. A later substantial phase gets a new chat; small remediation remains with the active worker.

All normal commits, pushes, PR updates and qualified merges belong to the requested GitHub workflow. Do not repeatedly ask the user for the same routine authorization. The user has approved execution and the private repository default. Paid model configuration/caps and any public deployment need concrete configuration before dependent work starts.

## Worker prompt template

```text
Task: <Uxx task and capability IDs>
Role: implementation only
Model: gpt-6-sol / medium
Repository/worktree: <verified>
Base commit: <accepted>
Branch/PR: <assigned>

Objective:
<Concrete outcome>

Read:
<Assigned inherited files, original analysis and current contracts>

Preserve:
<Existing behavior/actions/research/languages/exports that cannot regress>

Implement:
<Exact changes and failure/migration/rollback semantics>

Allowed writes/dependencies/commands:
<Explicit list>

Attribution:
<Source version, license and patch record to preserve>

Acceptance criteria for main:
<Behavioral tests/observations main will perform; do not run them yourself>

Do not run tests, checks, audits, validation builds or browser QA.
Do not merge, publish, alter shared contracts or certify parity.
Report missing contracts or suspected problems rather than simplifying scope.

Return:
<Handoff file with changed files, reuse notes, commands, migrations,
incomplete behavior and explicit unverified status>
```

## User-facing phase report

Main reports what now works, which inherited capabilities remained intact, which improvements were demonstrated, the PR/merge revision and actual check results, remaining limitations, and next phase. Do not claim that a new framework, an imported codebase or a green build proves a better simulator.

## Current state

Execution authorized. Graphiti + self-hosted Neo4j Community is locked; Zep is not required. Preserve all 44 requirements. Actual repository/worker/PR/check state belongs in coordination/ledger.md. User-reported permission remains task context, not a fabricated reviewed legal document.

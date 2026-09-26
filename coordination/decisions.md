# Execution decisions

- Approved v3 architecture: Graphiti + self-hosted Neo4j Community, retaining
  Vue/Flask/OASIS/CAMEL; no mandatory Zep account or runtime fallback.
- DeepSeek official API is the initial generation provider, model `deepseek-flash`
  (V4.1 Flash); future providers/models and embeddings are separately configurable.
  Paid-test cap and local credentials are not configured yet.
- All implementation tasks are attached to the MiroFish project. Isolated
  worktrees now live under its `_implementation_worktrees/` directory. The
  attributed integration repository remains the approved sibling folder.
- Old projectless tasks were archived after their work was preserved. U00's
  uncommitted worktree was moved with git worktree move, not copied/discarded.
- Main owns all audits/reviews/tests and GitHub. Workers use GPT-6 Sol/medium.
  User requests Fast mode for main and all new workers until revoked. Codex's
  default is already service_tier=priority; preserve it. Task creation has no
  explicit Fast-mode override, so no unsupported per-task assertion is made.
- Main follow-up `mirofish-main-orchestration` is active every 15 minutes. Respect
  future user pauses, product availability and usage limits; no 24/7 guarantee.
- GitHub returned403 for private branch protection under the current plan.
  Keep private visibility. Main-controlled PR/CI/acceptance is procedural, not
  an enforced two-person or protected-branch guarantee.

## Task routing

| Phase | Current project task | Worktree |
|---|---|---|
| U00 | 01a0dbb8-53e4-76e0-911f-14824a764e0f | _implementation_worktrees/u00 |
| U01 | 01a0dbb8-8cdd-7063-ba8a-14753f6a585c | _implementation_worktrees/u01 |

U00 PR: https://github.com/desanv01/mirofish-research-lab/pull/2.
No phase is accepted by this decision record.

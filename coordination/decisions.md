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
  User resumed 2026-09-29: main GPT6 Astra/high Fast OFF, workers GPT6 Sol/medium
  Fast ON only for workers. This supersedes earlier Fast settings. Task tools
  expose worker model/reasoning but no Fast flag or main-model setter; user must
  confirm those UI controls. Do not alter unrelated global defaults.
- Main follow-up `mirofish-main-orchestration` is active every 10 minutes (verified
  from its current saved configuration). Respect
  future user pauses, product availability and usage limits; no 24/7 guarantee.
- GitHub returned403 for private branch protection under the current plan.
  Keep private visibility. Main-controlled PR/CI/acceptance is procedural, not
  an enforced two-person or protected-branch guarantee.

## Task routing

| Phase | Current project task | Worktree |
|---|---|---|
| U00 | 01a0dbb8-53e4-76e0-911f-14824a764e0f | _implementation_worktrees/u00 |
| U01 | 01a0dbb8-8cdd-7063-ba8a-14753f6a585c | _implementation_worktrees/u01 |
| U02 | 01a0dbc4-d633-7732-a922-e9a7ad5e82b6 | _implementation_worktrees/u02 |
| U03 | 01a0dbe3-ed5b-7f51-a339-3b09cdd02fe6 | _implementation_worktrees/u03 |

U00 PR: https://github.com/desanv01/mirofish-research-lab/pull/2.
U00 delivery accepted at merge1166188b2d5dafbab178e72ac1b1cc130bb732f1;
post-merge CI36214869721 passed. No inherited application capability is accepted
on that basis. U01 issue3 and U02a issue4 track the next bounded implementation.

Local disposable Neo4j startup stalled at Docker network creation. Main stopped
only its own compose CLI, without restarting Docker or changing other projects'
containers. The real database qualification is routed to an isolated CI job.

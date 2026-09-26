# Execution ledger

## Authorization and locked decisions

User approved the Graphiti/Neo4j consolidated plan and execution of U00–U14.
Primary: self-hosted Graphiti + Neo4j Community; configurable paid model APIs.
No Zep requirement. Preserve the 24 inherited capabilities and 20 upgrades.
Main owns checks and GitHub; fresh GPT-6 Sol/medium workers implement only.

## Current work

| Task | State | Evidence / next gate |
|---|---|---|
| Plan v3 synchronization | Completed | All active reference docs synchronized; v2 archived |
| U00 repository bootstrap | Accepted delivery baseline | PR2 merged as 1166188b2d5dafbab178e72ac1b1cc130bb732f1; post-merge CI36214869721 green; Linux167 tests,128-file manifest and frontend build passed; not application qualification |
| U01 parity and knowledge compatibility | U01a/U01b/U01c bounded slices accepted | PR9/merge5d77aab; post-merge CI36216733446 green;236 Linux tests,17 knowledge and3 native tests; no paid/full capability qualification |
| U02 security | U02a–U02e accepted; U02f Main review | PR22 exacthead0739746/mergeb4f4c9e, CI36219445042 and post36219619751 all6 passed:417 Linux,29 knowledge,27 operations,3 native plus imports,manifest128/15,frontend. Issue23 worker corrected parser-process handoff, Main rerun pending on task/u02-parser-process. No route cutover; upload admission/auth/rendering/hard resource sandbox remain |
| U03 adapters and operation authority | U03a–U03c accepted; U03d implementing | PR21 exacthead689d7f9/merge7e8913e, CI36218981185 and post36219172231 green. Worker01a0dbe3-ed5b-7f51-a339-3b09cdd02fe6 assigned issue24, task/u03-command-boundary atb4f4c9e, full packet in u03/coordination/tasks/U03d.md. Strict internal dispatcher only, no listener, paid execution or full provider cutover |
| U04–U14 | Planned | See approved master plan; no gate accepted |

Repository: https://github.com/desanv01/mirofish-research-lab (private).
Implementation root: `C:\Users\Dv\Desktop\MiroFishResearchLab`.
Reference archive remains outside the repository, untouched.

Workers are attached to the MiroFish project and use worktrees inside that
project's `_implementation_worktrees/` directory. See decisions.md for task IDs,
Fast-mode preference and recurring main supervision. Main is active; changing
Fast mode is not a user request to pause execution.

No capability, live provider, Neo4j integration or application security gate has
passed merely because source was imported. Model credentials and spend limits
remain unconfigured for live tests. Zep credentials are neither requested nor needed.

## Bootstrap exception

Main creates a minimal governance commit on main before branch gates exist.
The archive import and U00 implementation go through a phase branch and PR.
All later work uses reviewed phase/task PRs. No worker acceptance substitutes for
main review. GitHub protection availability will be probed and recorded honestly.

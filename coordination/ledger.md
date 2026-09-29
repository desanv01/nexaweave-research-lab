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
| U02 security | U02a–U02g accepted; U02h Main verification | PR28 mergebf7c641, exact-head and postmerge36221146686 all6 passed. Issue30 safe rendering passed8 DOM tests/build; actual-browser fixture under Main review, not accepted |
| U03 adapters and operation authority | U03a–U03d accepted; U03e CI candidate | Issue29 private-pipe client/stdio boundary corrected after Main review. Local78 passed1 POSIX skip in21.92s, manifest128/15 and diff passed. Initial one-off10s fixture timeout not reproduced, cause unconfirmed. Needs six exact-head CI gates; no bootstrap/cutover/paid execution |
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

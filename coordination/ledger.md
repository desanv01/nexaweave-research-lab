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
| U02 security | U02a/U02b/U02c accepted; U02d implementing | PR14 exactheadb99279e/merge9da8b0c, CI36217628059 all6 green (317 Linux,17 knowledge,12 ledger,3 native plus actual script imports). Worker01a0dbc4-d633-7732-a922-e9a7ad5e82b6 on task/u02-browser-boundaries; authentication/upload/rendering remain |
| U03 adapters and operation authority | U03a accepted; U03b implementing | PR12 exactheadd097843, merge24108c5; all6 gates green, post-merge CI36217434202 green;12 ledger tests. Worker01a0dbe3-ed5b-7f51-a339-3b09cdd02fe6 implementing task/u03-ledger-ingest in _implementation_worktrees/u03. No full provider cutover |
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

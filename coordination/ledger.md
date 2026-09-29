# Execution ledger

## RESUMED — 2026-09-29

User explicitly resumed full orchestration. Main GPT6 Astra/high Fast OFF;
workers GPT6 Sol/medium Fast ON. Task tools have no Fast flag; user notified.
No global defaults changed. Historical pause note below is superseded.

## Historical pause — 2026-09-26

All work paused until explicit resumption; heartbeat disabled. See reference
`C:\Users\Dv\Desktop\MiroFish\_upgrade_plan\PAUSED.md`. Resume main GPT6 Astra high,
workers GPT6 Sol medium, Fast OFF for all (overrides previous Fast preference).
U03e PR31 CI completed successfully but not accepted/merged. U02h stays dirty:
initial browser fixture passed, final fixture restart exit0/no listener unresolved.
Preserve work; do not continue checks/implementation/Git while paused.

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
| U02 security | U02a–U02g accepted; U02h CI preparation | Issue30: Main8 DOM tests/build/128-19 manifest passed. Actual Step5 synthetic report/chat and collapse/expand repeated Sept29 after fixture lifecycle fix. Step4 source sinks reviewed; no full live/backend qualification. Exact-head CI pending |
| U03 adapters and operation authority | U03a–U03e bounded slices accepted; postmerge regression under correction | PR31 exacthead76cc513 all6 CI36222171376 passed; merged60fc7e8. Postmerge36502731553 other5green but Linux502pass1fail after Werkzeug3.1.9 changed multipart test encoding behavior. U01d separate fixture correction under Main review. No bootstrap/cutover/paid qualification |
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

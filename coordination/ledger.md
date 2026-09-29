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
| U01 parity and knowledge compatibility | U01a–U01d bounded slices accepted | PR32 head43cb6b8 all6 CI36504019341 green, mergea34da3c. Multipart fixture compatibility51 tests passed on Werkzeug3.1.8/3.1.9; production policy unchanged. No paid/full capability qualification |
| U02 security | U02a–U02h bounded slices accepted | PR33 head0940090 all6 CI36504074280 green, merge7894b6a. Main8 DOM tests/build and actual Step5 synthetic browser report/chat/collapse-expand; seven HTML sinks reviewed. No full live/backend/security qualification |
| U03 adapters and operation authority | U03a–U03f accepted; U03g reviewed candidate | PR35 exact32e033d all6 CI36505686164 passed, including108knowledge with realNeo4j metadata. Merged dcb6da0; postmerge36506001586 success. Issue36 U03g corrected after Main Unicode/fixture review;47targeted pass, actual dispatcher probe pass,533full pass18skip2known Windows symlink failures. Needs six CI gates. No bootstrap/cutover/paid qualification |
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

# GitHub delivery, CI/CD and repository presentation

Version 3.0, approved Graphiti/Neo4j execution baseline. Repository operations are now authorized; actual writes and acceptance are recorded in coordination/ledger.md.

## 1. Repository reference and presentation contract

User-selected reference: [desanv01/deepseek-harness-desktop-app](https://github.com/desanv01/deepseek-harness-desktop-app).

Pinned reference commit read through GitHub's public API: `7a798fd79d3a611a1a591cd00752841130f3bcf0`.

The raw README at that commit has 438 lines. The web cache served an older/different line count, so the pinned raw document is the presentation reference. We inspected its header, complete section order and final status/roadmap/security/attribution areas. The repository's CI/release workflow sources were also inspected. No screenshot asset was downloaded or reused.

Match the README's native GitHub Markdown presentation:

1. H1 project name.
2. Bold one-sentence product description.
3. Inline shields-style badge row.
4. Short explanatory paragraph.
5. Blockquoted project-status callout with actual verified environment and limitations.
6. Horizontal separator.
7. Captioned screenshots in the same stacked presentation.
8. The section order listed below, with readable prose, parallel feature bullets, tables and a Mermaid flow.
9. License/attribution, closing separator and short italic closing line.

Do not add a centered marketing hero, invented benchmark badges, desktop-platform badges or a separate website design in place of this structure. Do not copy the reference project's MIT badge, .NET/Windows requirements, commands, screenshots or verification claims into an unrelated stack. The structural template stays the same; factual content belongs to this project.

### Exact main section sequence

| Reference section | New project content, same position/layout |
|---|---|
| Screenshots | Real guided workflow, dual-platform monitor, graph/evidence and report/experiment captures; captions |
| Why this exists | Improve the existing research/simulation workflow while preserving its capabilities |
| What it does | Verified inherited functions and completed upgrades; clear status for planned work |
| How it works | Actual source -> graph -> profiles/config -> simulation -> research/report -> interaction diagram |
| Requirements | Tested Python/Node/container/service/model requirements, with self-hosted hybrid and fully local profiles |
| Quick start | Run released package/Compose profile, build from source, smoke test, packaging and release instructions |
| Command-line reference | Only implemented setup/run/export/diagnostic commands and their flags/defaults |
| Where things live | App config, documents, DBs, native snapshots, model artifacts, logs and exports |
| Project structure | Current repository tree, not speculative folders presented as existing |
| Troubleshooting | Provider auth/quota, parse failure, graph lag, engine failure, migrations and restore |
| Current status | Accepted commit/environment, check evidence, benchmark scope and known limitations |
| Roadmap | Checkboxes derived from accepted phase/capability records; link detailed roadmap |
| Security | Actual binding/access/data/provider/source-delivery behavior and disclosure route |
| Contributing | Focused PRs, worker/main workflow, reproduction expectations, inherited notices |
| License and attribution | MiroFish derivation, applicable license(s), OASIS/CAMEL/Graphiti/Neo4j/legacy-Zep and other credits and new contributions |

Keep the reference's useful Quick start progression. Its plugin-publishing subsection maps to packaging/publishing our actual deliverables; there is no fictional npm plugin. The launch-sequence subsection becomes our actual launch/processing sequence. README-ZH mirrors the accepted structure and user-facing instructions; Malay docs can follow the language rollout.

Status badge starts as planning/importing, then baseline verified, then upgraded beta/release only after evidence. License badge reflects the effective inherited terms, not a desired future relicensing. Add working CI/release badges only after the corresponding workflow/tag exists. Screenshots are captured from the accepted app, with synthetic/sanitized data; label placeholders honestly before captures exist.

Repository About uses a concise product description, truthful topics and links to actual docs/releases. Use native GitHub Releases, Actions, issues, PRs and license detection. A separate GitHub Pages site is not implied by the request for the repository's page look; it can be a later scoped addition.

Reference README roadmap text has some stale entries relative to its existing workflows. Follow its layout without copying those inconsistencies. Main verifies our status/roadmap against accepted work at each phase.

## 2. Repository ownership and visibility

**Latest direct human decision — 2026-10-05:** the human resumed implementation and explicitly authorized public source visibility and a new product/repository name without further confirmation. Main recorded and verified PUBLIC [desanv01/nexaweave-research-lab](https://github.com/desanv01/nexaweave-research-lab); the product is NexaWeave. This supersedes the initial private-source restriction for this repository. Public application deployment and uncapped paid calls are not authorized. Keep existing runtime, package, API and persisted compatibility identifiers; a visible rename is not a data migration or history rewrite.

**Historical U00 decision:** approved default `desanv01/mirofish-research-lab`, initially private. Main confirmed authenticated owner/repo identity and availability before creation during U00. Reading a public reference did not establish write access to a future repository.

The initial proposal was a separate private repository with an attributed import, subject to GitHub constraints. That initial visibility choice is superseded by the dated public-source decision above. Retain upstream history only when actually available/matched. Initial governance/bootstrap is recorded before protections are enforceable; subsequent work goes through PRs.

Maintain `UPSTREAM.md`, the ZIP hash, selected upstream SHA(s), `LICENSE`, third-party notices and a change/provenance log. Upstream MiroFish code and tests remain attributed even when refactored or visibly rebranded. No relicensing is implied. Qualified application release/deployment and any special licensing grant remain separate recorded choices.

## 3. Branch model

`main` is the protected, accepted integration branch. No permanent `develop` branch is required for this team size.

Phase branches follow `phase/00-baseline-repository` through `phase/14-release-qualification` as listed in MASTER-PLAN.md. Create each from the latest accepted `main`. A phase may have a series of small PRs under the same milestone; do not hold weeks of changes unreviewed until a giant final PR.

For a phase too large for one branch, approved task branches use `task/uNN-<short-scope>`. Each has an explicit base and destination. Prefer independently reviewable PRs to `main` with feature flags when safe; use a phase integration branch only when dependencies require it. Re-run the full phase suite on the combined result.

Emergency fixes use `fix/<scope>`; upstream selected changes use `upstream/<short-sha>-<scope>`; dependency updates use `deps/<package>-<version>`. Main allocates names and serializes migrations/lockfile edits. No unattended merge bot or broad dependency-upgrade batch.

## 4. Commit, push, PR and merge cycle

1. Main writes a phase issue/milestone and a bounded worker packet with capability IDs.
2. Main creates/assigns the isolated worktree and fresh medium-effort implementation chat.
3. Worker edits assigned files and returns an unverified handoff. Worker does not run QA or push.
4. Main inspects the change scope, credentials/data exposure, notices and dependencies before sending it to GitHub.
5. Main creates logical commits: e.g. `fix(storage): scope report deletion to project objects`. Commit frequency follows coherent checkpoints, not every edit or a fixed timer.
6. Main pushes the phase/task branch and opens or updates a draft PR early enough to retain a remote checkpoint. PR includes problem/behavior, capability IDs, migration/rollback, provenance, and verification status.
7. Main attaches every created PR to the current Codex task. Implementation work is now reviewable on GitHub; unverified status remains explicit.
8. GitHub Actions runs automatic checks. Main reads failures/artifacts and performs remaining local/browser/live-model checks. Automatic CI belongs to main's verification process; it does not authorize workers to run tests.
9. Main sends targeted remediation to the worker. Corrections become new commits/pushes on the same PR, keeping review history.
10. Main resolves integration conflicts, checks the latest head and target combination, records acceptance and makes the PR ready.
11. Merge only with required checks green and unresolved blocking findings cleared. Prefer merge commits for phase PRs to preserve the reviewable commit series; do not combine a merge-commit policy with a conflicting linear-history rule.
12. Verify the actual merged `main` revision through CI/integration checks; update milestone and main ledger, then delete the merged branch after confirming no dependent work needs it.
13. Create immutable milestone/release tags from accepted commits. Never force-move published tags.

Routine commits, pushes, PR management and qualified merges are part of the requested execution workflow once the plan is approved; main does not request redundant permission for each normal phase action. Broad publication/deployment or unexpected destructive operations remain separately scoped.

The review record must identify the exact reviewed HEAD and base. Changes after acceptance invalidate that acceptance until rechecked. Store final exact-SHA acceptance as a PR/check artifact rather than changing source after its final test merely to record its own hash.

The public-source decision does not relax delivery gates. Main reviews every required PR and push job and full combined/step logs for the exact reviewed revision before a match-head merge, then every required exact post-merge job and full logs before integration acceptance. Preserve failures and unchanged accepted evidence; do not substitute CI-only success for remaining local/browser/live workflow qualification or invent reviewer approvals.

## 5. GitHub protection and single-account constraint

Configure PR-only changes, required status checks, current-base/merge checks where appropriate, resolved conversations, protection against branch deletion/force pushes, and protected release tags/environments as the account/repository plan allows. GitHub feature availability differs for public/private repositories and plans; U00 verifies enforceability rather than claiming protection from a settings file alone. [Protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)

GitHub does not allow an author to approve their own PR as a separate required reviewer. Main and worker chats using one user account are not separate GitHub identities. Therefore do not configure two-person approval that this arrangement cannot satisfy. Main records its audit, controls merging and consumes required checks; if an independent collaborator is later available, enable formal required-review rules. [GitHub review rules](https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/approving-a-pull-request-with-required-reviews)

Where supported, a main-controlled acceptance check tied to exact HEAD can be required. Its credential/app identity must be unavailable to workers. Otherwise describe main acceptance as a process control, not a cryptographic second-person guarantee. Do not manufacture an approving bot identity to pretend two independent reviews occurred.

## 6. GitHub Actions workflow specification

Use separate, understandable workflows instead of an opaque omnibus script.

| Workflow | Trigger | Work | Secrets / gate |
|---|---|---|---|
| `ci.yml` | PRs; pushes to active phase/task branches and main | Backend tests, frontend build/type/component checks as introduced, contracts and deterministic parity fixtures | No paid credentials; stable job names |
| `integration.yml` | PRs affecting integration; main; manual | Real disposable Neo4j Community/PostgreSQL, fake model responses, Graphiti adapter; workflow/identity/migration/browser cases as introduced | Synthetic data; no production volumes |
| `security.yml` | PR/main; scheduled | Secrets, dependency/container vulnerabilities, license inventory/diff and SBOM where applicable | Tools selected for repo/account availability; findings adjudicated by main |
| `docs.yml` | Documentation or relevant product changes | Markdown/links, schemas, README/version/command consistency, localization completeness | No publish credentials |
| `live-evaluation.yml` | Main-authorized manual dispatch on reviewed code | Budgeted approved-model + self-hosted Graphiti journeys; optional separately authorized Zep comparison only | Protected environment; capped spend; sanitized artifacts; never untrusted fork code |
| `release.yml` | Accepted version tag or explicit dispatch | Verify tag/version/review state, package exact source/images, checksums/SBOM/notices, create draft release | Narrow content/package writes; no broad default token |
| `deploy.yml` | Approved release/environment action when hosting exists | Deploy already-built digest, migrations, health checks and rollback | OIDC/scoped secret where supported; environment controls |
| `upstream-check.yml` | Manual or later agreed schedule | Compare recorded upstream revision, produce update report/issue/PR candidate | Read-only baseline unless main explicitly directs a PR; never auto-merge |

Automatic PR checks do not call paid models. Mocked provider tests verify application behavior, not live provider quality. The live-model gate remains distinct and is recorded for milestones that require it. No ordinary CI job or normal deployment requires Zep credentials. Real database integration is not replaced by graph mocks. Keep nightly/scheduled expensive workloads off until a budget and cadence are agreed.

Backend CI preserves existing tests and adds behavior-specific regression cases. Frontend checks expand with typing/component changes; do not call a build a comprehensive UI test. Integration runs each supported migration path and real browser journeys with fixtures. Native OASIS compatibility tests validate state/action behavior even when language generation is faked.

Always emit stable required-check results: path-filtered or skipped optional jobs cannot leave a required check pending indefinitely or falsely report a missing integration as passed. Configure cancellation of superseded branch runs and bounded timeouts/artifact retention to control CI minutes. Windows setup smoke and Linux container integration are separate jobs; no claim of macOS support without tests.

## 7. Actions security and artifacts

Pin third-party actions to reviewed commit SHAs and update through PRs. Default token permissions to `contents: read`; give release/deploy jobs only required write scopes. Avoid running untrusted PR code with `pull_request_target` secrets or privileged self-hosted runners. Do not interpolate arbitrary PR titles/branch names/dispatch inputs into shell source. [GitHub Actions secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use)

Use ephemeral runners or isolated jobs; no mounted real research data or production Docker sockets for untrusted checks. Treat restored caches and downloaded artifacts as untrusted inputs. Separate provider secrets from build credentials. Use OIDC short-lived deployment credentials where supported rather than copying the reference project's npm token setup into this project.

Artifacts include test reports, sanitized browser captures, coverage where meaningful, migration summaries, capability results, benchmark manifests and SBOMs. Raw documents, keys, user content and sensitive provider payloads stay out of public CI artifacts. Define retention and maximum sizes. Every result identifies commit, dependencies, test profile and skipped portions.

## 8. Release and CD policy

Use semantic project versions by default (`v0.1.0`, `v0.2.0`, etc.), separately from upstream versions and engine schema/checkpoint versions. The user's request fixes README/page layout, not the reference app's date-based executable version algorithm. One version source is checked against tags and release metadata.

Milestone tags such as `milestone/u02-hardened-parity` are immutable accepted checkpoints, not necessarily public releases. PR merges can produce private build artifacts continuously. Tagged release workflows create draft releases; final publication follows the agreed visibility and acceptance policy. Do not automatically publish every phase as a stable user release.

Release bundle includes application source corresponding to the build, container digests or supported installation package, configuration examples without secrets, dependency lockfiles, source/third-party notices, SBOM, checksum manifest, changelog, supported runtime matrix and upgrade/rollback instructions. Checksum comparison detects corruption, not a compromised release publisher; provenance/signing guarantees must be described accurately.

Deployment promotes the same tested image digest rather than rebuilding from a mutable branch. Before a schema-changing deployment, create/verify backup and assess backward compatibility. Use expand/contract migrations where feasible. Rollback covers application and database compatibility; rolling back only the container is not universally safe.

U00-U02 run locally and do not expose an insecure imported baseline. Staging begins only after its access/security gates. Hosting accounts, domains, credentials and costs are execution choices, not resources already available or purchased.

## 9. Phase updates and upstream maintenance

Every accepted phase updates its GitHub milestone/issues, capability evidence, CHANGELOG, relevant README/current-status entries and ROADMAP. Screenshots are updated when visible behavior changes. README CI/release badges point to this repo's actual jobs/releases, not the reference project.

Main reports PR URL, commits/checks, accepted capability changes, remaining limitations and the next phase. User-facing status should make it possible to distinguish source reuse, implemented changes and verified improvements.

Upstream fixes are reviewed as separate changes with source SHA and affected tests. Re-run all changed capability gates. A fixed baseline makes comparison reproducible while selective upstream maintenance prevents stagnation; it is not a permanent freeze on security updates.

# Contributing

This is an attributed MiroFish derivative under active, gated development. Open an issue for a focused defect or capability, then use a small PR with the affected capability IDs from `docs/plan/CAPABILITY-REGISTER.md`. Explain behavior, migration and rollback impact, original source/patch provenance, dependencies, and what was actually checked. Do not label a mock-only check as live provider qualification.

The main maintainer controls review, tests, CI interpretation, commits, merges and acceptance of an exact revision. Worker handoffs are explicitly unverified. The private repository currently has process-controlled PR/CI/main acceptance; required private-branch protection is unavailable on the current GitHub plan, so no protected-main guarantee is claimed. Keep changes to imported application behavior separate from documentation/bootstrap changes where practical.

Use [development notes](docs/development.md) for the current limited checks. Preserve imported notices and the baseline manifest. When changing an imported file, record an explicit original/current SHA-256 exception with a reason in `docs/upstream/patches.json`; do not rewrite the archive inventory. Tests should cover meaningful behavior and report provider/database assumptions. Never include credentials, uploaded documents, model payloads or runtime data in a PR.

Security reports follow [SECURITY.md](SECURITY.md). Public deployment and paid live evaluations require separately configured gates and limits.

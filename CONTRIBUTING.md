# Contributing to NexaWeave

NexaWeave is a public source research workbench under active development. Contributions that make evidence, simulation behavior and investigative results easier to verify are welcome.

## Before a pull request

Choose a focused change and refer to relevant IDs in the [capability register](docs/plan/CAPABILITY-REGISTER.md). Explain the behavior, data or migration impact, source provenance and what you actually checked. Keep synthetic fixture results separate from real database, browser and provider qualification. Do not claim a mock run proves live model behavior.

For the currently supported source checks, follow [development instructions](docs/product/development.md). Include meaningful tests for changed behavior where appropriate. Main reviews the exact source and complete CI logs before accepting a revision; a worker handoff alone is unverified.

## Source and data care

Preserve the [license](LICENSE), [third-party notices](THIRD_PARTY_NOTICES.md) and imported source provenance. Record approved changes to imported files with original and current hashes in docs/upstream/patches.json; do not rewrite the archive manifest. Ordinary visible branding is NexaWeave, while package names, persisted identifiers and environment names remain compatibility interfaces until a separately qualified migration.

Never put credentials, uploads, private documents, database files or raw model payloads in a PR. Public source visibility does not authorize app deployment. Paid evaluations require actual local credentials and a concrete total spending cap. Send sensitive security reports through [private reporting](SECURITY.md).

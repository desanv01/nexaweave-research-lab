# Development at the U00 import stage

This page documents limited, offline-oriented source checks. It is not a supported application startup recipe. The imported backend remains Zep-backed and has not passed the Research Lab access, provider and cost gates. Do not expose it to a network or feed it private documents.

## Selected tool versions

Initial CI selects Python 3.12 and Node 24.14.1; `.python-version` and `.node-version` mirror them. Backend metadata permits Python 3.11–3.12. The CI unit profile pins direct packages to versions observed in `backend/uv.lock`; pip resolves their transitive dependencies. It omits OASIS/CAMEL and Torch. This profile is for inherited fixture/unit tests only, not a runnable engine environment or a faithful reproduction of the full backend lock.

## Commands

Run from the repository root in a disposable Python environment:

```sh
python -m pip install -r tools/ci-unit-requirements.txt
python tools/check_baseline_manifest.py
python tools/run_unit_tests.py
```

The launcher runs both inherited test directories and its guard regression cases on Windows or Linux. It starts pytest with a small environment containing dummy LLM/Zep keys, disables `python-dotenv`, removes inherited provider tokens and proxy settings, and rejects non-loopback DNS lookups and socket connections. Local IPv4/IPv6 fixtures remain available. Any blocked attempt fails the run even if a test catches the socket error. The launcher uses a temporary home and temporary directory; the imported application's hard-coded `backend/logs` and `backend/uploads` paths are not redirected at U00. Package installation fetches public dependencies. Main owns execution and interpretation of results; a green result is offline fixture qualification, not engine or live-provider qualification.

For the inherited frontend build:

```sh
cd frontend
npm ci --ignore-scripts
npm run build
```

The frontend build does not exercise browser journeys. The root `npm run dev`, `setup:all` and inherited Docker setup still point at the pre-cutover application and must not be presented as Research Lab deployment. The next phases will document real Neo4j Community compatibility, model configuration, volumes, backups, migrations and supervised startup after qualification.

## Provider direction

The approved initial generation target is the official DeepSeek API, V4.1 Flash (`deepseek-flash` at `https://api.deepseek.com`). Later settings must allow provider and model switching. Generation and embeddings are separate configuration capabilities; no DeepSeek embedding endpoint is assumed. This choice is not yet implemented or live-qualified, and no paid model budget or key is configured. Graphiti with self-hosted Neo4j Community remains the target knowledge stack; the imported source still uses Zep until U03.

## Change and evidence process

Small PRs record capability IDs, source provenance, dependency changes and verification status. Main runs checks, reviews the exact HEAD and records acceptance. Import patches belong in `docs/upstream/patches.json` with both hashes and a reason. Do not regenerate the archive manifest to normalize a changed source file. See [CONTRIBUTING.md](../CONTRIBUTING.md) and [coordination records](../coordination/).

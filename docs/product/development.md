# Develop NexaWeave from source

These commands support source inspection, guarded unit checks and a frontend build. They are not a qualified service launch or deployment recipe. Use synthetic data, keep credentials out of the repository, and review [current status](status.md) before interpreting results.

## Toolchain

Use Python 3.12 and Node 24.14.1 or newer. The lean Python profile is pinned in tools/ci-unit-requirements.txt and resolves its transitive dependencies from a package index. It omits the full OASIS/CAMEL engine stack and is suitable for guarded source tests, not a live simulation engine. Use a disposable Python environment.

## Install preflight

Check each dependency profile against the [committed CI workflow](../../.github/workflows/ci.yml). The locked jobs select Python 3.12.13 and uv 0.11.25. Python 3.12 is the shared minor version for the [backend](../../backend/pyproject.toml) and [knowledge package](../../services/knowledge/pyproject.toml). CI keeps the lean unit profile, locked runtime profiles and frontend lock separate.

| Profile | Prerequisites recorded in source |
| --- | --- |
| Native backend fixture | Locked `backend` environment with `dev`, `native-store` and `native-temporal` extras. The backend pins OASIS 0.2.5 and CAMEL 0.2.78. The native extras pin psycopg 3.3.6 and Temporal SDK 1.33.0. The Windows lane applies and checks a fingerprinted optional-import correction before installing the local knowledge package into its fixture environment. |
| Knowledge and retained-source fixtures | Separate locked `services/knowledge` environment with Graphiti 0.30.2 and psycopg 3.3.6. CI selects the `test` extra for fixture checks, `source-pdf` for retained PDFs with PyMuPDF 1.26.7, and `execution` for the opt-in Temporal lane. The browser workbench lane also prepares a separate pruned HTTP environment. |

The protected development entry point is [`backend/run.py`](../../backend/run.py). In `research_local` or `graphiti_readonly` it validates the protected configuration before startup and defaults to `127.0.0.1:5001`. Check the selected mode and explicit host and port settings before any authorized local launch.

The [Neo4j Community](../../infra/compose.knowledge-test.yml) and [PostgreSQL](../../infra/compose.operations-test.yml) Compose files are loopback fixtures. Their host ports are `17687` and `17474` for Neo4j and `15432` for PostgreSQL. Each requires its own disposable password through the environment. The Linux Temporal lane uses a checksum-verified CLI 1.9.1 development server on `127.0.0.1:17233` with fixture-owned storage. The Windows lane uses its own guarded fixture helpers. Reuse these settings only with disposable test services.

A supported clean install still needs an evidenced sequence. First provision and health-check owned PostgreSQL, Neo4j Community and Temporal with explicit local configuration. Then qualify the PostgreSQL bootstrap and migration dependency order from an empty database, beginning with project and revision authority before dependent catalogs. Establish scoped graph access and the Temporal namespace and worker configuration before enabling the protected service. Verify that a clean start and restart use the installed package, preserve the migrated data and enforce protected access. The fixture workflows provide evidence for parts of this sequence.

## Python source checks

From the repository root:

    python -m pip install -r tools/ci-unit-requirements.txt
    python -m pip install --no-deps services/knowledge
    python tools/check_baseline_manifest.py
    python tools/run_unit_tests.py

The installed distribution is `nexaweave-knowledge`; its canonical modules are `nexaweave_knowledge`, `nexaweave_storage` and `nexaweave_execution`. New configuration uses `NEXAWEAVE_` names. Existing import and configuration inputs remain explicit aliases, while conflicting old and new values fail closed. See [compatibility](compatibility.md) before working with existing data.

The manifest command checks archived source bytes and exceptions. The unit launcher uses dummy provider keys, disables .env loading, and blocks non-loopback connections in its guarded test processes while allowing local fixtures. Installing dependencies may contact a package registry. A passing run does not establish native engine, provider, browser or deployment qualification. Main owns execution and interpretation of project acceptance checks.

## Frontend build

From the repository root:

    cd frontend
    npm ci --ignore-scripts
    npm run build

The build checks the Vue artifact; it does not exercise a browser journey. If running Vite for a separately authorized local investigation, use BROWSER=none and avoid automatic URL opening. Do not treat the inherited root development scripts or Docker setup as a supported full launch.

## Model and runtime boundaries

Graphiti and self-hosted Neo4j Community form the knowledge direction. PostgreSQL and Temporal provide durable authority and orchestration. Generation is configurable for official DeepSeek deepseek-flash, with embeddings separately configured. Paid calls require actual local credentials and a concrete total spending cap. There is no qualified fully local, no-egress profile, full release sequence or public app deployment yet.

For scoped work and review expectations, see [contributing](../../CONTRIBUTING.md). The older [import-stage development notes](../development.md) remain a historical source record, not the current status page.

# Develop NexaWeave from source

These commands support source inspection, guarded unit checks and a frontend build. They are not a qualified service launch or deployment recipe. Use synthetic data, keep credentials out of the repository, and review [current status](status.md) before interpreting results.

## Toolchain

Use Python 3.12 and Node 24.14.1 or newer. The lean Python profile is pinned in tools/ci-unit-requirements.txt and resolves its transitive dependencies from a package index. It omits the full OASIS/CAMEL engine stack and is suitable for guarded source tests, not a live simulation engine. Use a disposable Python environment.

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

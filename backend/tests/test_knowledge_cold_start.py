"""A genuinely fresh read-mode interpreter must not load legacy SDKs."""

import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID


def _uid(number):
    return str(UUID(int=number))


def test_readonly_cold_start_blocks_legacy_imports_without_model_keys(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    bootstrap = tmp_path / "site-packages" / "nexaweave_knowledge" / "read_bootstrap.py"
    bootstrap.parent.mkdir(parents=True)
    bootstrap.write_text("# trusted installed-layout fixture\n", encoding="utf-8")
    scope = {"schema_version": 1, "workspace_id": _uid(1), "project_id": _uid(2),
             "graph_id": _uid(3), "run_id": None, "branch_id": None, "layer": "source"}
    environment = os.environ.copy()
    for name in ("LLM_API_KEY", "ZEP_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
                 "FLASK_DEBUG", "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE",
                 "PGPASSFILE", "PGOPTIONS"):
        environment.pop(name, None)
    environment.update({
        "TEST_BACKEND": str(backend), "NEXAWEAVE_APP_MODE": "graphiti_readonly",
        "PYTHON_DOTENV_DISABLED": "1",
        "FLASK_HOST": "127.0.0.1", "KNOWLEDGE_PYTHON": sys.executable,
        "KNOWLEDGE_BOOTSTRAP_SCRIPT": str(bootstrap),
        "KNOWLEDGE_READ_TOKEN": "0123456789abcdef" * 4,
        "KNOWLEDGE_PRINCIPAL": "fixture-owner", "KNOWLEDGE_DISPLAY_GRAPH_ID": "display-1",
        "KNOWLEDGE_BOUND_SCOPE_JSON": json.dumps(scope, separators=(",", ":")),
        "KNOWLEDGE_PG_HOST": "127.0.0.1", "KNOWLEDGE_PG_PORT": "5432",
        "KNOWLEDGE_PG_DATABASE": "fixture", "KNOWLEDGE_PG_USER": "fixture",
        "KNOWLEDGE_PG_PASSWORD": "fixture-password", "KNOWLEDGE_NEO4J_URI": "bolt://localhost:7687",
        "KNOWLEDGE_NEO4J_USER": "neo4j", "KNOWLEDGE_NEO4J_PASSWORD": "fixture-password",
    })
    script = r'''
import builtins
import os
import sys
sys.path.insert(0, os.environ["TEST_BACKEND"])
original = builtins.__import__
blocked = ("openai", "zep_cloud", "graphiti_core", "torch", "transformers", "camel", "oasis")
def guarded(name, *args, **kwargs):
    if any(name == prefix or name.startswith(prefix + ".") for prefix in blocked):
        raise AssertionError("legacy SDK import during read-only cold start: " + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from app import create_app
app = create_app()
response = app.test_client().get("/health")
assert response.status_code == 200, response.get_data(as_text=True)
assert response.json["mode"] == "graphiti_readonly"
assert "population_preview" in response.json["capabilities"]
from app.services.knowledge_reader import EntityNode
from app.services.oasis_profile_generator import OasisProfileGenerator
generator = OasisProfileGenerator(basic_only=True)
profile = generator.generate_profile_from_entity(
    EntityNode("00000000-0000-0000-0000-000000000010", "Alice",
               ["Entity", "Person"], "Fixture summary", {}), 0, use_llm=False)
assert profile.source_entity_type == "Person"
assert generator.client is None and generator.zep_client is None
print("healthy cold read factory")
'''
    result = subprocess.run([sys.executable, "-I", "-c", script], env=environment,
                            cwd=backend, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "healthy cold read factory" in result.stdout

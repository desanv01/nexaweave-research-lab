"""Public namespace migration preserves class identity and recorded v1 contracts."""
import importlib
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from nexaweave_knowledge.configuration import environment


@pytest.mark.parametrize("module,symbol", [
    ("knowledge.contracts", "KnowledgeScope"),
    ("knowledge.operations", "OperationRecord"),
    ("storage.store", "ProjectStore"),
    ("storage.validation", "InvalidProject"),
    ("execution.budget", "BudgetLedger"),
    ("execution.report_contracts", "budget_episode"),
])
def test_old_and_canonical_imports_share_identity(module, symbol):
    canonical = importlib.import_module("nexaweave_" + module)
    legacy = importlib.import_module("mirofish_" + module)
    assert legacy is canonical
    assert getattr(legacy, symbol) is getattr(canonical, symbol)
    assert Path(canonical.__file__).parent.name.startswith("nexaweave_")


def test_package_root_exports_do_not_load_optional_graph_provider():
    canonical = importlib.import_module("nexaweave_knowledge")
    legacy = importlib.import_module("mirofish_knowledge")
    assert legacy.KnowledgeScope is canonical.KnowledgeScope
    assert legacy.__all__ == canonical.__all__


@pytest.mark.parametrize("current,previous,expected", [
    (None, "legacy", "legacy"), ("canonical", None, "canonical"),
    ("same", "same", "same"), ("", None, ""), (None, "", ""),
])
def test_configuration_aliases_preserve_empty_values(monkeypatch, current, previous, expected):
    for key, value in (("NEXAWEAVE_APPSTORE_DSN", current), ("MIROFISH_APPSTORE_DSN", previous)):
        monkeypatch.delenv(key, raising=False)
        if value is not None:
            monkeypatch.setenv(key, value)
    assert environment["NEXAWEAVE_APPSTORE_DSN"] == expected


def test_conflicting_configuration_fails_without_exposing_values(monkeypatch):
    monkeypatch.setenv("NEXAWEAVE_APPSTORE_DSN", "canonical-private-value")
    monkeypatch.setenv("MIROFISH_APPSTORE_DSN", "legacy-private-value")
    with pytest.raises(ValueError, match="^conflicting configuration inputs$"):
        environment.get("NEXAWEAVE_APPSTORE_DSN")
    assert environment["MIROFISH_APPSTORE_DSN"] == "legacy-private-value"


def test_missing_configuration_retains_mapping_semantics(monkeypatch):
    monkeypatch.delenv("NEXAWEAVE_APPSTORE_DSN", raising=False)
    monkeypatch.delenv("MIROFISH_APPSTORE_DSN", raising=False)
    assert environment.get("NEXAWEAVE_APPSTORE_DSN", "fallback") == "fallback"
    with pytest.raises(KeyError):
        environment["NEXAWEAVE_APPSTORE_DSN"]


@pytest.mark.parametrize("input_name", ["NEXAWEAVE_NATIVE_TEST_OFFLINE", "MIROFISH_NATIVE_TEST_OFFLINE"])
def test_native_child_scrub_restores_offline_flag_and_removes_secrets(monkeypatch, input_name):
    import os
    from nexaweave_execution import native_process_worker

    isolated = {input_name: "1", "PATH": "fixture-path", "LLM_API_KEY": "fixture-secret"}
    monkeypatch.setattr(os, "environ", isolated)
    guarded = []
    monkeypatch.setattr(native_process_worker, "_deny_external_sockets", lambda: guarded.append(True))
    native_process_worker.scrub_child_environment()
    assert isolated == {"PATH": "fixture-path", "PYTHON_DOTENV_DISABLED": "1",
                        "NEXAWEAVE_NATIVE_TEST_OFFLINE": "1", "HF_HUB_OFFLINE": "1",
                        "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1"}
    assert guarded == [True]

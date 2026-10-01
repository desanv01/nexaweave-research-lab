"""Spawn import-safe bridge to the accepted offline native engine fixture."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _engine_fixture():
    path = Path(__file__).resolve().parents[1] / "engine_tests" / "test_native_prepared_workflow.py"
    spec = importlib.util.spec_from_file_location("native_prepared_engine_fixture", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("offline native fixture unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepared(root):
    return _engine_fixture()._prepared(root)


def offline_models():
    model = _engine_fixture()._offline_model
    return {"twitter": model(), "reddit": model()}

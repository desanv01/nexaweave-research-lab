"""Import-safe access to the accepted prepared offline test fixture.

The spawned child imports this helper before loading the native model fixture;
the accepted child environment scrub and socket guard have already run.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _fixture():
    path = (Path(__file__).resolve().parents[1] / "native_store_tests"
            / "offline_fixture.py")
    spec = importlib.util.spec_from_file_location("accepted_native_offline_fixture", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("accepted offline fixture unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepared(root):
    return _fixture().prepared(root)


def offline_models():
    return _fixture().offline_models()

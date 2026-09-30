"""Native scripts remain inert when embedded by the trusted workflow."""

import os
from pathlib import Path
import subprocess
import sys


def test_cold_native_script_imports_do_not_activate_engines_or_cli_setup():
    backend = Path(__file__).resolve().parents[1]
    code = r'''
import builtins
import importlib.util
import inspect
import os
import pathlib
import sys
import types
backend = pathlib.Path(os.environ["TEST_BACKEND"])
original_import = builtins.__import__
original_open = builtins.open
original_env = dict(os.environ)
dotenv_calls = []
dotenv = types.ModuleType("dotenv")
def observed_load_dotenv(*args, **kwargs):
    caller = pathlib.Path(inspect.currentframe().f_back.f_code.co_filename).resolve()
    dotenv_calls.append((caller, args, kwargs))
    return False
dotenv.load_dotenv = observed_load_dotenv
sys.modules["dotenv"] = dotenv
def guarded(name, *args, **kwargs):
    if name.split(".")[0] in {"camel", "oasis", "zep_cloud"}:
        raise AssertionError("native or Zep engine imported: " + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded
for name in ("run_parallel_simulation", "run_twitter_simulation", "run_reddit_simulation"):
    path = backend / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
assert len(dotenv_calls) == 1, dotenv_calls
assert dotenv_calls[0][0] == (backend / "app" / "config.py").resolve()
assert dotenv_calls[0][2] == {"override": True}
assert not any(name.split(".")[0] in {"camel", "oasis", "zep_cloud"}
               for name in sys.modules)
assert builtins.open is original_open
assert dict(os.environ) == original_env
'''
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        timeout=30, env={**os.environ, "TEST_BACKEND": str(backend)})
    assert result.returncode == 0, result.stderr

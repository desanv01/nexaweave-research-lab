"""Deprecated import/command compatibility; implementation is nexaweave_execution.native_run_coordinator."""
if __name__ == "__main__":
    import runpy
    runpy.run_module("nexaweave_execution.native_run_coordinator", run_name="__main__")
else:
    import importlib
    import sys
    sys.modules[__name__] = importlib.import_module("nexaweave_execution.native_run_coordinator")

"""Deprecated import/command compatibility; implementation is nexaweave_execution.native_launch_store."""
if __name__ == "__main__":
    import runpy
    runpy.run_module("nexaweave_execution.native_launch_store", run_name="__main__")
else:
    import importlib
    import sys
    sys.modules[__name__] = importlib.import_module("nexaweave_execution.native_launch_store")

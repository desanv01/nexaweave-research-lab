"""Deprecated import compatibility; implementation is nexaweave_knowledge."""
import importlib as _importlib
_canonical = _importlib.import_module("nexaweave_knowledge")
__all__ = getattr(_canonical, "__all__", [])
def __getattr__(name):
    return getattr(_canonical, name)
def __dir__():
    return sorted(set(globals()) | set(dir(_canonical)))

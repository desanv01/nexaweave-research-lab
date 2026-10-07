"""Canonical configuration with explicit, non-mutating legacy input aliases."""
from collections.abc import Mapping
import os

class Environment(Mapping):
    def __getitem__(self, key):
        if not isinstance(key, str) or not key.startswith("NEXAWEAVE_"):
            return os.environ[key]
        legacy = "MIROFISH_" + key.removeprefix("NEXAWEAVE_")
        current = os.environ.get(key)
        previous = os.environ.get(legacy)
        if current is not None and previous is not None and current != previous:
            raise ValueError("conflicting configuration inputs")
        if current is not None:
            return current
        if previous is not None:
            return previous
        raise KeyError(key)
    def __iter__(self):
        return iter(os.environ)
    def __len__(self):
        return len(os.environ)

environment = Environment()

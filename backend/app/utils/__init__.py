"""Utility exports loaded only when an inherited caller requests them."""

from importlib import import_module

__all__ = ['FileParser', 'LLMClient', 't', 'get_locale', 'set_locale', 'get_language_instruction']

_EXPORTS = {
    'FileParser': 'file_parser',
    'LLMClient': 'llm_client',
    't': 'locale',
    'get_locale': 'locale',
    'set_locale': 'locale',
    'get_language_instruction': 'locale',
}


def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(name)
    value = getattr(import_module(f'.{module}', __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))


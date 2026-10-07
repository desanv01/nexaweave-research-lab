"""Resolve canonical NexaWeave settings and explicit legacy input aliases."""

import os


def environment_value(canonical, legacy, default=None):
    """Read both names without changing the process environment or leaking values."""
    canonical_present = canonical in os.environ
    legacy_present = legacy in os.environ
    if canonical_present and legacy_present and os.environ[canonical] != os.environ[legacy]:
        raise ValueError('Conflicting application configuration')
    if canonical_present:
        return os.environ[canonical]
    if legacy_present:
        return os.environ[legacy]
    return default


def config_value(config_class, base_class, canonical, legacy, *, environment_first=False):
    """Use the nearest explicit subclass definition across compatibility names.

    Mode historically takes its environment setting first. Browser Origins
    historically take an explicit subclass setting first, including empty.
    """
    environment = environment_value(canonical, legacy)
    names = (canonical, legacy)
    missing = object()

    def defined_value(candidate):
        defined = vars(candidate)
        values = [defined[name] for name in names if name in defined]
        if values and any(value != values[0] for value in values[1:]):
            raise ValueError('Conflicting application configuration')
        return values[0] if values else missing

    subclass_value = missing
    for candidate in getattr(config_class, '__mro__', ()):
        if candidate in (base_class, object):
            break
        value = defined_value(candidate)
        if value is not missing:
            subclass_value = value
            break

    if environment_first and environment is not None:
        return environment
    if subclass_value is not missing:
        return subclass_value
    if environment is not None:
        return environment
    return defined_value(base_class)


def app_mode(config_class, base_class):
    return config_value(config_class, base_class, 'NEXAWEAVE_APP_MODE',
                        'MIROFISH_APP_MODE', environment_first=True)


def allowed_origins(config_class, base_class):
    return config_value(config_class, base_class, 'NEXAWEAVE_ALLOWED_ORIGINS',
                        'MIROFISH_ALLOWED_ORIGINS')


def appstore_dsn():
    return environment_value('NEXAWEAVE_APPSTORE_DSN', 'MIROFISH_APPSTORE_DSN')

"""Canonical runtime inputs with explicit, non-leaking legacy aliases."""

import os
from types import SimpleNamespace

import pytest

from app.config import Config
from app.utils.branding import (allowed_origins, app_mode, appstore_dsn,
                                environment_value)


@pytest.mark.parametrize('canonical,legacy', [
    ('NEXAWEAVE_APP_MODE', 'MIROFISH_APP_MODE'),
    ('NEXAWEAVE_ALLOWED_ORIGINS', 'MIROFISH_ALLOWED_ORIGINS'),
    ('NEXAWEAVE_APPSTORE_DSN', 'MIROFISH_APPSTORE_DSN'),
])
def test_environment_aliases_accept_either_or_matching_and_reject_conflicts(monkeypatch, canonical, legacy):
    monkeypatch.delenv(canonical, raising=False)
    monkeypatch.delenv(legacy, raising=False)
    assert environment_value(canonical, legacy, 'fallback') == 'fallback'
    monkeypatch.setenv(legacy, 'private-value')
    assert environment_value(canonical, legacy) == 'private-value'
    monkeypatch.delenv(legacy)
    monkeypatch.setenv(canonical, 'private-value')
    assert environment_value(canonical, legacy) == 'private-value'
    monkeypatch.setenv(legacy, 'private-value')
    assert environment_value(canonical, legacy) == 'private-value'
    before = dict(os.environ)
    monkeypatch.setenv(legacy, 'different-private-value')
    with pytest.raises(ValueError) as error:
        environment_value(canonical, legacy)
    assert 'private-value' not in str(error.value)
    assert dict(os.environ)[canonical] == before[canonical]


def test_mode_environment_precedence_and_nearest_subclass(monkeypatch):
    for name in ('NEXAWEAVE_APP_MODE', 'MIROFISH_APP_MODE'):
        monkeypatch.delenv(name, raising=False)

    assert app_mode(Config, Config) == 'legacy'

    class Parent(Config):
        MIROFISH_APP_MODE = 'graphiti_readonly'

    class Child(Parent):
        NEXAWEAVE_APP_MODE = 'research_local'

    assert app_mode(Parent, Config) == 'graphiti_readonly'
    assert app_mode(Child, Config) == 'research_local'

    class CanonicalParent(Config):
        NEXAWEAVE_APP_MODE = 'graphiti_readonly'

    class LegacyChild(CanonicalParent):
        MIROFISH_APP_MODE = 'research_local'

    assert app_mode(LegacyChild, Config) == 'research_local'
    monkeypatch.setenv('NEXAWEAVE_APP_MODE', 'legacy')
    assert app_mode(Child, Config) == 'legacy'
    class Conflicting(Config):
        NEXAWEAVE_APP_MODE = 'legacy'
        MIROFISH_APP_MODE = 'research_local'

    with pytest.raises(ValueError, match='Conflicting application configuration'):
        app_mode(Conflicting, Config)
    monkeypatch.setenv('MIROFISH_APP_MODE', 'research_local')
    with pytest.raises(ValueError, match='Conflicting application configuration'):
        app_mode(Child, Config)


def test_origin_nearest_subclass_overrides_environment_and_preserves_empty(monkeypatch):
    monkeypatch.delenv('NEXAWEAVE_ALLOWED_ORIGINS', raising=False)
    monkeypatch.delenv('MIROFISH_ALLOWED_ORIGINS', raising=False)
    monkeypatch.setenv('NEXAWEAVE_ALLOWED_ORIGINS', 'https://environment.example')

    class Parent(Config):
        MIROFISH_ALLOWED_ORIGINS = ('https://parent.example',)

    class Child(Parent):
        NEXAWEAVE_ALLOWED_ORIGINS = ''

    assert allowed_origins(Config, Config) == 'https://environment.example'
    assert allowed_origins(Parent, Config) == ('https://parent.example',)
    assert allowed_origins(Child, Config) == ''

    class CanonicalParent(Config):
        NEXAWEAVE_ALLOWED_ORIGINS = ('https://canonical-parent.example',)

    class LegacyChild(CanonicalParent):
        MIROFISH_ALLOWED_ORIGINS = ()

    assert allowed_origins(LegacyChild, Config) == ()
    monkeypatch.setenv('MIROFISH_ALLOWED_ORIGINS', 'https://different.example')
    with pytest.raises(ValueError, match='Conflicting application configuration'):
        allowed_origins(Child, Config)


def test_same_layer_conflicts_rejected_and_matching_aliases_accepted(monkeypatch):
    for name in ('NEXAWEAVE_ALLOWED_ORIGINS', 'MIROFISH_ALLOWED_ORIGINS'):
        monkeypatch.delenv(name, raising=False)
    assert allowed_origins(Config, Config) == Config.NEXAWEAVE_ALLOWED_ORIGINS

    class Matching(Config):
        NEXAWEAVE_ALLOWED_ORIGINS = ()
        MIROFISH_ALLOWED_ORIGINS = ()

    class Conflicting(Config):
        NEXAWEAVE_ALLOWED_ORIGINS = ()
        MIROFISH_ALLOWED_ORIGINS = ('https://private.example',)

    assert allowed_origins(Matching, Config) == ()
    with pytest.raises(ValueError) as error:
        allowed_origins(Conflicting, Config)
    assert 'private.example' not in str(error.value)


def test_cli_dsn_aliases_share_the_same_resolver(monkeypatch):
    from app.services.document_source_cli import _appstore_dsn as document_dsn
    from app.services.native_experiment_cli import _appstore_dsn as experiment_dsn

    monkeypatch.delenv('NEXAWEAVE_APPSTORE_DSN', raising=False)
    monkeypatch.setenv('MIROFISH_APPSTORE_DSN', 'postgresql://legacy-private')
    assert appstore_dsn() == document_dsn() == experiment_dsn() == 'postgresql://legacy-private'
    monkeypatch.setenv('NEXAWEAVE_APPSTORE_DSN', 'postgresql://canonical-private')
    for reader in (appstore_dsn, document_dsn, experiment_dsn):
        with pytest.raises(ValueError) as error:
            reader()
        assert 'postgresql://' not in str(error.value)


def test_read_factory_uses_shared_origin_resolver(monkeypatch):
    from app.knowledge_read_app import create_read_app
    from app.services.knowledge_read_facade import ReadHostSettings

    monkeypatch.delenv('MIROFISH_ALLOWED_ORIGINS', raising=False)
    monkeypatch.setenv('NEXAWEAVE_ALLOWED_ORIGINS', 'https://research.example')
    monkeypatch.setenv('FLASK_HOST', '127.0.0.1')
    monkeypatch.setattr(ReadHostSettings, 'from_config',
                        classmethod(lambda _cls, _config: SimpleNamespace(token='fixture-token')))

    class ReadConfig(Config):
        DEBUG = False

    app = create_read_app(ReadConfig, facade=object())
    assert app.config['NEXAWEAVE_ALLOWED_ORIGINS'] == ('https://research.example',)
    response = app.test_client().get('/api/graph/data/fixture',
                                     headers={'Origin': 'https://research.example'})
    assert response.status_code == 401
    assert response.headers['Access-Control-Allow-Origin'] == 'https://research.example'


def test_legacy_factory_uses_shared_mode_and_empty_origin(monkeypatch):
    from app import create_app
    from app.services.simulation_runner import SimulationRunner

    for name in ('NEXAWEAVE_APP_MODE', 'MIROFISH_APP_MODE',
                 'NEXAWEAVE_ALLOWED_ORIGINS', 'MIROFISH_ALLOWED_ORIGINS'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(SimulationRunner, 'register_cleanup', classmethod(lambda _cls: None))

    class LocalConfig(Config):
        NEXAWEAVE_APP_MODE = 'legacy'
        NEXAWEAVE_ALLOWED_ORIGINS = ''

    app = create_app(LocalConfig)
    assert app.config['NEXAWEAVE_APP_MODE'] == 'legacy'
    assert app.config['NEXAWEAVE_ALLOWED_ORIGINS'] == ()
    assert app.test_client().get('/health').json['service'] == 'NexaWeave Backend'
    response = app.test_client().get('/api/graph/fixture',
                                     headers={'Origin': 'https://untrusted.example'})
    assert response.status_code == 403

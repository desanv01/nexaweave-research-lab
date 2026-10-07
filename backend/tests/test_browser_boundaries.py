"""Browser-origin and request-log boundaries using the actual Flask factory."""

import traceback

from flask import Request, jsonify
import pytest

import app as app_module
from app import create_app
from app.config import Config
from app.services.simulation_runner import SimulationRunner
from app.utils.browser_origins import validate_origin


@pytest.fixture
def make_app(monkeypatch):
    monkeypatch.delenv("NEXAWEAVE_ALLOWED_ORIGINS", raising=False)
    monkeypatch.setattr(SimulationRunner, "register_cleanup", classmethod(lambda _cls: None))

    def build(origins=None):
        if origins is None:
            config_class = Config
        else:
            config_class = type("OriginConfig", (Config,), {"NEXAWEAVE_ALLOWED_ORIGINS": origins})
        app = create_app(config_class)
        app.config["TESTING"] = True
        calls = []

        @app.route("/api/fixture", methods=["GET", "POST", "PATCH"])
        def fixture():
            calls.append("fixture")
            return jsonify({"ok": True}), 201 if app_module.request.method == "POST" else 200

        @app.route("/api/fixture/<path:token>", methods=["POST"])
        def fixture_token(token):
            calls.append("fixture_token")
            return jsonify({"ok": True}), 201

        return app, calls

    return build


def test_allowed_exact_origin_and_no_origin_client(make_app):
    app, calls = make_app()
    client = app.test_client()
    for origin in ("http://localhost:3000", "http://127.0.0.1:3000"):
        response = client.get("/api/fixture", headers={"Origin": origin})
        assert response.status_code == 200
        assert response.json == {"ok": True}
        assert response.headers["Access-Control-Allow-Origin"] == origin
        assert "Origin" in response.headers["Vary"]
        assert "Access-Control-Allow-Credentials" not in response.headers
    response = client.post("/api/fixture", json={"example": True})
    assert response.status_code == 201
    assert response.json == {"ok": True}
    assert "Access-Control-Allow-Origin" not in response.headers
    assert "Origin" in response.headers["Vary"]
    assert len(calls) == 3


def test_allowed_preflight_is_route_scoped_and_does_not_mutate(make_app):
    app, calls = make_app()
    response = app.test_client().options("/api/fixture", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type, Authorization",
    })
    assert response.status_code == 204
    assert calls == []
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
    assert "Origin" in response.headers["Vary"]
    assert response.headers["Access-Control-Allow-Headers"] == "Content-Type, Authorization"
    assert set(response.headers["Access-Control-Allow-Methods"].split(", ")) == {
        "GET", "POST", "PATCH", "OPTIONS",
    }
    assert "Access-Control-Allow-Credentials" not in response.headers


@pytest.mark.parametrize("path", [
    "/api/graph/ontology/generate", "/api/simulation/create", "/api/report/generate",
])
def test_real_blueprint_preflights_do_not_invoke_business_handlers(make_app, monkeypatch, path):
    app, _ = make_app()
    rule = next(rule for rule in app.url_map.iter_rules() if rule.rule == path)

    def forbidden(**kwargs):
        raise AssertionError("preflight reached a business handler")

    monkeypatch.setitem(app.view_functions, rule.endpoint, forbidden)
    response = app.test_client().options(path, headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type",
    })
    assert response.status_code == 204
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
    assert "POST" in response.headers["Access-Control-Allow-Methods"]


@pytest.mark.parametrize("origin", [
    "null", "http://localhost:3000,https://evil.example",
    "http://localhost:3000.evil.example", "http://localhost:3000/extra",
    "https://localhost:3000", "http://localhost:3001",
    "http://127.0.0.1:3000.evil.example", "http://localhost:3000$",
])
def test_disallowed_origin_never_reaches_handler(make_app, origin):
    app, calls = make_app()
    response = app.test_client().post("/api/fixture", headers={"Origin": origin})
    assert response.status_code == 403
    assert response.json == {"success": False, "error": "Origin not allowed"}
    assert "Access-Control-Allow-Origin" not in response.headers
    assert "Origin" in response.headers["Vary"]
    assert calls == []


def test_unsupported_preflight_method_and_header_are_not_granted(make_app):
    app, calls = make_app()
    client = app.test_client()
    for method, headers in (("DELETE", "Content-Type"), ("POST", "X-Secret"),
                            ("POST", "Content-Type, X-Secret")):
        response = client.options("/api/fixture", headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers,
        })
        assert response.status_code == 403
        assert response.json == {"success": False, "error": "Origin not allowed"}
        assert "Access-Control-Allow-Headers" not in response.headers
        assert "Access-Control-Allow-Methods" not in response.headers
    assert calls == []


def test_explicit_empty_allowlist_and_environment_override(make_app, monkeypatch):
    empty_app, empty_calls = make_app("")
    response = empty_app.test_client().get(
        "/api/fixture", headers={"Origin": "http://localhost:3000"},
    )
    assert response.status_code == 403
    assert empty_calls == []
    assert empty_app.test_client().get("/api/fixture").status_code == 200

    monkeypatch.setenv("NEXAWEAVE_ALLOWED_ORIGINS", "https://research.example:8443")
    env_app = create_app(Config)
    env_app.config["TESTING"] = True

    @env_app.route("/api/env", methods=["GET"])
    def env_route():
        return {"ok": True}

    client = env_app.test_client()
    assert client.get("/api/env", headers={"Origin": "https://research.example:8443"}).status_code == 200
    assert client.get("/api/env", headers={"Origin": "http://localhost:3000"}).status_code == 403

    class ExplicitConfig(Config):
        NEXAWEAVE_ALLOWED_ORIGINS = "http://localhost:3000"

    override_app = create_app(ExplicitConfig)
    override_app.config["TESTING"] = True

    @override_app.route("/api/override", methods=["GET"])
    def override_route():
        return {"ok": True}

    assert override_app.test_client().get(
        "/api/override", headers={"Origin": "http://localhost:3000"},
    ).status_code == 200


def test_inherited_config_override_precedes_environment(make_app, monkeypatch):
    monkeypatch.setenv("NEXAWEAVE_ALLOWED_ORIGINS", "https://environment.example")

    class ExplicitConfig(Config):
        NEXAWEAVE_ALLOWED_ORIGINS = "http://localhost:3000"

    class ChildConfig(ExplicitConfig):
        pass

    app = create_app(ChildConfig)
    app.config["TESTING"] = True

    @app.route("/api/inherited", methods=["GET"])
    def inherited_route():
        return {"ok": True}

    client = app.test_client()
    assert client.get(
        "/api/inherited", headers={"Origin": "http://localhost:3000"},
    ).status_code == 200
    denied = client.get(
        "/api/inherited", headers={"Origin": "https://environment.example"},
    )
    assert denied.status_code == 403
    assert "Origin" in denied.headers["Vary"]


@pytest.mark.parametrize("configured", [
    "*", "null", "https://example.com/path", "https://example.com?x=1",
    "https://example.com#fragment", "https://user@example.com",
    "https://example.com:", "https://example.com:70000",
    "https://example.com:03000", "https://example.com ",
    "https://example.com\r\nSECRET", "https://*.example.com",
    "https://example.com, https://other.example", "https://[::1",
])
def test_invalid_config_fails_before_cleanup_registration(monkeypatch, configured):
    called = []
    monkeypatch.setattr(SimulationRunner, "register_cleanup",
                        classmethod(lambda _cls: called.append(True)))

    class BadConfig(Config):
        NEXAWEAVE_ALLOWED_ORIGINS = configured

    with pytest.raises(ValueError) as error:
        create_app(BadConfig)
    assert str(error.value) == "Invalid allowed browser origin configuration"
    assert called == []


def test_ipv6_origin_is_accepted_exactly(make_app):
    app, calls = make_app("http://[::1]:3000")
    client = app.test_client()
    accepted = client.get("/api/fixture", headers={"Origin": "http://[::1]:3000"})
    assert accepted.status_code == 200
    assert accepted.headers["Access-Control-Allow-Origin"] == "http://[::1]:3000"
    assert client.get("/api/fixture", headers={"Origin": "http://[::1]:3001"}).status_code == 403
    assert len(calls) == 1


def test_health_is_minimal_and_ignores_browser_origin(make_app):
    app, _ = make_app()
    response = app.test_client().get("/health", headers={"Origin": "null"})
    assert response.status_code == 200
    assert response.json == {"status": "ok", "service": "NexaWeave Backend"}
    assert "Access-Control-Allow-Origin" not in response.headers


def test_request_log_excludes_body_query_path_headers_and_does_not_parse_json(make_app, monkeypatch):
    app, calls = make_app()
    secret = "SENTINEL_PRIVATE_VALUE"

    class NoLoggingJsonParse(Request):
        def get_json(self, *args, **kwargs):
            raise AssertionError("Request logging parsed a JSON body")

    class Recorder:
        def __init__(self):
            self.messages = []

        def debug(self, template, *args):
            self.messages.append(template % args)

    recorder = Recorder()
    monkeypatch.setattr(app_module, "get_logger", lambda _name: recorder)
    app.request_class = NoLoggingJsonParse
    response = app.test_client().post(
        f"/api/fixture/{secret}?token={secret}",
        data=f'{{"secret":"{secret}"}}', content_type="application/json",
        headers={"Origin": "http://localhost:3000", "Authorization": f"Bearer {secret}"},
    )
    assert response.status_code == 201
    assert calls == ["fixture_token"]
    missing = app.test_client().get(f"/api/missing/{secret}?token={secret}")
    assert missing.status_code == 404
    assert any("method=POST endpoint=fixture_token status=201" in message
               for message in recorder.messages)
    assert any("method=GET endpoint=unmatched status=404" in message
               for message in recorder.messages)
    assert all(secret not in message for message in recorder.messages)


@pytest.mark.parametrize("origin", [
    "HTTP://localhost:3000", "http://LOCALHOST:3000", "http://example.com/",
    "http://example.com:abc", "http://[::1%zone]:3000", "ftp://example.com",
    "http://999.999.999.999", "http://example.com:0", "http://example.com:0001",
])
def test_origin_parser_rejects_noncanonical_values(origin):
    with pytest.raises(ValueError, match="Invalid allowed browser origin configuration"):
        validate_origin(origin)


def test_invalid_ipv6_traceback_does_not_echo_config_value():
    sentinel = "SENTINEL_PRIVATE_IPV6"
    with pytest.raises(ValueError) as error:
        validate_origin(f"http://[{sentinel}]:3000")
    rendered = ''.join(traceback.format_exception(error.value))
    assert sentinel not in rendered
    assert str(error.value) == "Invalid allowed browser origin configuration"

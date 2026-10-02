"""Auth/HTTP/DTO regression sources; injected clients are not live qualification."""
import base64
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5

import pytest

from app import create_app
from flask import Blueprint
from app.config import Config
from app.services.knowledge_read_facade import ReadHostSettings
from app.services.knowledge_reader import KnowledgeReadError
from app.services.knowledge_source_client import (MAX_BYTES, TEXT_BYTES, declarations,
    encoded, validate_result, KnowledgeSourceProcessClient)
from app.services.knowledge_source_facade import KnowledgeSourceFacade, validate_upload
from app.services.knowledge_transport import KnowledgeInvalidRequest, KnowledgeTransportFailure
from app.utils import docx_extraction
from test_docx_extraction import package, paragraph


def scope():
    return {"schema_version": 1, "workspace_id": str(UUID(int=1)), "project_id": str(UUID(int=2)),
        "graph_id": str(UUID(int=3)), "run_id": None, "branch_id": None, "layer": "source"}


def upload(text="A😀猫\r\n", *, revision=None, docx=False):
    raw = text if docx else text.encode()
    return {"schema_version": 1, "source_revision": revision or str(uuid4()),
        "source_name": "source 猫", "format": "docx" if docx else "text",
        "content": base64.b64encode(raw).decode() if docx else text,
        "input_sha256": hashlib.sha256(raw).hexdigest()}


class InjectedClient:
    def __init__(self):
        self.calls = []
        self.records = {}
        self.denied = None
        self.corrupt = None

    def call(self, raw):
        request = json.loads(raw)
        self.calls.append(request)
        method, payload = request["method"], request["payload"]
        common = {"schema_version": 1, "binary_retained": False, "graph_ingestion_executed": False}
        if self.denied:
            return encoded({"version": 1, "request_id": request["request_id"], "ok": False,
                            "error": {"code": self.denied}})
        if method == "context":
            result = {**common, "scope": scope()}
        elif method == "list":
            result = {**common, "sources": [], "has_more": False, "window_limit": 20}
        else:
            revision = payload["source_revision"]
            if method == "retain":
                text = payload["text"]
                source = {"project_id": scope()["project_id"], "source_revision": revision,
                    "source_name": payload["source_name"], "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "byte_length": len(text.encode()), "codepoint_length": len(text),
                    "recorded_at": "2026-10-02T00:00:00+00:00"}
                passages = [{**p, "page": None,
                    "excerpt_sha256": hashlib.sha256(text[p["start"]:p["end"]].encode()).hexdigest()}
                    for p in declarations(revision, text, payload["blocks"])]
                self.records[revision] = {**common, "source": source, "offset_unit": "unicode_codepoint",
                                          "passages": passages, "text": text}
            result = deepcopy(self.records[revision])
            if method == "retain":
                result.pop("text")
        reply = {"version": 1, "request_id": request["request_id"], "ok": True, "result": result}
        if self.corrupt:
            self.corrupt(reply)
        return encoded(reply)


@pytest.fixture
def host(monkeypatch):
    monkeypatch.setenv("MIROFISH_APP_MODE", "research_local")
    monkeypatch.delenv("FLASK_HOST", raising=False)
    monkeypatch.delenv("MIROFISH_ALLOWED_ORIGINS", raising=False)
    token = "0123456789abcdef" * 4
    settings = ReadHostSettings("python", "read_bootstrap.py", token, "owner", "display-1", scope(), {})
    monkeypatch.setattr(ReadHostSettings, "from_config", classmethod(lambda cls, config: settings))
    client = InjectedClient()
    facade = KnowledgeSourceFacade(settings, client_factory=lambda: client)
    app = create_app(source_facade=facade)
    app.config["TESTING"] = True
    return app.test_client(), client, {"Authorization": "Bearer " + token}, settings


def test_explicit_mode_and_readonly_absence(host, monkeypatch):
    http, child, headers, settings = host
    health = http.get("/health").json
    assert health["mode"] == "research_local"
    assert health["capabilities"][-2:] == ["source_library", "source_retention"]
    monkeypatch.setenv("MIROFISH_APP_MODE", "graphiti_readonly")
    readonly = create_app(source_facade=object()).test_client()
    assert readonly.get("/api/source/library/display-1", headers=headers).status_code == 404
    assert "source_retention" not in readonly.get("/health").json["capabilities"]
    assert not child.calls


def test_injected_facade_does_not_skip_settings_validation(monkeypatch):
    monkeypatch.setenv("MIROFISH_APP_MODE", "research_local")
    def denied(cls, config):
        raise ValueError("invalid knowledge read configuration")
    monkeypatch.setattr(ReadHostSettings, "from_config", classmethod(denied))
    with pytest.raises(ValueError):
        create_app(source_facade=object())


def test_default_legacy_has_no_source_library(monkeypatch):
    monkeypatch.delenv("MIROFISH_APP_MODE", raising=False)
    monkeypatch.delenv("MIROFISH_ALLOWED_ORIGINS", raising=False)
    class LegacyConfig(Config):
        DEBUG = True
        MIROFISH_APP_MODE = "legacy"
    # Only unrelated legacy registration is replaced here; this is a factory
    # mode test, not qualification of legacy simulation behavior.
    monkeypatch.setitem(sys.modules, "app.services.simulation_runner", SimpleNamespace(
        SimulationRunner=SimpleNamespace(register_cleanup=lambda: None)))
    monkeypatch.setitem(sys.modules, "app.api", SimpleNamespace(
        graph_bp=Blueprint("legacy_graph", __name__), simulation_bp=Blueprint("legacy_sim", __name__),
        report_bp=Blueprint("legacy_report", __name__)))
    app = create_app(LegacyConfig, source_facade=object()).test_client()
    assert app.get("/health").json["service"] == "MiroFish Backend"
    assert app.get("/api/source/library/display-1").status_code == 404


def test_auth_origin_preflight_and_fixed_private_errors(host):
    http, child, headers, _ = host
    url = "/api/source/retain/display-1"
    assert http.post(url, json=upload()).status_code == 401
    assert http.post(url, headers={"Authorization": "Bearer wrong"}, json=upload()).status_code == 401
    assert http.post(url, headers={"Authorization": "Bearer é"}, json=upload()).status_code == 401
    denied = http.post(url, headers={**headers, "Origin": "https://untrusted.example"}, json=upload())
    assert denied.status_code == 403
    for response in (denied, http.get("/api/source/library/display-1")):
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
    good = {"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization, Content-Type"}
    response = http.options(url, headers=good)
    assert response.status_code == 204 and response.headers["Cache-Control"] == "no-store"
    assert response.headers["Access-Control-Allow-Origin"] == good["Origin"]
    assert http.options(url, headers={**good, "Access-Control-Request-Headers": "X-Owner"}).status_code == 403
    assert not child.calls


@pytest.mark.parametrize("change", [
    lambda p: p.update(extra="authority"), lambda p: p.update(schema_version=True),
    lambda p: p.update(source_revision="AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"),
    lambda p: p.update(source_name=" "), lambda p: p.update(source_name="a\x85b"),
    lambda p: p.update(source_name="a" * 257), lambda p: p.update(content="\ud800"),
    lambda p: p.update(content="a\x00b"), lambda p: p.update(content="a\x7fb"),
    lambda p: p.update(content="a\x01b"), lambda p: p.update(content=" "),
    lambda p: p.update(content="x" * (TEXT_BYTES + 1)),
    lambda p: p.update(format="pdf"), lambda p: p.update(input_sha256="F" * 64),
    lambda p: p.update(input_sha256="0" * 64), lambda p: p.update(content={"path": "C:/private"}),
])
def test_http_upload_denies_before_child(host, change):
    http, child, headers, _ = host
    value = upload()
    change(value)
    response = http.post("/api/source/retain/display-1", data=json.dumps(value), content_type="application/json", headers=headers)
    assert response.status_code == 400 and not child.calls
    assert "private" not in response.get_data(as_text=True)


@pytest.mark.parametrize("raw", [b"", b"{}", b"[]", b"{", b"\xff", b'{"schema_version":1,"schema_version":1}',
    b'{"x":' + b'[' * 40 + b'0' + b']' * 40 + b'}'])
def test_raw_json_denials(host, raw):
    http, child, headers, _ = host
    response = http.post("/api/source/retain/display-1", data=raw, content_type="application/json", headers=headers)
    assert response.status_code == 400 and not child.calls


def test_wire_caps_media_queries_and_get_payload(host):
    http, child, headers, _ = host
    url = "/api/source/retain/display-1"
    assert http.post(url, data=b"x" * (MAX_BYTES + 1), content_type="application/json", headers=headers).status_code == 400
    assert http.post(url, json=upload(), headers={**headers, "Content-Encoding": "gzip"}).status_code == 400
    assert http.post(url, json=upload(), headers={**headers, "Transfer-Encoding": "chunked"}).status_code == 400
    assert http.post(url, data="x", content_type="text/plain", headers=headers).status_code == 400
    assert http.post(url, json=upload(), headers=headers, environ_overrides={"CONTENT_LENGTH": ""}).status_code == 400
    assert http.post(url + "?project=other", json=upload(), headers=headers).status_code == 400
    assert http.get("/api/source/library/display-1?after=1", headers=headers).status_code == 400
    assert http.get("/api/source/library/display-1", data=b"x", headers=headers).status_code == 400
    assert http.post("/api/source/retain/foreign", json=upload(), headers=headers).status_code == 404
    assert http.get("/api/source/item/display-1/not-a-uuid", headers=headers).status_code == 400
    assert not child.calls


@pytest.mark.parametrize("content", ["YQ==\n", "YR==", "YQ", "YQ===", "YQ-_", "é", "AAAA" * 700000],
                         ids=["newline", "noncanonical", "unpadded", "overpadded", "urlsafe", "nonascii", "oversize"])
def test_canonical_base64_and_encoded_bounds(content):
    value = upload(b"a", docx=True)
    value["content"] = content
    with pytest.raises(KnowledgeReadError):
        validate_upload(value)


def test_text_chunk_unicode_exact_roundtrip_and_metadata_only_retain(host):
    http, child, headers, _ = host
    value = upload("😀" * 8193 + "猫\r\n雪")
    response = http.post("/api/source/retain/display-1", json=value, headers=headers)
    assert response.status_code == 200
    retained = response.json["data"]
    assert "text" not in retained and [r["method"] for r in child.calls] == ["context", "retain"]
    assert [(p["start"], p["end"]) for p in retained["passages"]] == [(0, 8192), (8192, len(value["content"]))]
    get = http.get("/api/source/item/display-1/" + value["source_revision"], headers=headers)
    assert get.status_code == 200 and get.json["data"]["text"] == value["content"]
    assert get.json["data"]["passages"] == retained["passages"]
    assert retained["extraction"]["input_hash_verified"] is True
    assert retained["extraction"]["input_digest_persisted"] is False
    assert retained["binary_retained"] is retained["graph_ingestion_executed"] is False


def test_docx_authority_precedes_parser_and_fresh_retain(host, monkeypatch):
    http, child, headers, _ = host
    child.denied = "source_denied"
    def forbidden(*args, **kwargs):
        raise AssertionError("parser before authority")
    monkeypatch.setattr(docx_extraction, "extract_docx", forbidden)
    value = upload(b"not a DOCX", docx=True)
    response = http.post("/api/source/retain/display-1", json=value, headers=headers)
    assert response.status_code == 403
    assert [r["method"] for r in child.calls] == ["context"]


def test_docx_exact_mixed_cells_canonical_ids_transient_coverage(host):
    http, child, headers, _ = host
    body = paragraph("A😀猫") + '<w:tbl><w:tr><w:tc>' + paragraph("雪") + '</w:tc><w:tc><w:p/></w:tc></w:tr></w:tbl>'
    binary = package(body)
    value = upload(binary, docx=True)
    response = http.post("/api/source/retain/display-1", json=value, headers=headers)
    assert response.status_code == 200
    result = response.json["data"]
    extracted = docx_extraction.extract_docx(binary)
    assert child.calls[-1]["payload"]["text"] == extracted.text == "A😀猫\n\n雪\t"
    blocks = []
    for ordinal, block in enumerate(extracted.blocks):
        item = asdict(block)
        item.update(ordinal=ordinal, empty=block.start == block.end)
        blocks.append(item)
    assert result["extraction"]["blocks"] == blocks
    for passage, block in zip(result["passages"], [b for b in blocks if not b["empty"]]):
        identity = "docx-main-body-v1:" + json.dumps(block, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert passage["evidence_id"] == str(uuid5(UUID(value["source_revision"]), identity))
        assert passage["page"] is None
    assert result["extraction"]["excluded_parts"] == ["headers", "footers", "footnotes", "endnotes"]
    assert result["extraction"]["binary_persistently_bound"] is False


@pytest.mark.parametrize("body", [paragraph("a") * 101, paragraph("x" * 32769), '<w:p/>'],
                         ids=["too-many-passages", "oversize-passage", "empty-text"])
def test_docx_passage_admission_before_retention(host, body):
    http, child, headers, _ = host
    response = http.post("/api/source/retain/display-1", json=upload(package(body), docx=True), headers=headers)
    assert response.status_code == 400
    assert [r["method"] for r in child.calls] == ["context"]


@pytest.mark.parametrize("corrupt", [
    lambda r: r.update(request_id=str(uuid4())), lambda r: r.update(version=True),
    lambda r: r["result"].update(binary_retained=True), lambda r: r["result"].update(schema_version=True),
    lambda r: r["result"].update(extra="private"), lambda r: r["result"].update(has_more=True),
])
def test_injected_client_corruption_is_fixed_unknown(host, corrupt):
    http, child, headers, _ = host
    child.corrupt = corrupt
    response = http.get("/api/source/library/display-1", headers=headers)
    assert response.status_code == 503 and response.json["error"]["code"] == "outcome_unknown"
    assert "private" not in response.get_data(as_text=True)


def test_get_checksum_and_passage_tamper(host):
    http, child, headers, _ = host
    value = upload()
    assert http.post("/api/source/retain/display-1", json=value, headers=headers).status_code == 200
    stored = child.records[value["source_revision"]]
    stored["passages"][0]["excerpt_sha256"] = "0" * 64
    assert http.get("/api/source/item/display-1/" + value["source_revision"], headers=headers).status_code == 503
    stored["passages"][0]["excerpt_sha256"] = hashlib.sha256(stored["text"].encode()).hexdigest()
    stored["source"]["text_sha256"] = "0" * 64
    assert http.get("/api/source/item/display-1/" + value["source_revision"], headers=headers).status_code == 503


def test_parent_scope_request_and_error_vocabulary_before_spawn():
    client = object.__new__(KnowledgeSourceProcessClient)
    client._scope = scope()
    request_id = str(uuid4())
    raw = {"version": 1, "request_id": request_id, "method": "context", "scope": scope(), "payload": {}}
    assert client._validate_request(encoded(raw)) == request_id
    raw["scope"]["workspace_id"] = str(uuid4())
    with pytest.raises(KnowledgeInvalidRequest):
        client._validate_request(encoded(raw))
    with pytest.raises(KnowledgeTransportFailure):
        client._validate_reply(encoded({"version": 1, "request_id": request_id, "ok": False,
                                      "error": {"code": "private SQL diagnostic"}}), request_id)


def retained_dto(text, name, spans):
    revision = str(uuid4())
    source = {"project_id": scope()["project_id"], "source_revision": revision,
        "source_name": name, "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "byte_length": len(text.encode()), "codepoint_length": len(text),
        "recorded_at": "2026-10-02T00:00:00+00:00"}
    passages = [{"evidence_id": str(uuid4()), "start": start, "end": end, "page": None,
                 "excerpt_sha256": hashlib.sha256(text[start:end].encode()).hexdigest()}
                for start, end in spans]
    return {"schema_version": 1, "binary_retained": False, "graph_ingestion_executed": False,
            "source": source, "text": text, "passages": passages, "offset_unit": "unicode_codepoint"}


@pytest.mark.parametrize("text,name,spans", [
    ("A😀猫B雪Z", "legacy", [(1, 4), (3, 6)]),
    ("A😀猫B雪Z", "legacy", [(3, 6), (1, 4)]),
    (" \t\r\n ", " \x01\x85\t ", [(3, 5), (1, 4)]),
])
def test_retained_dto_preserves_legacy_text_name_and_declared_order(text, name, spans):
    data = retained_dto(text, name, spans)
    before = deepcopy(data)
    result = validate_result("get", data, scope(), {"source_revision": data["source"]["source_revision"]})
    assert result == before
    assert [(p["start"], p["end"]) for p in result["passages"]] == spans
    listed = {"schema_version": 1, "binary_retained": False, "graph_ingestion_executed": False,
              "sources": [data["source"]], "has_more": False, "window_limit": 20}
    assert validate_result("list", listed, scope(), {}) == listed
    # Reading retained records must not relax the new POST admission rules.
    if not text.strip():
        with pytest.raises(KnowledgeReadError):
            validate_upload(upload(text))
    value = upload("valid")
    value["source_name"] = " \x01\x85\t "
    with pytest.raises(KnowledgeReadError):
        validate_upload(value)


@pytest.mark.parametrize("change", [
    lambda d: d.update(text="\x00"), lambda d: d.update(text="\ud800"),
    lambda d: d["source"].update(source_name="a\x00b"),
    lambda d: d["source"].update(source_name="\ud800"),
    lambda d: d["passages"][0].update(start=True),
    lambda d: d["passages"][1].update(evidence_id=d["passages"][0]["evidence_id"]),
    lambda d: d["passages"][0].update(excerpt_sha256="0" * 64),
])
def test_legacy_retained_reads_still_reject_corrupt_dto(change):
    data = retained_dto("A😀猫B雪Z", "legacy", [(3, 6), (1, 4)])
    change(data)
    with pytest.raises(KnowledgeTransportFailure):
        validate_result("get", data, scope(), {"source_revision": data["source"]["source_revision"]})


def test_new_retention_requires_exact_ordered_generated_declarations():
    text = "😀" * 8193
    revision = str(uuid4())
    payload = {"source_revision": revision, "source_name": "new", "text": text, "blocks": None}
    data = retained_dto(text, "new", [])
    data["source"]["source_revision"] = revision
    data.pop("text")
    data["passages"] = [{**p, "page": None,
        "excerpt_sha256": hashlib.sha256(text[p["start"]:p["end"]].encode()).hexdigest()}
        for p in declarations(revision, text)]
    assert validate_result("retain", data, scope(), payload) == data
    data["passages"].reverse()
    with pytest.raises(KnowledgeTransportFailure):
        validate_result("retain", data, scope(), payload)


def test_main_busy_admission_does_not_spawn_and_recovers(host):
    _, child, _, settings = host
    facade = KnowledgeSourceFacade(settings, client_factory=lambda: child)
    assert facade._admission.acquire(blocking=False)
    assert facade._admission.acquire(blocking=False)
    from app.services.knowledge_transport import KnowledgeBusy
    try:
        with pytest.raises(KnowledgeBusy):
            facade.execute("retain", "display-1", upload())
        assert not child.calls
    finally:
        facade._admission.release()
        facade._admission.release()
    assert facade.execute("list", "display-1", {})["sources"] == []


def test_main_shared_deadline_expired_after_context_does_not_retain(host, monkeypatch):
    _, child, _, settings = host
    from app.services import knowledge_source_facade as module
    clock = {"now": 0}
    monkeypatch.setattr(module.time, "monotonic", lambda: clock["now"])
    original = child.call
    def delayed(raw):
        result = original(raw)
        clock["now"] = 61
        return result
    child.call = delayed
    facade = KnowledgeSourceFacade(settings, client_factory=lambda: child)
    with pytest.raises(KnowledgeTransportFailure) as error:
        facade.execute("retain", "display-1", upload())
    assert error.value.outcome_unknown is False
    assert [call["method"] for call in child.calls] == ["context"]
    assert child.records == {}


def test_main_late_retention_reports_unknown_without_retry_and_allows_get(host, monkeypatch):
    _, child, _, settings = host
    from app.services import knowledge_source_facade as module
    clock = {"now": 0}
    monkeypatch.setattr(module.time, "monotonic", lambda: clock["now"])
    original = child.call
    def delayed(raw):
        result = original(raw)
        if json.loads(raw)["method"] == "retain":
            clock["now"] = 61
        return result
    child.call = delayed
    facade = KnowledgeSourceFacade(settings, client_factory=lambda: child)
    value = upload()
    with pytest.raises(KnowledgeTransportFailure) as error:
        facade.execute("retain", "display-1", value)
    assert error.value.outcome_unknown is True
    assert [call["method"] for call in child.calls] == ["context", "retain"]
    assert value["source_revision"] in child.records
    result = facade.execute("get", "display-1", {"source_revision": value["source_revision"]})
    assert result["text"] == value["content"]


@pytest.fixture
def protected_entrypoint(monkeypatch, tmp_path):
    """Actual run.py and settings parser; only Flask.run is intercepted."""
    import importlib.util
    from pathlib import Path
    from flask import Flask
    monkeypatch.delenv("FLASK_HOST", raising=False)
    monkeypatch.delenv("MIROFISH_ALLOWED_ORIGINS", raising=False)
    for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE", "PGOPTIONS"):
        monkeypatch.delenv(key, raising=False)
    script = tmp_path / "site-packages" / "mirofish_knowledge" / "read_bootstrap.py"
    script.parent.mkdir(parents=True)
    script.write_text("# Path validation fixture; never launched.\n", encoding="utf-8")
    values = {"KNOWLEDGE_PYTHON": str(Path(sys.executable).absolute()),
        "KNOWLEDGE_BOOTSTRAP_SCRIPT": str(script), "KNOWLEDGE_READ_TOKEN": "0123456789abcdef" * 4,
        "KNOWLEDGE_PRINCIPAL": "owner", "KNOWLEDGE_DISPLAY_GRAPH_ID": "display-1",
        "KNOWLEDGE_BOUND_SCOPE_JSON": json.dumps(scope()), "KNOWLEDGE_PG_HOST": "127.0.0.1",
        "KNOWLEDGE_PG_PORT": "15432", "KNOWLEDGE_PG_DATABASE": "mirofish_operations_test",
        "KNOWLEDGE_PG_USER": "mirofish_fixture", "KNOWLEDGE_PG_PASSWORD": "fixture-password",
        "KNOWLEDGE_NEO4J_URI": "bolt://127.0.0.1:17687", "KNOWLEDGE_NEO4J_USER": "neo4j",
        "KNOWLEDGE_NEO4J_PASSWORD": "fixture-password", "FLASK_PORT": "5001"}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(Config, "DEBUG", False)
    # Legacy validation would fail, proving protected startup chooses the actual
    # validate_readonly path without replacing either validator or the factory.
    monkeypatch.setattr(Config, "LLM_API_KEY", "")
    monkeypatch.setattr(Config, "ZEP_API_KEY", "")
    launches = []
    def capture(app, **kwargs):
        launches.append((app, kwargs))
    monkeypatch.setattr(Flask, "run", capture)
    # run.py inserts its backend directory; monkeypatch restores sys.path after
    # this authored test so entrypoint loading cannot leak that mutation.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    spec = importlib.util.spec_from_file_location("mirofish_source_entrypoint_test",
        Path(__file__).resolve().parents[1] / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, launches


@pytest.mark.parametrize("mode", ["research_local", "graphiti_readonly"])
def test_actual_protected_entrypoint_uses_validated_loopback_defaults(protected_entrypoint, monkeypatch, mode):
    entrypoint, launches = protected_entrypoint
    monkeypatch.setenv("MIROFISH_APP_MODE", mode)
    assert Config.validate()  # No legacy model/Zep credentials configured.
    assert Config.validate_readonly() == []
    entrypoint.main()
    assert len(launches) == 1
    app, options = launches[0]
    assert options == {"host": "127.0.0.1", "port": 5001, "debug": False, "threaded": True}
    health = app.test_client().get("/health").json
    assert health["mode"] == mode
    assert ("source_retention" in health["capabilities"]) == (mode == "research_local")


@pytest.mark.parametrize("unsafe", ["debug", "host"])
def test_actual_research_entrypoint_rejects_unsafe_configuration(protected_entrypoint, monkeypatch, unsafe):
    entrypoint, launches = protected_entrypoint
    monkeypatch.setenv("MIROFISH_APP_MODE", "research_local")
    if unsafe == "debug":
        monkeypatch.setattr(Config, "DEBUG", True)
    else:
        monkeypatch.setenv("FLASK_HOST", "0.0.0.0")
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 1 and not launches

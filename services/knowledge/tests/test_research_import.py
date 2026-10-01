"""Pure authored import admission regressions; execution belongs to Main."""
from copy import deepcopy
import hashlib
from io import BytesIO, StringIO
import json
from uuid import UUID

import pytest

import mirofish_storage.research_import as importer
import mirofish_storage.research_import_cli as cli
from mirofish_storage.research_bundle import canonical, decode, MAX_ARTIFACT_BYTES
from mirofish_storage.research_import import ImportError, ResearchImportStore, prepare_import, remap_id
from mirofish_storage.validation import canonical_payload
from test_research_bundle import artifact, reseal, SOURCE, EVIDENCE

TARGET = "00000000-0000-0000-0000-000000000011"
SECOND = "00000000-0000-0000-0000-000000000012"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def prepare(raw):
    return prepare_import(raw, "local-owner", TARGET, 1, digest(raw))


def request(path, raw):
    return {"operation": "import", "principal": "local-owner", "target_project_id": TARGET,
            "expected_revision": 1, "input": str(path), "expected_sha256": digest(raw)}


def test_typed_deterministic_mapping_target_hash_isolation_and_copy():
    raw = artifact()
    plan = prepare(raw)
    mapped_source = remap_id(TARGET, "source", digest(raw), SOURCE)
    mapped_passage = remap_id(TARGET, "passage", digest(raw), EVIDENCE)
    assert mapped_source.version == mapped_passage.version == 5
    assert mapped_source == remap_id(UUID(TARGET), "source", digest(raw), UUID(SOURCE))
    assert len({mapped_source, mapped_passage, UUID(SOURCE), UUID(EVIDENCE),
                remap_id(SECOND, "source", digest(raw), SOURCE),
                remap_id(TARGET, "source", digest(raw + b" "), SOURCE),
                remap_id(TARGET, "passage", digest(raw), SOURCE)}) == 7
    assert plan.provenance["sources"][0]["imported_source_revision"] == str(mapped_source)
    assert plan.provenance["sources"][0]["passages"][0]["imported_evidence_id"] == str(mapped_passage)
    mutable = plan.payload
    mutable["sources"][0]["text"] = "changed"
    copied = plan.provenance
    copied["sources"].clear()
    assert prepare(raw) == plan and len(plan.provenance["sources"]) == 1
    assert plan.payload["sources"][0]["text"] != "changed"


def test_inert_metadata_and_original_identity_time_retained():
    raw = artifact()
    plan = prepare(raw)
    original = decode(raw)["payload"]
    assert plan.provenance["origin_project"] == original["project"]
    source = plan.provenance["sources"][0]
    assert source["source_revision"] == SOURCE
    assert source["recorded_at"] == original["sources"][0]["recorded_at"]
    assert source["passages"][0]["evidence_id"] == EVIDENCE
    assert "https://invalid.example/do-not-dispatch" in plan._provenance_json
    assert "../../private/<script>" in plan._provenance_json
    assert "text" not in source and "excerpt" not in source["passages"][0]


@pytest.mark.parametrize("principal,target,revision,hash_value", [
    ("", TARGET, 1, "valid"), ("local-owner", "bad", 1, "valid"),
    ("local-owner", TARGET, True, "valid"), ("local-owner", TARGET, 0, "valid"),
    ("local-owner", TARGET, 2_147_483_648, "valid"),
    ("local-owner", TARGET, 1, None), ("local-owner", TARGET, 1, "0" * 64),
    ("local-owner", TARGET, 1, "A" * 64),
])
def test_trusted_inputs_denied_before_connection(principal, target, revision, hash_value):
    raw = artifact()
    def forbidden():
        pytest.fail("connection before admission")
    with pytest.raises(ImportError):
        ResearchImportStore(forbidden).import_bundle(raw, principal, target, revision,
            digest(raw) if hash_value == "valid" else hash_value)


@pytest.mark.parametrize("raw", [b"{", b'{"x":1,"x":2}', b'{"x":NaN}',
                                  b"\xff", b" " * (MAX_ARTIFACT_BYTES + 1)],
                         ids=["malformed-json", "duplicate-key", "nonfinite", "invalid-utf8", "artifact-over-cap"])
def test_malformed_artifact_has_no_connection(raw):
    with pytest.raises(ImportError):
        ResearchImportStore(lambda: pytest.fail("connection")).import_bundle(raw, "owner", TARGET, 1, digest(raw))


@pytest.mark.parametrize("case", ["extra", "offset", "page", "uuid", "text_hash", "excerpt",
                                  "duplicate_source", "duplicate_passage", "boolean_length",
                                  "source_limit", "passage_limit", "text_limit", "payload_hash"])
def test_strict_bundle_validation_before_connection(case):
    value = decode(artifact())
    source = value["payload"]["sources"][0]
    passage = source["passages"][0]
    if case == "extra": value["extra"] = True
    elif case == "offset": passage["end"] = len(source["text"]) + 1
    elif case == "page": passage["page"] = True
    elif case == "uuid": source["source_revision"] = SOURCE.upper().replace("0002", "NOPE")
    elif case == "text_hash": source["text_sha256"] = "0" * 64
    elif case == "excerpt": passage["excerpt"] = "wrong"
    elif case == "duplicate_source": value["payload"]["sources"].append(deepcopy(source))
    elif case == "duplicate_passage": source["passages"].append(deepcopy(passage))
    elif case == "boolean_length": source["byte_length"] = True
    elif case == "source_limit": value["payload"]["sources"] *= 17
    elif case == "passage_limit": source["passages"] *= 101
    elif case == "text_limit": source["text"] = "a" * (1024 * 1024 + 1)
    raw = reseal(value)
    if case == "payload_hash":
        value["payload_sha256"] = "0" * 64
        raw = canonical(value)
    with pytest.raises(ImportError):
        ResearchImportStore(lambda: pytest.fail("connection")).import_bundle(raw, "owner", TARGET, 1, digest(raw))


@pytest.mark.parametrize("in_key", [False, True])
def test_nul_in_copied_postgres_metadata_denied_even_with_valid_digests(in_key):
    value = decode(artifact())
    project = value["payload"]["project"]
    project["snapshot"]["ontology"] = {"bad\x00key" if in_key else "safe": "safe" if in_key else "bad\x00value"}
    _, _, project["digest"] = canonical_payload(project["snapshot"], project["evidence"], project["display_id"])
    raw = reseal(value)
    with pytest.raises(ImportError):
        ResearchImportStore(lambda: pytest.fail("connection")).import_bundle(raw, "owner", TARGET, 1, digest(raw))


def test_raw_surrogate_and_provenance_cap_preconnection(monkeypatch):
    raw = artifact().replace(b'"name":"synthetic retained text"', b'"name":"\\ud800"')
    with pytest.raises(ImportError): prepare(raw)
    monkeypatch.setattr(importer, "MAX_PROVENANCE_BYTES", 64)
    with pytest.raises(ImportError):
        ResearchImportStore(lambda: pytest.fail("connection")).import_bundle(artifact(), "owner", TARGET, 1, digest(artifact()))


def test_actual_aggregate_utf8_cap_before_connection():
    value = decode(artifact())
    template = value["payload"]["sources"][0]
    text = "猫" * (1024 * 1024 // 3)
    template.update(text=text, text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    byte_length=len(text.encode()), codepoint_length=len(text), passages=[])
    value["payload"]["sources"] = []
    for number in range(9):
        item = deepcopy(template)
        item["source_revision"] = str(UUID(int=100 + number))
        value["payload"]["sources"].append(item)
    raw = reseal(value)
    assert len(raw) < MAX_ARTIFACT_BYTES
    with pytest.raises(ImportError):
        ResearchImportStore(lambda: pytest.fail("connection")).import_bundle(raw, "owner", TARGET, 1, digest(raw))


@pytest.mark.parametrize("field,value", [("expected_sha256", None), ("expected_revision", True),
    ("target_project_id", "bad"), ("principal", "PRIVATE\nOWNER"), ("operation", "export"),
    ("dsn", "private"), ("output", "/private"), ("expected_sha256", "f" * 65)])
def test_cli_request_strict_fields(field, value, tmp_path):
    data = request(tmp_path / "input.json", artifact())
    data[field] = value
    with pytest.raises(ImportError): cli.parse_request(canonical(data))


def test_cli_bad_file_digest_no_store_and_safe_output(tmp_path, monkeypatch):
    raw = artifact()
    path = tmp_path / "PRIVATE_PATH.json"
    path.write_bytes(raw + b" ")
    monkeypatch.setattr(cli, "_store", lambda: pytest.fail("database dependency"))
    out = StringIO()
    assert cli.main(stdin=BytesIO(canonical(request(path, raw))), stdout=out) == 2
    assert json.loads(out.getvalue()) == {"ok": False, "error": "invalid_import"}
    assert "PRIVATE" not in out.getvalue()


@pytest.mark.parametrize("setting", ["PGHOST", "PGSERVICE", "pgfutureoption"])
def test_cli_ambient_authority_denied(setting, monkeypatch):
    monkeypatch.setenv(setting, "PRIVATE")
    with pytest.raises(ImportError, match="authority_unavailable"): cli._store()


@pytest.mark.parametrize("option", ["service", "servicefile", "passfile"])
def test_cli_dsn_override_denied_before_connect(option, monkeypatch):
    for key in list(__import__("os").environ):
        if key.upper().startswith("PG"): monkeypatch.delenv(key)
    monkeypatch.setenv("MIROFISH_APPSTORE_DSN", f"host=127.0.0.1 {option}=PRIVATE")
    import psycopg
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: pytest.fail("connect"))
    with pytest.raises(ImportError, match="authority_unavailable"): cli._store()


def test_cli_duplicate_fields_and_request_cap():
    for raw in (b'{"operation":"import","operation":"import"}', b" " * (cli.MAX_REQUEST_BYTES + 1)):
        with pytest.raises(ImportError): cli.parse_request(raw)

"""Authored pure artifact tests; Main executes and qualifies."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from io import BytesIO, StringIO
import json
import stat
from types import SimpleNamespace
from uuid import UUID

import pytest

from mirofish_storage.research_bundle import (BundleError, MAX_ARTIFACT_BYTES,
    canonical, decode, export_bundle, inspect_bundle)
from mirofish_storage.research_bundle_cli import main, parse_request
from mirofish_storage.validation import canonical_payload
from test_project_store import snapshot

PROJECT = "00000000-0000-0000-0000-000000000001"
SOURCE = "00000000-0000-0000-0000-000000000002"
EVIDENCE = "00000000-0000-0000-0000-000000000003"


def fixture_records():
    snap = snapshot()
    snap["files"][0]["path"] = "../../private/<script>alert(1)</script>"
    snap["graph_build_task_id"] = "https://invalid.example/do-not-dispatch"
    snap, evidence, digest = canonical_payload(snap, [], "proj_1")
    project = SimpleNamespace(principal="owner", workspace_id=UUID(PROJECT),
        project_id=UUID(PROJECT), display_id="proj_1", revision=1,
        snapshot=snap, evidence=evidence, digest=digest)
    text = "\ufeffA😀\r\n猫e\u0301"
    excerpt = text[2:6]
    passage = SimpleNamespace(evidence_id=UUID(EVIDENCE), source_revision=UUID(SOURCE),
        project_id=UUID(PROJECT), start=2, end=6, page=3, excerpt=excerpt,
        excerpt_sha256=hashlib.sha256(excerpt.encode()).hexdigest())
    source = SimpleNamespace(project_id=UUID(PROJECT), source_revision=UUID(SOURCE),
        name="synthetic retained text", text=text,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(), byte_length=len(text.encode()),
        codepoint_length=len(text), recorded_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        passages=(passage,))
    return project, source


def artifact():
    project, source = fixture_records()
    calls = []
    def get(principal, identity, revision):
        calls.append((principal, identity, revision))
        return project
    def get_source(principal, identity, revision):
        calls.append((principal, identity, revision))
        return source
    raw = export_bundle(SimpleNamespace(get=get), SimpleNamespace(get_source=get_source),
                        "owner", PROJECT, 1, [SOURCE])
    assert calls == [("owner", PROJECT, 1), ("owner", PROJECT, SOURCE)]
    return raw


def reseal(value):
    value["payload_sha256"] = hashlib.sha256(canonical(value["payload"])).hexdigest()
    return canonical(value)


def test_deterministic_unicode_inert_metadata_and_summary():
    raw = artifact()
    assert raw == artifact()
    result = inspect_bundle(raw, hashlib.sha256(raw).hexdigest())
    assert result["source_count"] == result["passage_count"] == 1
    assert result["scope"] == "selected_sources_only"
    assert result["publisher_authenticated"] is False
    assert "script" not in json.dumps(result) and "invalid.example" not in json.dumps(result)
    assert "principal" not in decode(raw)["payload"]["project"]
    assert decode(raw)["payload"]["sources"][0]["passages"][0]["excerpt"] == "😀\r\n猫"


def test_project_reference_to_retained_passage_preserves_exact_metadata():
    project, source = fixture_records()
    reference = {"evidence_id": EVIDENCE, "source_revision": SOURCE,
                 "sha256": "0" * 64, "object_key": "inert/original.bin", "byte_length": 42}
    project.snapshot, project.evidence, project.digest = canonical_payload(
        project.snapshot, [reference], project.display_id)
    raw = export_bundle(SimpleNamespace(get=lambda *args: project),
        SimpleNamespace(get_source=lambda *args: source), "owner", PROJECT, 1, [SOURCE])
    result = inspect_bundle(raw)
    assert result["passage_count"] == 1 and result["original_binaries_included"] is False
    data = decode(raw)["payload"]
    assert data["project"]["evidence"] == [reference]
    assert data["project"]["digest"] == project.digest
    assert data["project"]["snapshot"] == project.snapshot
    # Metadata references do not permit duplicate retained passage records,
    # even when those records have otherwise valid joins to distinct sources.
    duplicate = deepcopy(data["sources"][0])
    duplicate["source_revision"] = "00000000-0000-0000-0000-000000000004"
    duplicate["passages"][0]["source_revision"] = duplicate["source_revision"]
    value = decode(raw)
    value["payload"]["sources"].append(duplicate)
    with pytest.raises(BundleError):
        inspect_bundle(reseal(value))


def test_export_stops_source_reads_immediately_after_actual_utf8_cap(monkeypatch):
    project, source = fixture_records()
    selected = [str(UUID(int=10 + index)) for index in range(12)]
    text = "猫" * (1024 * 1024 // 3)
    read_ids = []
    def get_source(principal, project_id, revision):
        read_ids.append(revision)
        # Intentionally misleading length metadata proves the early gate counts
        # actual UTF-8 bytes. The store double is only a pure control-flow test.
        return SimpleNamespace(**dict(vars(source), source_revision=UUID(revision),
                                      text=text, byte_length=1, passages=()))
    import mirofish_storage.research_bundle as bundle_module
    def no_artifact_assembly(value):
        raise AssertionError("oversized export must stop before artifact encoding")
    monkeypatch.setattr(bundle_module, "canonical", no_artifact_assembly)
    with pytest.raises(BundleError):
        export_bundle(SimpleNamespace(get=lambda *args: project),
            SimpleNamespace(get_source=get_source), "owner", PROJECT, 1, selected[::-1])
    assert read_ids == selected[:9]
    assert len(text.encode("utf-8")) * 8 <= 8 * 1024 * 1024
    assert len(text.encode("utf-8")) * 9 > 8 * 1024 * 1024


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(schema_version=True),
    lambda v: v.update(extra="unknown"),
    lambda v: v["payload"].update(scope="all_sources"),
    lambda v: v["payload"]["project"].update(revision=True),
    lambda v: v["payload"]["project"].update(project_id=PROJECT.replace("-", "")),
    lambda v: v["payload"]["project"].update(digest="0" * 64),
    lambda v: v["payload"]["sources"][0].update(text="tampered"),
    lambda v: v["payload"]["sources"][0].update(byte_length=True),
    lambda v: v["payload"]["sources"][0].update(codepoint_length=99),
    lambda v: v["payload"]["sources"][0].update(recorded_at="2026-02-30T00:00:00+00:00"),
    lambda v: v["payload"]["sources"][0].update(recorded_at="2026-10-01T00:00:00"),
    lambda v: v["payload"]["sources"][0].update(name="\ud800"),
    lambda v: v["payload"]["sources"][0].update(project_id=SOURCE),
    lambda v: v["payload"]["sources"][0]["passages"][0].update(source_revision=PROJECT),
    lambda v: v["payload"]["sources"][0]["passages"][0].update(project_id=SOURCE),
    lambda v: v["payload"]["sources"][0]["passages"][0].update(start=True),
    lambda v: v["payload"]["sources"][0]["passages"][0].update(end=999),
    lambda v: v["payload"]["sources"][0]["passages"][0].update(page=0),
    lambda v: v["payload"]["sources"][0]["passages"][0].update(excerpt="😀\n猫"),
    lambda v: v["payload"]["sources"][0]["passages"][0].update(excerpt_sha256="F" * 64),
    lambda v: v["payload"]["sources"].append(deepcopy(v["payload"]["sources"][0])),
    lambda v: v["payload"]["sources"][0]["passages"].append(deepcopy(v["payload"]["sources"][0]["passages"][0])),
])
def test_resealed_invalid_metadata_rejected(mutate):
    value = decode(artifact())
    mutate(value)
    with pytest.raises((BundleError, UnicodeError)):
        inspect_bundle(reseal(value))


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}',
    b'\xff', b'[' * 40 + b'0' + b']' * 40, b'{} trailing'])
def test_strict_json(raw):
    with pytest.raises(BundleError):
        inspect_bundle(raw)


@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"])
def test_raw_escaped_surrogate_rejected_by_decoder_and_inspection(surrogate):
    value = decode(artifact())
    value["payload"]["sources"][0]["name"] = surrogate
    # ASCII JSON carries an actual escaped surrogate onto the decoder boundary;
    # no UTF-8 canonical encoder is called before the assertions below.
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False).encode("ascii")
    assert b"\\ud800" in raw or b"\\udfff" in raw
    with pytest.raises(BundleError):
        decode(raw)
    with pytest.raises(BundleError):
        inspect_bundle(raw)


def test_caps_and_trusted_digest():
    with pytest.raises(BundleError):
        inspect_bundle(b" " * (MAX_ARTIFACT_BYTES + 1))
    with pytest.raises(BundleError):
        inspect_bundle(artifact(), "0" * 64)
    value = decode(artifact())
    value["payload"]["sources"] *= 17
    with pytest.raises(BundleError):
        inspect_bundle(reseal(value))
    value = decode(artifact())
    value["payload"]["sources"][0]["passages"] *= 101
    with pytest.raises(BundleError):
        inspect_bundle(reseal(value))


def test_aggregate_and_individual_text_caps():
    value = decode(artifact())
    seed = value["payload"]["sources"][0]
    seed.update(text="a" * (1024 * 1024), byte_length=1024 * 1024,
        codepoint_length=1024 * 1024, text_sha256=hashlib.sha256(b"a" * (1024 * 1024)).hexdigest(), passages=[])
    value["payload"]["sources"] = [dict(seed, source_revision=str(UUID(int=10 + i))) for i in range(9)]
    with pytest.raises(BundleError):
        inspect_bundle(reseal(value))
    seed["text"] += "a"
    value["payload"]["sources"] = [seed]
    with pytest.raises(BundleError):
        inspect_bundle(reseal(value))


def test_cli_inspect_without_authority_and_exclusive_output(tmp_path, monkeypatch):
    monkeypatch.delenv("MIROFISH_APPSTORE_DSN", raising=False)
    path = tmp_path / "bundle.json"
    path.write_bytes(artifact())
    stdout = StringIO()
    assert main(stdin=BytesIO(canonical({"operation": "inspect", "input": str(path)})), stdout=stdout) == 0
    assert json.loads(stdout.getvalue())["ok"] is True
    from mirofish_storage.research_bundle_cli import _write_new
    with pytest.raises(FileExistsError):
        _write_new(path, b"replacement")
    assert path.read_bytes() == artifact()


@pytest.mark.parametrize("wire_request", [
    {"operation": "inspect", "input": "https://invalid.example"},
    {"operation": "inspect", "input": "../relative"},
    {"operation": "inspect", "input": "/tmp/x", "dsn": "SECRET"},
    {"operation": "export", "principal": "SECRET"},
])
def test_fixed_safe_cli_errors(wire_request):
    out = StringIO()
    assert main(stdin=BytesIO(canonical(wire_request)), stdout=out) == 2
    assert json.loads(out.getvalue()) == {"ok": False, "error": "invalid_request"}


def test_link_input_and_ancestor_denied(tmp_path):
    target = tmp_path / "data.json"
    target.write_bytes(artifact())
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")
    from mirofish_storage.research_bundle_cli import _read
    with pytest.raises(BundleError):
        _read(link)
    directory = tmp_path / "dir"
    directory.mkdir()
    (directory / "bundle.json").write_bytes(artifact())
    parent_link = tmp_path / "parent"
    parent_link.symlink_to(directory, target_is_directory=True)
    with pytest.raises(BundleError):
        _read(parent_link / "bundle.json")


@pytest.mark.parametrize("path_final_ctime,descriptor_final_ctime,denied", [
    pytest.param(100, 200, False, id="stable-different-path-descriptor-ctime"),
    pytest.param(100, 201, True, id="descriptor-ctime-only-change"),
    pytest.param(101, 200, True, id="path-ctime-only-change"),
])
def test_read_compares_ctime_within_stat_api(
        monkeypatch, path_final_ctime, descriptor_final_ctime, denied):
    import mirofish_storage.research_bundle_cli as cli_module
    raw = b"controlled immutable file bytes"
    def metadata(ctime):
        return SimpleNamespace(st_dev=1, st_ino=2, st_mode=stat.S_IFREG | 0o600,
            st_file_attributes=0, st_size=len(raw), st_mtime_ns=300, st_ctime_ns=ctime)
    path_stats = iter([metadata(100), metadata(path_final_ctime)])
    descriptor_stats = iter([metadata(200), metadata(descriptor_final_ctime)])
    path = SimpleNamespace(lstat=lambda: next(path_stats))
    class ControlledStream(BytesIO):
        def fileno(self):
            return 42
    stream = ControlledStream(raw)
    # Control only the file-admission boundary. Full artifact and actual local
    # file tests remain separate; these cases isolate the Windows API discrepancy.
    monkeypatch.setattr(cli_module, "_ancestors", lambda target: [])
    monkeypatch.setattr(cli_module.os, "open", lambda target, flags: 42)
    monkeypatch.setattr(cli_module.os, "fdopen", lambda descriptor, mode: stream)
    monkeypatch.setattr(cli_module.os, "fstat", lambda descriptor: next(descriptor_stats))
    if denied:
        with pytest.raises(BundleError, match="^invalid_path$"):
            cli_module._read(path)
    else:
        assert cli_module._read(path) == raw

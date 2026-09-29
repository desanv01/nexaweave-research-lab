"""Pure contract tests; no database or application imports."""

from copy import deepcopy
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from mirofish_storage import InvalidProject, ProjectStore, canonical_payload
from mirofish_storage.__main__ import _export_data, _import_payload
from mirofish_storage.validation import principal_id, strict_json


def snapshot():
    return {
        "project_id": "proj_1", "name": "Name", "status": "created",
        "created_at": "2026-09-29T00:00:00", "updated_at": "2026-09-29T00:00:00",
        "files": [{"filename": "a.txt", "path": "../legacy/a.txt", "size": 3}],
        "total_text_length": 0, "ontology": None, "analysis_summary": None,
        "graph_id": None, "graph_build_task_id": None, "zep_batch_id": "inert",
        "zep_batch_operation_id": None, "simulation_requirement": None,
        "chunk_size": 500, "chunk_overlap": 50, "error": None,
    }


def test_canonical_copy_and_inert_path():
    source = snapshot()
    snap, evidence, digest = canonical_payload(source, [], "proj_1")
    source["files"][0]["path"] = "changed"
    assert snap["files"][0]["path"] == "../legacy/a.txt"
    assert len(digest) == 64 and evidence == []


def test_current_graph_upload_file_shape_is_preserved():
    value = snapshot()
    value["files"] = [{"filename": "uploaded.pdf", "size": 17}]
    stored, _, _ = canonical_payload(value, [], "proj_1")
    assert stored["files"] == [{"filename": "uploaded.pdf", "size": 17}]


def test_principal_printable_ascii_boundaries():
    assert principal_id("a b") == "a b"
    assert principal_id("x" * 128) == "x" * 128
    for value in ("", "   ", "x" * 129, "a\nb", "é"):
        with pytest.raises(InvalidProject):
            principal_id(value)


@pytest.mark.parametrize("change", [
    lambda s: s.update(project_id="other"),
    lambda s: s.update(status="bogus"),
    lambda s: s.update(total_text_length=True),
    lambda s: s.update(chunk_size=float("nan")),
    lambda s: s.update(name="\ud800"),
    lambda s: s.update(extra=1),
    lambda s: s.update(ontology={"too": object()}),
])
def test_snapshot_rejects_wrong_types_and_values(change):
    value = snapshot()
    change(value)
    with pytest.raises(InvalidProject):
        canonical_payload(value, [], "proj_1")


def test_evidence_manifest_limits_and_keys():
    entry = {"evidence_id": str(uuid4()), "source_revision": str(uuid4()),
             "sha256": "a" * 64, "object_key": "objects/one", "byte_length": 3}
    canonical_payload(snapshot(), [entry], "proj_1")
    for key in ("../one", "/abs", "a\\b", "https://host/x", "a//b", "a/./b", "a%2fb", "a\nb"):
        bad = dict(entry, object_key=key)
        with pytest.raises(InvalidProject):
            canonical_payload(snapshot(), [bad], "proj_1")
    with pytest.raises(InvalidProject):
        canonical_payload(snapshot(), [entry, entry], "proj_1")


def test_combined_payload_cap():
    value = snapshot()
    value["ontology"] = {f"field_{index}": "x" * 60000 for index in range(18)}
    with pytest.raises(InvalidProject):
        canonical_payload(value, [], "proj_1")


def test_json_input_duplicate_nonfinite_depth_and_size():
    for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'[' * 34 + b'0' + b']' * 34,
                b'"\xed\xa0\x80"', b' ' * (1024 * 1024 + 16385)):
        with pytest.raises(InvalidProject):
            strict_json(raw)


def test_envelope_roundtrip_and_scope_digest_binding():
    principal, workspace, project = "owner", str(uuid4()), str(uuid4())
    args = SimpleNamespace(principal=principal, workspace_id=workspace,
                           project_id=project, display_id="proj_1")
    snap, evidence, digest = canonical_payload(snapshot(), [], "proj_1")
    record = SimpleNamespace(principal=principal, workspace_id=uuid4(),
                             project_id=uuid4(), display_id="proj_1", revision=1,
                             snapshot=snap, evidence=tuple(evidence), digest=digest)
    args.workspace_id, args.project_id = str(record.workspace_id), str(record.project_id)
    data = _export_data(record)
    assert _import_payload(data, args) == (snap, evidence)
    for field, value in (("digest", "0" * 64), ("schema_version", 2),
                         ("principal", "other"), ("revision", 2)):
        changed = deepcopy(data)
        changed[field] = value
        with pytest.raises(InvalidProject):
            _import_payload(changed, args)


def test_invalid_payload_never_opens_connection():
    calls = []
    store = ProjectStore(lambda: calls.append(1))
    with pytest.raises(InvalidProject):
        store.create("owner", uuid4(), uuid4(), "proj_1", {"bad": True})
    assert calls == []


def test_package_import_has_no_connection_or_application_import_effects():
    script = (
        "import sys, psycopg; "
        "psycopg.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError('connected')); "
        "import mirofish_storage; "
        "assert 'flask' not in sys.modules; "
        "assert not any(k == 'graphiti_core' or k.startswith('graphiti_core.') for k in sys.modules); "
        "assert not any(k == 'mirofish_knowledge' or k.startswith('mirofish_knowledge.') for k in sys.modules)"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, check=False, timeout=10)
    assert result.returncode == 0, result.stderr

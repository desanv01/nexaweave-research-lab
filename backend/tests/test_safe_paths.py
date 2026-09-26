"""Adversarial source tests for the persisted resource boundary."""

import os
from pathlib import Path

import pytest

from app.utils.safe_paths import (
    InvalidResourcePath, MAX_RESOURCE_ID_LENGTH, safe_flat_report_path, safe_path,
    validate_resource_id, validate_tree,
)


@pytest.mark.parametrize("value", [
    None, 1, [], "", ".", "..", "../other", r"..\other",
    "/absolute", r"C:\absolute", r"\\server\share", "a:b",
    "a\x00b", "a\nb", "a ", "a.", "CON", "con.txt", "Lpt9",
    "é", "a" * (MAX_RESOURCE_ID_LENGTH + 1),
])
def test_invalid_components(value):
    with pytest.raises(InvalidResourcePath):
        validate_resource_id(value)


@pytest.mark.parametrize("value", ["proj_ab-12", "sim_abc123", "report_123", "legacy.v1"])
def test_existing_id_shapes_remain_valid(value):
    assert validate_resource_id(value) == value


def test_bounded_id_can_form_legacy_flat_filename(tmp_path):
    root = tmp_path / "reports"
    report_id = "r" * MAX_RESOURCE_ID_LENGTH
    assert safe_flat_report_path(root, report_id, ".json").endswith(f"{report_id}.json")


def test_lstat_permission_failure_is_client_safe(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    original_lstat = Path.lstat

    def denied(path):
        if path.name == "proj_denied":
            raise PermissionError("private local path")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", denied)
    with pytest.raises(InvalidResourcePath, match="^Invalid resource path$"):
        safe_path(root, "proj_denied")


def test_sibling_prefix_and_root_are_not_resources(tmp_path):
    root = tmp_path / "reports"
    root.mkdir()
    sibling = tmp_path / "reports-escape"
    sibling.mkdir()
    with pytest.raises(InvalidResourcePath):
        safe_path(root)
    with pytest.raises(InvalidResourcePath):
        safe_path(root, "..", "reports-escape")
    assert safe_path(root, "report_1").startswith(str(root))


def _symlink(link, target, is_directory=False):
    try:
        link.symlink_to(target, target_is_directory=is_directory)
    except (OSError, NotImplementedError) as exc:
        if os.name == "nt":
            pytest.skip(f"Windows symlink privilege unavailable: {exc}")
        raise


def test_nested_directory_and_final_file_links_cannot_escape(tmp_path):
    root = tmp_path / "projects"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "sentinel.txt").write_text("untouched")
    project = root / "proj_1"
    project.mkdir()
    _symlink(project / "files", outside, is_directory=True)
    with pytest.raises(InvalidResourcePath):
        safe_path(root, "proj_1", "files", "sentinel.txt")
    (project / "files").unlink()
    _symlink(project / "project.json", outside / "sentinel.txt")
    with pytest.raises(InvalidResourcePath):
        safe_path(root, "proj_1", "project.json")
    with pytest.raises(InvalidResourcePath):
        validate_tree(root, "proj_1")
    assert (outside / "sentinel.txt").read_text() == "untouched"


def test_same_root_sibling_link_is_rejected(tmp_path):
    root = tmp_path / "reports"
    source = root / "report_a"
    sibling = root / "report_b"
    source.mkdir(parents=True)
    sibling.mkdir()
    (sibling / "meta.json").write_text("sibling")
    _symlink(source / "meta.json", sibling / "meta.json")
    with pytest.raises(InvalidResourcePath):
        safe_path(root, "report_a", "meta.json")

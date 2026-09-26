"""Resource-level path tests; no live provider or model calls."""

import os

import pytest

from app.models.project import ProjectManager
from app.services.report_agent import ReportManager
from app.services.simulation_manager import SimulationManager
from app.services.simulation_runner import RunnerStatus, SimulationRunner
from app.utils.safe_paths import InvalidResourcePath


@pytest.fixture
def storage(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    simulations = tmp_path / "simulations"
    reports = tmp_path / "reports"
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(projects))
    monkeypatch.setattr(SimulationManager, "SIMULATION_DATA_DIR", str(simulations))
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(simulations))
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(reports))
    return projects, simulations, reports


def _symlink(link, target, is_directory=False):
    try:
        link.symlink_to(target, target_is_directory=is_directory)
    except (OSError, NotImplementedError) as exc:
        if os.name == "nt":
            pytest.skip(f"Windows symlink privilege unavailable: {exc}")
        raise


def test_missing_simulation_read_has_no_directory_side_effect(storage):
    _, simulations, _ = storage
    manager = SimulationManager()
    assert manager.get_simulation("sim_missing") is None
    assert SimulationRunner.get_run_state("sim_missing") is None
    assert not simulations.exists()


def test_project_listing_skips_unsafe_entry_and_preserves_outside(storage, tmp_path):
    projects, _, _ = storage
    projects.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "project.json").write_text('{"project_id":"proj_unsafe"}')
    _symlink(projects / "proj_unsafe", outside, is_directory=True)
    assert ProjectManager.list_projects() == []
    with pytest.raises(InvalidResourcePath):
        ProjectManager.delete_project("proj_unsafe")
    assert (outside / "project.json").exists()


def test_project_text_write_does_not_follow_outside_file(storage, tmp_path):
    projects, _, _ = storage
    project = projects / "proj_safe"
    project.mkdir(parents=True)
    outside = tmp_path / "sentinel.txt"
    outside.write_text("keep")
    _symlink(project / "extracted_text.txt", outside)
    with pytest.raises(InvalidResourcePath):
        ProjectManager.save_extracted_text("proj_safe", "overwrite")
    assert outside.read_text() == "keep"


def test_project_text_read_does_not_follow_outside_file(storage, tmp_path):
    projects, _, _ = storage
    project = projects / "proj_safe"
    project.mkdir(parents=True)
    outside = tmp_path / "sentinel.txt"
    outside.write_text("secret")
    _symlink(project / "extracted_text.txt", outside)
    with pytest.raises(InvalidResourcePath):
        ProjectManager.get_extracted_text("proj_safe")


def test_legacy_flat_report_and_invalid_section(storage):
    _, _, reports = storage
    reports.mkdir()
    (reports / "report_old.json").write_text("{}")
    (reports / "report_old.md").write_text("legacy")
    assert ReportManager.delete_report("report_old") is True
    assert not (reports / "report_old.json").exists()
    assert not (reports / "report_old.md").exists()
    with pytest.raises(InvalidResourcePath):
        ReportManager._get_section_path("report_ok", -1)
    with pytest.raises(InvalidResourcePath):
        ReportManager._get_section_path("report_ok", 1000)


def test_report_directory_deletion_rejects_outside_descendant(storage, tmp_path):
    _, _, reports = storage
    folder = reports / "report_safe"
    folder.mkdir(parents=True)
    outside = tmp_path / "sentinel.txt"
    outside.write_text("keep")
    _symlink(folder / "full_report.md", outside)
    with pytest.raises(InvalidResourcePath):
        ReportManager.delete_report("report_safe")
    assert outside.read_text() == "keep"
    assert folder.exists()


def test_runner_cache_cannot_bypass_path_check(storage, tmp_path):
    _, simulations, _ = storage
    simulations.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _symlink(simulations / "sim_cached", outside, is_directory=True)
    with pytest.raises(InvalidResourcePath):
        SimulationRunner.get_run_state("sim_cached")


def test_secondary_projection_path_failure_is_nonfatal(storage, monkeypatch):
    def projection_failure(_self, _simulation_id):
        raise InvalidResourcePath("Invalid resource path")

    monkeypatch.setattr(SimulationManager, "get_simulation", projection_failure)
    # state.json is a secondary projection. This method must return so the
    # authoritative run-state and producer/ingestion cleanup can continue.
    assert SimulationRunner._sync_simulation_status(
        "sim_valid", RunnerStatus.STOPPED
    ) is None


def test_route_invalid_id_status_is_client_safe(storage):
    from flask import Flask
    from app.api import graph_bp, simulation_bp, report_bp

    app = Flask(__name__)
    app.register_blueprint(graph_bp, url_prefix="/graph")
    app.register_blueprint(simulation_bp, url_prefix="/simulation")
    app.register_blueprint(report_bp, url_prefix="/report")
    client = app.test_client()
    for path in ("/graph/project/CON", "/simulation/C%3Aevil",
                 "/report/CON", "/report/report_ok/section/1000"):
        response = client.get(path)
        assert response.status_code == 400
        assert response.json == {"success": False, "error": "Invalid resource path"}


def test_conflicting_route_and_query_ids_are_each_checked(storage):
    from flask import Flask
    from app.api import report_bp, simulation_bp

    app = Flask(__name__)
    app.register_blueprint(report_bp, url_prefix="/report")
    app.register_blueprint(simulation_bp, url_prefix="/simulation")
    client = app.test_client()
    assert client.get("/report/CON?report_id=report_valid").status_code == 400
    assert client.get("/simulation/CON?simulation_id=sim_valid").status_code == 400


def test_report_route_rejects_linked_metadata(storage, tmp_path):
    from flask import Flask
    from app.api import report_bp

    _, _, reports = storage
    folder = reports / "report_valid"
    folder.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    _symlink(folder / "meta.json", outside)
    app = Flask(__name__)
    app.register_blueprint(report_bp, url_prefix="/report")
    response = app.test_client().get("/report/report_valid")
    assert response.status_code == 400
    assert response.json == {"success": False, "error": "Invalid resource path"}

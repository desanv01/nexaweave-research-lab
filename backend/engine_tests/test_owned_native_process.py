"""Spawned real OASIS execution with trusted offline models only."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import replace
from uuid import uuid4

import pytest

from nexaweave_execution.native_process_driver import NativeProcessDriver
from nexaweave_execution.native_run_contracts import NativeRunRequest, NativeRunUnavailable


def offline_models():
    from test_native_prepared_workflow import _offline_model
    return {"twitter": _offline_model(), "reddit": _offline_model()}


def prepared_binding(tmp_path):
    from app.services.native_owned_session import NativeOwnedSessionFactory, _manifest
    from test_native_prepared_workflow import _prepared
    root = _prepared(tmp_path)
    names = ("state.json", "simulation_config.json", "source_grounding.json",
             "twitter_profiles.csv", "reddit_profiles.json")
    originals = {name: (root / name).read_bytes() for name in names}
    artifact = _manifest(root, names, 2 * 1024 * 1024)
    project = uuid4()
    factory = NativeOwnedSessionFactory(
        str(root), "graph-fixture", "sim-fixture", "owner", str(project), 1,
        ("twitter", "reddit"), 7, 1, "b" * 64, offline_models)
    request = NativeRunRequest.from_wire(dict(schema_version=1, principal="owner",
        project_id=project, project_revision=1, simulation_id="sim-fixture",
        run_id=uuid4(), artifact_sha256=artifact, runtime_sha256="b" * 64,
        platforms=("twitter", "reddit"), seed=7, max_rounds=1))
    return root, factory, request, originals


def terminal(driver, request, attempt, child):
    # A fresh Windows interpreter imports the locked torch/CAMEL/OASIS stack.
    # Include cold import time as well as the session's bounded native round.
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        observed = driver.observe(request, attempt, child)
        if observed.status != "running":
            return observed
        time.sleep(0.05)
    pytest.fail("bounded native process deadline exceeded")


def test_owned_child_runs_real_both_platforms_and_preserves_prepared_inputs(tmp_path,
                                                                             monkeypatch):
    monkeypatch.chdir(tmp_path)
    root, factory, request, originals = prepared_binding(tmp_path)
    driver = NativeProcessDriver(factory, handshake_seconds=10,
                                 go_timeout_seconds=30, join_seconds=3)
    attempt = uuid4()
    try:
        child = driver.launch(request, attempt)
        assert not (root / ".native_prepared_start_claim").exists()
        observed = terminal(driver, request, attempt, child)
        assert observed.status == "completed" and observed.receipt is not None
        for platform in ("twitter", "reddit"):
            with sqlite3.connect(root / f"{platform}_simulation.db") as connection:
                assert connection.execute("SELECT COUNT(*) FROM user").fetchone()[0] == 2
                posts = [row[0] for row in connection.execute("SELECT content FROM post")]
                assert any(post.startswith("Synthetic native post ") for post in posts)
            assert (root / platform / "actions.jsonl").is_file()
        assert (root / ".native_prepared_start_claim").is_file()
        assert all((root / name).read_bytes() == data for name, data in originals.items())
    finally:
        driver.close()


@pytest.mark.parametrize("change", ["artifact", "runtime", "changed-input"])
def test_mismatched_binding_has_no_native_side_effect(tmp_path, monkeypatch, change):
    monkeypatch.chdir(tmp_path)
    root, factory, request, originals = prepared_binding(tmp_path)
    wrong = (replace(request, artifact_sha256="a" * 64) if change == "artifact"
             else replace(request, runtime_sha256="c" * 64)
             if change == "runtime" else request)
    if change == "changed-input":
        path = root / "source_grounding.json"
        path.write_bytes(path.read_bytes() + b"\n")
        originals["source_grounding.json"] = path.read_bytes()
    driver = NativeProcessDriver(factory, handshake_seconds=10)
    try:
        with pytest.raises(NativeRunUnavailable):
            driver.launch(wrong, uuid4())
        assert not (root / ".native_prepared_start_claim").exists()
        assert all((root / name).read_bytes() == data for name, data in originals.items())
    finally:
        driver.close()

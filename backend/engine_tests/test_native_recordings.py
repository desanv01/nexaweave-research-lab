"""Authored offline OASIS/CAMEL -> closed recording -> fresh inert CLI flow.

Main owns execution under the locked native runner. This file does not use a
mock recording/engine/SQLite boundary. Existing offline native fixture supplies
synthetic model responses without a model provider.
"""
from collections import Counter
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sqlite3
import socket
import subprocess
import sys
import pytest

from app.services.native_simulation import NativeSimulationSession
from app.services.native_recording_contracts import RecordingAnchors
from app.services.native_recordings import capture_recording, NativeRecording
from test_native_prepared_workflow import _prepared, _offline_model


@pytest.fixture(autouse=True)
def _offline_boundary(monkeypatch):
    for key in list(os.environ):
        upper = key.upper()
        if (any(word in upper for word in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))
                or upper.startswith(("LLM_", "OPENAI_", "DEEPSEEK_", "ZEP_", "ANTHROPIC_"))):
            monkeypatch.delenv(key, raising=False)
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    def guarded_connect(sock, address):
        if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError("external native fixture network attempted")
        return original_connect(sock, address)
    def guarded_connect_ex(sock, address):
        if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError("external native fixture network attempted")
        return original_connect_ex(sock, address)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)


CHILD = r'''
import builtins
import pathlib
import runpy
import socket
import sys
cli = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(cli.parent))
original_import = builtins.__import__
forbidden = {"app", "flask", "camel", "oasis", "openai", "graphiti_core", "neo4j",
             "psycopg", "psycopg2", "zep_cloud", "dotenv", "torch", "transformers"}
def guarded(name, *args, **kwargs):
    if name.split(".")[0] in forbidden:
        raise AssertionError("engine/provider import attempted")
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded
connections = []
original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex
def connect(self, address):
    connections.append(address)
    if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1", "localhost"):
        raise AssertionError("external network attempted")
    return original_connect(self, address)
def connect_ex(self, address):
    connections.append(address)
    if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1", "localhost"):
        raise AssertionError("external network attempted")
    return original_connect_ex(self, address)
socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
sys.argv = [str(cli), *sys.argv[2:]]
status = 0
try:
    runpy.run_path(str(cli), run_name="__main__")
except SystemExit as error:
    status = error.code
assert not connections, "recording reads unexpectedly connected a socket"
assert not forbidden.intersection(name.split(".")[0] for name in sys.modules)
raise SystemExit(status)
'''


def _hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def _cli(bundle, revision, anchors, requests, cwd):
    cli = Path(__file__).resolve().parents[1] / "app" / "services" / "native_recording_cli.py"
    command = [sys.executable, "-I", "-c", CHILD, str(cli), "--bundle", str(bundle),
               "--revision", revision]
    for key, value in anchors.wire().items():
        command += ["--" + key.replace("_", "-"), str(value)]
    # Only OS process bootstrap variables; no provider keys/base URLs/.env.
    allowed = {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "TMPDIR", "COMSPEC",
               "SYSTEMDRIVE", "PATHEXT", "LANG", "LC_ALL"}
    environment = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    raw = b"".join(json.dumps(r, allow_nan=False).encode("utf-8") + b"\n" for r in requests)
    completed = subprocess.run(command, input=raw, capture_output=True, cwd=cwd,
                               env=environment, timeout=30, check=False)
    assert completed.stderr == b""
    return completed.returncode, [json.loads(line) for line in completed.stdout.splitlines()]


def test_actual_native_closed_recording_fresh_process_metrics_and_parent_preservation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = _prepared(tmp_path)
    model = _offline_model()
    session = NativeSimulationSession(root, graph_id="graph-fixture", simulation_id="sim-fixture",
        models={"twitter": model, "reddit": model}, seed=7, max_rounds=1)
    with session:
        assert set(session._results) == {"twitter", "reddit"}
    assert session._closed and session._results == {}
    before = _hashes(root)
    events = {p: [json.loads(line) for line in (root / p / "actions.jsonl").read_text(
                     encoding="utf-8").splitlines()] for p in ("twitter", "reddit")}
    native_counts = {}
    for p in events:
        assert events[p][0]["event_type"] == "simulation_start"
        assert events[p][-1]["event_type"] == "simulation_end"
        assert any(e.get("round") == 0 and "action_type" in e for e in events[p])
        assert any(e.get("round") == 1 and "action_type" in e for e in events[p])
        # Independent expectations from the real native DB, not recorder output.
        with sqlite3.connect(f"{(root / (p + '_simulation.db')).as_uri()}?mode=ro", uri=True) as connection:
            native_counts[p] = {t: connection.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                                for t in ("post", "follow", "trace")}
            contents = [row[0] for row in connection.execute("SELECT content FROM post")]
            assert "Synthetic initial event" in contents
            assert any(text.startswith("Synthetic native post ") for text in contents)
            assert native_counts[p]["post"] >= 3
    anchors = RecordingAnchors("graph-fixture", "sim-fixture", "recording-run",
                               "parent-branch", "recording-project", 1)
    bundle = tmp_path / "recording"
    revision = capture_recording(root, bundle, anchors=anchors, platforms=("twitter", "reddit"),
        closed_run_confirmed=True, runtime_versions={"oasis": importlib.metadata.version("camel-oasis"),
            "camel": importlib.metadata.version("camel-ai")}, runtime_sha256="b" * 64)
    assert _hashes(root) == before
    # The CLI uses a fresh interpreter for every page; cursors survive process exit.
    for p in events:
        cursor = None
        played, offsets = [], []
        for _ in range(100):
            status, replies = _cli(bundle, revision, anchors, [{"version": 1,
                "operation": "playback", "platform": p, "limit": 2, "cursor": cursor}], tmp_path)
            assert status == 0 and len(replies) == 1 and replies[0]["ok"] is True
            page = replies[0]["result"]
            assert page["anchors"] == anchors.wire()
            assert page["recording_revision"] == revision
            assert page["continuation_supported"] is False
            assert page["branch_execution_supported"] is False
            assert page["deterministic_model_rerun"] is False
            assert all(item["platform"] == p for item in page["events"])
            played.extend(item["record"] for item in page["events"])
            offsets.extend(item["source_event_offset"] for item in page["events"])
            cursor = page["next_cursor"]
            if cursor is None:
                assert page["exhausted"] is True
                break
            assert page["exhausted"] is False
        else:
            raise AssertionError("bounded recording pages did not exhaust")
        assert played == events[p]
        assert offsets == list(range(len(events[p])))
        status, replies = _cli(bundle, revision, anchors, [{"version": 1,
            "operation": "metrics", "platform": p, "limit": 100}], tmp_path)
        assert status == 0 and replies[0]["ok"] is True
        metrics = replies[0]["result"]
        assert metrics["logged_action_counts"] == dict(Counter(
            item["action_type"] for item in events[p] if "action_type" in item))
        for table, count in native_counts[p].items():
            assert metrics["final_native_tables"][table]["count"] == count
        assert metrics["coverage"]["historical_per_round_graph_state"] is False
        assert metrics["coverage"]["exact_event_to_native_row_links"] is False
        assert _hashes(root) == before
    # Tamper rejection happens before DB opening even in the fresh CLI process.
    tampered = bundle / "twitter" / "actions.jsonl"
    tampered.write_bytes(tampered.read_bytes() + b" ")
    status, replies = _cli(bundle, revision, anchors, [], tmp_path)
    assert status == 2 and len(replies) == 1
    assert replies[0]["ok"] is False and replies[0]["error"] == "invalid_recording"
    assert replies[0]["recording_admitted"] is False
    assert replies[0]["anchors"] == anchors.wire()
    assert replies[0]["recording_revision"] == revision
    assert _hashes(root) == before


def test_fresh_cli_wrong_trusted_anchor_is_not_a_wire_override(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = _prepared(tmp_path)
    model = _offline_model()
    with NativeSimulationSession(root, graph_id="graph-fixture", simulation_id="sim-fixture",
            models={"twitter": model, "reddit": model}, seed=7, max_rounds=1):
        pass
    anchors = RecordingAnchors("graph-fixture", "sim-fixture", "run", "branch", "project", 1)
    bundle = tmp_path / "recording"
    revision = capture_recording(root, bundle, anchors=anchors, platforms=("twitter", "reddit"),
        closed_run_confirmed=True, runtime_versions={"oasis": importlib.metadata.version("camel-oasis"),
            "camel": importlib.metadata.version("camel-ai")}, runtime_sha256="b" * 64)
    wrong = RecordingAnchors("graph-fixture", "sim-fixture", "other-run", "branch", "project", 1)
    status, replies = _cli(bundle, revision, wrong, [], tmp_path)
    assert status == 2 and len(replies) == 1
    assert replies[0]["ok"] is False and replies[0]["error"] == "invalid_recording"
    assert replies[0]["recording_admitted"] is False and replies[0]["anchors"] == wrong.wire()
    status, replies = _cli(bundle, revision, anchors, [{"version": 1, "operation": "playback",
        "platform": "twitter", "limit": 1, "run_id": "other-run"}], tmp_path)
    assert status == 0 and replies[0]["ok"] is False and replies[0]["error"] == "invalid_request"

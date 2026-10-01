"""Synthetic artifact contract tests; actual engine qualification is separate."""
import base64
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest

# Load ONLY the fixed new sibling files, avoiding backend initialization.
SERVICE = Path(__file__).resolve().parents[1] / "app" / "services"
for name in ("native_recording_contracts", "native_recordings", "native_recording_cli"):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, SERVICE / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)

from native_recording_contracts import (RecordingAnchors, RecordingError, canonical,
                                       request_from_wire, strict_json)
from native_recordings import NativeRecording, capture_recording, digest
import native_recordings as recordings

ANCHORS = RecordingAnchors("g", "s", "run", "branch", "project", 1)
VERSIONS = {"oasis": "1.0.0", "camel": "0.2.0"}


def fixture_source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    state = {"graph_id": "g", "simulation_id": "s", "status": "ready",
             "profiles_generated": True, "config_generated": True,
             "enable_twitter": True, "enable_reddit": True}
    config = {"graph_id": "g", "simulation_id": "s", "agent_configs": [{"agent_id": 0}]}
    for name, value in (("state.json", state), ("simulation_config.json", config),
                        ("source_grounding.json", {}), ("reddit_profiles.json", [{"user_id": 0}])):
        (root / name).write_bytes(canonical(value))
    (root / "twitter_profiles.csv").write_text("user_id,name\n0,Synthetic\n", encoding="utf-8")
    (root / ".native_prepared_start_claim").touch()
    original = []
    for p in ("twitter", "reddit"):
        (root / p).mkdir()
        # Equal timestamps, duplicate initial actions, unknown inert arguments.
        action = {"timestamp": "2026-01-01T00:00:00", "round": 0, "agent_id": 0,
                  "action_type": "CREATE_POST", "success": True,
                  "action_args": {"content": "Synthetic", "module": "never.import.me"}}
        events = [{"timestamp": "2026-01-01T00:00:00", "event_type": "simulation_start", "platform": p},
                  {"timestamp": "2026-01-01T00:00:00", "event_type": "round_start", "round": 0},
                  action, action,
                  {"timestamp": "2026-01-01T00:00:00", "event_type": "round_end", "round": 0},
                  {"timestamp": "2026-01-01T00:00:00", "event_type": "simulation_end", "platform": p}]
        (root / p / "actions.jsonl").write_bytes(b"".join(canonical(e) + b"\n" for e in events))
        with sqlite3.connect(root / f"{p}_simulation.db") as conn:
            conn.executescript('CREATE TABLE post(post_id INTEGER, content TEXT);'
                               'CREATE TABLE follow(follower_id INTEGER, followee_id INTEGER);'
                               'CREATE TABLE trace(user_id INTEGER, action TEXT, info TEXT);')
            conn.execute("INSERT INTO post VALUES(1, 'Synthetic')")
            conn.execute("INSERT INTO follow VALUES(0, 1)")
            conn.execute("INSERT INTO trace VALUES(0, 'create_post', '{}')")
        original.append(events)
    return root, original


def capture(root, target, **kwargs):
    return capture_recording(root, target, anchors=ANCHORS, platforms=("twitter", "reddit"),
                             closed_run_confirmed=True, runtime_versions=VERSIONS,
                             runtime_sha256="1" * 64, **kwargs)


def request(operation="playback", platform="twitter", limit=2, cursor=None):
    return canonical(dict(version=1, operation=operation, platform=platform, limit=limit, cursor=cursor))


def hashes(root):
    return {p.relative_to(root).as_posix(): digest(p.read_bytes())
            for p in root.rglob("*") if p.is_file()}


def test_describe_is_detached_pinned_bounded_metadata_only(tmp_path):
    root, _ = fixture_source(tmp_path)
    target = tmp_path / "bundle"
    revision = capture(root, target)
    host = NativeRecording(target, anchors=ANCHORS, expected_revision=revision)
    expected = host.describe()
    assert expected == {"version": 1, "recording_revision": revision,
        "anchors": ANCHORS.wire(), "platforms": ["twitter", "reddit"],
        "runtime_sha256": "1" * 64,
        "runtime_versions": strict_json((target / "recording.json").read_bytes())["runtime_versions"],
        "artifact_sha256": {name: digest((root / name).read_bytes()) for name in (
            "simulation_config.json", "source_grounding.json", "twitter_profiles.csv", "reddit_profiles.json")}}
    assert len(canonical(expected)) < 4096
    edited = host.describe()
    edited["anchors"]["run_id"] = "other"
    edited["runtime_versions"]["camel"] = "other"
    edited["artifact_sha256"].clear()
    edited["platforms"].clear()
    (target / "simulation_config.json").write_bytes(b"tamper")
    (target / "recording.json").write_bytes(b"tamper")
    assert host.describe() == expected
    with pytest.raises(RecordingError):
        NativeRecording(target, anchors=ANCHORS, expected_revision=revision)


@pytest.mark.parametrize("raw", [
    b'{"version":1,"version":1}', b'NaN', b'Infinity', b'{}', b'[]',
    b'{"version":true,"operation":"playback","platform":"twitter","limit":1}',
    b'{"version":1,"operation":"playback","platform":"twitter","limit":true}',
    b'{"version":1,"operation":"playback","platform":"twitter","limit":101}',
    b'{"version":1,"operation":"playback","platform":"twitter","limit":1,"path":"private"}',
    b'{"version":1,"operation":"resume","platform":"twitter","limit":1}',
    b'{"version":1,"operation":"metrics","platform":"twitter","limit":1,"cursor":"x"}',
    b'{"version":1,"operation":"playback","platform":"twitter","limit":1,"cursor":false}',
    b'{"version":1,"operation":"playback","platform":"twitter","limit":1,"nested":{"x":NaN}}',
    b'\xff', b'x' * 8193,
])
def test_strict_wire_rejects_malformed_or_selector_input(raw):
    with pytest.raises(RecordingError, match="invalid_request"):
        request_from_wire(raw)


def test_faithful_pages_duplicate_counts_and_parent_preservation(tmp_path):
    root, expected = fixture_source(tmp_path)
    before = hashes(root)
    target = tmp_path / "bundle"
    revision = capture(root, target)
    host = NativeRecording(target, anchors=ANCHORS, expected_revision=revision)
    for index, p in enumerate(("twitter", "reddit")):
        output, offsets, cursor = [], [], None
        while True:
            page = host.read(request(platform=p, cursor=cursor))
            output += [e["record"] for e in page["events"]]
            offsets += [e["source_event_offset"] for e in page["events"]]
            assert all(e["platform"] == p for e in page["events"])
            assert page["anchors"] == ANCHORS.wire()
            assert page["continuation_supported"] is False
            assert page["branch_execution_supported"] is False
            cursor = page["next_cursor"]
            if cursor is None:
                assert page["exhausted"] is True
                break
        assert output == expected[index]
        assert offsets == list(range(len(expected[index])))
        metric = host.read(request("metrics", p))
        assert metric["logged_action_counts"] == {"CREATE_POST": 2}
        assert metric["logged_round_counts"] == {"0": 2}
        assert metric["final_native_tables"]["post"]["count"] == 1
        assert metric["final_native_tables"]["follow"]["rows"] == [{"follower_id": 0, "followee_id": 1}]
        assert metric["final_native_tables"]["like"] == {"present": False}
        assert metric["coverage"]["exact_event_to_native_row_links"] is False
    assert hashes(root) == before


def test_detached_results_and_pinned_bytes(tmp_path):
    root, expected = fixture_source(tmp_path)
    bundle = tmp_path / "bundle"
    revision = capture(root, bundle)
    host = NativeRecording(bundle, anchors=ANCHORS, expected_revision=revision)
    response = host.read(request(limit=6))
    response["events"][2]["record"]["action_args"]["content"] = "Changed"
    (bundle / "twitter" / "actions.jsonl").write_bytes(b"tampered")
    assert [e["record"] for e in host.read(request(limit=6))["events"]] == expected[0]
    with pytest.raises(RecordingError):
        NativeRecording(bundle, anchors=ANCHORS, expected_revision=revision)


def test_cursor_binds_revision_platform_offset_and_has_truthful_exhaustion(tmp_path):
    root, _ = fixture_source(tmp_path)
    bundle = tmp_path / "bundle"
    revision = capture(root, bundle)
    host = NativeRecording(bundle, anchors=ANCHORS, expected_revision=revision)
    cursor = host.read(request())["next_cursor"]
    with pytest.raises(RecordingError, match="invalid_cursor"):
        host.read(request(platform="reddit", cursor=cursor))
    decoded = strict_json(base64.urlsafe_b64decode(cursor))
    for field, value in (("offset", True), ("offset", 99), ("revision", "2" * 64), ("platform", "reddit")):
        changed = dict(decoded, **{field: value})
        bad = base64.urlsafe_b64encode(canonical(changed)).decode()
        with pytest.raises(RecordingError, match="invalid_cursor"):
            host.read(request(cursor=bad))
    done = host.read(request(cursor=host._cursor("twitter", 6)))
    assert done["events"] == [] and done["exhausted"] and done["next_cursor"] is None


@pytest.mark.parametrize("name", ["twitter/actions.jsonl", "twitter_simulation.db", "state.json", "recording.json"])
def test_tamper_rejected_before_any_database_open(tmp_path, monkeypatch, name):
    root, _ = fixture_source(tmp_path)
    bundle = tmp_path / "bundle"
    revision = capture(root, bundle)
    with (bundle / name).open("ab") as stream:
        stream.write(b" ")
    def forbidden(_data):
        raise AssertionError("SQLite opened before artifact validation")
    monkeypatch.setattr(recordings, "_database", forbidden)
    with pytest.raises(RecordingError):
        NativeRecording(bundle, anchors=ANCHORS, expected_revision=revision)


def test_wrong_scope_extra_path_and_partial_destination(tmp_path, monkeypatch):
    root, _ = fixture_source(tmp_path)
    bundle = tmp_path / "bundle"
    revision = capture(root, bundle)
    wrong = RecordingAnchors("g", "s", "other-run", "branch", "project", 1)
    with pytest.raises(RecordingError):
        NativeRecording(bundle, anchors=wrong, expected_revision=revision)
    (bundle / "plugin.py").write_text("raise AssertionError('must never execute')", encoding="utf-8")
    with pytest.raises(RecordingError):
        NativeRecording(bundle, anchors=ANCHORS, expected_revision=revision)
    with pytest.raises(RecordingError, match="destination_unavailable"):
        capture(root, bundle)
    partial = tmp_path / "partial"
    partial.mkdir()
    with pytest.raises(RecordingError):
        NativeRecording(partial, anchors=ANCHORS, expected_revision=revision)


@pytest.mark.parametrize("marker", [".native_active", ".native_incomplete", "twitter_simulation.db-wal",
                                    "reddit_simulation.db-shm", "twitter_simulation.db-journal"])
def test_active_sidecar_boundaries_are_rejected(tmp_path, marker):
    root, _ = fixture_source(tmp_path)
    (root / marker).touch()
    with pytest.raises(RecordingError):
        capture(root, tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()


def test_host_close_confirmation_is_required(tmp_path):
    root, _ = fixture_source(tmp_path)
    with pytest.raises(RecordingError, match="invalid_request"):
        capture_recording(root, tmp_path / "bundle", anchors=ANCHORS,
            platforms=("twitter", "reddit"), closed_run_confirmed=False,
            runtime_versions=VERSIONS, runtime_sha256="1" * 64)


def test_changing_source_fails_without_a_valid_manifest(tmp_path, monkeypatch):
    root, _ = fixture_source(tmp_path)
    original = recordings._read
    calls = [0]
    def change(source, name, limit=recordings.FILE_LIMIT):
        data = original(source, name, limit)
        if source == root and name == "twitter/actions.jsonl":
            calls[0] += 1
            if calls[0] == 1:
                (root / name).write_bytes(data.replace(b"Synthetic", b"Different"))
        return data
    monkeypatch.setattr(recordings, "_read", change)
    with pytest.raises(RecordingError):
        capture(root, tmp_path / "bundle")
    assert not (tmp_path / "bundle" / "recording.json").exists()


def test_symlink_source_and_bundle_rejected(tmp_path):
    root, _ = fixture_source(tmp_path)
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("OS symlink privilege unavailable; Linux qualification required")
    with pytest.raises(RecordingError):
        capture(alias, tmp_path / "bundle")
    bundle = tmp_path / "bundle"
    revision = capture(root, bundle)
    artifact = bundle / "state.json"
    artifact.unlink()
    artifact.symlink_to(root / "state.json")
    with pytest.raises(RecordingError):
        NativeRecording(bundle, anchors=ANCHORS, expected_revision=revision)


def test_incomplete_json_duplicate_keys_and_credentials_rejected(tmp_path):
    root, _ = fixture_source(tmp_path)
    path = root / "twitter" / "actions.jsonl"
    original = path.read_bytes()
    for data in (original[:-1], original.replace(b'"round":0', b'"round":0,"round":0'),
                 original.replace(b'"module":', b'"api_key":')):
        path.write_bytes(data)
        with pytest.raises(RecordingError):
            capture(root, tmp_path / "bundle")
        assert not (tmp_path / "bundle").exists()
    path.write_bytes(original)
    (root / "unknown.py").write_text("raise AssertionError", encoding="utf-8")
    with pytest.raises(RecordingError):
        capture(root, tmp_path / "bundle")


def test_metrics_native_preview_is_bounded_and_reports_truncation(tmp_path):
    root, _ = fixture_source(tmp_path)
    with sqlite3.connect(root / "twitter_simulation.db") as connection:
        connection.execute("INSERT INTO post VALUES(2, 'Second')")
    bundle = tmp_path / "bundle"
    host = NativeRecording(bundle, anchors=ANCHORS, expected_revision=capture(root, bundle))
    table = host.read(request("metrics", limit=1))["final_native_tables"]["post"]
    assert table["count"] == 2 and len(table["rows"]) == 1 and table["truncated"] is True


def test_sqlite_executable_schema_rejected(tmp_path):
    root, _ = fixture_source(tmp_path)
    with sqlite3.connect(root / "twitter_simulation.db") as connection:
        connection.execute("CREATE VIEW payload AS SELECT load_extension('never')")
    with pytest.raises(RecordingError):
        capture(root, tmp_path / "bundle")


def test_successful_follow_fields_are_retained_in_file_order(tmp_path):
    root, _ = fixture_source(tmp_path)
    path = root / "twitter" / "actions.jsonl"
    events = [json.loads(line) for line in path.read_bytes().splitlines()]
    follow = {"timestamp": "2026-01-01T00:00:00", "round": 0, "agent_id": 0,
              "action_type": "FOLLOW", "action_args": {"follow_id": 1,
              "target_user_name": "Synthetic target", "future_field": ["inert", {"x": True}]},
              "result": None, "success": True}
    events.insert(4, follow)
    path.write_bytes(b"".join(canonical(e) + b"\n" for e in events))
    bundle = tmp_path / "bundle"
    revision = capture(root, bundle)
    host = NativeRecording(bundle, anchors=ANCHORS, expected_revision=revision)
    page = host.read(request(limit=100))
    assert page["events"][4] == {"platform": "twitter", "source_event_offset": 4, "record": follow}
    assert host.read(request("metrics"))["logged_action_counts"] == {"CREATE_POST": 2, "FOLLOW": 1}


@pytest.mark.parametrize("changes", [
    {"schema_version": True}, {"closed_run_confirmed": 1},
    {"platforms": ["reddit", "twitter"]}, {"runtime_versions": {"oasis": "private"}},
    {"anchors": dict(ANCHORS.wire(), project_revision=True)},
])
def test_malformed_pinned_manifest_rejected_before_sqlite(tmp_path, monkeypatch, changes):
    root, _ = fixture_source(tmp_path)
    bundle = tmp_path / "bundle"
    capture(root, bundle)
    manifest = json.loads((bundle / "recording.json").read_bytes())
    manifest.update(changes)
    raw = canonical(manifest)
    (bundle / "recording.json").write_bytes(raw)
    def forbidden(_data):
        raise AssertionError("malformed manifest opened SQLite")
    monkeypatch.setattr(recordings, "_database", forbidden)
    with pytest.raises(RecordingError):
        NativeRecording(bundle, anchors=ANCHORS, expected_revision=digest(raw))


def test_file_and_event_bounds_reject_before_destination_creation(tmp_path, monkeypatch):
    root, _ = fixture_source(tmp_path)
    monkeypatch.setattr(recordings, "TOTAL_LIMIT", 1)
    with pytest.raises(RecordingError, match="limit_exceeded"):
        capture(root, tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()


def test_cli_frame_bounds_and_fixed_error_provenance(tmp_path):
    import io
    from native_recording_cli import serve
    root, _ = fixture_source(tmp_path)
    bundle = tmp_path / "bundle"
    revision = capture(root, bundle)
    host = NativeRecording(bundle, anchors=ANCHORS, expected_revision=revision)
    output = io.BytesIO()
    assert serve(host, io.BytesIO(b'{"path":"PRIVATE_PATH"}\n'), output) == 0
    error = json.loads(output.getvalue())
    assert error["error"] == "invalid_request"
    assert error["anchors"] == ANCHORS.wire() and error["recording_revision"] == revision
    assert b"PRIVATE_PATH" not in output.getvalue()
    output = io.BytesIO()
    assert serve(host, io.BytesIO(b"x" * 8193 + b"\n"), output) == 2
    assert json.loads(output.getvalue())["error"] == "invalid_request"


@pytest.mark.parametrize("sql", [
    "CREATE VIRTUAL\nTABLE payload USING fts5(content)",
    "CREATE VIRTUAL/* native recording rejection regression */TABLE payload USING fts5(content)",
    "CREATE /* gap */ VIRTUAL\tTABLE payload USING fts5(content)",
])
def test_virtual_table_metadata_rejects_whitespace_and_comment_spellings(tmp_path, sql):
    root, _ = fixture_source(tmp_path)
    with sqlite3.connect(root / "twitter_simulation.db") as connection:
        connection.execute(sql)
        # SQLite may normalize the leading CREATE spelling on ordinary creation.
        # Store the equivalent valid spelling to exercise the actual bypass
        # rather than accidentally relying on that normalization in the test.
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute("UPDATE sqlite_schema SET sql=? WHERE name='payload'", (sql,))
        connection.execute("PRAGMA writable_schema=OFF")
    with sqlite3.connect(root / "twitter_simulation.db") as connection:
        stored = connection.execute("SELECT sql FROM sqlite_schema WHERE name='payload'").fetchone()[0]
        assert "CREATE VIRTUAL TABLE" not in stored.upper()
        classifications = {row[1]: row[2] for row in connection.execute("PRAGMA table_list")}
        assert classifications["payload"] == "virtual"
        assert "shadow" in classifications.values()
    with pytest.raises(RecordingError):
        capture(root, tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()


def test_explicit_null_event_type_rejected_consistently_before_database_admission(tmp_path, monkeypatch):
    root, _ = fixture_source(tmp_path)
    bundle = tmp_path / "bundle"
    capture(root, bundle)
    path = root / "twitter" / "actions.jsonl"
    events = [json.loads(line) for line in path.read_bytes().splitlines()]
    events[2]["event_type"] = None
    raw_events = b"".join(canonical(event) + b"\n" for event in events)
    path.write_bytes(raw_events)
    (bundle / "twitter" / "actions.jsonl").write_bytes(raw_events)
    # Re-pin a internally consistent modified bundle to prove JSON semantics,
    # rather than the outer hash, causes admission to reject the null action.
    manifest = json.loads((bundle / "recording.json").read_bytes())
    for entry in manifest["files"]:
        if entry["name"] == "twitter/actions.jsonl":
            entry.update(size=len(raw_events), sha256=digest(raw_events))
    raw_manifest = canonical(manifest)
    (bundle / "recording.json").write_bytes(raw_manifest)
    def forbidden(_data):
        raise AssertionError("null event_type reached SQLite")
    monkeypatch.setattr(recordings, "_database", forbidden)
    with pytest.raises(RecordingError):
        capture(root, tmp_path / "other-bundle")
    with pytest.raises(RecordingError):
        NativeRecording(bundle, anchors=ANCHORS, expected_revision=digest(raw_manifest))
    assert not (tmp_path / "other-bundle").exists()


def _observe_scandir(monkeypatch):
    original = recordings.os.scandir
    observation = {"entries": 0, "open": 0, "peak_open": 0}
    class ObservedScan:
        def __init__(self, path):
            self.scan = original(path)
        def __enter__(self):
            observation["open"] += 1
            observation["peak_open"] = max(observation["peak_open"], observation["open"])
            return self
        def __exit__(self, *_args):
            self.scan.close()
            observation["open"] -= 1
        def __iter__(self):
            return self
        def __next__(self):
            entry = next(self.scan)
            observation["entries"] += 1
            return entry
    monkeypatch.setattr(recordings.os, "scandir", ObservedScan)
    def forbidden_walk(*_args, **_kwargs):
        raise AssertionError("eager os.walk directory discovery attempted")
    monkeypatch.setattr(recordings.os, "walk", forbidden_walk)
    return observation


def test_source_scan_global_limit_stops_incrementally_and_closes_iterators(tmp_path, monkeypatch):
    root, _ = fixture_source(tmp_path)
    # Real extra files, exceeding the global bound in a single directory.
    for index in range(recordings.SOURCE_ENTRY_LIMIT + 10):
        (root / f"extra-{index}.txt").touch()
    observed = _observe_scandir(monkeypatch)
    with pytest.raises(RecordingError, match="limit_exceeded"):
        capture(root, tmp_path / "bundle")
    assert observed["entries"] == recordings.SOURCE_ENTRY_LIMIT + 1
    assert observed["open"] == 0 and observed["peak_open"] <= 4
    assert not (tmp_path / "bundle").exists()


def test_bundle_extra_entries_rejected_incrementally_before_sqlite(tmp_path, monkeypatch):
    root, _ = fixture_source(tmp_path)
    bundle = tmp_path / "bundle"
    revision = capture(root, bundle)
    for index in range(30):
        (bundle / f"extra-{index}.txt").touch()
    observed = _observe_scandir(monkeypatch)
    def forbidden(_data):
        raise AssertionError("extra bundle path reached SQLite")
    monkeypatch.setattr(recordings, "_database", forbidden)
    with pytest.raises(RecordingError):
        NativeRecording(bundle, anchors=ANCHORS, expected_revision=revision)
    maximum = len(recordings._names(("twitter", "reddit"))) + 1 + 2
    assert observed["entries"] <= maximum + 1
    assert observed["open"] == 0 and observed["peak_open"] == 1


def test_bundle_unknown_subdirectory_is_not_traversed(tmp_path, monkeypatch):
    root, _ = fixture_source(tmp_path)
    bundle = tmp_path / "bundle"
    revision = capture(root, bundle)
    unknown = bundle / "twitter" / "unknown"
    unknown.mkdir()
    (unknown / "inert.txt").touch()
    original = recordings.os.scandir
    def guarded(path):
        assert Path(path) != unknown, "unknown bundle directory was traversed"
        return original(path)
    monkeypatch.setattr(recordings.os, "scandir", guarded)
    with pytest.raises(RecordingError):
        NativeRecording(bundle, anchors=ANCHORS, expected_revision=revision)


def _stat_with_ctime(info, ctime):
    from types import SimpleNamespace
    return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino,
                           st_size=info.st_size, st_mtime_ns=info.st_mtime_ns,
                           st_ctime_ns=ctime, st_mode=info.st_mode)


@pytest.mark.parametrize("name", ["state.json", "twitter_simulation.db"])
def test_windows_unchanged_closed_file_accepts_cross_api_ctime_difference(tmp_path, monkeypatch, name):
    root, _ = fixture_source(tmp_path)
    path = root / name
    expected = path.read_bytes()
    path_ctime = path.lstat().st_ctime_ns
    original_fstat = recordings.os.fstat
    def windows_fstat(descriptor):
        return _stat_with_ctime(original_fstat(descriptor), path_ctime + 1000000)
    monkeypatch.setattr(recordings, "_WINDOWS_STAT", True)
    monkeypatch.setattr(recordings.os, "fstat", windows_fstat)
    assert recordings._read(root, name) == expected
    assert path.read_bytes() == expected


def test_posix_cross_api_ctime_still_compared_exactly(tmp_path, monkeypatch):
    root, _ = fixture_source(tmp_path)
    path_ctime = (root / "state.json").lstat().st_ctime_ns
    original_fstat = recordings.os.fstat
    def changed_fstat(descriptor):
        return _stat_with_ctime(original_fstat(descriptor), path_ctime + 1000000)
    monkeypatch.setattr(recordings, "_WINDOWS_STAT", False)
    monkeypatch.setattr(recordings.os, "fstat", changed_fstat)
    with pytest.raises(RecordingError):
        recordings._read(root, "state.json")


def test_windows_same_descriptor_ctime_change_still_rejected(tmp_path, monkeypatch):
    root, _ = fixture_source(tmp_path)
    path_ctime = (root / "state.json").lstat().st_ctime_ns
    original_fstat = recordings.os.fstat
    calls = [0]
    def changed_fstat(descriptor):
        calls[0] += 1
        return _stat_with_ctime(original_fstat(descriptor), path_ctime + calls[0] * 1000000)
    monkeypatch.setattr(recordings, "_WINDOWS_STAT", True)
    monkeypatch.setattr(recordings.os, "fstat", changed_fstat)
    with pytest.raises(RecordingError):
        recordings._read(root, "state.json")
    assert calls[0] == 2


def _after_descriptor_read(monkeypatch, action):
    original_fdopen = recordings.os.fdopen
    class MutatingRead:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            self.stream.__enter__()
            return self
        def __exit__(self, *args):
            return self.stream.__exit__(*args)
        def read(self, limit):
            data = self.stream.read(limit)
            action()
            return data
    def fdopen(*args, **kwargs):
        return MutatingRead(original_fdopen(*args, **kwargs))
    monkeypatch.setattr(recordings.os, "fdopen", fdopen)


def test_windows_same_path_ctime_change_still_rejected(tmp_path, monkeypatch):
    root, _ = fixture_source(tmp_path)
    path = root / "state.json"
    original_lstat = Path.lstat
    before_ctime = original_lstat(path).st_ctime_ns
    def change_path_observation():
        def changed_lstat(selected, *args, **kwargs):
            info = original_lstat(selected, *args, **kwargs)
            if selected == path:
                return _stat_with_ctime(info, before_ctime + 1000000)
            return info
        monkeypatch.setattr(Path, "lstat", changed_lstat)
    monkeypatch.setattr(recordings, "_WINDOWS_STAT", True)
    _after_descriptor_read(monkeypatch, change_path_observation)
    with pytest.raises(RecordingError):
        recordings._read(root, "state.json")


@pytest.mark.parametrize("change", ["mutation", "replacement"])
def test_windows_actual_same_size_replacement_at_open_or_mutation_during_read_rejected(tmp_path, monkeypatch, change):
    root, _ = fixture_source(tmp_path)
    path = root / "state.json"
    original_bytes = path.read_bytes()
    original_mtime = path.stat().st_mtime_ns
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(original_bytes)
    # Preserve size and mtime on the replacement: dev/inode must still detect it.
    recordings.os.utime(replacement, ns=(replacement.stat().st_atime_ns, original_mtime))
    monkeypatch.setattr(recordings, "_WINDOWS_STAT", True)
    if change == "replacement":
        # Replace after the initial path observation, before opening the file.
        # This real race does not depend on Windows open-handle delete sharing.
        original_open = recordings.os.open
        def replaced_open(selected_path, *args, **kwargs):
            if Path(selected_path) == path:
                recordings.os.replace(replacement, path)
            return original_open(selected_path, *args, **kwargs)
        monkeypatch.setattr(recordings.os, "open", replaced_open)
    else:
        def mutate():
            # Real same-length in-place mutation, with explicit changed mtime.
            path.write_bytes(original_bytes.replace(b'"g"', b'"h"'))
            recordings.os.utime(path, ns=(path.stat().st_atime_ns, original_mtime + 1000000000))
        _after_descriptor_read(monkeypatch, mutate)
    with pytest.raises(RecordingError):
        recordings._read(root, "state.json")

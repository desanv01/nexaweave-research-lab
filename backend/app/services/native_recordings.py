"""Closed-run capture and immutable analytical playback. No engine imports.

Run ownership and successful close are trusted host facts, never wire inputs.
The enduring native start claim is not a live-process indicator.
"""
from __future__ import annotations

import base64
from collections import Counter
import csv
import hashlib
import io
import os
from pathlib import Path
import platform as python_platform
import sqlite3
import stat

try:  # Direct sibling import supports the CLI without importing backend app.
    from .native_recording_contracts import (RecordingAnchors, RecordingError,
        HEX, IDENTIFIER, MAX_JSON, MAX_EVENTS, MAX_EVENT_BYTES,
        bounded_tree, canonical, request_from_wire, strict_json)
except ImportError:
    from native_recording_contracts import (RecordingAnchors, RecordingError,
        HEX, IDENTIFIER, MAX_JSON, MAX_EVENTS, MAX_EVENT_BYTES,
        bounded_tree, canonical, request_from_wire, strict_json)

FILE_LIMIT = 64 * 1024 * 1024
TOTAL_LIMIT = 160 * 1024 * 1024
TABLES = ("post", "follow", "like", "dislike", "comment", "comment_like",
          "comment_dislike", "mute", "trace")
ROW_LIMIT = 100000
RESPONSE_LIMIT = 2 * 1024 * 1024
SOURCE_ENTRY_LIMIT = 2000
SOURCE_DEPTH_LIMIT = 3
_WINDOWS_STAT = os.name == "nt"
SECRET_KEYS = {"api_key", "apikey", "access_token", "refresh_token", "password",
               "secret", "private_key", "credentials", "authorization"}


def _no_credentials(value):
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise RecordingError()
            normalized = key.lower().replace("-", "_")
            if (normalized in SECRET_KEYS or normalized.endswith("_api_key")
                    or normalized.endswith("_password") or normalized.endswith("_secret")):
                raise RecordingError()
            _no_credentials(item)
    elif type(value) is list:
        for item in value:
            _no_credentials(item)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _safe(path):
    for part in (path, *path.parents):
        info = part.lstat()
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise RecordingError()
    return path


def _root(value):
    path = Path(value)
    if not path.is_absolute() or path != path.resolve(strict=True):
        raise RecordingError()
    _safe(path)
    if not path.is_dir():
        raise RecordingError()
    return path


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _cross_identity(info):
    # Windows path stat and descriptor stat expose different ctime semantics.
    # Compare the exact common fields across APIs; retain full same-API checks.
    identity = _identity(info)
    return identity[:4] if _WINDOWS_STAT else identity


def _read(root, name, limit=FILE_LIMIT):
    # name comes solely from a fixed allowlist, never a manifest or request.
    path = _safe(root / name)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
        raise RecordingError("limit_exceeded")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0)
                         | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if _cross_identity(before) != _cross_identity(opened):
            raise RecordingError()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        descriptor_after = os.fstat(descriptor)
        path_after = path.lstat()
        if (_identity(opened) != _identity(descriptor_after)
                or _identity(before) != _identity(path_after)
                or _cross_identity(descriptor_after) != _cross_identity(path_after)
                or len(data) != before.st_size):
            raise RecordingError()
        _safe(path)
        return data
    finally:
        os.close(descriptor)


def _names(platforms):
    names = ["state.json", "simulation_config.json", "source_grounding.json"]
    if "twitter" in platforms:
        names.append("twitter_profiles.csv")
    if "reddit" in platforms:
        names.append("reddit_profiles.json")
    for platform in platforms:
        names.extend((f"{platform}/actions.jsonl", f"{platform}_simulation.db"))
    return tuple(sorted(names))


def _platforms(value):
    if (type(value) not in (tuple, list) or not value
            or tuple(value) not in (("twitter",), ("reddit",), ("twitter", "reddit"))):
        raise RecordingError()
    return tuple(value)


def _events(data, platform):
    if not data.endswith(b"\n"):
        raise RecordingError()
    lines = data.splitlines()
    if not 2 <= len(lines) <= MAX_EVENTS:
        raise RecordingError("limit_exceeded")
    events = []
    for line in lines:
        event = strict_json(line, MAX_EVENT_BYTES)
        _no_credentials(event)
        if (type(event) is not dict or type(event.get("timestamp")) is not str
                or not 1 <= len(event["timestamp"]) <= 128
                or ("platform" in event and event["platform"] != platform)):
            raise RecordingError()
        kind = event.get("event_type")
        # Native action records omit event_type. Explicit null is malformed,
        # rather than admitting an action that metrics could silently omit.
        if "event_type" in event and kind is None:
            raise RecordingError()
        if kind is not None:
            if kind not in ("simulation_start", "simulation_end", "round_start", "round_end"):
                raise RecordingError()
        else:
            if (type(event.get("action_type")) is not str
                    or not 1 <= len(event["action_type"]) <= 128
                    or type(event.get("agent_id")) is not int
                    or not 0 <= event["agent_id"] < 500
                    or type(event.get("action_args")) is not dict
                    or type(event.get("success")) is not bool):
                raise RecordingError()
        if kind in (None, "round_start", "round_end"):
            if type(event.get("round")) is not int or not 0 <= event["round"] <= 1000:
                raise RecordingError()
        events.append(event)
    if (events[0].get("event_type") != "simulation_start"
            or events[-1].get("event_type") != "simulation_end"
            or sum(e.get("event_type") == "simulation_start" for e in events) != 1
            or sum(e.get("event_type") == "simulation_end" for e in events) != 1):
        raise RecordingError()
    return events


def _prepared(artifacts, anchors, platforms):
    state = strict_json(artifacts["state.json"])
    config = strict_json(artifacts["simulation_config.json"])
    grounding = strict_json(artifacts["source_grounding.json"])
    if (type(state) is not dict or type(config) is not dict or type(grounding) is not dict
            or any(doc.get("graph_id") != anchors.graph_id
                   or doc.get("simulation_id") != anchors.simulation_id for doc in (state, config))
            or state.get("status") != "ready"
            or state.get("profiles_generated") is not True
            or state.get("config_generated") is not True
            or any(type(state.get(f"enable_{p}")) is not bool for p in ("twitter", "reddit"))
            or tuple(p for p in ("twitter", "reddit") if state[f"enable_{p}"]) != platforms
            or type(config.get("agent_configs")) is not list
            or not 1 <= len(config["agent_configs"]) <= 500):
        raise RecordingError()
    for name, data in artifacts.items():
        if name.endswith(".json"):
            _no_credentials(strict_json(data))
        elif name.endswith(".csv"):
            rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
            _no_credentials(rows)
            if len(rows) != len(config["agent_configs"]):
                raise RecordingError()


def _database(data):
    if not data.startswith(b"SQLite format 3\x00") or not hasattr(sqlite3.Connection, "deserialize"):
        raise RecordingError()
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(data)
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA query_only=ON")
        # VM work is bounded even for malicious/tampered native schemas.
        ticks = [0]
        def progress():
            ticks[0] += 1
            return int(ticks[0] > 10000)
        connection.set_progress_handler(progress, 1000)
        return connection
    except BaseException:
        connection.close()
        raise


def _schema(data):
    connection = _database(data)
    try:
        rows = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name").fetchmany(101)
        if len(rows) > 100 or any(row[0] not in ("table", "index") for row in rows):
            raise RecordingError()
        # SQLite's parsed table classification cannot be evaded with SQL
        # whitespace/comments. Fail closed if this runtime lacks table_list.
        table_cursor = connection.execute("PRAGMA table_list")
        if (table_cursor.description is None
                or [item[0] for item in table_cursor.description][:3]
                != ["schema", "name", "type"]):
            raise RecordingError()
        parsed_tables = table_cursor.fetchmany(103)
        if (len(parsed_tables) > 102
                or any(row[2] != "table" for row in parsed_tables)):
            raise RecordingError()
        tables = [row[1] for row in rows if row[0] == "table"]
        if not {"post", "follow", "trace"} <= set(tables):
            raise RecordingError()
        return {"user_version": connection.execute("PRAGMA user_version").fetchone()[0],
                "schema_sha256": digest(canonical(rows)), "tables": tables}
    finally:
        connection.close()


def _source_entries(root):
    """Stream entries with a global bound and at most four open iterators."""
    count = [0]

    def visit(parent, depth):
        _safe(parent)
        with os.scandir(parent) as entries:
            for entry in entries:
                count[0] += 1
                if count[0] > SOURCE_ENTRY_LIMIT:
                    raise RecordingError("limit_exceeded")
                child = _safe(parent / entry.name)
                details = child.lstat()
                if not (stat.S_ISREG(details.st_mode) or stat.S_ISDIR(details.st_mode)):
                    raise RecordingError()
                yield child
                if stat.S_ISDIR(details.st_mode):
                    if depth >= SOURCE_DEPTH_LIMIT:
                        raise RecordingError()
                    yield from visit(child, depth + 1)

    yield from visit(root, 0)


def _bundle_paths(root, names, platforms):
    """Validate only fixed allowed directories, rejecting extras immediately.

    scandir never materializes a directory listing. The found set and pending
    directory list are bounded by the fixed artifact allowlist, not the input.
    """
    allowed_files = set(names) | {"recording.json"}
    allowed_dirs = set(platforms)
    found_files, found_dirs = set(), set()
    count = 0
    maximum = len(allowed_files) + len(allowed_dirs)
    # No discovered directory becomes a traversal selector.
    for parent in (root, *(root / platform for platform in platforms)):
        _safe(parent)
        with os.scandir(parent) as entries:
            for entry in entries:
                count += 1
                if count > maximum:
                    raise RecordingError("limit_exceeded")
                child = _safe(parent / entry.name)
                name = child.relative_to(root).as_posix()
                mode = child.lstat().st_mode
                if stat.S_ISDIR(mode):
                    if parent != root or name not in allowed_dirs or name in found_dirs:
                        raise RecordingError()
                    found_dirs.add(name)
                elif stat.S_ISREG(mode):
                    if name not in allowed_files or name in found_files:
                        raise RecordingError()
                    found_files.add(name)
                else:
                    raise RecordingError()
    if found_files != allowed_files or found_dirs != allowed_dirs:
        raise RecordingError()


def _source_boundary(root, platforms):
    # A retained start claim is expected after close, not evidence of closure.
    _read(root, "state.json", MAX_JSON)
    for child in _source_entries(root):
        if child.suffix.lower() in {".py", ".pyc", ".exe", ".dll", ".so", ".bat",
                                    ".cmd", ".ps1", ".sh", ".js", ".pkl", ".pickle"}:
            raise RecordingError()
    claim = _safe(root / ".native_prepared_start_claim")
    if not claim.is_file() or claim.stat().st_size != 0:
        raise RecordingError()
    for marker in (".native_active", ".native_incomplete", ".recording_incomplete"):
        if os.path.lexists(root / marker):
            raise RecordingError()
    for p in ("twitter", "reddit"):
        for suffix in ("-wal", "-shm", "-journal"):
            if os.path.lexists(root / (f"{p}_simulation.db" + suffix)):
                raise RecordingError()
        if p not in platforms and (os.path.lexists(root / f"{p}_simulation.db")
                                   or os.path.lexists(root / p / "actions.jsonl")):
            raise RecordingError()
    for marker in ("env_status.json", "run_state.json"):
        if os.path.lexists(root / marker):
            value = strict_json(_read(root, marker, MAX_JSON))
            # This capture contract supports the closed in-process session;
            # legacy/process status markers require a future qualified adapter.
            if type(value) is not dict or value.get("status") not in ("completed", "closed"):
                raise RecordingError()


def capture_recording(source, destination, *, anchors, platforms,
                      closed_run_confirmed, runtime_versions, runtime_sha256):
    """Trusted host API. Caller must own the run and confirm successful close.

    A failure leaves a new partial destination for inspection, never deletes it.
    The manifest is written last. Source files are never opened for writing.
    """
    try:
        if (type(anchors) is not RecordingAnchors or closed_run_confirmed is not True
                or type(runtime_versions) is not dict
                or set(runtime_versions) != {"oasis", "camel"}
                or any(type(v) is not str or not IDENTIFIER.fullmatch(v)
                       for v in runtime_versions.values())
                or type(runtime_sha256) is not str or not HEX.fullmatch(runtime_sha256)):
            raise RecordingError("invalid_request")
        platforms = _platforms(platforms)
        root = _root(source)
        target = Path(destination)
        if (not target.is_absolute() or target.name in ("", ".", "..")
                or target.parent != target.parent.resolve(strict=True)
                or os.path.lexists(target) or root == target or root in target.parents
                or target in root.parents):
            raise RecordingError("destination_unavailable")
        _safe(target.parent)
        _source_boundary(root, platforms)
        artifacts = {name: _read(root, name, MAX_JSON if name.endswith((".json", ".csv"))
                                 else FILE_LIMIT) for name in _names(platforms)}
        if sum(map(len, artifacts.values())) > TOTAL_LIMIT:
            raise RecordingError("limit_exceeded")
        _prepared(artifacts, anchors, platforms)
        for p in platforms:
            _events(artifacts[f"{p}/actions.jsonl"], p)
        schemas = {p: _schema(artifacts[f"{p}_simulation.db"]) for p in platforms}
        _source_boundary(root, platforms)
        for name, data in artifacts.items():
            if _read(root, name) != data:
                raise RecordingError("source_unavailable")
        target.mkdir(mode=0o700)
        for name, data in artifacts.items():
            path = target / name
            path.parent.mkdir(exist_ok=True)
            with path.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        # Catch changes during copying as well as during initial collection.
        _source_boundary(root, platforms)
        for name, data in artifacts.items():
            if _read(root, name) != data:
                raise RecordingError("source_unavailable")
        manifest = {"schema_version": 1, "kind": "native_recording",
                    "anchors": anchors.wire(), "platforms": list(platforms),
                    "runtime_versions": dict(runtime_versions,
                        python=python_platform.python_version(), sqlite=sqlite3.sqlite_version),
                    "runtime_sha256": runtime_sha256, "native_schemas": schemas,
                    "closed_run_confirmed": True,
                    "files": [{"name": name, "size": len(data), "sha256": digest(data)}
                              for name, data in artifacts.items()]}
        raw = canonical(manifest)
        with (target / "recording.json").open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        return digest(raw)
    except RecordingError:
        raise
    except (OSError, ValueError, TypeError, UnicodeError, sqlite3.Error, csv.Error,
            RecursionError, OverflowError):
        raise RecordingError("source_unavailable") from None


class NativeRecording:
    """Pin manifest and artifact bytes once; never reopen a bundle for reads."""
    def __init__(self, bundle, *, anchors, expected_revision):
        try:
            if (type(anchors) is not RecordingAnchors or type(expected_revision) is not str
                    or not HEX.fullmatch(expected_revision)):
                raise RecordingError()
            root = _root(bundle)
            raw = _read(root, "recording.json", MAX_JSON)
            if digest(raw) != expected_revision:
                raise RecordingError()
            manifest = strict_json(raw)
            fields = {"schema_version", "kind", "anchors", "platforms", "runtime_versions",
                      "runtime_sha256", "native_schemas", "closed_run_confirmed", "files"}
            if (type(manifest) is not dict or set(manifest) != fields
                    or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
                    or manifest["kind"] != "native_recording"
                    or manifest["anchors"] != anchors.wire()
                    or manifest["closed_run_confirmed"] is not True
                    or type(manifest["runtime_sha256"]) is not str
                    or not HEX.fullmatch(manifest["runtime_sha256"])
                    or type(manifest["runtime_versions"]) is not dict
                    or set(manifest["runtime_versions"]) != {"python", "sqlite", "oasis", "camel"}
                    or any(type(v) is not str or not IDENTIFIER.fullmatch(v)
                           for v in manifest["runtime_versions"].values())):
                raise RecordingError()
            platforms = _platforms(manifest["platforms"])
            if (type(manifest["anchors"]) is not dict
                    or RecordingAnchors(**manifest["anchors"]).wire() != anchors.wire()):
                raise RecordingError()
            names = _names(platforms)
            files = manifest["files"]
            if type(files) is not list or len(files) != len(names):
                raise RecordingError()
            artifacts = {}
            for name, entry in zip(names, files):
                if (type(entry) is not dict or set(entry) != {"name", "size", "sha256"}
                        or entry["name"] != name or type(entry["size"]) is not int
                        or not 0 < entry["size"] <= FILE_LIMIT
                        or type(entry["sha256"]) is not str or not HEX.fullmatch(entry["sha256"])):
                    raise RecordingError()
                data = _read(root, name, MAX_JSON if name.endswith((".json", ".csv")) else FILE_LIMIT)
                if len(data) != entry["size"] or digest(data) != entry["sha256"]:
                    raise RecordingError()
                artifacts[name] = data
            if sum(map(len, artifacts.values())) > TOTAL_LIMIT:
                raise RecordingError("limit_exceeded")
            _bundle_paths(root, names, platforms)
            _prepared(artifacts, anchors, platforms)
            events = {p: _events(artifacts[f"{p}/actions.jsonl"], p) for p in platforms}
            # Only after every path/hash/JSON/scope check can SQLite be opened.
            schemas = {p: _schema(artifacts[f"{p}_simulation.db"]) for p in platforms}
            if manifest["native_schemas"] != schemas:
                raise RecordingError()
            self._anchors = anchors
            self._revision = expected_revision
            self._platforms = platforms
            self._artifacts = artifacts
            self._events = events
            self._schemas = schemas
        except RecordingError:
            raise
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, sqlite3.Error,
                RecursionError, OverflowError, csv.Error):
            raise RecordingError() from None

    def _cursor(self, platform, offset):
        return base64.urlsafe_b64encode(canonical(
            {"version": 1, "revision": self._revision, "platform": platform,
             "offset": offset})).decode("ascii")

    def _offset(self, cursor, platform):
        if cursor is None:
            return 0
        try:
            value = strict_json(base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True), 512)
            if (type(value) is not dict or set(value) != {"version", "revision", "platform", "offset"}
                    or type(value["version"]) is not int or value["version"] != 1
                    or value["revision"] != self._revision or value["platform"] != platform
                    or type(value["offset"]) is not int
                    or not 0 <= value["offset"] <= len(self._events[platform])
                    or cursor != self._cursor(platform, value["offset"])):
                raise RecordingError()
            return value["offset"]
        except (ValueError, UnicodeError, RecordingError):
            raise RecordingError("invalid_cursor") from None

    def read(self, raw):
        request = request_from_wire(raw)
        p = request["platform"]
        if p not in self._platforms:
            raise RecordingError("invalid_request")
        result = {"version": 1, "recording_revision": self._revision,
                  "anchors": self._anchors.wire(), "platform": p,
                  "continuation_supported": False, "branch_execution_supported": False,
                  "deterministic_model_rerun": False}
        if request["operation"] == "playback":
            offset = self._offset(request.get("cursor"), p)
            end = min(offset + request["limit"], len(self._events[p]))
            result.update(events=[{"platform": p, "source_event_offset": index,
                                   "record": self._events[p][index]} for index in range(offset, end)],
                          total_events=len(self._events[p]), exhausted=end == len(self._events[p]),
                          next_cursor=None if end == len(self._events[p]) else self._cursor(p, end))
        else:
            result.update(self._metrics(p, request["limit"]))
        # Return a detached JSON tree, never references to internal event objects.
        data = canonical(result)
        if len(data) > RESPONSE_LIMIT:
            raise RecordingError("limit_exceeded")
        return strict_json(data, RESPONSE_LIMIT)

    def _metrics(self, platform, limit):
        actions, agents, rounds = Counter(), Counter(), Counter()
        for event in self._events[platform]:
            if "event_type" not in event:
                actions[event["action_type"]] += 1
                agents[str(event["agent_id"])] += 1
                rounds[str(event["round"])] += 1
        final = {}
        connection = None
        try:
            connection = _database(self._artifacts[f"{platform}_simulation.db"])
            tables = set(self._schemas[platform]["tables"])
            for table in TABLES:
                if table not in tables:
                    final[table] = {"present": False}
                    continue
                # Identifiers are hardcoded, no manifest or wire SQL selectors.
                count = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                if count > ROW_LIMIT:
                    raise RecordingError("limit_exceeded")
                cursor = connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid LIMIT ?', (limit,))
                columns = [item[0] for item in cursor.description]
                if len(columns) > 64:
                    raise RecordingError()
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                for row in rows:
                    for cell in row.values():
                        if type(cell) is bytes or (type(cell) is str and len(cell.encode("utf-8")) > 16384):
                            raise RecordingError("limit_exceeded")
                    bounded_tree(row)
                final[table] = {"present": True, "count": count, "rows": rows,
                                "truncated": count > len(rows), "order": "native_rowid"}
        except sqlite3.Error:
            raise RecordingError() from None
        finally:
            if connection is not None:
                connection.close()
        return {"logged_action_counts": dict(actions), "logged_agent_counts": dict(agents),
                "logged_round_counts": dict(rounds), "final_native_tables": final,
                "coverage": {"sequence": "file_order_per_platform",
                    "counts": "logged_actions_including_possible_initial_trace_duplicates",
                    "final_state": "captured_native_rows_only",
                    "post_log_interviews_may_exist_in_trace": True,
                    "exact_event_to_native_row_links": False,
                    "historical_per_round_graph_state": False,
                    "causal_or_truth_conclusions": False}}

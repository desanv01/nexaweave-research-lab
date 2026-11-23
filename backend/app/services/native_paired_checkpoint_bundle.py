"""File custody for one committed, two-platform native checkpoint."""

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from time import monotonic

from nexaweave_execution.native_owned_binding import _INPUT_LIMIT, _manifest

from .native_recordings import _identity, _root, _round_file_stat, _safe


_MIB = 1024 * 1024
_CHUNK_BYTES = 65536
_MANIFEST = "manifest.json"
_MANIFEST_LIMIT = 8 * _MIB
_MEMBER_LIMITS = {
    "twitter.sqlite3": 64 * _MIB,
    "reddit.sqlite3": 64 * _MIB,
    "twitter.jsonl": 8 * _MIB,
    "reddit.jsonl": 8 * _MIB,
}
_BUNDLE_FILES = frozenset((*_MEMBER_LIMITS, _MANIFEST))
_AGGREGATE_LIMIT = 160 * _MIB
_MANIFEST_FIELDS = {"schema_version", "anchors", "completed_round", "runtime_state", "members"}
_DIGEST = re.compile(r"[0-9a-f]{64}")
ENGINE_VERSIONS = {"camel-ai": "0.2.78", "camel-oasis": "0.2.5"}
PREPARED_FILES = ("state.json", "simulation_config.json", "source_grounding.json",
                  "twitter_profiles.csv", "reddit_profiles.json")


def prepared_digest(root):
    return _manifest(root, PREPARED_FILES, _INPUT_LIMIT)


def paired_anchors(root, graph_id, simulation_id, seed, max_rounds, runtime_sha256, scope):
    return {
        "scope": dict(scope), "graph_id": graph_id, "simulation_id": simulation_id,
        "seed": seed, "round_limit": max_rounds, "prepared_sha256": prepared_digest(root),
        "runtime_sha256": runtime_sha256, "engine_versions": dict(ENGINE_VERSIONS),
    }


def _check_deadline(deadline):
    if type(deadline) not in (int, float) or not -math.inf < deadline < math.inf:
        raise ValueError("invalid paired checkpoint deadline")
    if deadline <= monotonic():
        raise TimeoutError("paired checkpoint deadline exceeded")


def _json_size_floor(value, remaining, deadline, depth=0):
    """Reject non-JSON types and bound work before building encoder output."""
    _check_deadline(deadline)
    kind = type(value)
    if depth > 64 or remaining < 1:
        raise ValueError("paired checkpoint JSON exceeds its bounds")
    if kind is str:
        remaining -= max(1, len(value))
    elif kind in (dict, list):
        remaining -= 1
        if kind is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError("invalid paired checkpoint JSON key")
                remaining = _json_size_floor(key, remaining, deadline, depth + 1)
                remaining = _json_size_floor(item, remaining, deadline, depth + 1)
        else:
            for item in value:
                remaining = _json_size_floor(item, remaining, deadline, depth + 1)
    elif value is None or kind in (bool, int) or (kind is float and math.isfinite(value)):
        remaining -= 1
    else:
        raise ValueError("invalid paired checkpoint JSON value")
    if remaining < 0:
        raise ValueError("paired checkpoint JSON exceeds its bounds")
    return remaining


def _canonical(value, deadline):
    _json_size_floor(value, _MANIFEST_LIMIT, deadline)
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True,
                               separators=(",", ":"))
    data = bytearray()
    for item in encoder.iterencode(value):
        _check_deadline(deadline)
        if len(item) > _MANIFEST_LIMIT - len(data):
            raise ValueError("paired checkpoint manifest exceeds its bound")
        encoded = item.encode("utf-8")
        if len(encoded) > _MANIFEST_LIMIT - len(data):
            raise ValueError("paired checkpoint manifest exceeds its bound")
        data.extend(encoded)
    return bytes(data)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate paired checkpoint field")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("invalid paired checkpoint JSON constant")


def _decode_manifest(raw, deadline):
    _check_deadline(deadline)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=_invalid_constant)
    except (ValueError, RecursionError) as error:
        raise ValueError("invalid paired checkpoint manifest") from error
    if _canonical(value, deadline) != raw:
        raise ValueError("noncanonical paired checkpoint manifest")
    if (type(value) is not dict or value.keys() != _MANIFEST_FIELDS
            or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        raise ValueError("invalid paired checkpoint manifest")
    return value


def _validate_state(anchors, completed_round, runtime_state):
    if (type(anchors) is not dict or type(runtime_state) is not dict
            or type(completed_round) is not int or completed_round < 1):
        raise ValueError("invalid paired checkpoint state")


def _sum_within_bound(sizes):
    if sum(sizes) > _AGGREGATE_LIMIT:
        raise ValueError("paired checkpoint bundle exceeds its bound")


def _file_id(details):
    return details.st_dev, details.st_ino


def _directory_id(path):
    return _file_id(_root(path).lstat())


def _same_directory(path, expected):
    if _directory_id(path) != expected:
        raise ValueError("changed paired checkpoint directory")


@contextmanager
def _directory_handle(path, expected):
    """Anchor writes to a held directory where relative descriptors are supported."""
    descriptor = None
    try:
        if os.open in os.supports_dir_fd:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            details = os.fstat(descriptor)
            if not stat.S_ISDIR(details.st_mode) or _file_id(details) != expected:
                raise ValueError("changed paired checkpoint directory")
        _same_directory(path, expected)
        yield descriptor
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _at(path, descriptor):
    return path.name if descriptor is not None else path


def _inventory(root, root_id, deadline):
    _check_deadline(deadline)
    _same_directory(root, root_id)
    before = _identity(root.lstat())
    result = {}
    with os.scandir(root) as entries:
        for entry in entries:
            _check_deadline(deadline)
            if entry.name not in _BUNDLE_FILES or entry.name in result:
                raise ValueError("unexpected paired checkpoint file")
            result[entry.name] = _identity(_round_file_stat(_safe(root / entry.name)))
    _same_directory(root, root_id)
    if (result.keys() != _BUNDLE_FILES or len({item[:2] for item in result.values()}) != len(result)
            or _identity(root.lstat()) != before):
        raise ValueError("incomplete or changed paired checkpoint inventory")
    return before, result


def _write_private(path, data, bundle_fd, created, deadline):
    _check_deadline(deadline)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(_at(path, bundle_fd), flags, 0o600, dir_fd=bundle_fd)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise ValueError("invalid paired checkpoint staging file")
        created[path.name] = _file_id(opened)
        remaining = memoryview(data)
        while remaining:
            _check_deadline(deadline)
            written = os.write(descriptor, remaining[:_CHUNK_BYTES])
            if written == 0:
                raise OSError("incomplete paired checkpoint write")
            remaining = remaining[written:]
        os.fsync(descriptor)
        written = os.fstat(descriptor)
        if written.st_size != len(data):
            raise OSError("incomplete paired checkpoint write")
    finally:
        os.close(descriptor)
    # Windows can finalize the last-write time only when the writer closes.
    on_disk = _round_file_stat(_safe(path))
    if (on_disk.st_size != len(data) or written.st_nlink != 1
            or _file_id(written) != created[path.name] or _file_id(on_disk) != created[path.name]):
        raise ValueError("changed paired checkpoint staging file")
    return _identity(on_disk)


def _discard_private(root, parent_id, root_id, parent_fd, created):
    """Remove only tracked files in the original exclusive directory."""
    try:
        _same_directory(root.parent, parent_id)
        with _directory_handle(root, root_id) as bundle_fd:
            for name in reversed(created):
                path = root / name
                _same_directory(root.parent, parent_id)
                _same_directory(root, root_id)
                details = os.stat(_at(path, bundle_fd), dir_fd=bundle_fd, follow_symlinks=False)
                if (not stat.S_ISREG(details.st_mode) or details.st_nlink != 1
                        or _file_id(details) != created[name]):
                    return
                os.unlink(_at(path, bundle_fd), dir_fd=bundle_fd)
        _same_directory(root.parent, parent_id)
        _same_directory(root, root_id)
        os.rmdir(_at(root, parent_fd), dir_fd=parent_fd)
    except Exception:
        # Leave any directory whose custody cannot be proved.
        pass


def publish_paired_checkpoint(destination, *, anchors, completed_round, runtime_state, members, deadline) -> str:
    """Publish four immutable byte strings and their manifest under a new private directory."""
    _check_deadline(deadline)
    _validate_state(anchors, completed_round, runtime_state)
    if type(members) is not dict or members.keys() != _MEMBER_LIMITS.keys():
        raise ValueError("invalid paired checkpoint members")
    members = members.copy()
    records = {}
    for name, limit in _MEMBER_LIMITS.items():
        _check_deadline(deadline)
        data = members[name]
        if type(data) is not bytes or not 0 < len(data) <= limit:
            raise ValueError("invalid paired checkpoint member size")
        records[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    raw = _canonical(dict(schema_version=1, anchors=anchors, completed_round=completed_round,
                          runtime_state=runtime_state, members=records), deadline)
    _sum_within_bound([len(raw), *(entry["bytes"] for entry in records.values())])
    digest = hashlib.sha256(raw).hexdigest()

    root = Path(destination)
    if not root.is_absolute() or not root.name or root != _root(root.parent) / root.name:
        raise ValueError("invalid paired checkpoint destination")
    parent_id = _directory_id(root.parent)
    with _directory_handle(root.parent, parent_id) as parent_fd:
        _check_deadline(deadline)
        os.mkdir(_at(root, parent_fd), mode=0o700, dir_fd=parent_fd)
        _same_directory(root.parent, parent_id)
        root_id = _directory_id(root)
        created = {}
        try:
            with _directory_handle(root, root_id) as bundle_fd:
                # The manifest is the commit marker. Readers require its complete
                # pinned bytes, the exact inventory, and all four member hashes.
                written = {}
                for name, data in (*members.items(), (_MANIFEST, raw)):
                    _check_deadline(deadline)
                    _same_directory(root.parent, parent_id)
                    _same_directory(root, root_id)
                    written[name] = _write_private(root / name, data, bundle_fd, created, deadline)
                if bundle_fd is not None:
                    os.fsync(bundle_fd)
                    os.fsync(parent_fd)
                _, published = _inventory(root, root_id, deadline)
                if published != written:
                    raise ValueError("changed paired checkpoint staging file")
                _same_directory(root.parent, parent_id)
                _check_deadline(deadline)
        except BaseException:
            _discard_private(root, parent_id, root_id, parent_fd, created)
            raise
    return digest


def load_paired_checkpoint(root, *, expected_digest, expected_anchors, deadline) -> tuple[int, dict, dict]:
    """Read and verify a published checkpoint without opening caller-selected members."""
    from .native_twitter_restore import _read_lineage_input

    _check_deadline(deadline)
    if type(expected_digest) is not str or not _DIGEST.fullmatch(expected_digest):
        raise ValueError("invalid paired checkpoint digest")
    if type(expected_anchors) is not dict:
        raise ValueError("invalid paired checkpoint anchors")
    expected = _canonical(expected_anchors, deadline)
    root = _root(root)
    root_id = _directory_id(root)
    parent_id = _directory_id(root.parent)
    stamp, inventory = _inventory(root, root_id, deadline)
    raw, digest, identity = _read_lineage_input(root / _MANIFEST, _MANIFEST_LIMIT, deadline)
    if digest != expected_digest or identity != inventory[_MANIFEST]:
        raise ValueError("paired checkpoint manifest digest differs")
    value = _decode_manifest(raw, deadline)
    anchors, completed_round, runtime_state = (value[key] for key in ("anchors", "completed_round", "runtime_state"))
    _validate_state(anchors, completed_round, runtime_state)
    if _canonical(anchors, deadline) != expected:
        raise ValueError("paired checkpoint anchors differ")

    records = value["members"]
    if type(records) is not dict or records.keys() != _MEMBER_LIMITS.keys():
        raise ValueError("invalid paired checkpoint member inventory")
    for name, limit in _MEMBER_LIMITS.items():
        record = records[name]
        if (type(record) is not dict or record.keys() != {"bytes", "sha256"}
                or type(record["bytes"]) is not int or not 0 < record["bytes"] <= limit
                or type(record["sha256"]) is not str or not _DIGEST.fullmatch(record["sha256"])
                or record["bytes"] != inventory[name][2]):
            raise ValueError("invalid paired checkpoint member metadata")
    _sum_within_bound([len(raw), *(record["bytes"] for record in records.values())])

    members = {}
    for name in _MEMBER_LIMITS:
        record = records[name]
        data, digest, identity = _read_lineage_input(root / name, record["bytes"], deadline)
        if len(data) != record["bytes"] or digest != record["sha256"] or identity != inventory[name]:
            raise ValueError("paired checkpoint member digest differs")
        members[name] = data
    _same_directory(root.parent, parent_id)
    if _inventory(root, root_id, deadline) != (stamp, inventory):
        raise ValueError("changed paired checkpoint bundle")
    _check_deadline(deadline)
    return completed_round, runtime_state, members

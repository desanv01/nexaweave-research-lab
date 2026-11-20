"""Trusted spawn bindings for checkpointing and continuing one native Twitter round."""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from uuid import UUID

from .native_owned_binding import (
    NativeOwnedSessionFactory,
    _INPUT_LIMIT,
    _OUTPUT_LIMIT,
    _regular_bound_file,
    _safe_ancestors,
)
from .native_run_contracts import NativeRunRequest, canonical_uuid, sha256

_LINEAGE_NAME = "lineage.json"
_LINEAGE_LIMIT = 7200
_PREPARED_NAMES = ("state.json", "simulation_config.json", "source_grounding.json",
                   "twitter_profiles.csv")
_CHILD_OUTPUT_NAMES = ("native.sqlite3", "actions.jsonl")
_HASH_BLOCK = 65536
_ANCHOR_KEYS = {"graph_id", "simulation_id", "run_id", "branch_id", "project_id",
                "project_revision"}
_LINEAGE_KEYS = {"version", "anchors", "producer_pid", "manifest_sha256", "prepared",
                 "owners", "runner"}
_PREPARED_KEYS = {"artifact_sha256", "config_sha256", "profile_sha256",
                  "source_grounding_sha256"}
_OWNER_KEYS = {"platform_id", "name", "camel_id", "token_limit"}
_RUNNER_KEYS = {"completed_round", "horizon", "minutes_per_round", "simulated_minutes",
                "total_actions"}


def _directory(value, *, output=False, allow_empty=False):
    path = Path(value)
    if (not path.is_absolute() or not path.name or not _safe_ancestors(path.parent)
            or path.parent.resolve(strict=True) != path.parent):
        raise ValueError("invalid owned Twitter directory")
    if not os.path.lexists(path):
        if output:
            return path
        raise ValueError("missing owned Twitter directory")
    if (not _safe_ancestors(path) or path.resolve(strict=True) != path
            or not path.is_dir()):
        raise ValueError("invalid owned Twitter directory")
    if output and (not allow_empty or next(path.iterdir(), None) is not None):
        raise ValueError("occupied owned Twitter output")
    return path


def _require_disjoint(*directories):
    for index, left in enumerate(directories):
        if any(left == right or left in right.parents or right in left.parents
               for right in directories[index + 1:]):
            raise ValueError("overlapping owned Twitter directories")


def _identity(details):
    return (details.st_dev, details.st_ino, details.st_size,
            details.st_mtime_ns, details.st_ctime_ns)


def _require_regular_file(details, limit):
    reparse = (getattr(details, "st_file_attributes", 0)
               & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    if (not stat.S_ISREG(details.st_mode) or details.st_nlink != 1 or reparse
            or not 1 <= details.st_size <= limit):
        raise ValueError("invalid owned Twitter file")


def _owned_file_stat(path, limit):
    if not _safe_ancestors(path) or path.resolve(strict=True) != path:
        raise ValueError("invalid owned Twitter path")
    details = path.lstat()
    _require_regular_file(details, limit)
    return details


def _prepared_identities(root):
    identities = tuple(_identity(_owned_file_stat(root / name, _INPUT_LIMIT))
                       for name in _PREPARED_NAMES)
    if len({identity[:2] for identity in identities}) != len(identities):
        raise ValueError("aliased owned Twitter preparation")
    return identities


def _read_lineage(root):
    path = root / _LINEAGE_NAME
    before = _identity(_owned_file_stat(path, _LINEAGE_LIMIT))
    raw = _regular_bound_file(root, _LINEAGE_NAME, _LINEAGE_LIMIT)
    if _identity(_owned_file_stat(path, _LINEAGE_LIMIT)) != before:
        raise ValueError("changed owned Twitter lineage")
    return raw


def _cross_identity(details):
    identity = _identity(details)
    # On Windows, compare timestamps only within the same stat API.
    return identity[:3] if os.name == "nt" else identity


def _hash_child_output(root, name, expected_identity):
    path = root / name
    before = _owned_file_stat(path, _OUTPUT_LIMIT)
    if _identity(before) != expected_identity:
        raise ValueError("changed owned Twitter output")
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        _require_regular_file(opened, _OUTPUT_LIMIT)
        if _cross_identity(opened) != _cross_identity(before):
            raise ValueError("changed owned Twitter output")
        digest = hashlib.sha256()
        remaining = opened.st_size
        while remaining:
            block = os.read(descriptor, min(_HASH_BLOCK, remaining))
            if not block:
                raise ValueError("truncated owned Twitter output")
            digest.update(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise ValueError("extended owned Twitter output")
        after = os.fstat(descriptor)
        _require_regular_file(after, _OUTPUT_LIMIT)
        path_after = _owned_file_stat(path, _OUTPUT_LIMIT)
        if (_identity(after) != _identity(opened) or _identity(path_after) != expected_identity
                or _cross_identity(after) != _cross_identity(path_after)):
            raise ValueError("changed owned Twitter output")
        return {"name": name, "sha256": digest.hexdigest(), "size": opened.st_size}
    finally:
        os.close(descriptor)


def _native_request(prepared, request):
    request = NativeRunRequest.from_wire(request)
    if (type(prepared) is not NativeOwnedSessionFactory
            or request.platforms != ("twitter",)
            or type(prepared.timeout_seconds) not in (float, int)
            or not 1 <= prepared.timeout_seconds <= 600):
        raise ValueError("invalid owned Twitter request")
    return request


def _checked_anchors(values, prepared, request, run_id):
    from app.services.native_recording_contracts import RecordingAnchors

    if type(values) is not dict or values.keys() != _ANCHOR_KEYS:
        raise ValueError("invalid owned Twitter anchors")
    anchors = RecordingAnchors(**values)
    if (anchors.graph_id != prepared.graph_id or anchors.simulation_id != request.simulation_id
            or anchors.run_id != str(run_id) or anchors.project_id != str(request.project_id)
            or anchors.project_revision != request.project_revision):
        raise ValueError("owned Twitter anchors mismatch")
    return anchors


def _lineage_section(value, keys):
    if type(value) is not dict or value.keys() != keys:
        raise ValueError("invalid owned Twitter lineage")
    return value


def _check_lineage_body(lineage, request):
    from app.services.native_recording_contracts import IDENTIFIER

    sha256(lineage["manifest_sha256"])
    prepared = _lineage_section(lineage["prepared"], _PREPARED_KEYS)
    for digest in prepared.values():
        sha256(digest)
    if prepared["artifact_sha256"] != request.artifact_sha256:
        raise ValueError("owned Twitter preparation mismatch")

    owners = lineage["owners"]
    if type(owners) is not list or len(owners) != 2:
        raise ValueError("invalid owned Twitter cohort")
    for platform_id, value in enumerate(owners):
        owner = _lineage_section(value, _OWNER_KEYS)
        if (type(owner["platform_id"]) is not int or owner["platform_id"] != platform_id
                or type(owner["name"]) is not str or not owner["name"]
                or type(owner["camel_id"]) is not str
                or not IDENTIFIER.fullmatch(owner["camel_id"])
                or type(owner["token_limit"]) is not int
                or not 0 < owner["token_limit"] < 2**63):
            raise ValueError("invalid owned Twitter owner")
    if (owners[0]["name"] == owners[1]["name"]
            or owners[0]["camel_id"] == owners[1]["camel_id"]):
        raise ValueError("duplicate owned Twitter owner")

    runner = _lineage_section(lineage["runner"], _RUNNER_KEYS)
    if (any(type(value) is not int or not 0 <= value < 2**63 for value in runner.values())
            or runner["completed_round"] != 1 or runner["horizon"] < 1
            or runner["minutes_per_round"] < 1
            or runner["simulated_minutes"] != 0):
        raise ValueError("invalid owned Twitter runner")


class _CaptureTwitterSession:
    def __init__(self, session, checkpoint_dir, lineage_dir, anchors,
                 prepared_dir, prepared_identities):
        self._session = session
        self._checkpoint_dir = checkpoint_dir
        self._lineage_dir = lineage_dir
        self._anchors = anchors
        self._prepared_dir = prepared_dir
        self._prepared_identities = prepared_identities

    def start(self):
        from app.services.native_twitter_restore import _capture_twitter_lineage

        if _prepared_identities(self._prepared_dir) != self._prepared_identities:
            raise ValueError("changed owned Twitter preparation")
        self._session.start()
        _capture_twitter_lineage(self._session, self._checkpoint_dir, self._lineage_dir,
                                 self._anchors)
        return self

    def close(self):
        self._session.close()


@dataclass(frozen=True)
class NativeTwitterCaptureFactory:
    prepared: NativeOwnedSessionFactory
    checkpoint_dir: str
    lineage_dir: str
    anchors: dict

    def _checked_capture(self, request):
        request = _native_request(self.prepared, request)
        if request.schema_version != 1 or request.max_rounds != 1:
            raise ValueError("invalid owned Twitter capture request")
        prepared_dir = self.prepared._root()
        identities = _prepared_identities(prepared_dir)
        self.prepared.validate(request)
        checkpoint_dir = _directory(self.checkpoint_dir, output=True)
        lineage_dir = _directory(self.lineage_dir, output=True, allow_empty=True)
        _require_disjoint(prepared_dir, checkpoint_dir, lineage_dir)
        if checkpoint_dir.parent != prepared_dir.parent:
            raise ValueError("invalid owned Twitter checkpoint location")
        anchors = _checked_anchors(self.anchors, self.prepared, request, request.run_id)
        if _prepared_identities(prepared_dir) != identities:
            raise ValueError("changed owned Twitter preparation")
        return checkpoint_dir, lineage_dir, anchors, prepared_dir, identities

    def validate(self, request):
        self._checked_capture(request)

    def create_session(self, request):
        checkpoint_dir, lineage_dir, anchors, prepared_dir, identities = self._checked_capture(request)
        session = self.prepared.create_session(request, round_capture={
            "destination": checkpoint_dir,
            "anchors": anchors,
            "include_memory": True,
            "include_runner_state": True,
            "include_engine_clock": True,
            "include_recommendation_state": True,
            "include_topology": True,
        })
        return _CaptureTwitterSession(session, checkpoint_dir, lineage_dir, anchors,
                                      prepared_dir, identities)

    def evidence(self, request):
        # The child protocol asks for evidence only after successful session close.
        return hashlib.sha256(_read_lineage(_directory(self.lineage_dir))).hexdigest()


class _RestoredTwitterSession:
    def __init__(self, factory, request):
        self._factory = factory
        self._request = request
        self._started = False
        self._closed = False

    def start(self):
        if self._started or self._closed:
            raise ValueError("owned Twitter restore already consumed")
        self._started = True
        factory = self._factory
        deadline = time.monotonic() + factory.prepared.timeout_seconds
        request = _native_request(factory.prepared, self._request)
        prepared_dir, checkpoint_dir, child_dir, lineage = factory._checked_restore(request)
        from app.services.native_twitter_restore import _start_fresh_restored_twitter

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("owned Twitter restore expired")
        operation = _start_fresh_restored_twitter(
            prepared_dir, checkpoint_dir, child_dir, lineage, factory.prepared.model_factory,
            factory.expected_post_content, remaining,
            **({"intervention": request.intervention} if request.intervention is not None else {}),
        )
        if isinstance(operation, Coroutine):
            asyncio.run(asyncio.wait_for(operation, max(0.0, deadline - time.monotonic())))
        if time.monotonic() >= deadline:
            raise ValueError("owned Twitter restore expired")
        return self

    def close(self):
        # The application operation owns the writer and closes it before returning.
        self._closed = True


@dataclass(frozen=True)
class NativeRestoredTwitterFactory:
    prepared: NativeOwnedSessionFactory
    checkpoint_dir: str
    lineage_dir: str
    child_dir: str
    expected_lineage_sha256: str
    expected_producer_pid: int
    expected_producer_run: str
    expected_post_content: str | None
    expected_producer_instance_id: UUID

    def __post_init__(self):
        canonical_uuid(self.expected_producer_instance_id)

    def _checked_restore(self, request):
        producer_run = canonical_uuid(self.expected_producer_run)
        producer_pid = self.expected_producer_pid
        expected_digest = sha256(self.expected_lineage_sha256)
        if (request.schema_version in (2, 3)
                and (request.source_run_id != producer_run
                     or request.checkpoint_sha256 != expected_digest)):
            raise ValueError("owned Twitter continuation binding mismatch")
        self.prepared._check_prepared_request(request)
        expected_post_content = self.expected_post_content
        if (producer_run == request.run_id or type(producer_pid) is not int
                or not 1 <= producer_pid <= 2_147_483_647
                or (expected_post_content is not None
                    and (type(expected_post_content) is not str or not expected_post_content))):
            raise ValueError("invalid owned Twitter restore authorization")
        prepared_dir = self.prepared._root()
        checkpoint_dir = _directory(self.checkpoint_dir)
        lineage_dir = _directory(self.lineage_dir)
        child_dir = _directory(self.child_dir, output=True, allow_empty=True)
        _require_disjoint(prepared_dir, checkpoint_dir, lineage_dir, child_dir)

        raw = _read_lineage(lineage_dir)
        if hashlib.sha256(raw).hexdigest() != expected_digest:
            raise ValueError("owned Twitter lineage digest mismatch")
        from app.services.native_recording_contracts import canonical, strict_json

        lineage = _lineage_section(strict_json(raw, _LINEAGE_LIMIT), _LINEAGE_KEYS)
        if (canonical(lineage) != raw or type(lineage["version"]) is not int
                or lineage["version"] != 1 or type(lineage["producer_pid"]) is not int
                or lineage["producer_pid"] != producer_pid):
            raise ValueError("owned Twitter lineage identity mismatch")
        _checked_anchors(lineage["anchors"], self.prepared, request, producer_run)
        _check_lineage_body(lineage, request)
        if (request.intervention is not None
                and request.intervention.manifest_sha256 != lineage["manifest_sha256"]):
            raise ValueError("owned Twitter fork manifest mismatch")
        return prepared_dir, checkpoint_dir, child_dir, lineage

    def validate(self, request):
        request = _native_request(self.prepared, request)
        self._checked_restore(request)
        self.prepared._validate_prepared_files(request)

    def create_session(self, request):
        self.validate(request)
        return _RestoredTwitterSession(self, request)

    def evidence(self, request):
        # Hash the fixed outputs only after the child protocol has closed the session.
        root = _directory(self.child_dir)
        before = tuple(_identity(_owned_file_stat(root / name, _OUTPUT_LIMIT))
                       for name in _CHILD_OUTPUT_NAMES)
        if len({identity[:2] for identity in before}) != len(before):
            raise ValueError("aliased owned Twitter outputs")
        files = [_hash_child_output(root, name, identity)
                 for name, identity in zip(_CHILD_OUTPUT_NAMES, before, strict=True)]
        after = tuple(_identity(_owned_file_stat(root / name, _OUTPUT_LIMIT))
                      for name in _CHILD_OUTPUT_NAMES)
        if after != before:
            raise ValueError("changed owned Twitter outputs")
        canonical = json.dumps({"schema_version": 1, "files": files},
                               sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True).encode("ascii")
        return hashlib.sha256(canonical).hexdigest()

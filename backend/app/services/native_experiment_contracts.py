"""Immutable trusted cohort bindings and a selector-only JSON request."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat

from nexaweave_execution.native_run_contracts import NativeRunRequest, principal_id

try:
    from .native_recording_contracts import (RecordingAnchors, RecordingError,
        HEX, IDENTIFIER, canonical, strict_json)
except ImportError:
    from native_recording_contracts import (RecordingAnchors, RecordingError,
        HEX, IDENTIFIER, canonical, strict_json)

MAX_REQUEST = 8192
MAX_MANIFEST = 65536
MAX_RESULT = 512 * 1024
MAX_MEMBERS = 16


class ExperimentError(ValueError):
    def __init__(self, code="invalid_request"):
        self.code = code if code in {"invalid_request", "invalid_binding",
            "authority_unavailable", "invalid_recording", "limit_exceeded"} else "invalid_request"
        super().__init__(self.code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def label(value):
    if type(value) is not str or not 1 <= len(value.encode("utf-8")) <= 160 or any(
            ord(c) < 32 or ord(c) == 127 for c in value) or not value.strip():
        raise ExperimentError("invalid_binding")
    return value


def identifier(value):
    if type(value) is not str or not IDENTIFIER.fullmatch(value):
        raise ExperimentError("invalid_binding")
    return value


def canonical_path(value):
    """Lexical admission only; filesystem inspection follows run authority."""
    if type(value) is not str or not 1 <= len(value) <= 4096 or "\x00" in value:
        raise ExperimentError("invalid_binding")
    path = Path(value)
    if not path.is_absolute() or str(path) != value or ".." in path.parts:
        raise ExperimentError("invalid_binding")
    return value


@dataclass(frozen=True)
class RecordingPin:
    bundle: str
    revision: str
    anchors: RecordingAnchors

    def __post_init__(self):
        canonical_path(self.bundle)
        if (type(self.revision) is not str or not HEX.fullmatch(self.revision)
                or type(self.anchors) is not RecordingAnchors):
            raise ExperimentError("invalid_binding")

    def wire(self):
        return {"bundle": self.bundle, "revision": self.revision, "anchors": self.anchors.wire()}


@dataclass(frozen=True)
class ExperimentMember:
    member_id: str
    member_label: str
    case_label: str
    request: NativeRunRequest
    recording: RecordingPin | None = None

    def __post_init__(self):
        identifier(self.member_id)
        label(self.member_label)
        label(self.case_label)
        try:
            if type(self.request) is not NativeRunRequest or NativeRunRequest.from_wire(self.request) != self.request:
                raise ExperimentError("invalid_binding")
            if self.recording is not None:
                if type(self.recording) is not RecordingPin:
                    raise ExperimentError("invalid_binding")
                a, r = self.recording.anchors, self.request
                if (a.run_id != str(r.run_id) or a.project_id != str(r.project_id)
                        or a.project_revision != r.project_revision or a.simulation_id != r.simulation_id):
                    raise ExperimentError("invalid_binding")
        except ExperimentError:
            raise
        except Exception:
            raise ExperimentError("invalid_binding") from None

    def wire(self):
        return {"member_id": self.member_id, "member_label": self.member_label,
                "case_label": self.case_label, "request": self.request.to_wire(),
                "recording": None if self.recording is None else self.recording.wire()}


@dataclass(frozen=True)
class ExperimentCohort:
    principal: str
    members: tuple[ExperimentMember, ...]

    def __post_init__(self):
        try:
            principal_id(self.principal)
            if (type(self.members) is not tuple or not 1 <= len(self.members) <= MAX_MEMBERS
                    or any(type(m) is not ExperimentMember for m in self.members)):
                raise ExperimentError("invalid_binding")
            if (len({m.member_id for m in self.members}) != len(self.members)
                    or len({m.request.run_id for m in self.members}) != len(self.members)
                    or len({(m.request.project_id, m.request.project_revision) for m in self.members}) != 1
                    or any(m.request.principal != self.principal for m in self.members)):
                raise ExperimentError("invalid_binding")
        except ExperimentError:
            raise
        except Exception:
            raise ExperimentError("invalid_binding") from None

    def wire(self):
        return {"version": 1, "members": [m.wire() for m in self.members]}

    @property
    def digest(self):
        return sha(canonical(self.wire()))


def cohort_from_manifest(raw, principal):
    try:
        value = strict_json(raw, MAX_MANIFEST)
        if (type(value) is not dict or set(value) != {"version", "members"}
                or type(value["version"]) is not int or value["version"] != 1
                or type(value["members"]) is not list or not 1 <= len(value["members"]) <= MAX_MEMBERS):
            raise ExperimentError("invalid_binding")
        members = []
        for item in value["members"]:
            if type(item) is not dict or set(item) != {"member_id", "member_label", "case_label", "request", "recording"}:
                raise ExperimentError("invalid_binding")
            pin = item["recording"]
            if pin is not None:
                if type(pin) is not dict or set(pin) != {"bundle", "revision", "anchors"} or type(pin["anchors"]) is not dict:
                    raise ExperimentError("invalid_binding")
                pin = RecordingPin(pin["bundle"], pin["revision"], RecordingAnchors(**pin["anchors"]))
            members.append(ExperimentMember(item["member_id"], item["member_label"], item["case_label"],
                NativeRunRequest.from_wire(item["request"]), pin))
        return ExperimentCohort(principal, tuple(members))
    except Exception:
        raise ExperimentError("invalid_binding") from None


def request_from_wire(raw, cohort):
    try:
        value = strict_json(raw, MAX_REQUEST)
        if (type(value) is not dict or set(value) != {"version", "title", "member_ids"}
                or type(value["version"]) is not int or value["version"] != 1):
            raise ExperimentError()
        label(value["title"])
        ids = value["member_ids"]
        if (type(ids) is not list or not 1 <= len(ids) <= MAX_MEMBERS
                or any(type(i) is not str for i in ids) or len(set(ids)) != len(ids)
                or not set(ids) <= {m.member_id for m in cohort.members}):
            raise ExperimentError()
        return value
    except Exception:
        raise ExperimentError("invalid_request") from None


def read_manifest(path_value, expected_sha256):
    """Bounded regular-file read, no links/reparse points or changed identity."""
    try:
        path = Path(canonical_path(path_value))
        if type(expected_sha256) is not str or not HEX.fullmatch(expected_sha256) or path != path.resolve(strict=True):
            raise ExperimentError("invalid_binding")
        def inspect():
            for p in (path, *path.parents):
                s = p.lstat()
                if stat.S_ISLNK(s.st_mode) or getattr(s, "st_file_attributes", 0) & 0x400:
                    raise ExperimentError("invalid_binding")
            return path.lstat()
        def identity(s):
            return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns
        before = inspect()
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_MANIFEST:
            raise ExperimentError("invalid_binding")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            common = 4 if os.name == "nt" else 5
            if not stat.S_ISREG(opened.st_mode) or identity(before)[:common] != identity(opened)[:common]:
                raise ExperimentError("invalid_binding")
            raw = stream.read(MAX_MANIFEST + 1)
            if identity(opened) != identity(os.fstat(stream.fileno())):
                raise ExperimentError("invalid_binding")
        if identity(before) != identity(inspect()) or len(raw) != before.st_size or sha(raw) != expected_sha256:
            raise ExperimentError("invalid_binding")
        return raw
    except Exception:
        raise ExperimentError("invalid_binding") from None

"""Small, identifier-only contracts for one prepared native simulation run."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Mapping
from uuid import UUID

_PRINCIPAL = re.compile(r"[\x20-\x7e]{1,128}\Z")
_SIMULATION = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_REQUEST_KEYS = frozenset({"schema_version", "principal", "project_id", "project_revision",
    "simulation_id", "run_id", "artifact_sha256", "runtime_sha256", "platforms", "seed", "max_rounds"})
_CHILD_KEYS = frozenset({"instance_id", "process_id", "process_fingerprint"})
_RECEIPT_KEYS = frozenset({"run_id", "attempt_id", "instance_id", "request_fingerprint",
                           "outcome", "evidence_sha256"})


class NativeRunError(RuntimeError):
    code = "native_run_error"

    def __init__(self):
        super().__init__(self.code)


class InvalidNativeRun(NativeRunError):
    code = "invalid_native_run"


class NativeRunDenied(NativeRunError):
    code = "native_run_denied"


class NativeRunConflict(NativeRunError):
    code = "native_run_conflict"


class NativeRunBusy(NativeRunError):
    code = "native_run_busy"


class NativeRunUncertain(NativeRunError):
    code = "native_run_uncertain"


class NativeRunUnavailable(NativeRunError):
    code = "native_run_unavailable"


class NativeRunMigrationMismatch(NativeRunError):
    code = "native_run_migration_mismatch"


class RunState(StrEnum):
    declared = "declared"
    starting = "starting"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    uncertain = "uncertain"


def canonical_uuid(value: object) -> UUID:
    if type(value) is UUID:
        return value
    if type(value) is str:
        try:
            parsed = UUID(value)
        except ValueError:
            pass
        else:
            if value == str(parsed):
                return parsed
    raise InvalidNativeRun()


def sha256(value: object) -> str:
    if type(value) is not str or not _HEX.fullmatch(value):
        raise InvalidNativeRun()
    return value


def principal_id(value: object) -> str:
    if type(value) is not str or not _PRINCIPAL.fullmatch(value) or not value.strip(" "):
        raise InvalidNativeRun()
    return value


@dataclass(frozen=True)
class NativeRunRequest:
    schema_version: int
    principal: str
    project_id: UUID
    project_revision: int
    simulation_id: str
    run_id: UUID
    artifact_sha256: str
    runtime_sha256: str
    platforms: tuple[str, ...]
    seed: int
    max_rounds: int

    @classmethod
    def from_wire(cls, value: object) -> NativeRunRequest:
        if isinstance(value, cls):
            value = asdict(value)
        if not isinstance(value, Mapping) or set(value) != _REQUEST_KEYS:
            raise InvalidNativeRun()
        try:
            platforms = value["platforms"]
            if (type(value["schema_version"]) is not int or value["schema_version"] != 1
                    or type(value["project_revision"]) is not int
                    or not 1 <= value["project_revision"] <= 2_147_483_647
                    or type(value["simulation_id"]) is not str
                    or not _SIMULATION.fullmatch(value["simulation_id"])
                    or type(platforms) not in (tuple, list)
                    or tuple(platforms) not in (("twitter",), ("reddit",), ("twitter", "reddit"))
                    or type(value["seed"]) is not int or not -(2**63) <= value["seed"] < 2**63
                    or type(value["max_rounds"]) is not int or not 1 <= value["max_rounds"] <= 24):
                raise InvalidNativeRun()
            return cls(1, principal_id(value["principal"]), canonical_uuid(value["project_id"]),
                       value["project_revision"], value["simulation_id"], canonical_uuid(value["run_id"]),
                       sha256(value["artifact_sha256"]), sha256(value["runtime_sha256"]),
                       tuple(platforms), value["seed"], value["max_rounds"])
        except (KeyError, TypeError, ValueError, OverflowError):
            raise InvalidNativeRun() from None

    def to_wire(self) -> dict[str, object]:
        value = asdict(self.from_wire(self))
        value["project_id"] = str(self.project_id)
        value["run_id"] = str(self.run_id)
        value["platforms"] = list(self.platforms)
        return value

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(self.to_wire(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class NativeChildIdentity:
    instance_id: UUID
    process_id: int
    process_fingerprint: str

    @classmethod
    def from_wire(cls, value: object) -> NativeChildIdentity:
        if isinstance(value, cls):
            value = asdict(value)
        if not isinstance(value, Mapping) or set(value) != _CHILD_KEYS:
            raise InvalidNativeRun()
        pid = value["process_id"]
        if type(pid) is not int or not 1 <= pid <= 2_147_483_647:
            raise InvalidNativeRun()
        return cls(canonical_uuid(value["instance_id"]), pid, sha256(value["process_fingerprint"]))


@dataclass(frozen=True)
class NativeRunReceipt:
    run_id: UUID
    attempt_id: UUID
    instance_id: UUID
    request_fingerprint: str
    outcome: str
    evidence_sha256: str

    @classmethod
    def from_wire(cls, value: object) -> NativeRunReceipt:
        if isinstance(value, cls):
            value = asdict(value)
        if not isinstance(value, Mapping) or set(value) != _RECEIPT_KEYS:
            raise InvalidNativeRun()
        outcome = value["outcome"]
        if type(outcome) is not str or outcome not in ("completed", "failed", "cancelled"):
            raise InvalidNativeRun()
        return cls(canonical_uuid(value["run_id"]), canonical_uuid(value["attempt_id"]),
                   canonical_uuid(value["instance_id"]), sha256(value["request_fingerprint"]),
                   outcome, sha256(value["evidence_sha256"]))

    def to_wire(self) -> dict[str, str]:
        value = asdict(self)
        for key in ("run_id", "attempt_id", "instance_id"):
            value[key] = str(value[key])
        return value


@dataclass(frozen=True)
class NativeObservation:
    """One bounded driver observation; unknown/absent are never terminal proof."""

    status: str
    receipt: NativeRunReceipt | None = None

    @classmethod
    def validated(cls, value: object) -> NativeObservation:
        if not isinstance(value, cls) or value.status not in (
                "running", "unknown", "absent", "completed", "failed", "cancelled"):
            raise InvalidNativeRun()
        if value.status in ("completed", "failed", "cancelled"):
            receipt = NativeRunReceipt.from_wire(value.receipt)
            if receipt.outcome != value.status:
                raise InvalidNativeRun()
            return cls(value.status, receipt)
        if value.receipt is not None:
            raise InvalidNativeRun()
        return value

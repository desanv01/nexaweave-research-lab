"""Host-bound prepared NativeSimulationSession factory for a spawned child.

The host constructs this object with a trusted picklable model factory. Request
JSON never selects Python modules, executables, credentials or model providers.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

_INPUT_LIMIT = 2 * 1024 * 1024
_OUTPUT_LIMIT = 64 * 1024 * 1024


def _reparse(path: Path) -> bool:
    details = path.lstat()
    return (path.is_symlink()
            or getattr(os.path, "isjunction", lambda _: False)(path)
            or bool(getattr(details, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)))


def _safe_ancestors(path: Path) -> bool:
    return not any(_reparse(item) for item in (path, *path.parents))


def _regular_bound_file(root: Path, name: str, limit: int) -> bytes:
    path = root / name
    components = [root]
    for part in Path(name).parts:
        components.append(components[-1] / part)
    if (any(not _safe_ancestors(item) for item in components)
            or path.resolve(strict=True) != path
            or not path.is_file()):
        raise ValueError("invalid owned native file")
    details = path.stat()
    reparse = (getattr(details, "st_file_attributes", 0)
               & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    if (not stat.S_ISREG(details.st_mode) or details.st_size > limit
            or details.st_size < 1 or reparse):
        raise ValueError("invalid owned native file")
    # Open with no-follow where available, then compare file identity and size.
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_BINARY", 0))
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_size != details.st_size
                or (opened.st_dev, opened.st_ino) != (details.st_dev, details.st_ino)):
            raise ValueError("changed owned native file")
        chunks = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        before_identity = (opened.st_dev, opened.st_ino, opened.st_size,
                           opened.st_mtime_ns, opened.st_ctime_ns)
        after_identity = (after.st_dev, after.st_ino, after.st_size,
                          after.st_mtime_ns, after.st_ctime_ns)
        if len(data) != opened.st_size or after_identity != before_identity:
            raise ValueError("changed owned native file")
        return data
    finally:
        os.close(descriptor)


def _manifest(root: Path, names: tuple[str, ...], limit: int) -> str:
    files = []
    for name in names:
        data = _regular_bound_file(root, name, limit)
        files.append({"name": name, "sha256": hashlib.sha256(data).hexdigest(),
                      "size": len(data)})
    canonical = json.dumps({"schema_version": 1, "files": files},
                           sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class NativeOwnedSessionFactory:
    simulation_dir: str
    graph_id: str
    simulation_id: str
    principal: str
    project_id: str
    project_revision: int
    platforms: tuple[str, ...]
    seed: int
    max_rounds: int
    runtime_sha256: str
    model_factory: object
    timeout_seconds: float = 120.0

    def _root(self) -> Path:
        path = Path(self.simulation_dir)
        if not path.is_absolute() or not _safe_ancestors(path):
            raise ValueError("invalid owned native directory")
        root = path.resolve(strict=True)
        if root != path or not root.is_dir():
            raise ValueError("invalid owned native directory")
        return root

    def validate(self, request) -> None:
        from mirofish_execution.native_run_contracts import NativeRunRequest
        request = NativeRunRequest.from_wire(request)
        if (request.principal != self.principal
                or str(request.project_id) != self.project_id
                or request.project_revision != self.project_revision
                or request.simulation_id != self.simulation_id
                or request.platforms != self.platforms or request.seed != self.seed
                or request.max_rounds != self.max_rounds
                or request.runtime_sha256 != self.runtime_sha256
                or not callable(self.model_factory)):
            raise ValueError("owned native binding mismatch")
        root = self._root()
        names = ("state.json", "simulation_config.json", "source_grounding.json")
        if "twitter" in self.platforms:
            names += ("twitter_profiles.csv",)
        if "reddit" in self.platforms:
            names += ("reddit_profiles.json",)
        if _manifest(root, names, _INPUT_LIMIT) != request.artifact_sha256:
            raise ValueError("owned native manifest mismatch")
        state = json.loads(_regular_bound_file(root, "state.json", _INPUT_LIMIT))
        config = json.loads(_regular_bound_file(root, "simulation_config.json", _INPUT_LIMIT))
        if (type(state) is not dict or type(config) is not dict
                or state.get("status") != "ready"
                or state.get("simulation_id") != self.simulation_id
                or state.get("graph_id") != self.graph_id
                or config.get("simulation_id") != self.simulation_id
                or config.get("graph_id") != self.graph_id
                or tuple(platform for platform in ("twitter", "reddit")
                         if state.get(f"enable_{platform}") is True) != self.platforms):
            raise ValueError("owned native preparation mismatch")
        from mirofish_execution.native_seed_contracts import validate_native_seed_plan
        validate_native_seed_plan(config)

    def create_session(self, request):
        self.validate(request)
        models = self.model_factory()
        if type(models) is not dict or set(models) != set(self.platforms):
            raise ValueError("invalid owned native models")
        from app.services.native_simulation import NativeSimulationSession
        return NativeSimulationSession(
            self.simulation_dir, graph_id=self.graph_id,
            simulation_id=self.simulation_id, models=models, seed=self.seed,
            max_rounds=self.max_rounds, timeout_seconds=self.timeout_seconds)

    def evidence(self, request) -> str:
        root = self._root()
        names = tuple(name for platform in self.platforms for name in (
            f"{platform}_simulation.db", f"{platform}/actions.jsonl"))
        return _manifest(root, names, _OUTPUT_LIMIT)

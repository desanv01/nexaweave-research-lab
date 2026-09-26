"""Bound persisted resource paths beneath a configured storage root.

This is a path validation boundary, not a race-proof filesystem sandbox. A
local actor able to swap symlinks concurrently can still race a later open.
"""

import os
import re
import stat
from pathlib import Path


MAX_RESOURCE_ID_LENGTH = 128
_COMPONENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\Z", re.ASCII)
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.IGNORECASE)


class InvalidResourcePath(ValueError):
    """An input or existing filesystem entry escapes a resource boundary."""


def _is_link_like(path: Path) -> bool:
    """Detect symlinks and Windows reparse points without following them."""
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return False
    except (OSError, RuntimeError) as exc:
        raise InvalidResourcePath("Invalid resource path") from exc
    if stat.S_ISLNK(stat_result.st_mode):
        return True
    reparse = getattr(stat_result, "st_file_attributes", 0)
    return bool(reparse & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def validate_resource_id(value: str) -> str:
    """Accept one bounded, portable ASCII component, including legacy IDs."""
    if (not isinstance(value, str) or not value
            or len(value) > MAX_RESOURCE_ID_LENGTH
            or value in {".", ".."}
            or value.endswith((".", " "))
            or not _COMPONENT.fullmatch(value)
            or _DEVICE.fullmatch(value)):
        raise InvalidResourcePath("Invalid resource path")
    return value


def safe_path(root: str | os.PathLike, *components: str) -> str:
    """Validate every component and existing link before returning a path.

    ``resolve(strict=False)`` resolves existing parents and final targets while
    retaining a missing leaf for creation. Comparing resolved paths catches
    symlink, junction and reparse-point escapes on supported platforms.
    """
    if not components:
        raise InvalidResourcePath("Invalid resource path")
    try:
        trusted_root = Path(root).resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise InvalidResourcePath("Invalid resource path") from exc
    candidate = Path(root)
    for component in components:
        candidate = candidate / validate_resource_id(component)
        if _is_link_like(candidate):
            raise InvalidResourcePath("Invalid resource path")
    return _contained_path(trusted_root, candidate)


def _contained_path(trusted_root: Path, candidate: Path) -> str:
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise InvalidResourcePath("Invalid resource path") from exc
    try:
        common = os.path.commonpath((os.path.normcase(str(trusted_root)), os.path.normcase(str(resolved))))
    except ValueError as exc:
        raise InvalidResourcePath("Invalid resource path") from exc
    if common != os.path.normcase(str(trusted_root)) or resolved == trusted_root:
        raise InvalidResourcePath("Invalid resource path")
    return str(candidate)


def safe_flat_report_path(root: str | os.PathLike, report_id: str, suffix: str) -> str:
    """Bound a legacy flat report filename formed from a valid ID and suffix."""
    validate_resource_id(report_id)
    if suffix not in {".json", ".md"}:
        raise InvalidResourcePath("Invalid resource path")
    try:
        trusted_root = Path(root).resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise InvalidResourcePath("Invalid resource path") from exc
    candidate = Path(root) / f"{report_id}{suffix}"
    if _is_link_like(candidate):
        raise InvalidResourcePath("Invalid resource path")
    return _contained_path(trusted_root, candidate)


def ensure_directory(root: str | os.PathLike, *components: str) -> str:
    """Create a previously validated resource directory explicitly."""
    path = safe_path(root, *components)
    os.makedirs(path, exist_ok=True)
    return safe_path(root, *components)


def validate_tree(root: str | os.PathLike, resource_id: str) -> str:
    """Check existing descendants before a recursive resource deletion."""
    path = safe_path(root, resource_id)
    if os.path.isdir(path):
        for parent, dirs, files in os.walk(path, followlinks=False):
            for name in dirs + files:
                child = Path(parent) / name
                if _is_link_like(child):
                    raise InvalidResourcePath("Invalid resource path")
                try:
                    resolved = child.resolve(strict=False)
                    trusted_root = Path(root).resolve(strict=False)
                except (OSError, RuntimeError) as exc:
                    raise InvalidResourcePath("Invalid resource path") from exc
                try:
                    inside = os.path.commonpath((os.path.normcase(str(trusted_root)), os.path.normcase(str(resolved)))) == os.path.normcase(str(trusted_root))
                except ValueError:
                    inside = False
                if not inside:
                    raise InvalidResourcePath("Invalid resource path")
    return path

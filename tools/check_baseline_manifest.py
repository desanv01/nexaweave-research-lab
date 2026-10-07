"""Verify the 128-file archived NexaWeave snapshot retained by this repository.

Run from any working directory. Reviewed source edits must be recorded in
docs/upstream/patches.json; an absent file means strict byte equality.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/upstream/archive-manifest.json"
EXCEPTIONS = ROOT / "docs/upstream/patches.json"
ARCHIVE_SHA256 = "d3bef0afea92b99626526ffcce0508414feb3f9e88c3edda1f283ce5f447bf53"
EXPECTED_FILES = 128


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def safe_path(value: str) -> Path:
    if not isinstance(value, str):
        raise ValueError("path must be text")
    relative = PurePosixPath(value)
    if (not value or value.startswith("/") or "\\" in value
            or any(part in ("", ".", "..") for part in value.split("/"))
            or relative.is_absolute()):
        raise ValueError(f"unsafe path: {value!r}")
    candidate = ROOT.joinpath(*relative.parts)
    if not candidate.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError(f"path escapes repository: {value!r}")
    return candidate


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    files = manifest["files"]
    if (manifest.get("archive_sha256") != ARCHIVE_SHA256
            or manifest.get("file_count") != EXPECTED_FILES
            or len(files) != EXPECTED_FILES):
        raise ValueError("archive identity or file count changed")

    exceptions: dict[str, dict] = {}
    if EXCEPTIONS.exists():
        data = json.loads(EXCEPTIONS.read_text(encoding="utf-8"))
        entries = data.get("entries")
        if not isinstance(entries, list):
            raise ValueError("exceptions must contain an entries list")
        for entry in entries:
            name = entry["imported_path"]
            safe_path(name)
            if name in exceptions:
                raise ValueError(f"duplicate exception: {name}")
            if not all(entry.get(key) for key in ("original_sha256", "current_sha256", "reason", "phase")):
                raise ValueError(f"incomplete reviewed exception: {name}")
            exceptions[name] = entry

    seen: set[str] = set()
    failures: list[str] = []
    for entry in files:
        name = entry["imported_path"]
        path = safe_path(name)
        if name in seen:
            raise ValueError(f"duplicate manifest path: {name}")
        seen.add(name)
        expected = entry["sha256"]
        exception = exceptions.get(name)
        if exception:
            if exception["original_sha256"] != expected:
                failures.append(f"{name}: exception baseline differs from manifest")
            expected = exception["current_sha256"]
        if not path.is_file() or path.is_symlink():
            failures.append(f"{name}: missing or non-regular file")
        elif path.stat().st_size != entry["size"] and not exception:
            failures.append(f"{name}: size differs from archive")
        elif digest(path) != expected:
            failures.append(f"{name}: SHA-256 differs")
    for name in exceptions.keys() - seen:
        failures.append(f"{name}: exception has no archive entry")

    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print(f"Matched {len(files)} archive entries; {len(exceptions)} recorded patch entries")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"Baseline manifest error: {error}", file=sys.stderr)
        sys.exit(2)

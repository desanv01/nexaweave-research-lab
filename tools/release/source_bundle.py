"""Bounded deterministic, explicitly unqualified committed-source bundles.

Only build invokes read-only Git. Verify never extracts or executes source.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import time
import tomllib
from urllib.parse import urlsplit
import zipfile

MAX_FILES = 4096
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_ARTIFACT_BYTES = 148 * 1024 * 1024
MAX_PACKAGES = 8192
MAX_ARTIFACT_RECORDS = 32768
GIT_TIMEOUT = 15
BUILD_TIMEOUT = 300
ARCHIVE_SHA256 = "d3bef0afea92b99626526ffcce0508414feb3f9e88c3edda1f283ce5f447bf53"
MANIFEST_PATH = ".mirofish-release/manifest.json"
INVENTORY_PATH = ".mirofish-release/dependency-inventory.json"
LOCKS = ("backend/uv.lock", "services/knowledge/uv.lock", "frontend/package-lock.json")
REQUIRED = ("LICENSE", "docs/upstream/import-notes.md",
            "docs/upstream/archive-manifest.json", "docs/upstream/patches.json", *LOCKS)
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class BundleError(ValueError):
    """A fixed safe code, with no caller content or subprocess stderr."""


def fail(code: str) -> None:
    raise BundleError(code)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: object) -> bytes:
    try:
        result = (json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        fail("metadata_invalid")
    if len(result) > MAX_METADATA_BYTES:
        fail("metadata_limit")
    return result


def _pairs(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            fail("json_duplicate")
        result[key] = value
    return result


def parse_json(data: bytes) -> dict:
    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                            parse_constant=lambda _: fail("json_invalid"))
    except (UnicodeError, ValueError, RecursionError):
        fail("json_invalid")
    if not isinstance(result, dict):
        fail("json_invalid")
    return result


def text_field(value: object, limit: int = 256) -> str:
    if (not isinstance(value, str) or not value or len(value.encode("utf-8")) > limit
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        fail("inventory_invalid")
    return value


def safe_name(value: object, *, generated: bool = False) -> str:
    if (not isinstance(value, str) or not value or len(value.encode("utf-8")) > 1024
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or "\\" in value or ":" in value):
        fail("path_invalid")
    parts = value.split("/")
    if len(parts) > 64 or any(p in ("", ".", "..") or p.endswith((".", " ")) for p in parts):
        fail("path_invalid")
    for part in parts:
        if part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            fail("path_invalid")
    if parts[0].casefold() == ".mirofish-release":
        if generated and value in (MANIFEST_PATH, INVENTORY_PATH):
            return value
        fail("reserved_path")
    denied = {".git", "node_modules", ".venv", "venv", "__pycache__", "uploads", "data",
              "test-results", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "coverage"}
    if any(p.casefold() in denied for p in parts):
        fail("source_excluded")
    name = parts[-1].casefold()
    if ((name == ".env" or name.startswith(".env.")) and name != ".env.example"
            or name.endswith((".pem", ".key", ".db", ".sqlite", ".sqlite3", ".log", ".pt", ".pth", ".gguf", ".safetensors", ".onnx", ".ckpt", ".tflite"))):
        fail("source_excluded")
    return value


def _unique_names(names: list[str]) -> None:
    folded = set()
    for name in names:
        safe_name(name, generated=True)
        key = name.casefold()
        if key in folded:
            fail("path_collision")
        folded.add(key)
    # A file cannot also be an ancestor of another file.
    for name in names:
        parts = name.split("/")
        if any("/".join(parts[:i]).casefold() in folded for i in range(1, len(parts))):
            fail("path_collision")


def trusted_path(value: str | Path, *, directory: bool = False, new: bool = False) -> Path:
    path = Path(value)
    if not path.is_absolute() or str(path) != os.path.normpath(str(path)) or ".." in path.parts:
        fail("artifact_path_invalid")
    chain = list(reversed(path.parents)) + [path]
    for candidate in chain:
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            if candidate == path and new:
                continue
            fail("artifact_path_invalid")
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            fail("artifact_path_link")
        if candidate != path and not stat.S_ISDIR(info.st_mode):
            fail("artifact_path_invalid")
        if candidate == path:
            if new:
                fail("output_exists")
            if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
                fail("artifact_path_invalid")
    if str(path.resolve()) != str(path):
        fail("artifact_path_invalid")
    return path


class _Git:
    def __init__(self, root: Path):
        self.root = root
        self.deadline = time.monotonic() + BUILD_TIMEOUT

    def read(self, args: list[str], cap: int) -> bytes:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            fail("git_timeout")
        env = {k: v for k, v in os.environ.items() if k.upper() in
               {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT"}}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0",
                   GIT_NO_REPLACE_OBJECTS="1", LC_ALL="C")
        argv = ["git", "--no-pager", "-c", "core.fsmonitor=false", "-c",
                "core.hooksPath=" + os.devnull, "-c", "credential.helper=", "-C", str(self.root), *args]
        chunks = []
        errors = []
        try:
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       stdin=subprocess.DEVNULL, env=env, shell=False)
        except OSError:
            fail("git_failed")

        def reader() -> None:
            try:
                count = 0
                while True:
                    block = process.stdout.read(min(65536, cap + 1 - count))
                    if not block:
                        break
                    chunks.append(block)
                    count += len(block)
                    if count > cap:
                        errors.append("git_output_limit")
                        process.kill()
                        break
            except OSError:
                errors.append("git_failed")

        thread = threading.Thread(target=reader, daemon=True)
        try:
            thread.start()
            process.wait(timeout=min(GIT_TIMEOUT, remaining))
            thread.join(timeout=1)
            if thread.is_alive():
                fail("git_timeout")
            if errors:
                fail(errors[0])
            if process.returncode:
                fail("git_failed")
            return b"".join(chunks)
        except subprocess.TimeoutExpired:
            fail("git_timeout")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
            process.stdout.close()
            if thread.ident is not None:
                thread.join(timeout=1)

    def clean(self, revision: str) -> None:
        if self.read(["rev-parse", "--verify", "HEAD"], 128) != (revision + "\n").encode():
            fail("revision_mismatch")
        if self.read(["status", "--porcelain=v1", "-uno", "--ignore-submodules=all"], 2 * 1024 * 1024):
            fail("tracked_dirty")


def baseline(files: dict[str, tuple[str, bytes]]) -> None:
    if any(name not in files for name in REQUIRED):
        fail("required_missing")
    manifest = parse_json(files[REQUIRED[2]][1])
    entries = manifest.get("files")
    if (manifest.get("archive_sha256") != ARCHIVE_SHA256 or type(manifest.get("file_count")) is not int
            or manifest["file_count"] != 128 or not isinstance(entries, list) or len(entries) != 128):
        fail("baseline_identity")
    patches = parse_json(files[REQUIRED[3]][1]).get("entries")
    if not isinstance(patches, list) or len(patches) > 128:
        fail("patch_invalid")
    exceptions = {}
    for entry in patches:
        if not isinstance(entry, dict):
            fail("patch_invalid")
        name = safe_name(entry.get("imported_path"))
        if name in exceptions or not all(entry.get(k) for k in ("reason", "phase")):
            fail("patch_invalid")
        if not all(isinstance(entry.get(k), str) and HEX64.fullmatch(entry[k]) for k in ("original_sha256", "current_sha256")):
            fail("patch_invalid")
        exceptions[name] = entry
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            fail("baseline_invalid")
        safe_name(entry.get("path"))
        name = safe_name(entry.get("imported_path"))
        original = entry.get("sha256")
        size = entry.get("size")
        if name in seen or name not in files or not isinstance(original, str) or not HEX64.fullmatch(original) or type(size) is not int or size < 0 or size > MAX_FILE_BYTES:
            fail("baseline_invalid")
        seen.add(name)
        patch = exceptions.get(name)
        if patch and patch["original_sha256"] != original:
            fail("baseline_mismatch")
        data = files[name][1]
        if digest(data) != (patch["current_sha256"] if patch else original) or (not patch and len(data) != size):
            fail("baseline_mismatch")
    if exceptions.keys() - seen:
        fail("patch_invalid")


def _locator(value: object) -> tuple[str | None, str | None]:
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None, "unsafe_or_unbounded_locator"
    try:
        url = urlsplit(value)
        if url.scheme in ("https", "http") and url.hostname and not url.username and not url.password and not url.query and not url.fragment:
            return value, None
        if value == "." or (not url.scheme and not value.startswith("/")):
            safe_name(value)
            return value, None
    except (ValueError, BundleError):
        pass
    return None, "unsafe_or_unbounded_locator"


def inventory(files: dict[str, tuple[str, bytes]]) -> dict:
    locks = []
    packages = []
    artifact_count = 0
    for name in LOCKS:
        data = files[name][1]
        locks.append({"path": name, "sha256": digest(data)})
        try:
            lock = parse_json(data) if name.endswith(".json") else tomllib.loads(data.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            fail("inventory_invalid")
        if name.endswith(".json"):
            if type(lock.get("lockfileVersion")) is not int or lock["lockfileVersion"] not in (2, 3) or not isinstance(lock.get("packages"), dict):
                fail("inventory_invalid")
            records = list(lock["packages"].items())
        else:
            if type(lock.get("version")) is not int or lock["version"] != 1 or not isinstance(lock.get("package"), list):
                fail("inventory_invalid")
            records = [(None, p) for p in lock["package"]]
        if len(records) > MAX_PACKAGES or len(packages) + len(records) > MAX_PACKAGES:
            fail("inventory_limit")
        seen = set()
        for location, record in records:
            if not isinstance(record, dict):
                fail("inventory_invalid")
            if location is not None:
                if location:
                    # Lock package keys are identities, never exported filesystem paths.
                    text_field(location, 1024)
                    if not all(p not in ("", ".", "..") for p in location.split("/")) or "\\" in location or ":" in location:
                        fail("inventory_invalid")
                package_name = record.get("name") or (location.rsplit("node_modules/", 1)[-1] if location else lock.get("name"))
                source = {"resolved": record["resolved"]} if "resolved" in record else {}
                hashes = [text_field(record["integrity"], 1024)] if "integrity" in record else []
                artifacts = []
            else:
                package_name = record.get("name")
                source = record.get("source")
                if not isinstance(source, dict) or not source or len(source) > 4:
                    fail("inventory_invalid")
                wheels = record.get("wheels", [])
                if not isinstance(wheels, list) or len(wheels) > 4096:
                    fail("inventory_limit")
                artifacts = list(wheels)
                if "sdist" in record:
                    artifacts.append(record["sdist"])
                hashes = []
            version = text_field(record.get("version"))
            package_name = text_field(package_name)
            identity = (package_name, version, canonical_json(source), location)
            if identity in seen:
                fail("inventory_duplicate")
            seen.add(identity)
            locators = []
            omissions = []
            for kind, value in sorted(source.items()):
                if kind not in {"registry", "virtual", "editable", "directory", "git", "url", "resolved"}:
                    fail("inventory_invalid")
                locator, reason = _locator(value)
                if locator is not None:
                    locators.append({"kind": kind, "value": locator})
                else:
                    omissions.append({"kind": kind, "reason": reason})
            artifact_count += len(artifacts) + len(hashes)
            if artifact_count > MAX_ARTIFACT_RECORDS:
                fail("inventory_limit")
            for artifact in artifacts:
                if not isinstance(artifact, dict) or "hash" not in artifact:
                    fail("inventory_invalid")
                hash_value = text_field(artifact["hash"], 1024)
                if not re.fullmatch(r"sha256:[0-9a-f]{64}", hash_value):
                    fail("inventory_invalid")
                hashes.append(hash_value)
            if location is not None and any(not re.fullmatch(r"sha(?:256|384|512)-[A-Za-z0-9+/=]+", h) for h in hashes):
                fail("inventory_invalid")
            packages.append({"lock_path": name, "ecosystem": "npm" if location is not None else "pypi",
                             "name": package_name, "version": version, "lock_package_path": location,
                             "source_locators": locators, "locator_omissions": omissions,
                             "artifact_hashes": sorted(set(hashes)), "license": "NOASSERTION",
                             "installed_platform_status": "unknown"})
    packages.sort(key=lambda p: (p["lock_path"], p["name"], p["version"], p["lock_package_path"] or "", canonical_json(p)))
    return {"schema_version": 1, "inventory_kind": "lock-derived", "complete_sbom": False,
            "active_dependency_closure": "unknown", "locks": locks, "packages": packages}


def _tree_id(files: dict[str, tuple[str, bytes]]) -> str:
    """Reconstruct the SHA-1 Git tree without Git or filesystem extraction."""
    root = {}
    for name, (mode, data) in files.items():
        node = root
        parts = name.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).digest()
        node[parts[-1]] = (mode, blob)

    def tree_hash(node: dict) -> bytes:
        records = []
        for name, value in node.items():
            encoded = name.encode("utf-8")
            if isinstance(value, dict):
                records.append((encoded + b"/", b"40000 " + encoded + b"\0" + tree_hash(value)))
            else:
                mode, oid = value
                records.append((encoded, mode.encode() + b" " + encoded + b"\0" + oid))
        data = b"".join(record for _, record in sorted(records))
        return hashlib.sha1(b"tree " + str(len(data)).encode() + b"\0" + data).digest()

    return tree_hash(root).hex()


def _metadata(files: dict[str, tuple[str, bytes]], revision: str, tree: str) -> tuple[bytes, bytes]:
    if _tree_id(files) != tree:
        fail("git_tree_mismatch")
    baseline(files)
    inv = canonical_json(inventory(files))
    manifest = canonical_json({"schema_version": 1, "source_revision": revision, "git_tree_id": tree,
                               "artifact_kind": "source", "qualified_release": False, "all44_accepted": False,
                               "untracked_ignored_material": "excluded_not_audited",
                               "inventory_sha256": digest(inv), "source_file_count": len(files),
                               "source_total_bytes": sum(len(data) for _, data in files.values()),
                               "files": [{"path": name, "mode": mode, "size": len(data), "sha256": digest(data)}
                                         for name, (mode, data) in sorted(files.items())]})
    return manifest, inv


def _zip(files: dict[str, tuple[str, bytes]], metadata: tuple[bytes, bytes]) -> bytes:
    entries = dict(files)
    entries[MANIFEST_PATH] = ("100644", metadata[0])
    entries[INVENTORY_PATH] = ("100644", metadata[1])
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        for name, (mode, data) in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = int(mode, 8) << 16
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, data)
    result = stream.getvalue()
    if len(result) > MAX_ARTIFACT_BYTES:
        fail("artifact_limit")
    return result


def _report(operation: str, sha: str, revision: str, files: dict) -> dict:
    return {"operation": operation, "artifact_sha256": sha, "source_revision": revision,
            "source_file_count": len(files), "source_total_bytes": sum(len(data) for _, data in files.values()),
            "artifact_kind": "source", "qualified_release": False, "all44_accepted": False,
            "untracked_ignored_material": "excluded_not_audited"}


def build_bundle(repository: str | Path, revision: str, output: str | Path) -> dict:
    if not isinstance(revision, str) or not HEX40.fullmatch(revision):
        fail("revision_invalid")
    root = trusted_path(repository, directory=True)
    target = trusted_path(output, new=True)
    git = _Git(root)
    try:
        actual_root = git.read(["rev-parse", "--show-toplevel"], 4096).decode("utf-8").rstrip("\n")
        if Path(actual_root) != root:
            fail("repository_root_mismatch")
    except UnicodeError:
        fail("repository_root_mismatch")
    git.clean(revision)
    tree_raw = git.read(["rev-parse", "--verify", revision + "^{tree}"], 128)
    try:
        tree = tree_raw.decode("ascii").strip()
        if not HEX40.fullmatch(tree):
            fail("git_tree_invalid")
        raw = git.read(["ls-tree", "-rlz", "--full-tree", revision], 2 * 1024 * 1024)
        records = raw.split(b"\0")
        if records[-1] != b"" or len(records) - 1 > MAX_FILES:
            fail("source_count_limit")
        admitted = []
        total = 0
        for record in records[:-1]:
            header, name_raw = record.split(b"\t", 1)
            mode, kind, oid, size_raw = header.split()
            name = safe_name(name_raw.decode("utf-8"))
            if mode not in (b"100644", b"100755") or kind != b"blob" or not HEX40.fullmatch(oid.decode("ascii")):
                fail("source_mode_invalid")
            size = int(size_raw)
            total += size
            if size < 0 or size > MAX_FILE_BYTES or total > MAX_SOURCE_BYTES:
                fail("source_size_limit")
            admitted.append((name, mode.decode(), oid.decode(), size))
        _unique_names([r[0] for r in admitted])
    except BundleError:
        raise
    except (UnicodeError, ValueError):
        fail("git_tree_invalid")
    files = {}
    for name, mode, oid, size in admitted:
        data = git.read(["cat-file", "blob", oid], size)
        if len(data) != size:
            fail("git_blob_invalid")
        files[name] = (mode, data)
    artifact = _zip(files, _metadata(files, revision, tree))
    git.clean(revision)
    trusted_path(target, new=True)
    descriptor = None
    owned = None
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        owned = os.fstat(descriptor)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(artifact)
            stream.flush()
            os.fsync(stream.fileno())
        return _report("build", digest(artifact), revision, files)
    except FileExistsError:
        fail("output_exists")
    except OSError:
        fail("output_failed")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        # Cleanup only our inode after a failed write; never a preexisting/replaced path.
        if owned is not None and sys.exc_info()[0] is not None:
            try:
                current = target.lstat()
                if (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino) and stat.S_ISREG(current.st_mode):
                    target.unlink()
            except OSError:
                pass


def verify_bundle(artifact: str | Path, expected_sha256: str, revision: str) -> dict:
    if not isinstance(revision, str) or not HEX40.fullmatch(revision):
        fail("revision_invalid")
    if not isinstance(expected_sha256, str) or not HEX64.fullmatch(expected_sha256):
        fail("hash_invalid")
    path = trusted_path(artifact)
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        fail("artifact_limit")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            fail("artifact_path_invalid")
        data = stream.read(MAX_ARTIFACT_BYTES + 1)
    if len(data) > MAX_ARTIFACT_BYTES:
        fail("artifact_limit")
    if digest(data) != expected_sha256:
        fail("artifact_hash_mismatch")
    files = {}
    generated = {}
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES + 2:
                fail("source_count_limit")
            _unique_names([e.filename for e in entries])
            for entry in entries:
                name = safe_name(entry.filename, generated=True)
                mode = entry.external_attr >> 16
                if entry.compress_type != zipfile.ZIP_STORED or entry.flag_bits & 1 or mode not in (0o100644, 0o100755):
                    fail("zip_entry_invalid")
                is_generated = name in (MANIFEST_PATH, INVENTORY_PATH)
                cap = MAX_METADATA_BYTES if is_generated else MAX_FILE_BYTES
                if entry.file_size > cap or entry.compress_size != entry.file_size:
                    fail("source_size_limit")
                if not is_generated:
                    total += entry.file_size
                    if total > MAX_SOURCE_BYTES:
                        fail("source_size_limit")
                with archive.open(entry) as stream:
                    payload = stream.read(cap + 1)
                if len(payload) != entry.file_size:
                    fail("zip_entry_invalid")
                if is_generated:
                    generated[name] = payload
                else:
                    files[name] = (format(mode, "06o"), payload)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, OSError, UnicodeError):
        fail("zip_invalid")
    if set(generated) != {MANIFEST_PATH, INVENTORY_PATH}:
        fail("metadata_missing")
    manifest = parse_json(generated[MANIFEST_PATH])
    tree = manifest.get("git_tree_id")
    if not isinstance(tree, str) or not HEX40.fullmatch(tree):
        fail("metadata_invalid")
    metadata = _metadata(files, revision, tree)
    if metadata != (generated[MANIFEST_PATH], generated[INVENTORY_PATH]):
        fail("metadata_mismatch")
    if _zip(files, metadata) != data:
        fail("zip_noncanonical")
    return _report("verify", expected_sha256, revision, files)

"""Main-executed local Git fixtures; synthetic baseline is not archive proof."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.release import source_bundle as bundle


def encoded(value):
    return (json.dumps(value, sort_keys=True) + "\n").encode()


class Repo:
    """All commands are authored fixtures for Main, never worker execution."""
    def __init__(self, folder):
        self.path = folder / "repo"
        self.path.mkdir()
        self.env = {k: v for k, v in os.environ.items() if k.upper() in
                    {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT"}}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_TERMINAL_PROMPT="0", GIT_AUTHOR_NAME="Fixture",
                        GIT_AUTHOR_EMAIL="fixture@example.invalid",
                        GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid",
                        GIT_AUTHOR_DATE="2026-01-01T00:00:00Z", GIT_COMMITTER_DATE="2026-01-01T00:00:00Z")
        self.git("init", "--quiet")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "core.filemode", "true")
        self.files = {"LICENSE": b"Synthetic fixture notice; not the real archive.\n",
                      "docs/upstream/README.md.reference": b"Synthetic README notice\n",
                      "docs/upstream/workflows/docker-image.yml.reference": b"Synthetic inert workflow\n"}
        self.files.update({f"baseline/f{i:03}.txt": f"synthetic {i}\n".encode() for i in range(125)})
        baseline = [{"path": name, "imported_path": name, "size": len(data),
                     "sha256": hashlib.sha256(data).hexdigest()} for name, data in sorted(self.files.items())]
        self.files.update({
            "docs/upstream/archive-manifest.json": encoded({"archive_sha256": bundle.ARCHIVE_SHA256,
                                                           "file_count": 128, "files": baseline}),
            "docs/upstream/patches.json": encoded({"schema_version": 1, "entries": []}),
            "docs/upstream/import-notes.md": b"Synthetic fixture; original archive unavailable here.\n",
            "backend/uv.lock": b'version = 1\n[[package]]\nname = "fixture"\nversion = "1.0"\nsource = {registry = "https://pypi.org/simple"}\n',
            "services/knowledge/uv.lock": b'version = 1\n[[package]]\nname = "local"\nversion = "0.1"\nsource = {virtual = "."}\n',
            "frontend/package-lock.json": encoded({"name": "fixture-ui", "lockfileVersion": 3, "packages": {
                "": {"name": "fixture-ui", "version": "1"},
                "node_modules/pkg": {"version": "2", "resolved": "https://registry.npmjs.org/pkg/-/pkg.tgz",
                                     "integrity": "sha512-YWJjZA==", "license": "IGNORED"}}}),
            ".gitignore": b"ignored.txt\n",
            ".env.example": b"TOKEN=\n",
            "src/hello.py": b'print("committed")\n',
        })
        for name, data in self.files.items():
            self.write(name, data)
        self.commit()

    def git(self, *args, input=None):
        result = subprocess.run(["git", "--no-pager", "-c", "core.hooksPath=" + os.devnull,
                                 "-C", str(self.path), *args], input=input, env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=True)
        return result.stdout.decode().strip()

    def write(self, name, data):
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def commit(self):
        self.git("add", "--all")
        self.git("commit", "--quiet", "--allow-empty", "-m", "synthetic")
        self.revision = self.git("rev-parse", "HEAD")

    def indexed(self, mode, name, oid=None):
        if oid is None:
            oid = self.git("hash-object", "-w", "--stdin", input=b"fixture-object")
        self.git("update-index", "--add", "--cacheinfo", mode, oid, name)
        self.git("commit", "--quiet", "-m", "synthetic index")
        self.revision = self.git("rev-parse", "HEAD")
        # Index-only regular paths must have matching working bytes for clean admission.
        if mode in ("100644", "100755"):
            self.write(name, b"fixture-object")
            if mode == "100755" and os.name != "nt":
                (self.path / name).chmod(0o755)


class SourceBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="u14-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.repo = Repo(self.folder)
        self.output = self.folder / "source.zip"

    def build(self, output=None):
        return bundle.build_bundle(self.repo.path, self.repo.revision, output or self.output)

    def rejects(self, code, function, *args):
        with self.assertRaises(bundle.BundleError) as caught:
            function(*args)
        self.assertEqual(str(caught.exception), code)

    def repack(self, mutate):
        """Tamper independently of the implementation's archive writer."""
        self.build()
        with zipfile.ZipFile(self.output) as archive:
            entries = [(info, archive.read(info)) for info in archive.infolist()]
        entries = mutate(entries)
        modified = self.folder / "tampered.zip"
        with zipfile.ZipFile(modified, "w") as archive:
            for info, data in entries:
                archive.writestr(info, data)
        return modified, hashlib.sha256(modified.read_bytes()).hexdigest()

    def test_exact_blobs_and_determinism(self):
        self.repo.write("untracked-secret.txt", b"do not ship")
        self.repo.write("ignored.txt", b"do not ship")
        before = self.repo.git("rev-parse", "HEAD")
        original = dict(self.repo.files)
        first = self.build()
        second = self.build(self.folder / "second.zip")
        self.assertEqual(first["artifact_sha256"], second["artifact_sha256"])
        self.assertEqual(self.output.read_bytes(), (self.folder / "second.zip").read_bytes())
        with zipfile.ZipFile(self.output) as archive:
            self.assertNotIn("untracked-secret.txt", archive.namelist())
            self.assertNotIn("ignored.txt", archive.namelist())
            for name, content in original.items():
                self.assertEqual(archive.read(name), content)
            manifest = json.loads(archive.read(bundle.MANIFEST_PATH))
            self.assertEqual(manifest["git_tree_id"], self.repo.git("rev-parse", "HEAD^{tree}"))
            self.assertFalse(manifest["qualified_release"])
            self.assertFalse(manifest["all44_accepted"])
            inventory = json.loads(archive.read(bundle.INVENTORY_PATH))
            self.assertTrue(all(p["license"] == "NOASSERTION" for p in inventory["packages"]))
        result = bundle.verify_bundle(self.output, first["artifact_sha256"], self.repo.revision)
        self.assertEqual(result["source_file_count"], len(original))
        self.assertEqual(self.repo.git("rev-parse", "HEAD"), before)
        for name, data in original.items():
            self.assertEqual((self.repo.path / name).read_bytes(), data)

    def test_tracked_worktree_dirty(self):
        self.repo.write("src/hello.py", b"dirty")
        self.rejects("tracked_dirty", self.build)
        self.assertFalse(self.output.exists())

    def test_actual_linked_worktree(self):
        linked = self.folder / "linked-worktree"
        self.repo.git("worktree", "add", "--quiet", "--detach", str(linked), self.repo.revision)
        self.assertTrue((linked / ".git").is_file())
        first = self.build()
        output = self.folder / "linked.zip"
        second = bundle.build_bundle(linked, self.repo.revision, output)
        self.assertEqual(first["artifact_sha256"], second["artifact_sha256"])
        bundle.verify_bundle(output, second["artifact_sha256"], self.repo.revision)
        self.assertTrue((linked / ".git").is_file())

    def test_tracked_index_dirty(self):
        self.repo.write("src/hello.py", b"staged")
        self.repo.git("add", "src/hello.py")
        self.rejects("tracked_dirty", self.build)

    def test_tracked_deleted(self):
        (self.repo.path / "src/hello.py").unlink()
        self.rejects("tracked_dirty", self.build)

    def test_wrong_revision(self):
        self.rejects("revision_mismatch", bundle.build_bundle, self.repo.path, "0" * 40, self.output)

    def test_revision_required_canonical(self):
        for revision in ("HEAD", self.repo.revision.upper(), "", "a" * 39):
            with self.subTest(revision=revision):
                self.rejects("revision_invalid", bundle.build_bundle, self.repo.path, revision, self.output)

    def test_root_must_be_repository_root(self):
        self.rejects("repository_root_mismatch", bundle.build_bundle,
                     self.repo.path / "src", self.repo.revision, self.output)

    def test_committed_sensitive_paths(self):
        for name in (".env", "src/.env.production", "src/key.pem", "src/key.key", "a.sqlite",
                     "a.db", "run.log", "uploads/a.txt", "data/a.txt", "node_modules/pkg/a",
                     ".venv/a", "__pycache__/a", "test-results/a", "model.gguf"):
            with self.subTest(name=name):
                self.repo.write(name, b"excluded")
                self.repo.commit()
                self.rejects("source_excluded", self.build)
                (self.repo.path / name).unlink()
                self.repo.commit()

    def test_unsafe_git_names(self):
        # Slash/colon/control names are admitted by Git on POSIX but never by this tool.
        if os.name == "nt":
            self.skipTest("POSIX filesystem names required; Main hosted Linux must qualify")
        for name in ("src/a:b", "src/a\\b", "src/a\nb", "src/CON", "src/trailing."):
            with self.subTest(name=name):
                self.repo.indexed("100644", name)
                self.rejects("path_invalid", self.build)
                (self.repo.path / name).unlink()
                self.repo.commit()

    def test_reserved_generated_path(self):
        self.repo.write(bundle.MANIFEST_PATH, b"fake")
        self.repo.commit()
        self.rejects("reserved_path", self.build)

    def test_casefold_collision(self):
        if os.name == "nt":
            self.skipTest("POSIX case-sensitive filesystem required")
        self.repo.write("src/HELLO.py", b"collision")
        self.repo.commit()
        self.rejects("path_collision", self.build)

    def test_symlink_mode(self):
        if os.name == "nt":
            self.skipTest("POSIX link fixture; Main hosted Linux must qualify")
        os.symlink("hello.py", self.repo.path / "src/link")
        self.repo.commit()
        self.rejects("source_mode_invalid", self.build)

    def test_submodule_mode(self):
        self.repo.indexed("160000", "submodule", self.repo.revision)
        self.rejects("source_mode_invalid", self.build)

    def test_executable_mode_preserved(self):
        if os.name == "nt":
            self.skipTest("POSIX executable fixture")
        self.repo.write("src/run", b"fixture-object")
        (self.repo.path / "src/run").chmod(0o755)
        self.repo.commit()
        report = self.build()
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.getinfo("src/run").external_attr >> 16, 0o100755)
        bundle.verify_bundle(self.output, report["artifact_sha256"], self.repo.revision)

    def test_source_file_count_bound(self):
        with patch.object(bundle, "MAX_FILES", len(self.repo.files) - 1):
            self.rejects("source_count_limit", self.build)

    def test_source_per_file_bound(self):
        with patch.object(bundle, "MAX_FILE_BYTES", 1):
            self.rejects("source_size_limit", self.build)

    def test_total_source_bound(self):
        with patch.object(bundle, "MAX_SOURCE_BYTES", 1):
            self.rejects("source_size_limit", self.build)

    def test_artifact_bound(self):
        with patch.object(bundle, "MAX_ARTIFACT_BYTES", 1):
            self.rejects("artifact_limit", self.build)
        self.assertFalse(self.output.exists())

    def test_metadata_bound(self):
        with patch.object(bundle, "MAX_METADATA_BYTES", 16):
            self.rejects("metadata_limit", self.build)

    def test_git_output_bound(self):
        self.rejects("git_output_limit", bundle._Git(self.repo.path).read, ["rev-parse", "HEAD"], 1)

    def test_git_deadline(self):
        git = bundle._Git(self.repo.path)
        git.deadline = 0
        self.rejects("git_timeout", git.read, ["rev-parse", "HEAD"], 128)

    def test_required_files_missing(self):
        for name in bundle.REQUIRED:
            with self.subTest(name=name):
                data = (self.repo.path / name).read_bytes()
                (self.repo.path / name).unlink()
                self.repo.commit()
                self.rejects("required_missing", self.build)
                self.repo.write(name, data)
                self.repo.commit()

    def test_inherited_reference_missing(self):
        (self.repo.path / "docs/upstream/README.md.reference").unlink()
        self.repo.commit()
        self.rejects("baseline_invalid", self.build)

    def test_baseline_duplicate(self):
        name = "docs/upstream/archive-manifest.json"
        value = json.loads(self.repo.files[name])
        value["files"][-1] = value["files"][0]
        self.repo.write(name, encoded(value))
        self.repo.commit()
        self.rejects("baseline_invalid", self.build)

    def test_head_changed_during_build(self):
        original_clean = bundle._Git.clean
        calls = []

        def changed_head(git, revision):
            calls.append(revision)
            if len(calls) == 2:
                self.repo.git("commit", "--quiet", "--allow-empty", "-m", "concurrent")
            original_clean(git, revision)

        with patch.object(bundle._Git, "clean", changed_head):
            self.rejects("revision_mismatch", self.build)
        self.assertFalse(self.output.exists())

    def test_baseline_identity(self):
        name = "docs/upstream/archive-manifest.json"
        value = json.loads(self.repo.files[name])
        value["archive_sha256"] = "0" * 64
        self.repo.write(name, encoded(value))
        self.repo.commit()
        self.rejects("baseline_identity", self.build)

    def test_baseline_size_and_hash(self):
        self.repo.write("baseline/f000.txt", b"changed")
        self.repo.commit()
        self.rejects("baseline_mismatch", self.build)

    def test_patch_original_and_current_hash(self):
        path = "baseline/f000.txt"
        old = self.repo.files[path]
        current = b"reviewed synthetic patch"
        entry = {"imported_path": path, "original_sha256": hashlib.sha256(old).hexdigest(),
                 "current_sha256": hashlib.sha256(current).hexdigest(), "reason": "fixture", "phase": "fixture"}
        self.repo.write(path, current)
        for key in ("original_sha256", "current_sha256"):
            bad = dict(entry, **{key: "0" * 64})
            self.repo.write("docs/upstream/patches.json", encoded({"entries": [bad]}))
            self.repo.commit()
            self.rejects("baseline_mismatch", self.build)
        self.repo.write("docs/upstream/patches.json", encoded({"entries": [entry]}))
        self.repo.commit()
        report = self.build()
        bundle.verify_bundle(self.output, report["artifact_sha256"], self.repo.revision)

    def test_duplicate_and_orphan_patch(self):
        entry = {"imported_path": "src/hello.py", "original_sha256": "0" * 64,
                 "current_sha256": "1" * 64, "reason": "fixture", "phase": "fixture"}
        for entries in ([entry], [entry, entry]):
            self.repo.write("docs/upstream/patches.json", encoded({"entries": entries}))
            self.repo.commit()
            self.rejects("patch_invalid", self.build)

    def test_malformed_locks(self):
        for name, data in (("backend/uv.lock", b'version=1\nversion=1\n'),
                           ("backend/uv.lock", b'version=1\npackage="no"\n'),
                           ("frontend/package-lock.json", b'{"packages":{},"packages":{}}'),
                           ("frontend/package-lock.json", b'{"lockfileVersion":3,"packages":[]}')):
            with self.subTest(name=name, data=data):
                previous = (self.repo.path / name).read_bytes()
                self.repo.write(name, data)
                self.repo.commit()
                with self.assertRaises(bundle.BundleError):
                    self.build()
                self.repo.write(name, previous)
                self.repo.commit()

    def test_inventory_package_artifact_limits(self):
        with patch.object(bundle, "MAX_PACKAGES", 1):
            self.rejects("inventory_limit", self.build)
        with patch.object(bundle, "MAX_ARTIFACT_RECORDS", 0):
            self.rejects("inventory_limit", self.build)

    def test_duplicate_uv_records(self):
        name = "backend/uv.lock"
        data = self.repo.files[name]
        self.repo.write(name, data + data[data.index(b"[[package]]"):])
        self.repo.commit()
        self.rejects("inventory_duplicate", self.build)

    def test_locator_credentials_omitted(self):
        name = "backend/uv.lock"
        self.repo.write(name, self.repo.files[name].replace(b"https://pypi.org/simple", b"https://user:SUPERSECRET@example.invalid/simple?token=SECRET"))
        self.repo.commit()
        self.build()
        with zipfile.ZipFile(self.output) as archive:
            inventory = archive.read(bundle.INVENTORY_PATH)
            self.assertNotIn(b"SUPERSECRET", inventory)
            self.assertNotIn(b"user:", inventory)
            self.assertIn(b"unsafe_or_unbounded_locator", inventory)
            # Original lock is deliberately preserved: admission is not a content secret scan.
            self.assertIn(b"SUPERSECRET", archive.read(name))

    def test_no_overwrite(self):
        self.output.write_bytes(b"preexisting")
        self.rejects("output_exists", self.build)
        self.assertEqual(self.output.read_bytes(), b"preexisting")

    def test_output_parent_missing(self):
        self.rejects("artifact_path_invalid", self.build, self.folder / "missing" / "source.zip")

    def test_output_link(self):
        victim = self.folder / "victim"
        victim.write_bytes(b"preexisting")
        try:
            os.symlink(victim, self.output)
        except OSError:
            self.skipTest("link privilege unavailable; Main hosted Linux must qualify")
        self.rejects("artifact_path_link", self.build)
        self.assertEqual(victim.read_bytes(), b"preexisting")

    def test_output_ancestor_link(self):
        link = self.folder / "linked"
        try:
            os.symlink(self.folder, link, target_is_directory=True)
        except OSError:
            self.skipTest("directory link privilege unavailable")
        self.rejects("artifact_path_link", self.build, link / "source.zip")

    def test_owned_failure_cleanup(self):
        with patch.object(bundle.os, "fsync", side_effect=OSError("write failed")):
            self.rejects("output_failed", self.build)
        self.assertFalse(self.output.exists())
        self.assertTrue((self.repo.path / "LICENSE").is_file())

    def test_create_race_preserves_preexisting(self):
        real_open = os.open

        def raced_open(path, flags, mode=0o777):
            if Path(path) == self.output:
                self.output.write_bytes(b"raced-preexisting")
            return real_open(path, flags, mode)

        with patch.object(bundle.os, "open", side_effect=raced_open):
            self.rejects("output_exists", self.build)
        self.assertEqual(self.output.read_bytes(), b"raced-preexisting")

    def test_verify_hash_and_revision(self):
        report = self.build()
        self.rejects("artifact_hash_mismatch", bundle.verify_bundle, self.output, "0" * 64, self.repo.revision)
        self.rejects("metadata_mismatch", bundle.verify_bundle, self.output, report["artifact_sha256"], "0" * 40)
        self.rejects("hash_invalid", bundle.verify_bundle, self.output, "guess", self.repo.revision)

    def test_verify_has_no_git(self):
        report = self.build()
        with patch.object(bundle.subprocess, "Popen", side_effect=AssertionError("no subprocess")):
            bundle.verify_bundle(self.output, report["artifact_sha256"], self.repo.revision)

    def test_verify_artifact_limit(self):
        report = self.build()
        with patch.object(bundle, "MAX_ARTIFACT_BYTES", 1):
            self.rejects("artifact_limit", bundle.verify_bundle, self.output, report["artifact_sha256"], self.repo.revision)

    def test_verify_entry_and_total_bounds(self):
        report = self.build()
        for constant in ("MAX_FILES", "MAX_FILE_BYTES", "MAX_SOURCE_BYTES", "MAX_METADATA_BYTES"):
            with self.subTest(constant=constant), patch.object(bundle, constant, 1):
                with self.assertRaises(bundle.BundleError):
                    bundle.verify_bundle(self.output, report["artifact_sha256"], self.repo.revision)

    def test_unknown_entry(self):
        path, sha = self.repack(lambda entries: entries + [(zipfile.ZipInfo("unknown.txt"), b"extra")])
        with self.assertRaises(bundle.BundleError):
            bundle.verify_bundle(path, sha, self.repo.revision)

    def test_duplicate_entry(self):
        path, sha = self.repack(lambda entries: entries + [entries[0]])
        self.rejects("path_collision", bundle.verify_bundle, path, sha, self.repo.revision)

    def test_traversal_entry(self):
        path, sha = self.repack(lambda entries: entries + [(zipfile.ZipInfo("../escape"), b"escape")])
        self.rejects("path_invalid", bundle.verify_bundle, path, sha, self.repo.revision)

    def test_missing_metadata(self):
        path, sha = self.repack(lambda entries: [(i, d) for i, d in entries if i.filename != bundle.MANIFEST_PATH])
        self.rejects("metadata_missing", bundle.verify_bundle, path, sha, self.repo.revision)

    def test_duplicate_json_metadata(self):
        path, sha = self.repack(lambda entries: [(i, b'{"schema_version":1,"schema_version":1}'
                                                if i.filename == bundle.MANIFEST_PATH else d) for i, d in entries])
        self.rejects("json_invalid", bundle.verify_bundle, path, sha, self.repo.revision)

    def test_encrypted_flag(self):
        self.build()
        data = bytearray(self.output.read_bytes())
        # Set encryption in every central record; no extraction or actual cipher needed.
        offset = 0
        while True:
            offset = data.find(b"PK\x01\x02", offset)
            if offset < 0:
                break
            data[offset + 8] |= 1
            offset += 4
        self.output.write_bytes(data)
        self.rejects("zip_entry_invalid", bundle.verify_bundle, self.output,
                     hashlib.sha256(data).hexdigest(), self.repo.revision)

    def test_metadata_flags_schema_inventory_and_tree(self):
        mutations = ((bundle.MANIFEST_PATH, "qualified_release", True),
                     (bundle.MANIFEST_PATH, "all44_accepted", True),
                     (bundle.MANIFEST_PATH, "source_file_count", 1),
                     (bundle.MANIFEST_PATH, "schema_version", 2),
                     (bundle.MANIFEST_PATH, "unexpected", "field"),
                     (bundle.MANIFEST_PATH, "inventory_sha256", "0" * 64),
                     (bundle.MANIFEST_PATH, "git_tree_id", "0" * 40),
                     (bundle.INVENTORY_PATH, "complete_sbom", True))
        self.build()
        original = self.output.read_bytes()
        for name, key, value in mutations:
            with self.subTest(key=key):
                self.output.unlink()

                def mutate(entries):
                    result = []
                    for info, data in entries:
                        if info.filename == name:
                            doc = json.loads(data)
                            doc[key] = value
                            data = encoded(doc)
                        result.append((info, data))
                    return result

                path, sha = self.repack(mutate)
                with self.assertRaises(bundle.BundleError):
                    bundle.verify_bundle(path, sha, self.repo.revision)
                self.assertEqual(self.output.read_bytes(), original)

    def test_changed_source_blob(self):
        path, sha = self.repack(lambda entries: [(i, b"tamper" if i.filename == "src/hello.py" else d) for i, d in entries])
        self.rejects("git_tree_mismatch", bundle.verify_bundle, path, sha, self.repo.revision)

    def test_special_zip_mode(self):
        def mutate(entries):
            info, data = entries[0]
            info.external_attr = 0o120777 << 16
            return entries
        path, sha = self.repack(mutate)
        self.rejects("zip_entry_invalid", bundle.verify_bundle, path, sha, self.repo.revision)

    def test_compressed_zip_entry(self):
        def mutate(entries):
            entries[0][0].compress_type = zipfile.ZIP_DEFLATED
            return entries
        path, sha = self.repack(mutate)
        self.rejects("zip_entry_invalid", bundle.verify_bundle, path, sha, self.repo.revision)

    def test_zip_comment_noncanonical(self):
        report = self.build()
        with zipfile.ZipFile(self.output, "a") as archive:
            archive.comment = b"extra"
        sha = hashlib.sha256(self.output.read_bytes()).hexdigest()
        self.rejects("zip_noncanonical", bundle.verify_bundle, self.output, sha, self.repo.revision)

    def test_crc_corruption(self):
        self.build()
        data = bytearray(self.output.read_bytes())
        needle = b'print("committed")'
        offset = data.index(needle)
        data[offset] ^= 1
        self.output.write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        self.rejects("zip_invalid", bundle.verify_bundle, self.output, sha, self.repo.revision)

    def test_cli_safe_json(self):
        command = [sys.executable, str(ROOT / "tools/build_source_release.py")]
        result = subprocess.run(command + ["build", "--repository", str(self.repo.path),
                                           "--revision", self.repo.revision, "--output", str(self.output)],
                                capture_output=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        report = json.loads(result.stdout)
        result = subprocess.run(command + ["verify", "--artifact", str(self.output), "--revision", self.repo.revision,
                                           "--expected-sha256", report["artifact_sha256"]],
                                capture_output=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])
        result = subprocess.run(command + ["build", "--repository", "SECRET://user:credential", "--revision", "HEAD"],
                                capture_output=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertNotIn(b"SECRET", result.stdout + result.stderr)
        self.assertFalse(json.loads(result.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()

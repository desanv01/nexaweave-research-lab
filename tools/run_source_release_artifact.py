"""Main-owned real committed-tree packaging probe; no publication or inference."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools/build_source_release.py"
EXPECTED = re.compile(r"[0-9a-f]{40}\Z")


def main() -> int:
    if sys.argv[1:]:
        print("source_artifact_arguments_invalid", file=sys.stderr)
        return 1
    env = {key: value for key, value in os.environ.items() if key.upper() in
           {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT"}}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0",
               PYTHONDONTWRITEBYTECODE="1")
    try:
        revision = subprocess.run(
            ["git", "--no-pager", "-c", "core.fsmonitor=false", "-C", str(ROOT),
             "rev-parse", "--verify", "HEAD"], env=env, cwd=ROOT,
            capture_output=True, timeout=15, check=True).stdout.decode("ascii").strip()
        if not EXPECTED.fullmatch(revision):
            raise ValueError
        with tempfile.TemporaryDirectory(prefix="nexaweave-source-artifact-") as folder:
            directory = Path(folder).resolve()
            command = [sys.executable, "-I", str(CLI)]
            reports = []
            for name in ("first.zip", "second.zip"):
                artifact = directory / name
                child = subprocess.run(command + ["build", "--repository", str(ROOT),
                    "--revision", revision, "--output", str(artifact)],
                    cwd=directory, env=env, capture_output=True, timeout=330, check=False)
                if child.returncode or child.stderr or len(child.stdout) > 4096 or len(child.stdout.splitlines()) != 1:
                    raise ValueError
                report = json.loads(child.stdout)
                if (report.get("ok") is not True or report.get("qualified_release") is not False
                        or report.get("all44_accepted") is not False
                        or report.get("source_revision") != revision
                        or report.get("artifact_kind") != "source"
                        or type(report.get("source_file_count")) is not int
                        or not 128 <= report["source_file_count"] <= 4096):
                    raise ValueError
                sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
                if report.get("artifact_sha256") != sha:
                    raise ValueError
                checked = subprocess.run(command + ["verify", "--artifact", str(artifact),
                    "--revision", revision, "--expected-sha256", sha],
                    cwd=directory, env=env, capture_output=True, timeout=60, check=False)
                if checked.returncode or checked.stderr or len(checked.stdout) > 4096 or len(checked.stdout.splitlines()) != 1:
                    raise ValueError
                verified = json.loads(checked.stdout)
                if verified != dict(report, operation="verify"):
                    raise ValueError
                reports.append(report)
            if reports[0] != reports[1]:
                raise ValueError
            print(json.dumps({"source_artifact_probe": "passed", "revision": revision,
                "artifact_sha256": reports[0]["artifact_sha256"],
                "source_file_count": reports[0]["source_file_count"],
                "source_total_bytes": reports[0]["source_total_bytes"],
                "qualified_release": False, "all44_accepted": False}, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, UnicodeError, subprocess.SubprocessError):
        print("source_artifact_probe_failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

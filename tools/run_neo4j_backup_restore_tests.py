"""Opt-in Neo4j Community recovery with one explicitly qualified local Docker engine."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
from tools.run_unit_tests import LoopbackOnlySockets, _unit_environment

IMAGE = "neo4j:5.26.31-community@sha256:5eb12ad77fa46ab73e23df9ea1f43f5c0f2a79523435577648e046be042b9b93"
NODE = "test_actual_community_offline_restore_preserves_graph_evidence_and_isolation"
LABEL = "org.nexaweave.fixture.neo4j-recovery"
ROLE = LABEL + ".role"
ARCHIVE_MAX = 64 * 1024 * 1024
OUTPUT_MAX = 64 * 1024
FIXTURE_SECONDS = 420
CLEANUP_SECONDS = 60
VOLUME_OPTIONS = {"type": "tmpfs", "device": "tmpfs", "o": "size=256m,uid=7474,gid=7474,mode=0700"}
ID = re.compile(r"[0-9a-f]{64}\Z")
DAEMON_ID = re.compile(r"[A-Za-z0-9:_-]{8,128}\Z")


class RecoveryError(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise RecoveryError(reason)


def digest_file(path, limit):
    digest = sha256()
    count = 0
    with path.open("rb") as stream:
        while block := stream.read(65536):
            count += len(block)
            require(count <= limit, "file_byte_limit")
            digest.update(block)
    require(count > 0, "empty_file")
    return count, digest.hexdigest()


def command(argv, *, cwd, env, deadline, phase, stdin=None, archive=None):
    """Bound both streams while Docker is active. Preserve safe failure fingerprints."""
    require(time.monotonic() < deadline, "command_deadline:" + phase)
    count = [0, 0]
    digest = [sha256(), sha256()]
    stdout = bytearray()
    process = subprocess.Popen(argv, stdin=stdin if stdin is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env,
                               shell=False, close_fds=True, start_new_session=True)
    try:
        with selectors.DefaultSelector() as streams:
            streams.register(process.stdout, selectors.EVENT_READ, 0)
            streams.register(process.stderr, selectors.EVENT_READ, 1)
            while streams.get_map():
                left = deadline - time.monotonic()
                require(left > 0, "command_deadline:" + phase)
                for key, _ in streams.select(left):
                    block = os.read(key.fd, 65536)
                    if not block:
                        streams.unregister(key.fileobj)
                        continue
                    channel = key.data
                    count[channel] += len(block)
                    limit = ARCHIVE_MAX if channel == 0 and archive is not None else OUTPUT_MAX
                    require(count[channel] <= limit, "command_output_limit:" + phase)
                    digest[channel].update(block)
                    if channel == 0:
                        if archive is None:
                            stdout.extend(block)
                        else:
                            archive.write(block)
        status = process.wait(timeout=max(.01, deadline - time.monotonic()))
        require(status == 0, f"docker_command_failed:{phase}:{status}:"
                f"{count[0]}:{digest[0].hexdigest()}:{count[1]}:{digest[1].hexdigest()}")
        if archive is not None:
            require(count[0] > 0, "empty_dump")
        return bytes(stdout)
    except subprocess.TimeoutExpired:
        raise RecoveryError("command_deadline:" + phase) from None
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=3)
        process.stdout.close()
        process.stderr.close()


class RecoveryFixture:
    def __init__(self, args, directory):
        self.directory = directory
        self.image = args.image_id
        self.daemon = args.daemon_id
        self.nonce = uuid4().hex
        self.deadline = time.monotonic() + FIXTURE_SECONDS
        self.cleanup_deadline = None
        self.resource_ids = {}
        self.intent = []
        self.archive_record = None
        cfg = directory / "docker-config"
        cfg.mkdir(mode=0o700)
        self.argv = [str(args.docker), "--config", str(cfg), "--host", f"unix://{args.socket}"]
        self.env = {"HOME": str(directory), "PATH": os.defpath, "LANG": "C.UTF-8",
                    "DOCKER_CLI_HINTS": "false"}
        self.passwords = {role: "nexaweave-fixture-" + uuid4().hex
                          for role in ("source", "restored")}

    def name(self, role):
        return f"nexaweave-recovery-{self.nonce}-{role}"

    def docker(self, *args, phase, seconds=10, stdin=None, archive=None):
        deadline = self.cleanup_deadline or self.deadline
        return command([*self.argv, *args], cwd=self.directory, env=self.env,
                       deadline=min(deadline, time.monotonic() + seconds), phase=phase,
                       stdin=stdin, archive=archive)

    def verify_engine(self):
        fields = "{{json .ID}} {{json .OSType}} {{json .MemoryLimit}} {{json .SwapLimit}} {{json .PidsLimit}} {{json .CPUCfsQuota}}"
        server = self.docker("info", "--format", fields, phase="daemon_identity").decode("utf-8").strip()
        require(json.loads("[" + ",".join(server.split()) + "]") ==
                [self.daemon, "linux", True, True, True, True], "unqualified_daemon_identity_or_resource_controls")
        inspected = json.loads(self.docker("image", "inspect", "--format", "{{json .}}", IMAGE,
                                           phase="image_identity"))
        digest = IMAGE.split("@", 1)[1]
        require(inspected["Id"] == self.image and inspected["Os"] == "linux"
                and any(name.split("@")[-1] == digest for name in inspected["RepoDigests"]),
                "unqualified_image_identity")
        require(set(inspected["Config"].get("Volumes") or {}) <= {"/data", "/logs"},
                "unexpected_image_volume")

    def listed(self, kind, role):
        name = self.name(role)
        selection = f"name=^/{name}$" if kind == "container" else f"name=^{name}$"
        # Query labels and names independently during ownership validation.
        flags = ("--all", "--no-trunc") if kind == "container" else ("--no-trunc",) if kind == "network" else ()
        field = "{{.Name}}" if kind == "volume" else "{{.ID}}"
        return self.docker(kind, "ls", *flags, "--filter", selection, "--format", field,
                           phase="list_" + role).decode("ascii").splitlines()

    def inspect_owned(self, kind, role):
        name = self.name(role)
        immutable = self.resource_ids.get((kind, role), name)
        view = json.loads(self.docker(kind, "inspect", "--format", "{{json .}}", immutable,
                                      phase="inspect_" + role))
        if kind == "container":
            labels = view["Config"]["Labels"]
            require(view["Name"] == "/" + name and view["Image"] == self.image
                    and ID.fullmatch(view["Id"]) and view["Id"] == immutable,
                    "unowned_container")
            attach = view["HostConfig"]
            require(not attach["Privileged"] and not attach.get("Binds")
                    and not attach.get("VolumesFrom") and attach["LogConfig"]["Type"] == "none",
                    "unsafe_container_custody")
            memory = (32 if role.endswith("-holder") else 768) * 1024 * 1024
            require(attach["Memory"] == attach["MemorySwap"] == memory
                    and attach["NanoCpus"] == 1500000000 and attach["PidsLimit"] == 128,
                    "container_resource_limits_changed")
            mounts = [entry for entry in view["Mounts"] if entry["Type"] != "tmpfs"]
            data_role = "source-data" if role in {"source", "source-holder", "dump"} else "restored-data"
            require(len(mounts) == 1 and mounts[0]["Type"] == "volume"
                    and mounts[0]["Name"] == self.name(data_role)
                    and mounts[0]["Destination"] == "/data", "unowned_container_mount")
            require(attach["NetworkMode"] == (self.name("bridge") if role in self.passwords else "none"),
                    "unexpected_container_network")
        elif kind == "volume":
            labels = view["Labels"]
            require(view["Name"] == name and view["Driver"] == "local"
                    and view["Options"] == VOLUME_OPTIONS, "unowned_volume")
        else:
            labels = view["Labels"]
            require(view["Name"] == name and view["Id"] == immutable and ID.fullmatch(view["Id"])
                    and view["Driver"] == "bridge" and view["Scope"] == "local"
                    and view["Options"].get("com.docker.network.bridge.enable_ip_masquerade") == "false"
                    and view["Options"].get("com.docker.network.bridge.enable_icc") == "false",
                    "unowned_network")
        require(labels.get(LABEL) == self.nonce and labels.get(ROLE) == role, "unowned_resource_label")
        return view

    def create(self, kind, role, *args):
        name = self.name(role)
        require(not self.listed(kind, role), "preexisting_resource:" + role)
        self.intent.append((kind, role))
        prefix = (kind, "create") if kind != "container" else ("container", "create", "--name", name)
        suffix = () if kind == "container" else (name,)
        identity = self.docker(*prefix, "--label", f"{LABEL}={self.nonce}", "--label", f"{ROLE}={role}",
                               *args, *suffix, phase="create_" + role, seconds=20).decode("ascii").strip()
        require(identity == name if kind == "volume" else bool(ID.fullmatch(identity)), "invalid_resource_id")
        self.resource_ids[kind, role] = identity
        self.inspect_owned(kind, role)
        return identity

    def make_container(self, role, data, *args, network="none", memory=768, extra=()):
        volume_id = self.name(data)
        self.inspect_owned("volume", data)
        return self.create("container", role, "--pull=never", "--init", "--no-healthcheck", "--log-driver=none",
            "--network", network, "--memory", f"{memory}m", "--memory-swap", f"{memory}m",
            "--cpus", "1.5", "--pids-limit", "128", "--security-opt", "no-new-privileges",
            "--mount", f"type=volume,source={volume_id},destination=/data,volume-nocopy",
            "--tmpfs", "/logs:rw,size=16m,mode=1777", "--tmpfs", "/tmp:rw,size=64m,mode=1777",
            *extra, self.image, *args)

    @contextmanager
    def resources(self):
        try:
            self.verify_engine()
            self.create("network", "bridge", "--driver", "bridge",
                        "--opt", "com.docker.network.bridge.enable_ip_masquerade=false",
                        "--opt", "com.docker.network.bridge.enable_icc=false")
            for pair in ("source", "restored"):
                data, holder = pair + "-data", pair + "-holder"
                self.create("volume", data, "--driver", "local",
                            *(part for key, value in VOLUME_OPTIONS.items() for part in ("--opt", f"{key}={value}")))
                # The mount-only holder keeps its capped tmpfs alive across
                # a clean server stop and a separate offline admin container.
                identity = self.make_container(holder, data, str(FIXTURE_SECONDS + CLEANUP_SECONDS + 30),
                    memory=32, extra=("--entrypoint", "/bin/sleep", "--read-only"))
                self.docker("container", "start", identity, phase="start_" + holder)
                self.check_holder(pair)
            yield self
        finally:
            active = sys.exception()
            failures = self.cleanup()
            if failures:
                message = "owned_cleanup_incomplete:" + ",".join(failures)
                if active is None:
                    raise RecoveryError(message)
                active.add_note(message)

    def check_holder(self, pair):
        holder = self.inspect_owned("container", pair + "-holder")
        require(holder["State"]["Running"], "fixture_volume_holder_stopped:" + pair)

    def start_database(self, role):
        self.check_holder(role)
        secret = self.passwords[role]
        identity = self.make_container(role, role + "-data", "neo4j", network=self.name("bridge"),
            extra=("--publish", "127.0.0.1::7687",
                   "--env", f"NEO4J_AUTH=neo4j/{secret}",
                   "--env", "NEO4J_PLUGINS=[]",
                   "--env", "NEO4J_dbms_usage__report_enabled=false",
                   "--env", "NEO4J_server_http_enabled=false",
                   "--env", "NEO4J_server_memory_heap_initial__size=128m",
                   "--env", "NEO4J_server_memory_heap_max__size=256m",
                   "--env", "NEO4J_server_memory_pagecache_size=64m"))
        self.docker("container", "start", identity, phase="start_" + role, seconds=30)
        view = self.inspect_owned("container", role)
        bindings = {key: val for key, val in view["NetworkSettings"]["Ports"].items() if val}
        require(set(bindings) == {"7687/tcp"} and len(bindings["7687/tcp"]) == 1,
                "unexpected_published_ports")
        port = bindings["7687/tcp"][0]
        require(port["HostIp"] == "127.0.0.1" and port["HostPort"].isascii()
                and port["HostPort"].isdecimal() and 1024 <= int(port["HostPort"]) <= 65535,
                "non_loopback_bolt_publication")
        return f"bolt://127.0.0.1:{port['HostPort']}", secret

    def stop_database(self, role):
        self.inspect_owned("container", role)
        identity = self.resource_ids["container", role]
        self.docker("container", "stop", "--time", "15", identity, phase="stop_" + role, seconds=22)
        self.require_stopped(role)

    def require_stopped(self, role):
        state = self.inspect_owned("container", role)["State"]
        require(not state["Running"] and not state["OOMKilled"] and state["ExitCode"] in (0, 143),
                "database_not_cleanly_stopped:" + role)

    def restore(self):
        self.require_stopped("source")
        self.check_holder("source")
        self.check_holder("restored")
        holder = self.resource_ids["container", "restored-holder"]
        require(not self.docker("container", "exec", holder, "/bin/ls", "-A", "/data",
                                phase="empty_restore_store"), "restore_store_not_empty")
        archive = self.directory / "neo4j.dump"
        # Admin commands use no network and receive only the one owned store.
        dump = self.make_container("dump", "source-data", "neo4j-admin", "database", "dump", "neo4j",
                                   "--to-stdout", extra=("--env", "HEAP_SIZE=192m"))
        with os.fdopen(os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
            self.docker("container", "start", "--attach", dump, phase="offline_dump", seconds=60, archive=output)
        size, digest = digest_file(archive, ARCHIVE_MAX)
        self.archive_record = (size, digest)
        require(self.inspect_owned("container", "dump")["State"]["ExitCode"] == 0, "dump_failed")
        restore_id = self.make_container("load", "restored-data", "neo4j-admin", "database", "load", "neo4j",
                                         "--from-stdin", extra=("--interactive", "--env", "HEAP_SIZE=192m"))
        with archive.open("rb") as source:
            self.docker("container", "start", "--attach", "--interactive", restore_id,
                        phase="offline_load", seconds=60, stdin=source)
        load = self.inspect_owned("container", "load")["State"]
        require(not load["Running"] and not load["OOMKilled"] and load["ExitCode"] == 0, "load_failed")
        require(digest_file(archive, ARCHIVE_MAX) == (size, digest), "archive_changed")
        self.require_stopped("source")
        return size, digest

    def cleanup(self):
        self.cleanup_deadline = time.monotonic() + CLEANUP_SECONDS
        failures = []
        for kind, role in reversed(self.intent):
            try:
                listed = self.listed(kind, role)
                if not listed:
                    continue
                require(len(listed) == 1, "ambiguous_owned_cleanup")
                identity = self.resource_ids.setdefault((kind, role), listed[0])
                require(listed == [identity], "changed_resource_identity")
                self.inspect_owned(kind, role)
                options = ("--force",) if kind == "container" else ()
                self.docker(kind, "rm", *options, identity, phase="remove_" + role, seconds=7)
                require(not self.listed(kind, role), "resource_survived_cleanup")
            except (OSError, ValueError, KeyError, TypeError, RecoveryError, subprocess.SubprocessError):
                failures.append(role)
        for kind in {kind for kind, _ in self.intent}:
            try:
                flags = ("--all",) if kind == "container" else ()
                result = self.docker(kind, "ls", *flags, "--filter", f"label={LABEL}={self.nonce}",
                                     "--format", "{{.Names}}" if kind == "container" else "{{.Name}}",
                                     phase="verify_cleanup_" + kind, seconds=4)
                if result:
                    failures.append("remaining_" + kind)
            except (OSError, ValueError, KeyError, TypeError, RecoveryError, subprocess.SubprocessError):
                failures.append("unverified_" + kind)
        return failures


def qualify(args):
    require(sys.platform == "linux", "native_linux_docker_required")
    for path in (args.docker, args.socket):
        require(path.is_absolute() and path.resolve(strict=True) == path, "uncanonical_runtime_path")
    require(args.socket.is_relative_to(Path("/run")) and stat.S_ISSOCK(args.socket.stat().st_mode),
            "local_unix_daemon_socket_required")
    require(args.docker.is_file() and os.access(args.docker, os.X_OK)
            and digest_file(args.docker, 128 * 1024 * 1024)[1] == args.docker_sha256, "unqualified_docker_cli")
    require(args.image == IMAGE and args.image_id.startswith("sha256:") and ID.fullmatch(args.image_id[7:])
            and DAEMON_ID.fullmatch(args.daemon_id), "missing_qualified_image_or_daemon_identity")
    prefix = Path(sys.prefix).resolve()
    require(prefix != Path(sys.base_prefix).resolve(), "installed_knowledge_venv_required")
    for name in ("nexaweave_knowledge", "graphiti_core", "neo4j"):
        spec = importlib.util.find_spec(name)
        require(spec and spec.origin, "installed_package_required:" + name)
        origin = Path(spec.origin).resolve()
        require(origin.is_relative_to(prefix) and "site-packages" in origin.parts,
                "invalid_package_provenance:" + name)


def run_fixture(args, directory):
    import pytest

    class Outcome:
        collected = 0
        passed = 0
        skipped = 0

        @pytest.fixture(scope="session")
        def neo4j_recovery(self):
            return fixture

        def pytest_collection_finish(self, session):
            self.collected = len(session.items)

        def pytest_runtest_logreport(self, report):
            self.skipped += int(report.skipped)
            self.passed += int(report.when == "call" and report.passed and report.nodeid.endswith("::" + NODE))

    outcome = Outcome()
    fixture = RecoveryFixture(args, directory)
    try:
        with fixture.resources():
            target = ROOT / "services/knowledge/tests/test_neo4j_backup_restore.py"
            status = pytest.main(["-q", "--tb=short", "-p", "no:cacheprovider", "-p", "pytest_asyncio.plugin",
                                  "-o", "asyncio_mode=auto", str(target) + "::" + NODE], plugins=[outcome])
            require(status == 0 and outcome.collected == outcome.passed == 1 and outcome.skipped == 0,
                    "community_recovery_fixture_incomplete")
    finally:
        if fixture.archive_record is not None:
            size, digest = fixture.archive_record
            print(f"NEXAWEAVE_COMMUNITY_NEO4J_ARCHIVE {size} {digest}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--integration", action="store_true", required=True)
    parser.add_argument("--docker", type=Path, required=True)
    parser.add_argument("--docker-sha256", required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--daemon-id", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--image-id", required=True)
    args = parser.parse_args()
    try:
        qualify(args)
        with tempfile.TemporaryDirectory(prefix="nexaweave-community-recovery-") as path:
            directory = Path(path).resolve(strict=True)
            require(shutil.disk_usage(directory).free >= 2 * ARCHIVE_MAX, "insufficient_archive_disk")
            environment = _unit_environment(directory)
            previous = dict(os.environ)
            guard = LoopbackOnlySockets()
            try:
                os.environ.clear()
                os.environ.update(environment, GRAPHITI_TELEMETRY_ENABLED="false",
                                  NEXAWEAVE_NEO4J_RECOVERY_INTEGRATION="1")
                guard.install()
                run_fixture(args, directory)
                require(not guard.blocked_attempts, "non_loopback_attempt_blocked")
            finally:
                guard.restore()
                os.environ.clear()
                os.environ.update(previous)
        require(not directory.exists(), "private_archive_cleanup_incomplete")
    except (OSError, ValueError, KeyError, TypeError, RecoveryError, subprocess.SubprocessError) as error:
        reason = str(error) if isinstance(error, RecoveryError) else type(error).__name__
        print("community_neo4j_recovery_failed:" + reason, file=sys.stderr, flush=True)
        for note in getattr(error, "__notes__", ()):
            print(note, file=sys.stderr, flush=True)
        return 1
    print("NEXAWEAVE_COMMUNITY_NEO4J_RECOVERY_CLOSED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

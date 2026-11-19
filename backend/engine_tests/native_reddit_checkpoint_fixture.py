"""Spawn three independently owned native Reddit lanes for one existing node."""

import asyncio
from contextlib import closing
from dataclasses import dataclass, replace
from hashlib import sha256
import json
from multiprocessing import active_children
import os
from pathlib import Path
import sqlite3
from time import monotonic
from uuid import uuid4


_DB = "native.sqlite3"
_MANIFEST = "reddit-checkpoint.json"
_PROOF = "observation.json"
_COMMENT = "Synthetic comment on the retained seed post"
_NAMES = ("state.json", "simulation_config.json", "source_grounding.json", "reddit_profiles.json")
_EPOCH = "2026-01-01T00:00:00"
_SIGNUP_TICK = "2026-01-01 00:00:00"
_SEED_TICK = "2026-01-01 00:01:00"
_COMMENT_TICK = "2026-01-01 00:02:00"


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")


def _model():
    from test_native_prepared_workflow import _offline_model

    model = _offline_model()

    def unexpected(_tools):
        raise AssertionError("Reddit elapsed command must use no model calls")

    model._response = unexpected
    return {"reddit": model}


@dataclass(frozen=True)
class _Factory:
    """No backend/engine import during spawn or the owned ready handshake."""

    prepared: object
    fingerprint: str
    role: str
    output: str
    checkpoint: str = ""
    producer_request: object = None
    producer_pid: int = 0
    checkpoint_digest: str = ""

    def validate(self, request):
        self.prepared.validate(request)
        if request.fingerprint != self.fingerprint:
            raise ValueError("unexpected Reddit fixture owner")

    def create_session(self, request):
        return _Session(self, request)

    def evidence(self, request):
        self.validate(request)
        path = Path(self.checkpoint) / _MANIFEST if self.role == "producer" else Path(self.output) / _PROOF
        return sha256(_read(path, 7200)).hexdigest()


@dataclass(frozen=True)
class _Session:
    factory: _Factory
    request: object

    def start(self):
        from app.services.native_reddit_checkpoint import produce_reddit_checkpoint, restore_reddit_checkpoint

        job = self.factory
        deadline = monotonic() + 25
        if job.role == "restore":
            proof = asyncio.run(restore_reddit_checkpoint(
                job.prepared, self.request, producer_request=job.producer_request,
                producer_pid=job.producer_pid, expected_digest=job.checkpoint_digest,
                checkpoint_dir=job.checkpoint, child_dir=job.output, deadline=deadline,
            ))
        else:
            proof = asyncio.run(produce_reddit_checkpoint(
                job.prepared, self.request, job.output, checkpoint_dir=job.checkpoint or None, deadline=deadline,
            ))
            if job.role == "producer":
                assert proof == job.evidence(self.request)
                return
        data = _json(proof)
        assert len(data) <= 7200
        with (Path(job.output) / _PROOF).open("xb") as output:
            assert output.write(data) == len(data)

    def close(self):
        # The native coroutine closes the writer and its connection before
        # returning evidence. asyncio.run has closed its event loop.
        pass


def _read(path, limit):
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    assert 1 <= len(data) <= limit
    return data


def _pin(path):
    data = _read(path, 4 * 1024 * 1024)
    return {"bytes": len(data), "sha256": sha256(data).hexdigest()}


def _owner(request):
    return {"graph_id": "graph-fixture", "request": request.to_wire(),
            "request_fingerprint": request.fingerprint}


def _rows(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)) as db:
        assert db.execute("PRAGMA quick_check(1)").fetchone() == ("ok",)
        users = db.execute(
            "SELECT user_id, agent_id, name, created_at FROM user ORDER BY user_id"
        ).fetchall()
        posts = db.execute(
            "SELECT post_id, user_id, content, created_at FROM post ORDER BY post_id"
        ).fetchall()
        comments = db.execute(
            "SELECT comment_id, post_id, user_id, content, created_at FROM comment ORDER BY comment_id"
        ).fetchall()
        trace = [(cursor, actor, action, json.loads(info), tick) for cursor, actor, action, info, tick
                 in db.execute("SELECT rowid, user_id, action, info, created_at FROM trace ORDER BY rowid")]
        recs = db.execute("SELECT user_id, post_id FROM rec ORDER BY user_id, post_id").fetchall()
        return users, posts, comments, trace, recs


def assert_fresh_reddit_continuation(tmp_path):
    from app.services.native_owned_session import NativeOwnedSessionFactory, _manifest
    from native_twitter_fresh_fixture import _run_child
    from nexaweave_execution.native_run_contracts import NativeRunRequest
    from test_native_prepared_workflow import _prepared
    from test_owned_native_process import terminal

    private = tmp_path / "reddit-elapsed"
    private.mkdir(mode=0o700)
    prepared_root = _prepared(private)
    state_path = prepared_root / "state.json"
    state = json.loads(_read(state_path, 7200))
    state_path.write_bytes(_json({**state, "enable_twitter": False}))
    config_path = prepared_root / "simulation_config.json"
    config = json.loads(_read(config_path, 7200))
    del config["twitter_config"]
    config["reddit_checkpoint_comment"] = _COMMENT
    config_path.write_bytes(_json(config))
    originals = {path: _pin(path) for path in (prepared_root / name for name in (*_NAMES, "twitter_profiles.csv"))}
    binding = _manifest(prepared_root, _NAMES, 2 * 1024 * 1024)
    request = NativeRunRequest.from_wire({
        "schema_version": 1, "principal": "owner", "project_id": uuid4(), "project_revision": 1,
        "simulation_id": "sim-fixture", "run_id": uuid4(), "artifact_sha256": binding,
        "runtime_sha256": "b" * 64, "platforms": ("reddit",), "seed": 7, "max_rounds": 1,
    })
    prepared = NativeOwnedSessionFactory(
        str(prepared_root), "graph-fixture", request.simulation_id, request.principal,
        str(request.project_id), request.project_revision, request.platforms, request.seed,
        request.max_rounds, request.runtime_sha256, _model,
    )
    producer, checkpoint, child, control = (private / name for name in ("producer", "cut", "child", "control"))
    deadline = monotonic() + 120
    pids = {os.getpid()}
    producer_pid, checkpoint_sha = _run_child(
        _Factory(prepared, request.fingerprint, "producer", str(producer), str(checkpoint)),
        request, terminal, deadline, pids,
    )
    pids.add(producer_pid)
    raw = _read(checkpoint / _MANIFEST, 7200)
    manifest = json.loads(raw)
    assert raw == _json(manifest) and sha256(raw).hexdigest() == checkpoint_sha
    assert manifest == {
        "version": 1, "platform": "reddit", "owner": _owner(request), "producer_pid": producer_pid,
        "config_sha256": originals[config_path]["sha256"], "database": _pin(producer / _DB),
        "trace_cursor": 3, "clock": {"epoch": _EPOCH, "elapsed_seconds": 60}, "actor_ids": [0, 1],
        "next_command": {"actor_id": 1, "post_id": 1, "action": "create_comment"},
    }
    original = {path: _pin(path) for path in (producer / _DB, checkpoint / _DB, checkpoint / _MANIFEST)}
    assert original[producer / _DB] == original[checkpoint / _DB]
    before = _rows(checkpoint / _DB)
    assert _rows(producer / _DB) == before
    users, posts, comments, prefix, recs = before
    assert users == [(0, 0, "actor0", _SIGNUP_TICK), (1, 1, "actor1", _SIGNUP_TICK)]
    assert posts == [(1, 0, "Synthetic initial event", _SEED_TICK)]
    assert comments == recs == []
    assert [(rowid, actor, action, tick) for rowid, actor, action, _, tick in prefix] == [
        (1, 0, "sign_up", _SIGNUP_TICK), (2, 1, "sign_up", _SIGNUP_TICK), (3, 0, "create_post", _SEED_TICK),
    ]
    assert prefix[-1][3] == {"content": "Synthetic initial event", "post_id": 1}

    control_request = replace(request, run_id=uuid4())
    control_driver = None
    failed_terminal = None

    def fixed_label(value, allowed):
        return next((label for label in allowed if value == label), "unexpected")

    def observe_control(driver, owner, attempt, child_identity, cutoff):
        nonlocal control_driver, failed_terminal
        observed = terminal(driver, owner, attempt, child_identity, cutoff)
        receipt = observed.receipt
        if observed.status != "completed" or receipt is None:
            control_driver = driver
            failed_terminal = {
                "status": fixed_label(observed.status, (
                    "absent", "running", "completed", "failed", "cancelled", "unknown",
                )),
                "receipt_present": receipt is not None,
                "receipt_outcome": "absent" if receipt is None else fixed_label(
                    receipt.outcome, ("completed", "failed", "cancelled", "unknown"),
                ),
            }
        return observed

    try:
        control_pid, control_sha = _run_child(
            _Factory(prepared, control_request.fingerprint, "control", str(control)),
            control_request, observe_control, deadline, pids,
        )
    except AssertionError as error:
        if failed_terminal is not None:
            # Inspect the original close result after the helper has unwound.
            fields = dict(failed_terminal, ordinary_close_finished=control_driver._closed is True)
            BaseException.add_note(error, "NEXAWEAVE_REDDIT_CONTROL_TERMINAL " + _json(fields).decode("ascii"))
        raise
    pids.add(control_pid)
    child_request = replace(request, run_id=uuid4())
    child_pid, child_sha = _run_child(
        _Factory(prepared, child_request.fingerprint, "restore", str(child), str(checkpoint),
                 request, producer_pid, checkpoint_sha),
        child_request, terminal, deadline, pids,
    )
    pids.add(child_pid)
    for path, pid, owner, evidence in (
            (control, control_pid, control_request, control_sha), (child, child_pid, child_request, child_sha)):
        raw = _read(path / _PROOF, 7200)
        proof = json.loads(raw)
        assert raw == _json(proof) and sha256(raw).hexdigest() == evidence
        expected = {
            "pid": pid, "owner": _owner(owner), "database": _pin(path / _DB),
            "step": {"trace_cursor": 4, "actor_id": 1, "post_id": 1, "comment_id": 1,
                     "clock": {"epoch": _EPOCH, "elapsed_seconds": 120}},
        }
        if path == child:
            expected.update(producer_run_id=str(request.run_id), checkpoint_sha256=checkpoint_sha)
        assert proof == expected
    final = _rows(child / _DB)
    assert final == _rows(control / _DB)
    assert final[:2] == (users, posts)
    assert final[2] == [(1, 1, 1, _COMMENT, _COMMENT_TICK)]
    assert final[3] == prefix + [(4, 1, "create_comment", {"comment_id": 1, "content": _COMMENT}, _COMMENT_TICK)]
    assert final[4] == [(0, 1), (1, 1)]
    assert {path: _pin(path) for path in original} == original
    assert {path: _pin(path) for path in originals} == originals
    roots = (producer, checkpoint, child, control)
    for index, path in enumerate(roots):
        others = roots[index + 1:]
        assert all(not (path / _DB).samefile(other / _DB) for other in others)
    assert {path.name for path in producer.iterdir()} == {_DB}
    assert {path.name for path in checkpoint.iterdir()} == {_DB, _MANIFEST}
    for path in (child, control):
        assert {file.name for file in path.iterdir()} == {_DB, _PROOF}
    assert not (pids - {os.getpid()}) & {process.pid for process in active_children()}
    assert monotonic() < deadline

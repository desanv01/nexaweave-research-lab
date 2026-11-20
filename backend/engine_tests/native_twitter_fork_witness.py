"""Protected fork witness sharing the completed Twitter capture case."""
import asyncio
import json
import os
from hashlib import sha256
from itertools import islice
from pathlib import Path
from uuid import uuid4

import pytest

from native_twitter_fresh_fixture import (
    _DB_LIMIT, _LOG_LIMIT, _PREPARED, _PROFILE_LIMIT, _RECEIPT_LIMIT, _RESTORE_OUTPUTS,
    _captured_checkpoint, _captured_owners, _checkpoint_post, _events,
)
from test_native_round_capture import _artifact, _native_rows, _read_bounded

from nexaweave_execution.budget import BudgetDenied, ReservationState
from nexaweave_execution.native_run_contracts import NativeRunDenied
from nexaweave_execution.native_twitter_checkpoint_binding import NativeRestoredTwitterFactory
from nexaweave_execution.native_owned_binding import _manifest


async def qualify_fork(post, launch, remaining, host, planned, capture, producer, continuation, normal, dispatched):
    """Prove one rejected preflight and one real owned fork within the case budget."""
    checkpoint = Path(capture.checkpoint_dir)
    lineage_path = Path(capture.lineage_dir) / 'lineage.json'
    prepared = Path(capture.prepared.simulation_dir)
    normal_dir = Path(continuation.child_dir)
    lineage_raw = _read_bounded(lineage_path, _RECEIPT_LIMIT)
    lineage = json.loads(lineage_raw)
    assert sha256(lineage_raw).hexdigest() == producer.receipt.evidence_sha256
    config = _read_bounded(prepared / 'simulation_config.json', _PROFILE_LIMIT)
    database, memory, log, manifest, _, limits, _ = _captured_checkpoint(checkpoint, lineage, config)
    _, frames = zip(*_captured_owners(memory, lineage['owners']), strict=True)
    parent_rows = _native_rows(database)
    _, old_trace, old_posts, _ = parent_rows
    prefix = _read_bounded(log, _LOG_LIMIT)

    limits.update({prepared / name: _PROFILE_LIMIT for name in _PREPARED})
    limits.update({
        lineage_path: _RECEIPT_LIMIT,
        prepared / 'twitter_simulation.db': _DB_LIMIT,
        prepared / 'twitter' / 'actions.jsonl': _LOG_LIMIT,
        normal_dir / 'native.sqlite3': _DB_LIMIT,
        normal_dir / 'actions.jsonl': _LOG_LIMIT,
    })
    pins = {path: _artifact(path, limit)[:2] for path, limit in limits.items()}
    runner = checkpoint / 'native-runner.bin'
    runner_raw = _read_bounded(runner, 2564)
    assert sha256(runner_raw).hexdigest() == manifest['runner_state']['sha256']
    launch_id = str(uuid4())
    declaration = {
        'schema_version': 3, 'launch_id': launch_id,
        'preparation': {key: planned[key] for key in ('operation_id', 'plan_sha256')},
        'checkpoint': {
            'mode': 'fork', 'source_run_id': str(producer.request.run_id),
            'checkpoint_sha256': producer.receipt.evidence_sha256,
            'intervention': {
                'kind': 'swap_active_actor_v1',
                'manifest_sha256': lineage['manifest_sha256'],
                'runner_state_sha256': manifest['runner_state']['sha256'],
            },
        },
    }
    dto = await asyncio.wait_for(post('plan', declaration), remaining(30))
    assert dto['state'] == 'planned' and dto['checkpoint'] == declaration['checkpoint']
    assert dto['request']['schema_version'] == 3
    assert dto['request']['intervention'] == declaration['checkpoint']['intervention']
    ref = {'schema_version': 3, 'launch_id': launch_id, 'launch_sha256': dto['launch_sha256']}
    account_before = host.budget.status('owner', host.account_id)
    dispatch_before = list(dispatched)
    planned_row = host.store.get('owner', launch_id)
    assert planned_row.state == 'planned' and planned_row.budget_attempt_id is None

    try:
        with runner.open('r+b') as stream:
            assert stream.write(bytes((runner_raw[0] ^ 1,))) == 1
        assert _read_bounded(runner, 2564) != runner_raw
        await asyncio.wait_for(post('start', ref, status=409, code='conflict'), remaining(30))
        refused = host.store.get('owner', launch_id)
        assert refused == planned_row and not refused.dispatch_claimed and refused.workflow is None
        assert dispatched == dispatch_before
        with pytest.raises(BudgetDenied):
            host.budget.native_reservation('owner', host.account_id, refused.request, refused.launch_sha256)
        with pytest.raises(NativeRunDenied):
            host.native.get('owner', launch_id)
        assert host.budget.status('owner', host.account_id) == account_before
    finally:
        with runner.open('r+b') as stream:
            assert stream.write(runner_raw) == len(runner_raw)
    assert _read_bounded(runner, 2564) == runner_raw

    fork, restoration, reservation = await launch(dto)
    assert type(restoration) is NativeRestoredTwitterFactory
    assert fork.request == planned_row.request and fork.request.source_run_id == producer.request.run_id
    assert fork.child.process_id not in {os.getpid(), producer.child.process_id, normal.child.process_id}
    assert fork.receipt.attempt_id == fork.attempt_id and fork.receipt.instance_id == fork.child.instance_id
    assert fork.receipt.request_fingerprint == fork.request.fingerprint
    assert reservation.state == ReservationState.settled and reservation.attempt_id != fork.attempt_id
    assert reservation.receipt.native_receipt == fork.receipt
    assert reservation.receipt.launch_sha256 == dto['launch_sha256']

    fork_dir = Path(restoration.child_dir)
    assert fork_dir.parent == normal_dir.parent and fork_dir not in {prepared, checkpoint, normal_dir}
    assert {path.name for path in islice(fork_dir.iterdir(), 3)} == set(_RESTORE_OUTPUTS)
    fork_db, fork_log = (fork_dir / name for name in _RESTORE_OUTPUTS)
    assert all(not fork_db.samefile(path) for path in (database, normal_dir / 'native.sqlite3',
                                                       prepared / 'twitter_simulation.db'))
    assert _manifest(fork_dir, _RESTORE_OUTPUTS, _DB_LIMIT) == fork.receipt.evidence_sha256
    fork_size = _artifact(fork_db, _DB_LIMIT)[0] + _artifact(fork_log, _LOG_LIMIT)[0]
    assert sum(size for size, _ in pins.values()) + fork_size <= 160 * 1024 * 1024

    _, _, normal_posts, _ = _native_rows(normal_dir / 'native.sqlite3')
    (_, normal_actor, _, normal_tick), = normal_posts[len(old_posts):]
    assert normal_actor in (0, 1)
    fork_actor = 1 - normal_actor
    fork_cursor, fork_trace, fork_posts, _ = _native_rows(fork_db)
    assert fork_posts[:len(old_posts)] == old_posts and fork_trace[:len(old_trace)] == old_trace
    (post_id, actor, content, tick), = fork_posts[len(old_posts):]
    assert post_id > old_posts[-1][0] and actor == fork_actor
    assert type(tick) is int and tick == normal_tick == manifest['engine_clock']['after_step']
    assert content == _checkpoint_post(fork.child.process_id, frames)
    (_, trace_actor, _, info, trace_tick), = (row for row in fork_trace[len(old_trace):] if row[2] == 'create_post')
    assert trace_actor == fork_actor and trace_tick == tick and fork_cursor > manifest['cursor']
    assert json.loads(info)['post_id'] == post_id and json.loads(info)['content'] == content
    raw_log = _read_bounded(fork_log, _LOG_LIMIT)
    assert raw_log.startswith(prefix)
    first, action, last = _events(raw_log[len(prefix):])
    assert first == {'round': 2, 'event_type': 'round_start', 'simulated_hour': 1}
    assert action['action_type'] == 'CREATE_POST' and action['agent_id'] == fork_actor and action['success'] is True
    assert action['action_args']['post_id'] == post_id and action['action_args']['content'] == content
    assert last == {'round': 2, 'event_type': 'round_end', 'actions_count': 1}

    for method in ('start', 'status'):
        replay = await asyncio.wait_for(post(method, ref), remaining(30))
        assert replay['state'] == 'completed' and replay['receipt'] == fork.receipt.to_wire()
        assert replay['checkpoint'] == dto['checkpoint'] and replay['launch_sha256'] == ref['launch_sha256']
        assert replay['cleanup'] == {'known': True, 'pending': False, 'owner_thread_alive': False}
    saved = host.store.get('owner', launch_id)
    assert saved.dispatch_claimed and saved.workflow is not None and saved.budget_attempt_id == reservation.attempt_id
    assert host.budget.native_reservation('owner', host.account_id, saved.request, saved.launch_sha256) == reservation
    assert dispatched == [*dispatch_before, fork.request.run_id]
    account_after = host.budget.status('owner', host.account_id)
    assert account_after.accounted_ceiling_microusd == account_before.accounted_ceiling_microusd + 4
    assert account_after.reserved_microusd == account_before.reserved_microusd
    assert account_after.uncertain_microusd == account_before.uncertain_microusd
    assert {path: _artifact(path, limit)[:2] for path, limit in limits.items()} == pins
    return fork.request.run_id

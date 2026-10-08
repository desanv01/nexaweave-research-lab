"""Real local Windows ownership tests; no hosted services/product imports.

Main runs this file explicitly in the Windows gate. Each synthetic command uses
the private injection seam; the production CLI remains hosted-only and fixed.
"""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from tools import run_native_ci_qualification as ci


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='actual Windows job trees required')


def local_lane(tmp_path, name, *, status=0, marker=True, wait=False, descendant=False):
    token = 'trusted-local-test-token'
    script = tmp_path / (name + '.py')
    ready = tmp_path / (name + '.ready')
    child_ready = tmp_path / (name + '.child-ready')
    # The descendant inherits the lane job and output file. Root may exit first;
    # the controller must still empty the job and release its inherited handles.
    child_code = (
        'import os,time\nfrom pathlib import Path\n'
        'print("local descendant output",flush=True)\n'
        f'Path({str(child_ready)!r}).write_text(str(os.getpid()))\n'
        'time.sleep(60)\n'
    )
    lines = ['import json,os,subprocess,sys,time', 'from pathlib import Path',
             f'print("complete output {name}",flush=True)',
             f'Path({str(ready)!r}).write_text(str(os.getpid()))']
    if descendant:
        lines.extend([f'subprocess.Popen([sys.executable,"-c",{child_code!r}])',
                      f'child_ready=Path({str(child_ready)!r})',
                      'deadline=time.monotonic()+10',
                      'while not child_ready.exists():',
                      ' if time.monotonic()>=deadline: raise RuntimeError("descendant not ready")',
                      ' time.sleep(0.01)'])
    if marker:
        record = dict(token=token, gate=name, status=status)
        lines.append(f'print({(ci.PREFIX + json.dumps(record))!r},flush=True)')
    if wait:
        lines.append('time.sleep(60)')
    lines.append(f'raise SystemExit({status})')
    script.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return ci.Lane(name, (sys.executable, str(script)), (name,), token), ready, child_ready


def run_owned(lanes, *, cancel=None, max_seconds=25, environment=None, sanitize_offline=False):
    owners = []
    owner_type = ci._owner_type()
    class RecordingOwner(owner_type):
        def start(self, popen, args, **kwargs):
            self.requested_creationflags = kwargs.get('creationflags', 0)
            return super().start(popen, args, **kwargs)
    def factory():
        owner = RecordingOwner()
        owners.append(owner)
        return owner
    output = io.BytesIO()
    status = ci._run_lanes(lanes, environment=os.environ if environment is None else environment, output=output,
                           max_seconds=max_seconds, cancel=cancel,
                           owner_factory=factory, sanitize_offline=sanitize_offline)
    assert len(owners) == 2
    assert all(owner.closed and owner.tree_empty for owner in owners)
    assert all(owner.process.poll() is not None for owner in owners)
    assert all(owner.requested_creationflags & subprocess.CREATE_NO_WINDOW for owner in owners)
    assert all(not Path(owner._directory_name).exists() for owner in owners)
    return status, output.getvalue()


def test_real_success_replays_both_lanes_and_closes_exited_root_descendant(tmp_path):
    first, _, child_ready = local_lane(tmp_path, 'first', descendant=True)
    second, _, _ = local_lane(tmp_path, 'second')
    status, output = run_owned((first, second))
    assert status == 0
    assert child_ready.is_file()
    assert b'complete output first' in output and b'complete output second' in output
    assert b'local descendant output' in output
    assert output.count(ci.PREFIX.encode()) == 2


def test_real_failure_does_not_hide_other_lane_output_or_success(tmp_path):
    failed, _, _ = local_lane(tmp_path, 'failed', status=7)
    success, _, _ = local_lane(tmp_path, 'success')
    status, output = run_owned((failed, success))
    assert status == 1
    assert b'complete output failed' in output and b'complete output success' in output
    assert b'"status": 7' in output and b'"status": 0' in output


def test_real_zero_exit_without_required_result_fails_closed(tmp_path):
    missing, _, _ = local_lane(tmp_path, 'missing', marker=False)
    success, _, _ = local_lane(tmp_path, 'success')
    status, output = run_owned((missing, success))
    assert status == 1
    assert b'result evidence incomplete' in output
    assert b'complete output missing' in output and b'complete output success' in output


def test_real_cancel_joins_both_active_trees_and_descendants(tmp_path):
    first, first_ready, first_child = local_lane(tmp_path, 'first', wait=True, descendant=True)
    second, second_ready, second_child = local_lane(tmp_path, 'second', wait=True, descendant=True)
    cancel = threading.Event()
    observed = []
    def cancel_when_both_running():
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if all(path.exists() for path in (first_ready, first_child, second_ready, second_child)):
                observed.append(True)
                cancel.set()
                return
            time.sleep(0.01)
        cancel.set()
    thread = threading.Thread(target=cancel_when_both_running)
    thread.start()
    try:
        status, output = run_owned((first, second), cancel=cancel)
    finally:
        thread.join(timeout=16)
    assert not thread.is_alive() and observed == [True]
    assert status == 1
    assert b'cancelled or whole deadline exhausted' in output
    assert b'complete output first' in output and b'complete output second' in output


def test_real_whole_deadline_closes_both_trees(tmp_path):
    first, _, _ = local_lane(tmp_path, 'first', wait=True)
    second, _, _ = local_lane(tmp_path, 'second', wait=True)
    started = time.monotonic()
    status, output = run_owned((first, second), max_seconds=12)
    assert status == 1
    assert time.monotonic() - started < 15
    assert b'whole deadline exhausted' in output


def test_cli_rejects_local_invocation_before_any_fixture(tmp_path):
    environment = dict(os.environ)
    environment.pop('GITHUB_ACTIONS', None)
    result = subprocess.run([sys.executable, str(Path(ci.__file__).absolute())],
                            env=environment, cwd=tmp_path, capture_output=True,
                            timeout=10, check=False)
    assert result.returncode != 0
    assert b'hosted Windows CI only' in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_real_offline_child_isolates_profile_and_secrets_but_keeps_runtime_selectors(tmp_path):
    environment = dict(os.environ)
    environment.update(OPENAI_API_KEY='not-a-real-key', HTTPS_PROXY='private-proxy-sentinel',
                       PROJECT_STORE_TEST_PASSWORD='private-fixture-sentinel',
                       TEMPORAL_TEST_ADDRESS='127.0.0.1:17233',
                       USERPROFILE='private-profile-sentinel',
                       APPDATA='private-appdata-sentinel', PYTHONSTARTUP='private-hook-sentinel')
    selectors = {name: str(tmp_path / (name + '.installed')) for name in ci.EXPERIMENT_SELECTORS}
    environment.update(selectors)
    lanes = []
    for name in ('offline', 'fixture'):
        script = tmp_path / (name + '-environment.py')
        record = dict(token='local-environment-token', gate=name, status=0)
        common = (
            'import os,json\nfrom pathlib import Path\n'
            f'selectors={selectors!r}\n'
            'assert all(os.environ.get(k)==v for k,v in selectors.items())\n'
        )
        if name == 'offline':
            checks = (
                "assert all(k not in os.environ for k in ('OPENAI_API_KEY','HTTPS_PROXY','PROJECT_STORE_TEST_PASSWORD','TEMPORAL_TEST_ADDRESS','PYTHONSTARTUP'))\n"
                "assert Path(os.environ['USERPROFILE'])==Path.cwd()\n"
                "assert Path(os.environ['APPDATA']).parent==Path.cwd()\n"
                "assert os.environ['BROWSER']=='none' and 'BROWSER_ARGS' not in os.environ\n"
            )
        else:
            checks = (
                "assert os.environ['PROJECT_STORE_TEST_PASSWORD']=='private-fixture-sentinel'\n"
                "assert os.environ['TEMPORAL_TEST_ADDRESS']=='127.0.0.1:17233'\n"
                "assert os.environ['USERPROFILE']=='private-profile-sentinel'\n"
            )
        script.write_text(common + checks + f'print({(ci.PREFIX + json.dumps(record))!r},flush=True)\n',
                          encoding='utf-8')
        lanes.append(ci.Lane(name, (sys.executable, str(script)), (name,), record['token']))
    status, output = run_owned(tuple(lanes), environment=environment, sanitize_offline=True)
    assert status == 0
    assert b'not-a-real-key' not in output and b'private-fixture-sentinel' not in output

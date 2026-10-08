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


def run_owned(lanes, *, cancel=None, max_seconds=25, environment=None,
              sanitize_offline=False, owner_type=None, exit_codes=None):
    owners = []
    owner_type = owner_type or ci._owner_type()
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
    if exit_codes is not None:
        # Actual recorded root exits, independently of marker-based rejection.
        exit_codes.update({lane.name: owner.process.returncode
                           for lane, owner in zip(lanes, owners)})
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


@pytest.mark.parametrize(('native_status', 'stop_status'), ((0, 0), (7, 0), (0, 7)))
def test_actual_generated_nested_helper_returns_with_extra_handle_and_keeps_failure(
        tmp_path, monkeypatch, native_status, stop_status):
    # Capture the actual stdlib ownership class before replacing only the trusted
    # generated plan's ROOT. No production CLI injection or hosted/DB bypass.
    owner_type = ci._owner_type()
    root = tmp_path / 'synthetic-root'
    tools = root / 'tools'
    tools.mkdir(parents=True)
    plan = tmp_path / 'plan'
    plan.mkdir()
    ready = tmp_path / 'daemon-ready'
    stop = tmp_path / 'daemon-stop'
    stopped = tmp_path / 'daemon-stopped'
    server_log = tmp_path / 'server.log'
    launcher = tools / 'launcher.py'
    control = tools / 'service-control.py'
    child_code = (
        'import os,time\nfrom pathlib import Path\n'
        f'Path({str(ready)!r}).write_text(str(os.getpid()))\n'
        'print("private daemon log",flush=True)\n'
        'deadline=time.monotonic()+30\n'
        f'while not Path({str(stop)!r}).exists():\n'
        ' if time.monotonic()>=deadline: raise RuntimeError("stop signal missing")\n'
        ' time.sleep(0.01)\n'
        f'Path({str(stopped)!r}).write_text("ordinary stop observed")\n'
    )
    launcher.write_text(
        'import ctypes,subprocess,sys,time\nfrom pathlib import Path\n'
        'api=ctypes.WinDLL("kernel32",use_last_error=True)\n'
        'api.GetStdHandle.argtypes=[ctypes.c_uint32]\n'
        'api.GetStdHandle.restype=ctypes.c_void_p\n'
        'api.SetHandleInformation.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32]\n'
        'api.SetHandleInformation.restype=ctypes.c_int\n'
        # Retain the extra original stdout handle, although Popen replaces the
        # child's *standard* stdout/stderr with a separate private logfile.
        'handle=api.GetStdHandle(0xfffffff5)\n'
        'assert api.SetHandleInformation(handle,1,1)\n'
        f'with open({str(server_log)!r},"wb") as log:\n'
        f' subprocess.Popen([sys.executable,"-c",{child_code!r}],stdin=subprocess.DEVNULL,\n'
        '  stdout=log,stderr=log,close_fds=False,creationflags=subprocess.CREATE_NO_WINDOW)\n'
        'deadline=time.monotonic()+3\n'
        f'while not Path({str(ready)!r}).exists():\n'
        ' if time.monotonic()>=deadline: raise RuntimeError("daemon not ready")\n'
        ' time.sleep(0.01)\n'
        'print("native launcher started; normal output redirected",flush=True)\n',
        encoding='utf-8')
    control.write_text(
        'import sys,time\nfrom pathlib import Path\n'
        'service,action=sys.argv[1:]\n'
        'print("nested helper native output "+service+" "+action,flush=True)\n'
        'if service=="postgres" and action=="stop":\n'
        f' Path({str(stop)!r}).write_text("stop")\n'
        ' deadline=time.monotonic()+3\n'
        f' while not Path({str(stopped)!r}).exists():\n'
        '  if time.monotonic()>=deadline: raise RuntimeError("ordinary stop unobserved")\n'
        '  time.sleep(0.01)\n'
        f'raise SystemExit({stop_status} if service=="temporal" and action=="stop" else 0)\n',
        encoding='utf-8')
    pg_helper = tools / 'native_store_ci_postgres.ps1'
    temporal_helper = tools / 'native_temporal_ci_server.ps1'
    python = ci._quote_ps(sys.executable)
    pg_helper.write_text(
        "param([ValidateSet('start','stop')][string]$Action)\n"
        "if ($Action -eq 'start') {\n"
        f'  & {python} {ci._quote_ps(launcher)}\n'
        '  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n'
        '  [Console]::Out.WriteLine("nested PG helper returned with daemon alive")\n'
        '} else {\n'
        f"  & {python} {ci._quote_ps(control)} postgres stop\n"
        '  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n'
        '}\nexit 0\n', encoding='utf-8')
    temporal_helper.write_text(
        "param([ValidateSet('start','stop')][string]$Action)\n"
        f'& {python} {ci._quote_ps(control)} temporal $Action\n'
        'exit $LASTEXITCODE\n', encoding='utf-8')
    live_check = (
        'import ctypes\n'
        'api=ctypes.WinDLL("kernel32",use_last_error=True)\n'
        'api.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]\n'
        'api.OpenProcess.restype=ctypes.c_void_p\n'
        'api.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32]\n'
        'api.WaitForSingleObject.restype=ctypes.c_uint32\n'
        'api.CloseHandle.argtypes=[ctypes.c_void_p]\n'
        f'pid=int(Path({str(ready)!r}).read_text())\n'
        'handle=api.OpenProcess(0x00100000,False,pid)\n'
        'assert handle\n'
        'try: assert api.WaitForSingleObject(handle,0)==258\n'
        'finally: assert api.CloseHandle(handle)\n'
        'print("next native gate observed daemon alive",flush=True)\n'
    )
    scripts = {script for _, script, _ in (*ci.OFFLINE_TESTS, *ci.FIXTURE_TESTS)}
    for name in scripts:
        code = 'import sys\nfrom pathlib import Path\nprint("runner output "+Path(__file__).name+" "+str(sys.argv[1:]),flush=True)\n'
        if name == 'run_native_store_tests.py':
            code += live_check + f'raise SystemExit({native_status})\n'
        (tools / name).write_text(code, encoding='utf-8')
    monkeypatch.setattr(ci, 'ROOT', root)
    lanes = ci._write_scripts(plan, Path(sys.executable), 'generated-regression-token')
    exit_codes = {}
    status, output = run_owned(lanes, owner_type=owner_type, exit_codes=exit_codes)
    expected_fixture_exit = int(native_status != 0 or stop_status != 0)
    assert exit_codes == {'offline': 0, 'fixture': expected_fixture_exit}
    assert status == expected_fixture_exit
    assert b'whole deadline exhausted' not in output
    assert b'native launcher started; normal output redirected' in output
    assert b'nested PG helper returned with daemon alive' in output
    assert b'next native gate observed daemon alive' in output
    assert b'nested helper native output temporal stop' in output
    assert b'nested helper native output postgres stop' in output
    assert stopped.read_text() == 'ordinary stop observed'
    assert b'private daemon log' in server_log.read_bytes()
    results = [json.loads(line[len(ci.PREFIX):]) for line in output.decode().splitlines()
               if line.startswith(ci.PREFIX)]
    expected = [gate for lane in lanes for gate in lane.expected]
    assert [row['gate'] for row in results] == expected
    assert len(results) == 27  # every offline13 and fixture14 result, once
    statuses = {row['gate']: row['status'] for row in results}
    assert statuses['postgres-start'] == 0 and statuses['temporal-start'] == 0
    assert statuses['native-store'] == native_status
    assert statuses['temporal-stop'] == stop_status and statuses['postgres-stop'] == 0
    assert all(statuses[gate] == 0 for gate in lanes[0].expected)
    assert all(statuses[gate] == 0 for gate in lanes[1].expected
               if gate not in ('native-store', 'temporal-stop'))

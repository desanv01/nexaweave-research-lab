"""Real local Windows ownership tests; no hosted services/product imports.

Main runs this file explicitly in the Windows gate. Each synthetic command uses
the private injection seam; the production CLI remains hosted-only and fixed.
"""
import io
import json
import math
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
              sanitize_offline=False, owner_type=None, exit_codes=None, output_sink=None):
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
    output = output_sink if output_sink is not None else io.BytesIO()
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


def timings(output):
    records = [json.loads(line[len(ci.TIMING_PREFIX):])
               for line in output.decode().splitlines() if line.startswith(ci.TIMING_PREFIX)]
    assert records
    for record in records:
        assert set(record) == {'token', 'lane', 'gate', 'phase', 'monotonic_seconds',
                               'elapsed_seconds', 'lane_elapsed_seconds'}
        for key in ('monotonic_seconds', 'elapsed_seconds', 'lane_elapsed_seconds'):
            assert type(record[key]) in (int, float)
            assert math.isfinite(record[key]) and record[key] >= 0
    return records


def assert_controller_lifecycle(output, lanes, *, interrupted=False):
    records = timings(output)
    for lane in lanes:
        rows = [row for row in records if row['lane'] == lane.name and row['gate'] == 'controller']
        assert all(row['token'] == lane.token for row in rows)
        phases = [row['phase'] for row in rows]
        assert phases[:2] == ['launch-start', 'launch-end']
        expected = (['whole-bound-interruption'] if interrupted else ['observed-exit'])
        expected += ['ownership-close-start', 'ownership-close-end', 'replay-start',
                     'replay-end', 'private-cleanup-start', 'private-cleanup-end']
        assert phases[2:] == expected
        assert [row['monotonic_seconds'] for row in rows] == sorted(row['monotonic_seconds'] for row in rows)


def test_real_success_replays_both_lanes_and_closes_exited_root_descendant(tmp_path):
    first, _, child_ready = local_lane(tmp_path, 'first', descendant=True)
    second, _, _ = local_lane(tmp_path, 'second')
    status, output = run_owned((first, second))
    assert status == 0
    assert child_ready.is_file()
    assert b'complete output first' in output and b'complete output second' in output
    assert b'local descendant output' in output
    assert output.count(ci.PREFIX.encode()) == 2
    assert_controller_lifecycle(output, (first, second))


def test_real_failure_does_not_hide_other_lane_output_or_success(tmp_path):
    failed, _, _ = local_lane(tmp_path, 'failed', status=7)
    success, _, _ = local_lane(tmp_path, 'success')
    status, output = run_owned((failed, success))
    assert status == 1
    assert b'complete output failed' in output and b'complete output success' in output
    assert b'"status": 7' in output and b'"status": 0' in output
    assert_controller_lifecycle(output, (failed, success))


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
    assert_controller_lifecycle(output, (first, second), interrupted=True)


def test_real_whole_deadline_closes_both_trees(tmp_path):
    first, _, _ = local_lane(tmp_path, 'first', wait=True)
    second, _, _ = local_lane(tmp_path, 'second', wait=True)
    started = time.monotonic()
    status, output = run_owned((first, second), max_seconds=12)
    assert status == 1
    assert time.monotonic() - started < 15
    assert b'whole deadline exhausted' in output
    assert_controller_lifecycle(output, (first, second), interrupted=True)


def test_controller_diagnostic_sink_failure_does_not_skip_either_tree_cleanup(tmp_path):
    first, _, _ = local_lane(tmp_path, 'first', descendant=True)
    second, _, _ = local_lane(tmp_path, 'second')
    class DiagnosticFailure(io.BytesIO):
        def write(self, value):
            if value.startswith(ci.TIMING_PREFIX.encode()):
                raise RuntimeError('diagnostic sink unavailable')
            return super().write(value)
    status, output = run_owned((first, second), output_sink=DiagnosticFailure())
    assert status == 0
    assert output.count(ci.PREFIX.encode()) == 2


@pytest.mark.parametrize('target', ('replay', 'cleanup-report', 'private-cleanup-report'))
@pytest.mark.parametrize('control_type', (None, KeyboardInterrupt, SystemExit))
def test_reporting_sink_failure_still_closes_both_real_trees_and_directories(tmp_path, target, control_type):
    first, _, first_child = local_lane(tmp_path, 'first', descendant=True)
    second, _, second_child = local_lane(tmp_path, 'second', descendant=True)
    owners = []
    cleaned = []
    injected = []
    control = control_type(23) if control_type is not None else None
    owner_type = ci._owner_type()

    class ReportingOwner(owner_type):
        def stop(self, *args, **kwargs):
            result = super().stop(*args, **kwargs)
            if self is owners[0] and target == 'cleanup-report':
                raise RuntimeError('report after actual tree closure')
            return result

        def cleanup_private_directory(self, directory):
            result = super().cleanup_private_directory(directory)
            cleaned.append(self)
            if self is owners[0] and target == 'private-cleanup-report':
                raise RuntimeError('report after actual directory cleanup')
            return result

    def factory():
        owner = ReportingOwner()
        owners.append(owner)
        return owner

    class ReportingFailure(io.BytesIO):
        def write(self, value):
            selected = (
                target == 'replay' and value.startswith(b'\n=== native CI lane first')
                or target == 'cleanup-report' and value.startswith(b'lane first cleanup failed:')
                or target == 'private-cleanup-report' and value.startswith(b'lane first private cleanup failed:')
            )
            if selected and not injected:
                injected.append(value)
                if control is not None:
                    raise control
                raise BrokenPipeError('initial reporting failure')
            if injected and not value.startswith(ci.TIMING_PREFIX.encode()):
                # The same unavailable sink also rejects the error report and
                # second lane's replay. Neither may skip its private cleanup.
                raise BrokenPipeError('persistent reporting failure')
            return super().write(value)

    sink = ReportingFailure()
    if control is None:
        assert ci._run_lanes((first, second), environment=os.environ, output=sink,
                             max_seconds=25, owner_factory=factory) == 1
    else:
        with pytest.raises(control_type) as raised:
            ci._run_lanes((first, second), environment=os.environ, output=sink,
                          max_seconds=25, owner_factory=factory)
        assert raised.value is control
        if control_type is SystemExit:
            assert raised.value.code == 23
    assert len(injected) == 1 and len(owners) == 2
    assert first_child.is_file() and second_child.is_file()
    assert cleaned == owners
    assert all(owner.closed and owner.tree_empty for owner in owners)
    assert all(owner.process.poll() is not None for owner in owners)
    assert all(not Path(owner._directory_name).exists() for owner in owners)


@pytest.mark.parametrize('control_type', (KeyboardInterrupt, SystemExit))
@pytest.mark.parametrize('phase', ('launch-end', 'ownership-close-start', 'replay-start', 'private-cleanup-start'))
def test_timing_control_propagates_exact_instance_after_all_owned_cleanup(tmp_path, control_type, phase):
    work = phase == 'launch-end'
    first, first_ready, first_child = local_lane(tmp_path, 'first', wait=work, descendant=True)
    second, second_ready, second_child = local_lane(tmp_path, 'second', wait=work, descendant=True)
    control = control_type(23)
    owners = []
    owner_type = ci._owner_type()
    def factory():
        owner = owner_type()
        owners.append(owner)
        return owner
    injected = []
    after_control = []
    class ControlSink(io.BytesIO):
        def write(self, value):
            if value.startswith(ci.TIMING_PREFIX.encode()):
                record = json.loads(value[len(ci.TIMING_PREFIX):])
                if injected:
                    after_control.append(record)
                target = 'second' if work else 'first'
                if not injected and record['lane'] == target and record['phase'] == phase:
                    # Work injection occurs with both actual roots/descendants
                    # active. Cleanup injection owns the real exited-root trees.
                    deadline = time.monotonic() + 10
                    while not all(path.is_file() for path in (first_ready, first_child, second_ready, second_child)):
                        if time.monotonic() >= deadline:
                            raise RuntimeError('both owned children did not become ready')
                        time.sleep(0.01)
                    if work:
                        assert all(owner.process.poll() is None for owner in owners)
                    injected.append(record)
                    raise control
            return super().write(value)
    output = ControlSink()
    with pytest.raises(control_type) as raised:
        ci._run_lanes((first, second), environment=os.environ, output=output,
                      max_seconds=25, owner_factory=factory)
    assert raised.value is control
    if control_type is SystemExit:
        assert raised.value.code == 23
    assert len(injected) == 1 and len(owners) == 2
    assert all(owner.closed and owner.tree_empty for owner in owners)
    assert all(owner.process.poll() is not None for owner in owners)
    assert all(not Path(owner._directory_name).exists() for owner in owners)
    assert not any(row['phase'] in ('launch-start', 'launch-end', 'observed-exit', 'whole-bound-interruption')
                   for row in after_control)
    assert all(any(row['lane'] == lane.name and row['phase'] == 'private-cleanup-end'
                   for row in after_control) for lane in (first, second))


@pytest.mark.parametrize('first_type', (None, KeyboardInterrupt, SystemExit))
@pytest.mark.parametrize('final_type', (RuntimeError, SystemExit))
def test_final_flush_failure_retains_first_control_after_owned_cleanup(tmp_path, first_type, final_type):
    first, _, _ = local_lane(tmp_path, 'first', descendant=True)
    second, _, _ = local_lane(tmp_path, 'second', descendant=True)
    first_control = first_type(23) if first_type is not None else None
    final_error = final_type(41)
    owners = []
    owner_type = ci._owner_type()
    def factory():
        owner = owner_type()
        owners.append(owner)
        return owner
    injected = []
    final_failures = []
    class FinalFlushFailure(io.BytesIO):
        flushes_until_final = None
        def write(self, value):
            if value.startswith(ci.TIMING_PREFIX.encode()):
                record = json.loads(value[len(ci.TIMING_PREFIX):])
                if (first_control is not None and not injected and record['lane'] == 'first'
                        and record['phase'] == 'ownership-close-start'):
                    injected.append(first_control)
                    raise first_control
                if record['lane'] == 'second' and record['phase'] == 'private-cleanup-end':
                    # The timing record's own flush succeeds; fail the next,
                    # actual final controller flush after both directories close.
                    self.flushes_until_final = 2
            return super().write(value)
        def flush(self):
            if self.flushes_until_final is not None:
                self.flushes_until_final -= 1
                if self.flushes_until_final == 0:
                    final_failures.append(final_error)
                    raise final_error
            return super().flush()
    output = FinalFlushFailure()
    expected = first_control if first_control is not None else final_error
    with pytest.raises(type(expected)) as raised:
        ci._run_lanes((first, second), environment=os.environ, output=output,
                      max_seconds=25, owner_factory=factory)
    assert raised.value is expected
    if isinstance(expected, SystemExit):
        assert raised.value.code == (23 if first_control is not None else 41)
    assert injected == ([first_control] if first_control is not None else [])
    assert final_failures == [final_error]
    assert len(owners) == 2
    assert all(owner.closed and owner.tree_empty for owner in owners)
    assert all(owner.process.poll() is not None for owner in owners)
    assert all(not Path(owner._directory_name).exists() for owner in owners)
    assert output.getvalue().count(ci.PREFIX.encode()) == 2


@pytest.mark.parametrize('control_type', (KeyboardInterrupt, SystemExit))
def test_actual_generated_offline_timing_control_stops_before_work_admission(tmp_path, monkeypatch, control_type):
    owner_type = ci._owner_type()
    root = tmp_path / 'synthetic-root'
    tools = root / 'tools'
    tools.mkdir(parents=True)
    admitted = tmp_path / 'admitted-gates'
    for _, name, _ in ci.OFFLINE_TESTS:
        (tools / name).write_text(
            'from pathlib import Path\n'
            f'with Path({str(admitted)!r}).open("a") as stream: stream.write("admitted\\n")\n', encoding='utf-8')
    plan = tmp_path / 'plan'
    plan.mkdir()
    monkeypatch.setattr(ci, 'ROOT', root)
    generated, _ = ci._write_scripts(plan, Path(sys.executable), 'offline-control-token')
    bootstrap = tmp_path / 'control-bootstrap.py'
    bootstrap.write_text(
        'import builtins,json,runpy\n'
        f'control={control_type.__name__}(23)\nprefix={ci.TIMING_PREFIX!r}\n'
        'original_print=builtins.print\n'
        'def controlled_print(*values,**kwargs):\n'
        ' original_print(*values,**kwargs)\n'
        ' if values and isinstance(values[0],str) and values[0].startswith(prefix):\n'
        '  record=json.loads(values[0][len(prefix):])\n'
        '  if record["gate"]=="read-boundary" and record["phase"]=="start": raise control\n'
        'builtins.print=controlled_print\n'
        'try:\n'
        f' runpy.run_path({str(plan / "offline.py")!r},run_name="__main__")\n'
        'except BaseException as error:\n'
        ' assert error is control\n'
        ' original_print("exact generated control propagated",flush=True)\n'
        ' raise\n', encoding='utf-8')
    interrupted = ci.Lane(generated.name, (sys.executable, str(bootstrap)), generated.expected, generated.token)
    companion, _, _ = local_lane(tmp_path, 'companion')
    exit_codes = {}
    status, output = run_owned((interrupted, companion), owner_type=owner_type, exit_codes=exit_codes)
    assert status == 1 and exit_codes['offline'] != 0 and exit_codes['companion'] == 0
    if control_type is SystemExit:
        assert exit_codes['offline'] == 23
    assert b'exact generated control propagated' in output
    assert not admitted.exists()
    rows = [row for row in timings(output) if row['lane'] == 'offline' and row['gate'] != 'controller']
    assert [(row['gate'], row['phase']) for row in rows] == [('read-boundary', 'start')]
    results = [json.loads(line[len(ci.PREFIX):]) for line in output.decode().splitlines()
               if line.startswith(ci.PREFIX)]
    assert [row['gate'] for row in results] == ['companion']


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


@pytest.mark.parametrize(('native_status', 'stop_status', 'offline_status', 'helper_fail', 'interrupt'), (
    (0, 0, 0, False, False),
    (7, 0, 0, False, False),
    (0, 7, 0, False, False),
    (0, 0, 7, False, False),
    (0, 0, 0, True, False),
    (0, 0, 0, False, True),
))
def test_actual_generated_nested_helper_returns_with_extra_handle_and_keeps_failure(
        tmp_path, monkeypatch, native_status, stop_status, offline_status, helper_fail, interrupt):
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
    waiting = tmp_path / 'native-gate-waiting'
    offline_waiting = tmp_path / 'offline-gate-waiting'
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
        # Actual generated JSON diagnostics must keep numeric values under a
        # comma-decimal culture as well as the hosted runner's default culture.
        "  [Threading.Thread]::CurrentThread.CurrentCulture = [Globalization.CultureInfo]::GetCultureInfo('fr-FR')\n"
        f'  & {python} {ci._quote_ps(launcher)}\n'
        '  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n'
        '  [Console]::Out.WriteLine("nested PG helper returned with daemon alive")\n'
        + ('  throw "synthetic helper failure"\n' if helper_fail else '') +
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
            code += live_check
            if interrupt:
                code += f'Path({str(waiting)!r}).write_text("started")\nimport time\ntime.sleep(60)\n'
            code += f'raise SystemExit({native_status})\n'
        if name == 'check_engine_imports.py':
            code += f'raise SystemExit({offline_status})\n'
        if interrupt and name == 'run_read_boundary_tests.py':
            code += f'Path({str(offline_waiting)!r}).write_text("started")\nimport time\ntime.sleep(60)\n'
        (tools / name).write_text(code, encoding='utf-8')
    monkeypatch.setattr(ci, 'ROOT', root)
    lanes = ci._write_scripts(plan, Path(sys.executable), 'generated-regression-token')
    exit_codes = {}
    cancel = threading.Event() if interrupt else None
    observed = []
    def cancel_when_gate_waits():
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if waiting.is_file() and offline_waiting.is_file():
                observed.append(True)
                cancel.set()
                return
            time.sleep(0.01)
        cancel.set()
    thread = threading.Thread(target=cancel_when_gate_waits) if interrupt else None
    if thread is not None:
        thread.start()
    try:
        status, output = run_owned(lanes, owner_type=owner_type, exit_codes=exit_codes, cancel=cancel)
    finally:
        if thread is not None:
            thread.join(timeout=16)
    if interrupt:
        assert not thread.is_alive() and observed == [True]
        assert status == 1 and exit_codes['fixture'] != 0
        assert not stopped.exists()
        assert b'whole deadline exhausted' in output
        records = timings(output)
        starts = [row for row in records if row['lane'] == 'fixture' and row['gate'] == 'native-store']
        assert [row['phase'] for row in starts] == ['start']
        offline_starts = [row for row in records if row['lane'] == 'offline' and row['gate'] == 'read-boundary']
        assert [row['phase'] for row in offline_starts] == ['start']
        assert exit_codes['offline'] != 0
        assert not any(row['gate'] in ('temporal-stop', 'postgres-stop') and row['phase'] == 'end'
                       for row in records)
        interrupted_results = [json.loads(line[len(ci.PREFIX):])
                               for line in output.decode().splitlines() if line.startswith(ci.PREFIX)]
        assert not any(row['gate'] == 'native-store' for row in interrupted_results)
        assert not any(row['gate'] == 'read-boundary' for row in interrupted_results)
        assert any(row['lane'] == 'fixture' and row['phase'] == 'whole-bound-interruption' for row in records)
        assert all(any(row['lane'] == lane.name and row['phase'] == 'private-cleanup-end' for row in records)
                   for lane in lanes)
        return
    expected_fixture_exit = int(native_status != 0 or stop_status != 0 or helper_fail)
    assert exit_codes == {'offline': int(offline_status != 0), 'fixture': expected_fixture_exit}
    assert status == int(expected_fixture_exit != 0 or offline_status != 0)
    assert_controller_lifecycle(output, lanes)
    assert b'whole deadline exhausted' not in output
    assert b'native launcher started; normal output redirected' in output
    assert b'nested PG helper returned with daemon alive' in output
    if not helper_fail:
        assert b'next native gate observed daemon alive' in output
    assert b'nested helper native output temporal stop' in output
    assert b'nested helper native output postgres stop' in output
    assert stopped.read_text() == 'ordinary stop observed'
    assert b'private daemon log' in server_log.read_bytes()
    results = [json.loads(line[len(ci.PREFIX):]) for line in output.decode().splitlines()
               if line.startswith(ci.PREFIX)]
    expected = [gate for lane in lanes for gate in lane.expected]
    if helper_fail:
        expected = list(lanes[0].expected) + ['postgres-start', 'temporal-stop', 'postgres-stop']
    assert [row['gate'] for row in results] == expected
    assert all(set(row) == {'token', 'gate', 'status'} and row['token'] == lanes[0].token for row in results)
    assert len(results) == (16 if helper_fail else 27)
    records = timings(output)
    for lane in lanes:
        gates = [row for row in results if row['gate'] in lane.expected]
        for result in gates:
            pair = [row for row in records if row['lane'] == lane.name and row['gate'] == result['gate']]
            assert [row['phase'] for row in pair] == ['start', 'end']
            assert all(row['token'] == lane.token for row in pair)
            assert pair[1]['monotonic_seconds'] >= pair[0]['monotonic_seconds']
            assert pair[1]['lane_elapsed_seconds'] >= pair[0]['lane_elapsed_seconds']
            assert pair[1]['elapsed_seconds'] >= pair[0]['elapsed_seconds']
    statuses = {row['gate']: row['status'] for row in results}
    if helper_fail:
        assert statuses['postgres-start'] == -1
        assert statuses['temporal-stop'] == 0 and statuses['postgres-stop'] == 0
        return
    assert statuses['postgres-start'] == 0 and statuses['temporal-start'] == 0
    assert statuses['native-store'] == native_status
    assert statuses['temporal-stop'] == stop_status and statuses['postgres-stop'] == 0
    assert statuses['native-imports'] == offline_status
    assert all(statuses[gate] == 0 for gate in lanes[0].expected if gate != 'native-imports')
    assert all(statuses[gate] == 0 for gate in lanes[1].expected
               if gate not in ('native-store', 'temporal-stop'))

"""Hosted Windows CI overlap; no product imports or qualification relaxation.

The only CLI runs two fixed lanes. _run_lanes is a trusted local-test seam,
not a command-line command/endpoint override. YAML retains always-stop fallbacks.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'NEXAWEAVE_CI_RESULT '
OFFLINE_TESTS = (
    ('read-boundary', 'run_read_boundary_tests.py', None),
    ('preparation-unit', 'run_preparation_tests.py', '--unit'),
    ('native-launch-unit', 'run_native_launch_tests.py', '--unit'),
    ('observations-unit', 'run_native_observations_tests.py', '--unit'),
    ('seed-unit', 'run_native_seed_tests.py', '--unit'),
    ('seed-engine', 'run_native_seed_tests.py', '--engine'),
    ('report-unit', 'run_connected_report_tests.py', '--unit'),
    ('report-engine', 'run_connected_report_tests.py', '--engine'),
    ('followup-unit', 'run_connected_followup_tests.py', '--unit'),
    ('pdf-unit', 'run_source_binary_tests.py', '--unit'),
    ('followup-engine', 'run_connected_followup_tests.py', '--engine'),
    ('native-imports', 'check_engine_imports.py', None),
    ('native-engine-64', 'run_engine_tests.py', None),
)
EXPERIMENT_SELECTORS = (
    'NEXAWEAVE_EXPERIMENT_TEST_PYTHON',
    'NEXAWEAVE_EXPERIMENT_TEST_HTTP_PYTHON',
    'NEXAWEAVE_EXPERIMENT_TEST_BOOTSTRAP',
)
FIXTURE_TESTS = (
    ('native-store', 'run_native_store_tests.py', '--postgres'),
    ('native-temporal', 'run_native_temporal_tests.py', '--integration'),
    ('preparation', 'run_preparation_tests.py', '--integration'),
    ('native-launch-integration', 'run_native_launch_tests.py', '--integration'),
    ('native-launch-engine', 'run_native_launch_tests.py', '--engine'),
    ('observations-integration', 'run_native_observations_tests.py', '--integration'),
    ('observations-engine', 'run_native_observations_tests.py', '--engine'),
    ('seed-integration', 'run_native_seed_tests.py', '--integration'),
    ('report-integration', 'run_connected_report_tests.py', '--integration'),
    ('followup-integration', 'run_connected_followup_tests.py', '--integration'),
)


@dataclass(frozen=True)
class Lane:
    name: str
    argv: tuple[str, ...]
    expected: tuple[str, ...]
    token: str


def _owner_type():
    # Load the stdlib-only ownership helper without importing the Flask app.
    spec = importlib.util.spec_from_file_location(
        'native_ci_owned_process', ROOT / 'backend/app/utils/owned_process.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.OwnedProcess


def _offline_environment(source, directory):
    """Outer offline lane allowlist; runners retain their stricter inner envs."""
    keep = ('PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'OS',
            'SYSTEMDRIVE', 'LANG', 'LC_ALL', 'TZ', *EXPERIMENT_SELECTORS)
    env = {name: source[name] for name in keep if name in source}
    # No inherited provider tokens, proxy/Python hooks, PG/Temporal flags or user
    # profile. These paths belong to the lane owner's private directory.
    for name in ('config', 'cache', 'appdata', 'localappdata'):
        (directory / name).mkdir()
    env.update(
        HOME=str(directory), USERPROFILE=str(directory),
        APPDATA=str(directory / 'appdata'), LOCALAPPDATA=str(directory / 'localappdata'),
        XDG_CONFIG_HOME=str(directory / 'config'), XDG_CACHE_HOME=str(directory / 'cache'),
        TMPDIR=str(directory), TMP=str(directory), TEMP=str(directory),
        PYTHONPATH=os.pathsep.join((str(ROOT), str(ROOT / 'backend'))),
        PYTHONDONTWRITEBYTECODE='1', PYTHON_DOTENV_DISABLED='1', PYTHONNOUSERSITE='1',
        PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
        HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
        DO_NOT_TRACK='1', GRAPHITI_TELEMETRY_ENABLED='false', BROWSER='none',
    )
    return env


def _replay_and_check(path, lane, output):
    """Replay every byte, then require exactly one successful result per gate."""
    seen = {}
    valid = True
    output.write(f'\n=== native CI lane {lane.name} ===\n'.encode())
    with path.open('rb') as stream:
        for line in stream:
            output.write(line)
            if not line.startswith(PREFIX.encode()):
                continue
            try:
                result = json.loads(line[len(PREFIX):])
                if (set(result) != {'token', 'gate', 'status'}
                        or result['token'] != lane.token
                        or result['gate'] not in lane.expected
                        or result['gate'] in seen
                        or type(result['status']) is not int):
                    valid = False
                else:
                    seen[result['gate']] = result['status']
            except (ValueError, TypeError, KeyError):
                valid = False
    output.flush()
    return valid and set(seen) == set(lane.expected) and all(v == 0 for v in seen.values())


def _run_lanes(lanes, *, environment, output, max_seconds=1000,
               cancel=None, owner_factory=None, sanitize_offline=False):
    """Own two real child trees through log replay/exit aggregation.

    A ten-second reserve is inside the whole bound for tree closure. Test callers
    can supply fixed synthetic commands and an Event; production never accepts
    commands, environment or deadlines from CLI input.
    """
    if (type(max_seconds) not in (int, float) or not math.isfinite(max_seconds)
            or not 0 < max_seconds <= 1000 or len(lanes) != 2
            or len({lane.name for lane in lanes}) != 2
            or any(not lane.expected or len(set(lane.expected)) != len(lane.expected)
                   for lane in lanes)):
        raise ValueError('invalid bounded CI plan')
    owner_factory = owner_factory or _owner_type()
    deadline = time.monotonic() + max_seconds
    reserve = min(10.0, max_seconds / 2)
    running = []
    failed = False
    interrupted = None
    cleanup_messages = []
    try:
        for lane in lanes:
            directory = tempfile.TemporaryDirectory(prefix=f'nexaweave-ci-{lane.name}-')
            owner = owner_factory()
            owner.bind_private_directory(directory)
            path = Path(directory.name) / 'complete.log'
            log = path.open('wb')
            entry = (lane, directory, owner, path, log)
            running.append(entry)  # partial startup also receives cleanup
            try:
                env = (_offline_environment(environment, Path(directory.name))
                       if sanitize_offline and lane.name == 'offline' else dict(environment))
                owner.start(subprocess.Popen, list(lane.argv), cwd=directory.name,
                            env=env, stdin=subprocess.DEVNULL,
                            stdout=log, stderr=subprocess.STDOUT,
                            shell=False, close_fds=True,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except Exception as error:
                failed = True
                output.write(f'lane {lane.name} startup failed: {type(error).__name__}\n'.encode())
        while any(owner.process is not None and owner.process.poll() is None
                  for _, _, owner, _, _ in running):
            if ((cancel is not None and cancel.is_set())
                    or time.monotonic() >= deadline - reserve):
                failed = True
                output.write(b'native CI cancelled or whole deadline exhausted\n')
                break
            time.sleep(min(0.05, max(0, deadline - reserve - time.monotonic())))
    except BaseException as error:
        interrupted = error
        failed = True
    finally:
        # Close *every* tree even if another owner fails. File redirection avoids
        # unjoined reader threads and pipe backpressure; replay happens afterward.
        for lane, _, owner, _, log in running:
            try:
                owner.stop([], timeout=min(3.0, max(0, deadline - time.monotonic())))
                if not owner.closed or (owner.process is not None
                                        and os.name == 'nt' and not owner.tree_empty):
                    raise RuntimeError('owned tree closure incomplete')
                if owner.process is None or owner.process.returncode != 0:
                    failed = True
            except BaseException as error:
                failed = True
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    interrupted = error
                cleanup_messages.append(f'lane {lane.name} cleanup failed: {type(error).__name__}\n')
            finally:
                try:
                    log.close()
                except BaseException as error:
                    failed = True
                    if isinstance(error, (KeyboardInterrupt, SystemExit)):
                        interrupted = error
                    cleanup_messages.append(f'lane {lane.name} log close failed: {type(error).__name__}\n')
        # Diagnostics cannot interrupt ownership closure of the other lane.
        for message in cleanup_messages:
            try:
                output.write(message.encode())
            except Exception:
                failed = True
        for lane, directory, owner, path, _ in running:
            try:
                if not _replay_and_check(path, lane, output):
                    failed = True
                    output.write(f'lane {lane.name} failed or result evidence incomplete\n'.encode())
            except BaseException as error:
                failed = True
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    interrupted = error
                output.write(f'lane {lane.name} log replay failed: {type(error).__name__}\n'.encode())
            finally:
                try:
                    owner.cleanup_private_directory(directory)
                except BaseException as error:
                    failed = True
                    if isinstance(error, (KeyboardInterrupt, SystemExit)):
                        interrupted = error
                    output.write(f'lane {lane.name} private cleanup failed: {type(error).__name__}\n'.encode())
        output.flush()
    if interrupted is not None:
        raise interrupted
    return int(failed or time.monotonic() > deadline)


def _quote_ps(value):
    return "'" + str(value).replace("'", "''") + "'"


def _write_scripts(directory, python, token):
    offline = directory / 'offline.py'
    commands = [(gate, str(ROOT / 'tools' / script), mode)
                for gate, script, mode in OFFLINE_TESTS]
    offline.write_text(
        'import json,subprocess,sys\n'
        f'commands={commands!r}\ntoken={token!r}\nfailed=False\n'
        'for gate,script,mode in commands:\n'
        ' try:\n'
        '  status=subprocess.run([sys.executable,script,*([mode] if mode else [])],check=False).returncode\n'
        ' except Exception:\n'
        '  status=-1\n'
        f' print({PREFIX!r}+json.dumps(dict(token=token,gate=gate,status=status)),flush=True)\n'
        ' failed=failed or status!=0\n'
        'raise SystemExit(int(failed))\n', encoding='utf-8')
    fixture = directory / 'fixture.ps1'
    pg = _quote_ps(ROOT / 'tools/native_store_ci_postgres.ps1')
    temporal = _quote_ps(ROOT / 'tools/native_temporal_ci_server.ps1')
    lines = ["$ErrorActionPreference = 'Stop'", '$failed = $false',
             'function Invoke-Gate($gate, [scriptblock]$body) {',
             '  $status = -1',
             '  try { $global:LASTEXITCODE = 0; & $body | ForEach-Object { [Console]::Out.WriteLine($_) }; $status = $LASTEXITCODE }',
             '  catch { [Console]::Out.WriteLine($_); $status = -1 }',
             f'  [Console]::Out.WriteLine(\'{PREFIX}\' + (@{{token={_quote_ps(token)};gate=$gate;status=[int]$status}} | ConvertTo-Json -Compress))',
             '  if ($status -ne 0) { $script:failed = $true }',
             '  return ($status -eq 0)',
             '}',
             'try {',
             f"  $pgReady = Invoke-Gate 'postgres-start' {{ & {pg} -Action start }}",
             '  if ($pgReady -ne $true) { throw \'PostgreSQL prerequisite failed\' }',
             f"  $null = Invoke-Gate 'native-store' {{ & {_quote_ps(python)} {_quote_ps(ROOT / 'tools/run_native_store_tests.py')} --postgres }}",
             f"  $temporalReady = Invoke-Gate 'temporal-start' {{ & {temporal} -Action start }}",
             '  if ($temporalReady -ne $true) { throw \'Temporal prerequisite failed\' }']
    # Native store then all nine original final-shell modes: no selection changes.
    for gate, script, mode in FIXTURE_TESTS[1:]:
        lines.append(f"  $null = Invoke-Gate {_quote_ps(gate)} {{ & {_quote_ps(python)} {_quote_ps(ROOT / 'tools' / script)} {mode} }}")
    lines.extend(['} catch { Write-Output $_; $failed = $true } finally {',
                  f"  $null = Invoke-Gate 'temporal-stop' {{ & {temporal} -Action stop }}",
                  f"  $null = Invoke-Gate 'postgres-stop' {{ & {pg} -Action stop }}",
                  '}', 'if ($failed) { exit 1 }', 'exit 0'])
    fixture.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return (
        Lane('offline', (str(python), str(offline)), tuple(g for g, _, _ in commands), token),
        Lane('fixture', ('pwsh', '-NoLogo', '-NoProfile', '-NonInteractive',
                         '-File', str(fixture)),
             ('postgres-start', 'native-store', 'temporal-start',
              *(g for g, _, _ in FIXTURE_TESTS[1:]), 'temporal-stop', 'postgres-stop'), token),
    )


def main():
    if (sys.argv[1:] or os.name != 'nt' or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'
            or not os.environ.get('RUNNER_TEMP')
            or not Path(os.environ['RUNNER_TEMP']).is_dir()):
        raise SystemExit('hosted Windows CI only; no arguments accepted')
    python = Path(sys.executable).absolute()  # preserve the locked venv identity
    if python != (ROOT / 'backend/.venv/Scripts/python.exe').absolute():
        raise SystemExit('locked backend interpreter required')
    with tempfile.TemporaryDirectory(prefix='nexaweave-native-ci-plan-') as directory:
        lanes = _write_scripts(Path(directory), python, uuid4().hex)
        return _run_lanes(lanes, environment=os.environ, output=sys.stdout.buffer,
                          sanitize_offline=True)


if __name__ == '__main__':
    raise SystemExit(main())

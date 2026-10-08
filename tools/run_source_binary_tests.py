"""Main-owned original PDF qualification: installed package, finite owned child."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_unit_tests import LoopbackOnlySockets, _unit_environment

UNIT = (ROOT/'services/knowledge/tests/test_source_binary.py',
        ROOT/'services/knowledge/tests/test_source_store.py',
        ROOT/'services/knowledge/tests/test_source_library.py',
        ROOT/'backend/tests/test_source_library_api.py')
POSTGRES = (ROOT/'services/knowledge/tests/test_source_binary_postgres.py',
            ROOT/'services/knowledge/tests/test_source_store_postgres.py',
            ROOT/'services/knowledge/tests/test_source_library_postgres.py',
            ROOT/'services/knowledge/tests/test_source_library_http_integration.py')

def child(mode):
    guard = LoopbackOnlySockets()
    guard.install()
    try:
        installed = Path(sys.prefix).resolve()
        for package in ('nexaweave_storage', 'nexaweave_knowledge'):
            spec = importlib.util.find_spec(package)
            if spec is None or spec.origin is None or not Path(spec.origin).resolve().is_relative_to(installed) or 'site-packages' not in Path(spec.origin).parts:
                print('source_binary_installed_package_required', file=sys.stderr)
                return 1
        import pytest
        targets = POSTGRES if mode == 'postgres' else UNIT
        if not all(path.is_file() for path in targets):
            print('source_binary_fixture_missing', file=sys.stderr)
            return 1
        class Accounting:
            collected = 0
            passed = 0
            skipped = 0
            names = {}
            per_target = {path.name: 0 for path in targets}
            def pytest_collection_finish(self, session):
                self.collected = len(session.items)
                self.names = {item.nodeid: Path(item.path).name for item in session.items}
            def pytest_runtest_logreport(self, report):
                self.skipped += int(report.skipped)
                if report.when == 'call' and report.passed:
                    self.passed += 1
                    self.per_target[self.names[report.nodeid]] += 1
        accounting = Accounting()
        result = int(pytest.main(['-q','--rootdir',str(ROOT),'-p','pytest_asyncio.plugin',
            '-o','asyncio_default_fixture_loop_scope=function','-o','markers=postgres: approved owned PostgreSQL fixture',
            *map(str,targets)],plugins=[accounting]))
        complete = result == 0 and accounting.collected > 0 and accounting.collected == accounting.passed and accounting.skipped == 0 and all(accounting.per_target.values()) and not guard.blocked_attempts
        for name,module in tuple(sys.modules.items()):
            if name.split('.')[0] not in ('nexaweave_storage','nexaweave_knowledge'):
                continue
            origin = getattr(module,'__file__',None)
            if origin is not None and (not Path(origin).resolve().is_relative_to(installed) or 'site-packages' not in Path(origin).parts):
                print('source_binary_loaded_source_package_refused',file=sys.stderr)
                complete = False
        print(json.dumps(dict(source_binary_mode=mode,collected=accounting.collected,passed=accounting.passed,
            skipped=accounting.skipped,targets=accounting.per_target,complete=bool(complete)),sort_keys=True))
        return result if complete else 1
    finally:
        guard.restore()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--unit',action='store_true')
    modes.add_argument('--postgres',action='store_true')
    parser.add_argument('--child',action='store_true',help=argparse.SUPPRESS)
    args = parser.parse_args()
    mode = 'postgres' if args.postgres else 'unit'
    if args.child:
        return child(mode)
    password = os.environ.get('PROJECT_STORE_TEST_PASSWORD')
    from nexaweave_configuration import environment
    backend = environment.get('NEXAWEAVE_WORKBENCH_BACKEND_PYTHON')
    if mode == 'postgres' and (not password or not backend or not Path(backend).is_absolute() or not Path(backend).is_file()):
        parser.error('explicit approved fixture password and locked HTTP interpreter required')
    spec = importlib.util.spec_from_file_location('_source_binary_owned',ROOT/'backend/app/utils/owned_process.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    directory = tempfile.TemporaryDirectory(prefix='nexaweave-source-original-')
    owner = helper.OwnedProcess()
    owner.bind_private_directory(directory)
    try:
        env = _unit_environment(Path(directory.name))
        env.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8',GRAPHITI_TELEMETRY_ENABLED='false')
        env['PYTHONPATH'] = os.pathsep.join((str(ROOT),str(ROOT/'backend'),str(ROOT/'backend/tests'),str(ROOT/'services/knowledge/tests')))
        if mode == 'postgres':
            from psycopg.conninfo import make_conninfo
            bootstrap = subprocess.run([sys.executable,'-I','-c',
                "import importlib.util; from pathlib import Path; p=Path(importlib.util.find_spec('nexaweave_knowledge.read_bootstrap').origin).resolve(); assert 'site-packages' in p.parts; print(p)"],
                env=env,cwd=directory.name,capture_output=True,timeout=30,check=False)
            if bootstrap.returncode or bootstrap.stderr:
                print('source_binary_installed_bootstrap_required',file=sys.stderr)
                return 1
            env.update(PROJECT_STORE_POSTGRES_INTEGRATION='1',KNOWLEDGE_POSTGRES_INTEGRATION='1',
                NEXAWEAVE_WORKBENCH_BACKEND_PYTHON=backend,KNOWLEDGE_PYTHON=str(Path(sys.executable).resolve()),
                KNOWLEDGE_BOOTSTRAP_SCRIPT=bootstrap.stdout.decode().strip(),
                PROJECT_STORE_POSTGRES_TEST_DSN=make_conninfo(host='127.0.0.1',port=15432,dbname='mirofish_operations_test',
                    user='mirofish_fixture',password=password,connect_timeout=5))
        process = owner.start(subprocess.Popen,[sys.executable,str(Path(__file__).resolve()),'--child','--'+mode],
            cwd=directory.name,env=env,stdin=subprocess.DEVNULL,shell=False,close_fds=True,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            return process.wait(timeout=180 if mode == 'unit' else 360)
        except subprocess.TimeoutExpired:
            print('source_binary_qualification_timeout',file=sys.stderr)
            return 1
    finally:
        owner.stop([])
        assert owner.closed and (os.name != 'nt' or owner.tree_empty)
        owner.cleanup_private_directory(directory)
        print('source_binary_owned_tree_private_cwd_closed',flush=True)

if __name__ == '__main__':
    raise SystemExit(main())

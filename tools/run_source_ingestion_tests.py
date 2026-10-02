"""Main qualification: real PG/Neo4j/fixed child and canned local SDK calls."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_unit_tests import LoopbackOnlySockets, _unit_environment
TARGETS = ('test_source_ingestion_host.py', 'test_source_ingestion_host_postgres.py',
           'test_source_ingestion_http_integration.py')


def child():
    import pytest
    guard = LoopbackOnlySockets(); guard.install()
    class Results:
        passed = {name: 0 for name in TARGETS}
        skipped = 0
        def pytest_runtest_logreport(self, report):
            self.skipped += int(report.skipped)
            if report.when == 'call' and report.passed:
                for name in TARGETS:
                    if name + '::' in report.nodeid: self.passed[name] += 1
    results = Results()
    try:
        status = int(pytest.main(['-q','-p','pytest_asyncio.plugin','--tb=short',
            *(str(ROOT/'services/knowledge/tests'/name) for name in TARGETS)], plugins=[results]))
    finally:
        guard.restore()
    if guard.blocked_attempts or results.skipped or not all(results.passed.values()):
        print('source_ingestion_qualification_incomplete', file=sys.stderr)
        return 1
    print('source_ingestion_groups=' + repr(results.passed), flush=True)
    return status


def main():
    if sys.argv[1:] == ['--child']: return child()
    if sys.argv[1:]: raise SystemExit('no user-selected fixture arguments supported')
    password = os.environ.get('PROJECT_STORE_TEST_PASSWORD')
    neo_password = os.environ.get('KNOWLEDGE_TEST_PASSWORD')
    backend = os.environ.get('MIROFISH_WORKBENCH_BACKEND_PYTHON')
    if not password or not neo_password or not backend or not Path(backend).is_absolute() or not Path(backend).is_file():
        raise SystemExit('approved fixture secrets and locked HTTP backend required')
    from psycopg.conninfo import make_conninfo
    spec = importlib.util.spec_from_file_location('main_source_ingestion_runner_owner',
                                                 ROOT/'backend/app/utils/owned_process.py')
    helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    directory = tempfile.TemporaryDirectory(prefix='mirofish-ingestion-qualified-')
    owner = helper.OwnedProcess(); owner.bind_private_directory(directory)
    threads, read_errors = [], []
    def forward(pipe):
        try:
            while True:
                value = pipe.read1(65536)
                if not value: break
                sys.stdout.buffer.write(value); sys.stdout.buffer.flush()
        except (OSError, ValueError): read_errors.append(True)
    try:
        env = _unit_environment(Path(directory.name))
        env['PYTHONPATH'] = os.pathsep.join([str(ROOT), str(ROOT/'services/knowledge/tests')])
        env['GRAPHITI_TELEMETRY_ENABLED'] = 'false'
        installed = subprocess.run([sys.executable,'-I','-c',
            "import mirofish_knowledge.source_ingestion_bootstrap as m; from pathlib import Path; "
            "p=Path(m.__file__).resolve(); assert 'site-packages' in p.parts; "
            "print(p.with_name('read_bootstrap.py'))"], env=env, cwd=directory.name,
            capture_output=True, timeout=30, check=False)
        if installed.returncode or installed.stderr:
            raise SystemExit('fresh non-editable installed ingestion candidate required')
        env.update(PROJECT_STORE_POSTGRES_INTEGRATION='1', KNOWLEDGE_INTEGRATION='1',
            KNOWLEDGE_TEST_PASSWORD=neo_password, MIROFISH_WORKBENCH_BACKEND_PYTHON=backend,
            KNOWLEDGE_PYTHON=str(Path(sys.executable).absolute()),
            KNOWLEDGE_BOOTSTRAP_SCRIPT=installed.stdout.decode().strip(),
            PROJECT_STORE_POSTGRES_TEST_DSN=make_conninfo(host='127.0.0.1',port=15432,
                dbname='mirofish_operations_test',user='mirofish_fixture',password=password,connect_timeout=5))
        process = owner.start(subprocess.Popen,[sys.executable,str(Path(__file__).resolve()),'--child'],
            cwd=directory.name, env=env, shell=False, close_fds=True, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        reader = threading.Thread(target=forward,args=(process.stdout,),daemon=True)
        threads.append(reader); reader.start()
        status = process.wait(timeout=420)
    finally:
        try:
            owner.stop(threads)
            assert owner.closed and (os.name != 'nt' or owner.tree_empty) and not read_errors
        finally:
            owner.cleanup_private_directory(directory)
    return status


if __name__ == '__main__': raise SystemExit(main())

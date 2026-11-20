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
LOCAL_EMBEDDING_NODE = 'test_real_protected_http_selected_retained_passage'
LOCAL_EMBEDDING_PORT = 'NEXAWEAVE_W63_EMBEDDING_PORT'


def embedding_port():
    value = os.environ.get(LOCAL_EMBEDDING_PORT, '')
    if (not 1 <= len(value) <= 5 or not value.isascii() or not value.isdecimal()
            or not 1 <= int(value) <= 65535 or str(int(value)) != value):
        raise SystemExit('explicit approved numeric loopback embedding port required')
    return int(value)


def child(*, local_embedding=False):
    if local_embedding:
        embedding_port()
    import pytest
    guard = LoopbackOnlySockets(); guard.install()
    class Results:
        passed = {name: 0 for name in TARGETS}
        skipped = 0
        collected = 0
        def pytest_collection_finish(self, session):
            self.collected = len(session.items)
        def pytest_runtest_logreport(self, report):
            self.skipped += int(report.skipped)
            if report.when == 'call' and report.passed:
                for name in TARGETS:
                    if name + '::' in report.nodeid: self.passed[name] += 1
    results = Results()
    tests = ROOT/'services/knowledge/tests'
    targets = [f'{tests / TARGETS[2]}::{LOCAL_EMBEDDING_NODE}'] if local_embedding else [
        str(tests / name) for name in TARGETS]
    try:
        status = int(pytest.main(['-q','-p','pytest_asyncio.plugin','--tb=short',
            *targets], plugins=[results]))
    finally:
        guard.restore()
    if local_embedding:
        if status or guard.blocked_attempts or results.skipped or results.collected != 1 or results.passed[TARGETS[2]] != 1:
            print('source_ingestion_local_embedding_incomplete', file=sys.stderr)
            return 1
        return 0
    if guard.blocked_attempts or results.skipped or not all(results.passed.values()):
        print('source_ingestion_qualification_incomplete', file=sys.stderr)
        return 1
    print('source_ingestion_groups=' + repr(results.passed), flush=True)
    return status


def main():
    args = sys.argv[1:]
    if args == ['--child']:
        os.environ.pop(LOCAL_EMBEDDING_PORT, None)
        return child()
    if args == ['--child', '--local-embedding']:
        return child(local_embedding=True)
    if args not in ([], ['--local-embedding']):
        raise SystemExit('only the fixed --local-embedding fixture may be selected')
    port = embedding_port() if args else None
    password = os.environ.get('PROJECT_STORE_TEST_PASSWORD')
    neo_password = os.environ.get('KNOWLEDGE_TEST_PASSWORD')
    backend = os.environ.get('NEXAWEAVE_WORKBENCH_BACKEND_PYTHON')
    if not password or not neo_password or not backend or not Path(backend).is_absolute() or not Path(backend).is_file():
        raise SystemExit('approved fixture secrets and locked HTTP backend required')
    from psycopg.conninfo import make_conninfo
    spec = importlib.util.spec_from_file_location('main_source_ingestion_runner_owner',
                                                 ROOT/'backend/app/utils/owned_process.py')
    helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    directory = tempfile.TemporaryDirectory(prefix='nexaweave-ingestion-qualified-')
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
            "import nexaweave_knowledge.source_ingestion_bootstrap as m; from pathlib import Path; "
            "p=Path(m.__file__).resolve(); assert 'site-packages' in p.parts; "
            "print(p.with_name('read_bootstrap.py'))"], env=env, cwd=directory.name,
            capture_output=True, timeout=30, check=False)
        if installed.returncode or installed.stderr:
            raise SystemExit('fresh non-editable installed ingestion candidate required')
        env.update(PROJECT_STORE_POSTGRES_INTEGRATION='1', KNOWLEDGE_INTEGRATION='1',
            KNOWLEDGE_TEST_PASSWORD=neo_password, NEXAWEAVE_WORKBENCH_BACKEND_PYTHON=backend,
            KNOWLEDGE_PYTHON=str(Path(sys.executable).absolute()),
            KNOWLEDGE_BOOTSTRAP_SCRIPT=installed.stdout.decode().strip(),
            PROJECT_STORE_POSTGRES_TEST_DSN=make_conninfo(host='127.0.0.1',port=15432,
                dbname='mirofish_operations_test',user='mirofish_fixture',password=password,connect_timeout=5))
        if port is not None:
            env[LOCAL_EMBEDDING_PORT] = str(port)
        process = owner.start(subprocess.Popen,[sys.executable,str(Path(__file__).resolve()),'--child', *args],
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
    if port is not None and status == 0:
        if (not owner.closed or (os.name == 'nt' and not owner.tree_empty)
                or read_errors or Path(directory.name).exists()):
            raise RuntimeError('source ingestion local embedding cleanup incomplete')
        print('source_ingestion_local_embedding=1', flush=True)
    return status


if __name__ == '__main__': raise SystemExit(main())

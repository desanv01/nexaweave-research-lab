"""Main-owned guarded PG and fresh HTTP source-retention qualification."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.run_unit_tests import LoopbackOnlySockets,_unit_environment
TARGETS=('test_source_library_postgres.py','test_source_library_http_integration.py')


def child():
    import pytest
    guard=LoopbackOnlySockets();guard.install()
    class Results:
        passed={name:0 for name in TARGETS}
        skipped=0
        def pytest_runtest_logreport(self,report):
            self.skipped+=int(report.skipped)
            if report.when=='call' and report.passed:
                for name in TARGETS:
                    if name+'::' in report.nodeid: self.passed[name]+=1
    results=Results()
    try:
        status=int(pytest.main(['-q','-p','pytest_asyncio.plugin','-o','markers=postgres: guarded PostgreSQL integration',
            *(str(ROOT/'services/knowledge/tests'/name) for name in TARGETS)],plugins=[results]))
    finally:
        guard.restore()
    if guard.blocked_attempts or results.skipped or not all(results.passed.values()):
        print('source_library_qualification_incomplete',file=sys.stderr)
        return 1
    return status


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.child: return child()
    with tempfile.TemporaryDirectory(prefix='mirofish-source-library-') as directory:
        env=_unit_environment(Path(directory))
        env['PYTHONPATH']=os.pathsep.join([str(ROOT),str(ROOT/'services/knowledge/src'),str(ROOT/'services/knowledge/tests')])
        env['GRAPHITI_TELEMETRY_ENABLED']='false'
        password=os.environ.get('PROJECT_STORE_TEST_PASSWORD')
        backend=os.environ.get('MIROFISH_WORKBENCH_BACKEND_PYTHON')
        if not password or not backend or not Path(backend).is_absolute() or not Path(backend).is_file():
            parser.error('approved fixture password and locked backend interpreter required')
        installed=subprocess.run([sys.executable,'-I','-c',
            "import mirofish_knowledge.source_bootstrap as m; from pathlib import Path; p=Path(m.__file__).resolve(); "
            "assert 'site-packages' in p.parts; print(p.with_name('read_bootstrap.py'))"],
            env=env,cwd=directory,capture_output=True,timeout=30,check=False)
        if installed.returncode or installed.stderr:
            parser.error('non-editable installed source candidate required')
        from psycopg.conninfo import make_conninfo
        env.update(PROJECT_STORE_POSTGRES_INTEGRATION='1',MIROFISH_WORKBENCH_BACKEND_PYTHON=backend,
            KNOWLEDGE_PYTHON=str(Path(sys.executable).absolute()),KNOWLEDGE_BOOTSTRAP_SCRIPT=installed.stdout.decode().strip(),
            PROJECT_STORE_POSTGRES_TEST_DSN=make_conninfo(host='127.0.0.1',port=15432,
                dbname='mirofish_operations_test',user='mirofish_fixture',password=password,connect_timeout=5))
        try:
            return subprocess.run([sys.executable,str(Path(__file__).resolve()),'--child'],
                env=env,cwd=directory,timeout=360,check=False).returncode
        except subprocess.TimeoutExpired:
            print('source_library_qualification_timeout',file=sys.stderr)
            return 1


if __name__=='__main__': raise SystemExit(main())

"""Actual isolated read-session and CI-controller lifecycle qualification."""
from pathlib import Path
import importlib.util
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.run_unit_tests import _unit_environment

def main():
    if sys.argv[1:]==['--child']:
        import pytest
        from tools.run_unit_tests import LoopbackOnlySockets
        guard=LoopbackOnlySockets();guard.install()
        try:
            result=int(pytest.main(['-q','-p','pytest_asyncio.plugin',
                str(ROOT/'backend/tests/test_knowledge_read_session.py'),
                str(ROOT/'backend/tests/test_native_ci_qualification.py'),
                str(ROOT/'services/knowledge/tests/test_read_session.py')]))
        finally:guard.restore()
        return 1 if guard.blocked_attempts else result
    if sys.argv[1:]:raise SystemExit('no arguments except private child')
    spec=importlib.util.spec_from_file_location('_read_boundary_owner',ROOT/'backend/app/utils/owned_process.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    directory=tempfile.TemporaryDirectory(prefix='nexaweave-read-boundary-')
    owner=module.OwnedProcess();owner.bind_private_directory(directory)
    try:
        process=owner.start(subprocess.Popen,[sys.executable,str(Path(__file__).absolute()),'--child'],
            cwd=directory.name,env=_unit_environment(Path(directory.name)),stdin=subprocess.DEVNULL,
            shell=False,close_fds=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return process.wait(timeout=90)
    finally:
        try:
            owner.stop([])
            assert owner.closed and (sys.platform!='win32' or owner.tree_empty)
        finally:owner.cleanup_private_directory(directory)
        print('read_boundary_owned_trees_private_cwd_closed',flush=True)

if __name__=='__main__':raise SystemExit(main())

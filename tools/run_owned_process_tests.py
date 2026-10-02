"""Main-owned actual Windows tree-lifetime and inherited parser qualification."""
import os
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.run_unit_tests import LoopbackOnlySockets,_unit_environment
TARGETS=('test_owned_process.py','test_parser_process.py')
WINDOWS_GROUPS={
    'test_real_windows_venv_descendant_cwd_and_stdout_lifetime':2,
    'test_windows_knowledge_descendant_cleanup_and_lock_reuse':5,
    'test_windows_knowledge_startup_failure_is_owned_and_reusable':2,
    'test_windows_descendant_parser_cleanup_and_repeated_reuse':5,
    'test_windows_parser_startup_failure_has_no_child_code_or_retry':2,
}

def child():
    import pytest
    guard=LoopbackOnlySockets();guard.install()
    class Results:
        passed={name:0 for name in TARGETS}
        groups={name:0 for name in WINDOWS_GROUPS}
        unexpected_skips=[]
        def pytest_runtest_logreport(self,report):
            if report.skipped and not report.nodeid.replace('\\','/').endswith('test_owned_process.py::test_real_posix_smoke_has_no_job_or_windows_flags'):
                self.unexpected_skips.append(report.nodeid)
            if report.when=='call' and report.passed:
                for name in TARGETS:
                    if name+'::' in report.nodeid: self.passed[name]+=1
                for name in WINDOWS_GROUPS:
                    if '::'+name+'[' in report.nodeid: self.groups[name]+=1
    results=Results()
    try:
        status=int(pytest.main(['-q','--tb=short',*(str(ROOT/'backend/tests'/n) for n in TARGETS)],plugins=[results]))
    finally: guard.restore()
    if (guard.blocked_attempts or results.unexpected_skips or not all(results.passed.values())
            or any(results.groups[name]!=count for name,count in WINDOWS_GROUPS.items())):
        print('owned_windows_process_qualification_incomplete',file=sys.stderr)
        return 1
    return status

def main():
    if os.name!='nt': raise SystemExit('actual Windows required; no substituted skip accepted')
    if sys.argv[1:]==['--child']: return child()
    if sys.argv[1:]: raise SystemExit('no arguments accepted')
    spec=importlib.util.spec_from_file_location('main_windows_gate_owner',ROOT/'backend/app/utils/owned_process.py')
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    private_directory=tempfile.TemporaryDirectory(prefix='mirofish-owned-windows-tests-')
    directory=private_directory.name
    owner=helper.OwnedProcess();owner.bind_private_directory(private_directory)
    readers=[];read_errors=[]
    def forward_output(pipe):
        try:
            while True:
                chunk=pipe.read1(65536)
                if not chunk: break
                sys.stdout.buffer.write(chunk);sys.stdout.buffer.flush()
        except (OSError,ValueError):
            read_errors.append(True)
    try:
        process=owner.start(subprocess.Popen,[sys.executable,str(Path(__file__).resolve()),'--child'],
            env=_unit_environment(Path(directory)),cwd=directory,shell=False,close_fds=True,
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        reader=threading.Thread(target=forward_output,args=(process.stdout,),daemon=True,
            name='mirofish-owned-gate-output')
        readers.append(reader);reader.start()
        status=process.wait(timeout=180)
        return status
    except subprocess.TimeoutExpired:
        print('owned_windows_process_qualification_timeout',file=sys.stderr)
        return 1
    except helper.OwnedProcessError:
        print('owned_windows_process_qualification_startup_failed',file=sys.stderr)
        return 1
    finally:
        try:
            owner.stop(readers)
            if owner.process is not None and not (owner.closed and owner.tree_empty):
                raise RuntimeError('owned Windows gate cleanup incomplete')
            if read_errors: raise RuntimeError('owned Windows gate output incomplete')
        finally:
            owner.cleanup_private_directory(private_directory)

if __name__=='__main__': raise SystemExit(main())

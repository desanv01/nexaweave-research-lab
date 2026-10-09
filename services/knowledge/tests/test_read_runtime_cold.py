"""Read-only child imports must work when every model SDK import is denied."""
import subprocess
import sys

import pytest


@pytest.mark.parametrize('module', ['read_bootstrap', 'read_runtime', 'evidence_research'])
def test_read_entrypoints_deny_model_sdk_imports_in_fresh_interpreter(module):
    program = r'''
import importlib, importlib.abc, sys
denied = ('graphiti_core', 'openai', 'camel', 'oasis')
class DenyModels(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in denied:
            raise ImportError('read path attempted a model SDK import')
sys.meta_path.insert(0, DenyModels())
importlib.import_module('nexaweave_knowledge.' + sys.argv[1])
assert not any(name.split('.')[0] in denied for name in sys.modules)
from nexaweave_knowledge.read_runtime import _DirectPageProvider
from nexaweave_knowledge.graph_page_provider import GraphPageProvider
assert _DirectPageProvider.__bases__ == (GraphPageProvider,)
print('sdk-cold read import passed')
'''
    result = subprocess.run([sys.executable, '-I', '-c', program, module],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'sdk-cold read import passed'

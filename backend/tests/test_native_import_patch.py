"""Synthetic patch wiring; actual locked OASIS/native proof is Main-owned."""
import builtins
from types import ModuleType, SimpleNamespace

import pytest

from tools import apply_native_import_patch as patch


SOURCE = b'''# upstream license retained
import torch
from sentence_transformers import SentenceTransformer
device = "cpu"
def rec_sys_random(posts):
    return posts
def rec_sys_reddit(posts):
    return posts
def load_model(model_name):
    try:
        if model_name == 'paraphrase-MiniLM-L6-v2':
            return SentenceTransformer(model_name,
                                       device=device,
                                       cache_folder="./models")
        raise ValueError("unknown model")
    except Exception as error:
        raise Exception("Failed to load model") from error
'''


@pytest.fixture
def fingerprint(monkeypatch):
    monkeypatch.setattr(patch, 'BEFORE_SHA256', patch.digest(SOURCE))


def installed(tmp_path):
    target = tmp_path / patch.TARGET
    target.parent.mkdir(parents=True)
    target.write_bytes(SOURCE)
    return target


def namespace(monkeypatch, model):
    calls = []
    original_import = builtins.__import__
    def controlled_import(name, *args, **kwargs):
        if name == 'torch':
            return SimpleNamespace()
        if name == 'sentence_transformers':
            calls.append(name)
            return SimpleNamespace(SentenceTransformer=model)
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', controlled_import)
    result = {'__name__': 'synthetic_recsys'}
    exec(patch.transform_source(SOURCE), result)
    return result, calls


def test_lazy_class_identity_constructor_and_injected_global(monkeypatch):
    constructed = []
    class RealClassWitness:
        def __init__(self, *args, **kwargs):
            constructed.append((args, kwargs))
    module, imports = namespace(monkeypatch, RealClassWitness)
    assert imports == []
    assert module['rec_sys_random']([1]) == [1] and module['rec_sys_reddit']([2]) == [2]
    assert imports == []
    with pytest.raises(AttributeError):
        module['__getattr__']('unknown')
    assert imports == []
    assert module['__getattr__']('SentenceTransformer') is RealClassWitness
    assert module['__getattr__']('SentenceTransformer') is RealClassWitness
    assert imports == ['sentence_transformers']
    assert isinstance(module['load_model']('paraphrase-MiniLM-L6-v2'), RealClassWitness)
    assert constructed == [(('paraphrase-MiniLM-L6-v2',), {'device': 'cpu', 'cache_folder': './models'})]
    sentinel = object()
    module['SentenceTransformer'] = lambda *args, **kwargs: sentinel
    assert module['load_model']('paraphrase-MiniLM-L6-v2') is sentinel
    assert imports == ['sentence_transformers']


def test_constructor_failure_keeps_original_chain(monkeypatch):
    failure = RuntimeError('constructor witness')
    def model(*args, **kwargs):
        raise failure
    module, imports = namespace(monkeypatch, model)
    with pytest.raises(Exception, match='Failed to load model') as raised:
        module['load_model']('paraphrase-MiniLM-L6-v2')
    assert raised.value.__cause__ is failure
    assert imports == ['sentence_transformers']


def test_injected_global_before_first_access_never_imports_dependency(monkeypatch):
    module, imports = namespace(monkeypatch, object)
    sentinel = object()
    calls = []
    def injected(*args, **kwargs):
        calls.append((args, kwargs))
        return sentinel
    module['SentenceTransformer'] = injected
    assert module['__getattr__']('SentenceTransformer') is injected
    assert module['load_model']('paraphrase-MiniLM-L6-v2') is sentinel
    assert imports == []
    assert calls == [(('paraphrase-MiniLM-L6-v2',), {'device': 'cpu', 'cache_folder': './models'})]


def test_normal_module_attribute_and_from_import_delegate_real_class(monkeypatch):
    class RealClassWitness:
        pass
    values, imports = namespace(monkeypatch, RealClassWitness)
    module = ModuleType('synthetic_recsys')
    module.__dict__.update(values)
    # Bind the hook's globals to the actual synthetic module namespace.
    exec(patch.transform_source(SOURCE), module.__dict__)
    assert imports == []
    assert module.SentenceTransformer is RealClassWitness
    assert imports == ['sentence_transformers']
    assert module.SentenceTransformer is RealClassWitness
    with pytest.raises(AttributeError):
        getattr(module, 'unknown')
    original_import = builtins.__import__
    def module_import(name, *args, **kwargs):
        if name == 'synthetic_recsys':
            return module
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', module_import)
    imported = {}
    exec('from synthetic_recsys import SentenceTransformer', imported)
    assert imported['SentenceTransformer'] is RealClassWitness
    assert imports == ['sentence_transformers']


def test_actual_attribute_access_routes_import_failure(monkeypatch):
    module, _ = namespace(monkeypatch, object)
    original_import = builtins.__import__
    failure = ImportError('unavailable real dependency')
    def denied(name, *args, **kwargs):
        if name == 'sentence_transformers':
            raise failure
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', denied)
    with pytest.raises(ImportError) as raised:
        module['__getattr__']('SentenceTransformer')
    assert raised.value is failure and 'SentenceTransformer' not in module


def test_pinned_before_after_idempotent_and_exact_other_source(fingerprint, tmp_path):
    target = installed(tmp_path)
    after = patch.transform_source(SOURCE)
    assert patch.classify(SOURCE)[0] == 'before'
    assert patch.classify(after)[0] == 'after'
    unchanged = after.decode().replace(patch.HELPER, '').replace(patch.LAZY_CALL, patch.CALL)
    unchanged = unchanged.replace('import torch\n', 'import torch\n' + patch.EAGER)
    assert unchanged.encode() == SOURCE  # All algorithms/license/imports otherwise exact.
    with pytest.raises(patch.PatchRefused):
        patch.patch_target(target, tmp_path, apply=False)
    assert target.read_bytes() == SOURCE
    result = patch.patch_target(target, tmp_path, apply=True)
    assert result['after_sha256'] == patch.digest(after) and result['status'] == 'applied'
    assert target.read_bytes() == after
    assert patch.patch_target(target, tmp_path, apply=True)['status'] == 'verified'
    assert patch.patch_target(target, tmp_path, apply=False)['status'] == 'verified'
    assert patch.classify(SOURCE.replace(b'\n', b'\r\n'))[1] == SOURCE


def test_bad_fingerprint_and_changed_after_leave_bytes_untouched(fingerprint, tmp_path):
    target = installed(tmp_path)
    for raw in (SOURCE + b'# changed\n', patch.transform_source(SOURCE) + b'# changed\n'):
        target.write_bytes(raw)
        with pytest.raises(patch.PatchRefused):
            patch.patch_target(target, tmp_path, apply=True)
        assert target.read_bytes() == raw
    assert not list(target.parent.glob('.nexaweave-recsys-*'))


def test_atomic_replace_failure_keeps_original(fingerprint, tmp_path, monkeypatch):
    target = installed(tmp_path)
    def refused(*args):
        raise OSError('replace witness')
    monkeypatch.setattr(patch.os, 'replace', refused)
    with pytest.raises(OSError):
        patch.patch_target(target, tmp_path, apply=True)
    assert target.read_bytes() == SOURCE
    assert not list(target.parent.glob('.nexaweave-recsys-*'))


def test_only_target_cache_invalidated(fingerprint, tmp_path):
    target = installed(tmp_path)
    cache = target.parent / '__pycache__'
    cache.mkdir()
    own = cache / 'recsys.cpython-312.pyc'
    optimized = cache / 'recsys.cpython-312.opt-1.pyc'
    other = cache / 'platform.cpython-312.pyc'
    for path in (own, optimized, other):
        path.write_bytes(b'cached')
    patch.patch_target(target, tmp_path, apply=True)
    assert not own.exists() and not optimized.exists() and other.read_bytes() == b'cached'


def test_target_outside_prefix_refused(fingerprint, tmp_path):
    target = installed(tmp_path)
    prefix = tmp_path / 'other-prefix'
    prefix.mkdir()
    with pytest.raises(patch.PatchRefused):
        patch.patch_target(target, prefix, apply=True)
    assert target.read_bytes() == SOURCE


def test_cache_root_switch_refused_before_unlink(fingerprint, tmp_path, monkeypatch):
    target = installed(tmp_path)
    cache = target.parent / '__pycache__'
    cache.mkdir()
    own = cache / 'recsys.cpython-312.pyc'
    own.write_bytes(b'original cache')
    outside = tmp_path / 'outside'
    outside.mkdir()
    unrelated = outside / own.name
    unrelated.write_bytes(b'unrelated data')
    original_root = patch.cache_root
    original_replace = patch.os.replace
    switched = []
    def replacing(source, destination):
        original_replace(source, destination)
        switched.append(True)
    def switched_root(path, prefix):
        actual = original_root(path, prefix)
        return outside if switched else actual
    monkeypatch.setattr(patch.os, 'replace', replacing)
    monkeypatch.setattr(patch, 'cache_root', switched_root)
    with pytest.raises(patch.PatchRefused, match='directory changed'):
        patch.patch_target(target, tmp_path, apply=True)
    assert target.read_bytes() == patch.transform_source(SOURCE)
    assert own.read_bytes() == b'original cache'
    assert unrelated.read_bytes() == b'unrelated data'


@pytest.mark.parametrize('apply', [False, True])
def test_linked_cache_shape_refused_for_apply_and_check(fingerprint, tmp_path, monkeypatch, apply):
    target = installed(tmp_path)
    target.write_bytes(patch.transform_source(SOURCE))
    cache = target.parent / '__pycache__'
    cache.mkdir()
    own = cache / 'recsys.cpython-312.pyc'
    own.write_bytes(b'retained')
    original = patch.Path.is_symlink
    monkeypatch.setattr(patch.Path, 'is_symlink', lambda path: path == cache or original(path))
    with pytest.raises(patch.PatchRefused, match='linked module cache'):
        patch.patch_target(target, tmp_path, apply=apply)
    assert own.read_bytes() == b'retained'
    assert target.read_bytes() == patch.transform_source(SOURCE)


@pytest.mark.parametrize('version,files', [('0.2.6', [patch.TARGET]), ('0.2.5', []), ('0.2.5', ['other.py'])])
def test_distribution_mismatch_refused(monkeypatch, version, files):
    monkeypatch.setattr(patch.metadata, 'distribution', lambda name: SimpleNamespace(version=version, files=files))
    with pytest.raises(patch.PatchRefused):
        patch.discover()


def test_structural_transform_refuses_unknown_layout():
    with pytest.raises(patch.PatchRefused):
        patch.transform_source(SOURCE.replace(patch.CALL.encode(), b'            return unknown(model_name,\n'))

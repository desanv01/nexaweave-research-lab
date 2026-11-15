"""Synthetic patch wiring; actual locked OASIS/native proof is Main-owned."""
import builtins
import ast
import importlib
from importlib import metadata
from types import ModuleType, SimpleNamespace

import pytest

from tools import apply_native_import_patch as patch


SOURCE = b'''# upstream license retained
import torch
from sentence_transformers import SentenceTransformer
from .process_recsys_posts import (generate_post_vector,
                                   generate_post_vector_openai)
from .typing import ActionType, RecsysType
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
def rec_sys_personalized_twh(corpus, twhin_model, twhin_tokenizer, use_openai_embedding):
    if use_openai_embedding:
            all_post_vector_list = generate_post_vector_openai(corpus,
                                                               batch_size=1000)
    else:
            all_post_vector_list = generate_post_vector(twhin_model,
                                                        twhin_tokenizer,
                                                        corpus,
                                                        batch_size=1000)
    return all_post_vector_list
'''


@pytest.fixture
def fingerprint(monkeypatch):
    monkeypatch.setattr(patch, 'BEFORE_SHA256', patch.digest(SOURCE))
    monkeypatch.setattr(patch, 'SENTENCE_SHA256', patch.digest(patch.transform_sentence_source(SOURCE)))
    monkeypatch.setattr(patch, 'AFTER_SHA256', patch.digest(patch.transform_source(SOURCE)))


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
        if name == 'typing' and args and args[-1] == 1:
            return SimpleNamespace(ActionType=object(), RecsysType=object())
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
    unchanged = after.decode().replace(patch.COMBINED_HELPER, '').replace(patch.LAZY_CALL, patch.CALL)
    unchanged = unchanged.replace(patch.VECTOR_ANCHOR, patch.VECTOR_IMPORT + patch.VECTOR_ANCHOR)
    for before_call, after_call in patch.VECTOR_CALLS:
        unchanged = unchanged.replace(after_call, before_call)
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


def test_sentence_only_patch_requires_upgrade(fingerprint, tmp_path):
    target = installed(tmp_path)
    sentence = patch.transform_sentence_source(SOURCE)
    target.write_bytes(sentence)
    assert patch.classify(sentence)[0] == 'sentence'
    with pytest.raises(patch.PatchRefused):
        patch.patch_target(target, tmp_path, apply=False)
    assert target.read_bytes() == sentence
    assert patch.patch_target(target, tmp_path, apply=True)['status'] == 'applied'
    assert target.read_bytes() == patch.transform_source(SOURCE)
    assert patch.patch_target(target, tmp_path, apply=True)['status'] == 'verified'


def test_combined_fingerprint_cannot_be_overridden_by_cached_shape(fingerprint, tmp_path, monkeypatch):
    target = installed(tmp_path)
    target.write_bytes(patch.transform_source(SOURCE))
    before = target.read_bytes()
    monkeypatch.setattr(patch, 'AFTER_SHA256', '0' * 64)
    with pytest.raises(patch.PatchRefused):
        patch.patch_target(target, tmp_path, apply=True)
    assert target.read_bytes() == before


def test_twh_arguments_and_other_body_ast_preserved():
    after = patch.transform_source(SOURCE).decode()
    for before_call, after_call in patch.VECTOR_CALLS:
        after = after.replace(after_call, before_call)
    before_tree, after_tree = ast.parse(SOURCE), ast.parse(after)
    original = next(n for n in before_tree.body if isinstance(n, ast.FunctionDef) and n.name == 'rec_sys_personalized_twh')
    restored = next(n for n in after_tree.body if isinstance(n, ast.FunctionDef) and n.name == original.name)
    assert ast.dump(original, include_attributes=False) == ast.dump(restored, include_attributes=False)


@pytest.mark.parametrize('first', ['named', 'star'])
def test_actual_vector_functions_exports_dir_and_injection(monkeypatch, first):
    # Optional dependency absence in generic unit environments is explicit.
    # Main's locked native targeted run must exercise this case, not count skip.
    try:
        dist = metadata.distribution('camel-oasis')
    except metadata.PackageNotFoundError:
        pytest.skip('actual locked OASIS required for real-function witness')
    assert dist.version == patch.VERSION
    raw = dist.locate_file(patch.TARGET).read_bytes()
    _, _, combined = patch.classify(raw)
    module = ModuleType('oasis.social_platform.recsys_witness')
    module.__package__ = 'oasis.social_platform'
    exec(combined, module.__dict__)
    assert {'generate_post_vector', 'generate_post_vector_openai'} <= set(dir(module))
    original_import = builtins.__import__
    def witness_import(name, *args, **kwargs):
        if name == 'actual_recsys_witness':
            return module
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', witness_import)
    named, exports = {}, {}
    if first == 'star':
        exec('from actual_recsys_witness import *', exports)
    exec('from actual_recsys_witness import generate_post_vector, generate_post_vector_openai', named)
    if first == 'named':
        exec('from actual_recsys_witness import *', exports)
    vectors = importlib.import_module('oasis.social_platform.process_recsys_posts')
    for name in ('generate_post_vector', 'generate_post_vector_openai'):
        assert exports[name] is getattr(vectors, name)
        assert named[name] is getattr(vectors, name)
        assert getattr(module, name) is getattr(vectors, name)
    for value in (None, 17, RuntimeError('identity witness')):
        module.generate_post_vector_openai = value
        del module.generate_post_vector
        assert module._nexaweave_vector_function('generate_post_vector') is vectors.generate_post_vector
        assert module._nexaweave_vector_function('generate_post_vector_openai') is value
    with pytest.raises((ValueError, RuntimeError)) as direct:
        vectors.generate_post_vector(None, None, [], batch_size=1)
    with pytest.raises(type(direct.value)) as delegated:
        module.generate_post_vector(None, None, [], batch_size=1)
    assert direct.value.args == delegated.value.args == ('torch.cat(): expected a non-empty list of Tensors',)
    # NEVER invoke OpenAI vector callable or construct weights/providers.


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

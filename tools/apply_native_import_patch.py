"""Guarded camel-oasis 0.2.5 optional recommender import correction."""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

VERSION = '0.2.5'
TARGET = 'oasis/social_platform/recsys.py'
BEFORE_SHA256 = '5c27e5219599b682a4658455c93adf382c007dfa3757bdaf48c7ff73a4b92345'
EAGER = 'from sentence_transformers import SentenceTransformer\n'
ANCHOR = 'def load_model(model_name):\n'
CALL = '            return SentenceTransformer(model_name,\n'
LAZY_CALL = '            return _nexaweave_sentence_transformer()(model_name,\n'
HELPER = '''def _nexaweave_sentence_transformer():
    if "SentenceTransformer" not in globals():
        from sentence_transformers import SentenceTransformer
        globals()["SentenceTransformer"] = SentenceTransformer
    return globals()["SentenceTransformer"]


def __getattr__(name):
    if name == "SentenceTransformer":
        return _nexaweave_sentence_transformer()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


'''


class PatchRefused(ValueError):
    pass


def canonical(raw):
    text = raw.decode('utf-8')
    text = text.replace('\r\n', '\n')
    if '\r' in text or '\ufeff' in text:
        raise PatchRefused('noncanonical upstream encoding')
    return text.encode('utf-8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def transform_source(before):
    """Pure exact structural transform; input fingerprint is checked separately."""
    text = before.decode('utf-8')
    if (text.count(EAGER) != 1 or text.count(ANCHOR) != 1 or text.count(CALL) != 1
            or '_nexaweave_sentence_transformer' in text or 'def __getattr__(' in text):
        raise PatchRefused('upstream structure mismatch')
    return text.replace(EAGER, '', 1).replace(ANCHOR, HELPER + ANCHOR, 1).replace(CALL, LAZY_CALL, 1).encode('utf-8')


def classify(raw):
    normalized = canonical(raw)
    if digest(normalized) == BEFORE_SHA256:
        return 'before', normalized, transform_source(normalized)
    text = normalized.decode('utf-8')
    if text.count(HELPER) != 1 or text.count(LAZY_CALL) != 1:
        raise PatchRefused('unrecognized installed fingerprint')
    recovered = text.replace(HELPER, '', 1).replace(LAZY_CALL, CALL, 1)
    if recovered.count('import torch\n') != 1:
        raise PatchRefused('upstream import anchor mismatch')
    recovered = recovered.replace('import torch\n', 'import torch\n' + EAGER, 1).encode('utf-8')
    if digest(recovered) != BEFORE_SHA256 or transform_source(recovered) != normalized:
        raise PatchRefused('unrecognized installed fingerprint')
    return 'after', recovered, normalized


def safe_path(path, prefix):
    path, prefix = Path(path).absolute(), Path(prefix).resolve(strict=True)
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(prefix) or resolved == prefix:
        raise PatchRefused('target outside interpreter prefix')
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise PatchRefused('linked target refused')
        if part == prefix:
            break
    if not resolved.is_file():
        raise PatchRefused('target is not a regular file')
    return resolved


def discover():
    dist = metadata.distribution('camel-oasis')
    if dist.version != VERSION or not dist.files or TARGET not in {str(p).replace('\\', '/') for p in dist.files}:
        raise PatchRefused('pinned distribution required')
    return safe_path(dist.locate_file(TARGET), sys.prefix)


def cache_root(target, prefix):
    cache = target.parent / '__pycache__'
    boundary = Path(prefix).resolve(strict=True)
    for part in (cache, *cache.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise PatchRefused('linked module cache refused')
        if part == boundary:
            break
    if not cache.exists():
        return None
    resolved = cache.resolve(strict=True)
    if (not cache.is_dir() or not resolved.is_relative_to(boundary)
            or resolved.parent != target.parent):
        raise PatchRefused('escaped module cache refused')
    return resolved


def validate_cache(path, target, prefix, pinned_root):
    current_root = cache_root(target, prefix)
    if current_root != pinned_root:
        raise PatchRefused('module cache directory changed during patch')
    if not path.exists():
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise PatchRefused('linked module cache refused')
        return False
    resolved = safe_path(path, prefix)
    if resolved.parent != pinned_root:
        raise PatchRefused('module cache changed during patch')
    return True


def module_caches(target, prefix):
    root = cache_root(target, prefix)
    if root is None:
        return None, []
    result = []
    for path in root.iterdir():
        if re.fullmatch(r'recsys\.[A-Za-z0-9_-]+(?:\.opt-[0-9]+)?\.pyc', path.name):
            if not validate_cache(path, target, prefix, root):
                raise PatchRefused('invalid module cache')
            result.append(path)
    return root, result


def patch_target(target, prefix, *, apply):
    target = safe_path(target, prefix)
    raw = target.read_bytes()
    state, before, after = classify(raw)
    if not apply and state != 'after':
        raise PatchRefused('native import patch not installed')
    pinned_cache_root, caches = module_caches(target, prefix)
    if apply and raw != after:
        mode = stat.S_IMODE(target.stat().st_mode)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.nexaweave-recsys-', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(after)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            if target.read_bytes() != raw:
                raise PatchRefused('installed source changed during patch')
            if cache_root(target, prefix) != pinned_cache_root:
                raise PatchRefused('module cache directory changed during patch')
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink()
    if apply:
        for cache in caches:
            # Revalidate immediately before invalidating only this module.
            if validate_cache(cache, target, prefix, pinned_cache_root):
                cache.unlink(missing_ok=True)
    if cache_root(target, prefix) != pinned_cache_root:
        raise PatchRefused('module cache directory changed during patch')
    return dict(distribution='camel-oasis', version=VERSION,
                status='applied' if apply and state == 'before' else 'verified',
                before_sha256=digest(before), after_sha256=digest(after))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--apply', action='store_true')
    group.add_argument('--check', action='store_true')
    args = parser.parse_args()
    try:
        result = patch_target(discover(), sys.prefix, apply=args.apply)
    except Exception:
        print('native_import_patch_refused', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

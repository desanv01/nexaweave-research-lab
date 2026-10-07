"""Read-only SDK-free owned-output reader. No SQLite open or model imports."""
from contextlib import ExitStack, contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import stat
from decimal import Decimal
from .native_observations_client import (NativeObservationsError, LOG_BYTES, LINE_BYTES, RECORDS,
    DEPTH, OUTPUT_BYTES, AGGREGATE_BYTES, COUNT_KEYS, output_names, digest)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _finite(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError
    return Decimal(value)


def _tree(value, depth=0):
    if type(value) in (dict, list):
        depth += 1
        if depth > DEPTH:
            raise ValueError
        if type(value) is dict:
            for key, child in value.items():
                key.encode('utf-8'); _tree(child, depth)
        else:
            for child in value:
                _tree(child, depth)
    elif type(value) is str:
        value.encode('utf-8')


def parse_record(raw):
    """Keep original lexemes; parsed values exist only for admission/counts."""
    if len(raw) > LINE_BYTES:
        raise NativeObservationsError('result_too_large')
    try:
        text = raw.decode('utf-8', errors='strict')
        if not text.strip() or '\n' in text or '\r' in text:
            raise ValueError
        value = json.loads(text, object_pairs_hook=_unique, parse_int=_finite,
                           parse_float=_finite, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if type(value) is not dict:
            raise ValueError
        _tree(value)
        counts = dict.fromkeys(COUNT_KEYS, 0)
        counts['event_records'] = int(type(value.get('event_type')) is str)
        action = type(value.get('action_type')) is str
        counts['action_records'] = int(action)
        counts['successful_action_records'] = int(action and value.get('success') is True)
        counts['failed_action_records'] = int(action and value.get('success') is False)
        return text, counts
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise NativeObservationsError('evidence_invalid') from None


def parse_log(blob, offset, limit):
    if len(blob) > LOG_BYTES:
        raise NativeObservationsError('result_too_large')
    lines = blob.split(b'\n') if blob else []
    if lines and lines[-1] == b'':
        lines.pop()  # One final newline is permitted, a blank physical line is not.
    if len(lines) > RECORDS:
        raise NativeObservationsError('result_too_large')
    counts, records = dict.fromkeys(COUNT_KEYS, 0), []
    for index, physical in enumerate(lines):
        # CR is removed only as part of CRLF, never from a final unterminated line.
        terminated = index < len(lines) - 1 or blob.endswith(b'\n')
        raw = physical[:-1] if terminated and physical.endswith(b'\r') else physical
        text, observed = parse_record(raw)
        for key in COUNT_KEYS:
            counts[key] += observed[key]
        if offset <= index < offset + limit:
            records.append({'index': index, 'record_sha256': hashlib.sha256(raw).hexdigest(), 'raw_json': text})
    if offset > len(lines):
        raise NativeObservationsError('invalid_request')
    end = offset + len(records)
    return {'total_records': len(lines), 'counts': counts, 'records': records,
            'next_offset': end if end < len(lines) else None}


def _identity(details):
    return (details.st_dev, details.st_ino, details.st_size, details.st_mtime_ns, details.st_ctime_ns, details.st_nlink)


def _safe(path, directory=False):
    details = path.lstat()
    if (stat.S_ISLNK(details.st_mode) or getattr(os.path, 'isjunction', lambda _: False)(path)
            or getattr(details, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)
            or not (stat.S_ISDIR(details.st_mode) if directory else stat.S_ISREG(details.st_mode))
            or not directory and details.st_nlink != 1):
        raise NativeObservationsError('evidence_invalid')
    return details


class NativeObservationReader:
    @contextmanager
    def page(self, root, platforms, platform, offset, limit, evidence_sha256):
        with self._read(root, platforms, platform, offset, limit, evidence_sha256) as result:
            yield result

    @contextmanager
    def context(self, root, platforms, windows, evidence_sha256):
        """Full-manifest lease with exact selected records, one physical read per file.

        Existing page parsing and limits stay unchanged. Connected complete
        coverage is all-or-refuse; the caller enforces its smaller context cap.
        Every platform's entire log is parsed even when only a window is selected.
        """
        with self._read(root, platforms, platforms[0], 0, 1, evidence_sha256,
                        selection=windows, connected=True) as result:
            yield result

    @contextmanager
    def _read(self, root, platforms, platform, offset, limit, evidence_sha256, *, selection=None, connected=False):
        """Hold descriptors through final authority validation; recheck every file/ancestor."""
        try:
            root = Path(root)
            names = output_names(list(platforms))
            if not root.is_absolute() or root.resolve(strict=True) != root or platform not in platforms:
                raise NativeObservationsError('evidence_invalid')
            ancestors = {}
            for path in (root, *root.parents):
                ancestors[path] = _identity(_safe(path, True))
            with ExitStack() as stack:
                opened, files, logs = [], [], {}
                total_size = 0
                for name in names:
                    path = root / name
                    for parent in path.parents:
                        if parent not in ancestors:
                            ancestors[parent] = _identity(_safe(parent, True))
                    before = _safe(path)
                    bound = LOG_BYTES if name.endswith('/actions.jsonl') else OUTPUT_BYTES
                    if before.st_size > bound:
                        raise NativeObservationsError('result_too_large')
                    total_size += before.st_size
                    if total_size > AGGREGATE_BYTES:
                        raise NativeObservationsError('result_too_large')
                    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
                    stack.callback(os.close, descriptor)
                    current = os.fstat(descriptor)
                    path_identity, descriptor_identity = _identity(before), _identity(current)
                    # Windows lstat/fstat timestamps can differ for an unchanged file.
                    # Cross-API binding uses common device/inode/size/link identity;
                    # timestamp stability remains exact within each API separately.
                    if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
                            or descriptor_identity[:3] != path_identity[:3]):
                        raise NativeObservationsError('evidence_invalid')
                    opened.append((path, descriptor, path_identity, descriptor_identity))
                    hasher, chunks, remaining = hashlib.sha256(), [], current.st_size
                    while remaining:
                        chunk = os.read(descriptor, min(remaining, 65536))
                        if not chunk:
                            raise NativeObservationsError('evidence_invalid')
                        remaining -= len(chunk); hasher.update(chunk)
                        if name.endswith('/actions.jsonl'):
                            chunks.append(chunk)
                    if os.read(descriptor, 1) or _identity(os.fstat(descriptor)) != descriptor_identity:
                        raise NativeObservationsError('evidence_invalid')
                    files.append({'name': name, 'sha256': hasher.hexdigest(), 'size': current.st_size})
                    if chunks or name.endswith('/actions.jsonl'):
                        logs[name.split('/')[0]] = b''.join(chunks)
                manifest = {'schema_version': 1, 'files': files}
                if digest(manifest) != evidence_sha256:
                    raise NativeObservationsError('evidence_invalid')
                pages = {p: parse_log(logs[p], offset if p == platform else 0, limit if p == platform else 1) for p in platforms}
                if connected:
                    from .connected_report_client import CONTEXT_BYTES, encoded
                    selected, coverage = {}, []
                    if selection is not None:
                        from mirofish_execution.report_contracts import windows
                        windows(selection)
                        if any(w['platform'] not in platforms for w in selection):
                            raise NativeObservationsError('invalid_request')
                    for p in platforms:
                        window = None if selection is None else next((w for w in selection if w['platform'] == p), None)
                        total = pages[p]['total_records']
                        start, count = (0, total) if selection is None else ((window['offset'], window['count']) if window else (0, 0))
                        if start + count > total:
                            raise NativeObservationsError('invalid_request')
                        selected[p] = parse_log(logs[p], start, max(1, count))['records'] if count else []
                        coverage.append(dict(platform=p, total_records=total, selected_records=count,
                            complete=count == total, windows=[dict(offset=start, count=count)] if count else []))
                    result = dict(manifest=manifest, records=selected, coverage=coverage)
                    if len(encoded(result)) > CONTEXT_BYTES:
                        raise NativeObservationsError('result_too_large')
                else:
                    result = dict(pages[platform], manifest=manifest, platform=platform, offset=offset, limit=limit)
                yield result
                for path, descriptor, path_identity, descriptor_identity in opened:
                    if (_identity(_safe(path)) != path_identity
                            or _identity(os.fstat(descriptor)) != descriptor_identity):
                        raise NativeObservationsError('evidence_invalid')
                for path, identity in ancestors.items():
                    # Directory size/mtime can change for unrelated siblings; identity and reparse protection
                    # suffice for directory binding. File timestamps above must remain exact.
                    now = _identity(_safe(path, True))
                    if now[:2] != identity[:2]:
                        raise NativeObservationsError('evidence_invalid')
        except NativeObservationsError:
            raise
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
            raise NativeObservationsError('evidence_invalid') from None

"""Render exact source-claim citations from the validated frozen report context."""

import json
import re


CLAIM_INSTRUCTIONS = (
    '\nFor source assertions you want reviewed, put each exact claim on its own top-level Markdown '
    'line starting with "- Claim: " and ending with one admitted source citation marker. '
    'Use this format only for source claims. The report will show the retained excerpt for independent '
    'review. Citation resolution does not assess claim support.'
)

_MAX_ROWS = 24
_MAX_REVIEW_BYTES = 65536
_CLAIM = re.compile(r'- Claim: (?P<claim>.+?) \[\[source:(?P<id>[0-9a-f-]{36})\]\][ \t]*')
_FENCE = re.compile(r' {0,3}(`{3,}|~{3,})(.*)')
_BACKTICKS = re.compile(r'`+')
_INLINE_TOKEN = re.compile(r'`+|<!--|-->')
_PROSE_BOUNDARY = re.compile(
    r'^[ \t]*$|^(?: {4}|\t| {0,3}(?:[<>]|#{1,6}(?:[ \t]|$)|'
    r'(?:[-+*]|\d{1,9}[.)])(?:[ \t]|$)|`{3,}|~{3,}|[-=*_][ \t]*[-=*_][ \t]*[-=*_]))'
)
_DOMAIN_LABEL = r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
_AUTOLINK = re.compile(
    r" {0,3}<(?:[A-Za-z][A-Za-z0-9+.-]{1,31}:[^\x00-\x20\x7f<>]*|"
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@" + _DOMAIN_LABEL +
    r"(?:\." + _DOMAIN_LABEL + r")*)>[ \t]*"
)
_JSON_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
_LITERAL_ESCAPES = str.maketrans({'[': r'\u005b', ']': r'\u005d', '<': r'\u003c',
                                 '>': r'\u003e', '&': r'\u0026', '\u0085': r'\u0085',
                                 '\u2028': r'\u2028', '\u2029': r'\u2029'})
_HEADING = (
    '\n\n## Source claim review\n\n'
    'Resolved references identify retained passages. Semantic support: not_reviewed.\n\n'
    'Offsets count Unicode code points, with an exclusive end.\n\n'
    '```jsonl\n'
).encode('utf-8')
_CLOSE = b'```\n'
_OMITTED = b'\nAdditional claim rows were omitted due to report limits.\n'


def _code_closers(lines):
    """Index equal-length backtick runs within ordinary prose boundaries."""
    closers, nearest = {}, {}
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        boundary = _PROSE_BOUNDARY.match(line)
        if boundary:
            nearest.clear()
        for run in reversed(tuple(_BACKTICKS.finditer(line))):
            width = run.end() - run.start()
            if width in nearest:
                closers[index, run.start()] = nearest[width]
            nearest[width] = (index, run.end())
        if boundary:
            nearest.clear()
    return closers


def _escaped(line, start):
    first = start
    while first and line[first - 1] == '\\':
        first -= 1
    return (start - first) % 2


def _visible_line(line, index, closers, code_end, in_comment):
    """Mask only proven code spans and locate real raw comment delimiters."""
    scratch = list(line)
    has_comment = in_comment
    through = 0
    if code_end[0] >= index:
        through = len(line) if code_end[0] > index else code_end[1]
        scratch[:through] = ' ' * through
    for marker in _INLINE_TOKEN.finditer(line, through):
        start = marker.start()
        if start < through:
            continue
        token = marker.group()
        if in_comment:
            if token == '-->':
                in_comment = False
        elif not _escaped(line, start):
            if token == '<!--':
                has_comment = in_comment = True
            elif token[0] == '`' and (index, start) in closers:
                code_end = closers[index, start]
                through = len(line) if code_end[0] > index else code_end[1]
                scratch[start:through] = ' ' * (through - start)
    return ''.join(scratch), code_end, in_comment, has_comment


def _claims(markdown, passages):
    """Accept explicit top-level prose claims and skip hidden Markdown regions."""
    rows = []
    fence = None
    in_comment = False
    code_end = (-1, 0)
    lines = re.split(r'\r\n?|\n', markdown)
    closers = _code_closers(lines)
    for index, line in enumerate(lines):
        if not in_comment:
            marker = _FENCE.fullmatch(line)
            if marker:
                marks, suffix = marker.groups()
                if fence and marks[0] == fence[0] and len(marks) >= len(fence) and not suffix.strip(' \t'):
                    fence = None
                    continue
                if fence is None and (marks[0] == '~' or '`' not in suffix):
                    fence = marks
            if fence:
                continue
        visible, code_end, in_comment, has_comment = _visible_line(line, index, closers, code_end, in_comment)
        if has_comment:
            if re.search(r'-->[ \t]*<(?!\!--)', visible):
                return []
            continue
        # Other raw HTML blocks can change the visibility of subsequent lines.
        if re.match(r' {0,3}<', visible) and not _AUTOLINK.fullmatch(line):
            return []
        if len(rows) > _MAX_ROWS:
            continue
        match = _CLAIM.fullmatch(line)
        if match:
            claim, evidence_id = match.group('claim', 'id')
            start, end = match.span('claim')
            visible_claim = visible[start:end]
            if (visible.startswith('- Claim: ') and visible[match.start('id'):match.end('id')] == evidence_id
                    and claim.strip() and '[[' not in claim and ']]' not in claim
                    and '<' not in visible_claim and '>' not in visible_claim and evidence_id in passages):
                rows.append((index + 1, claim, evidence_id))
    return [] if fence or in_comment else rows


def claim_review_block(markdown, context, byte_budget):
    """Append only complete rows that fit inside the existing report allowance."""
    byte_budget = min(byte_budget, _MAX_REVIEW_BYTES)
    sources = (context['sources'] if context['schema_version'] == 2 else
               [dict(context['binding']['source'], passages=context['passages'])])
    bound_keys = set(context['binding']['reference_keys'])
    passages = {p['evidence_id']: (source, p) for source in sources for p in source['passages']
                if 'source:' + p['evidence_id'] in bound_keys}
    claims = _claims(markdown, passages)
    if not claims:
        return b''

    selected_ids = {evidence_id for _, _, evidence_id in claims}
    providers = {evidence_id: [] for evidence_id in selected_ids}
    for kind in ('node', 'edge'):
        for item in context['graph'][kind + 's']:
            for evidence_id in selected_ids.intersection(item['evidence_ids']):
                providers[evidence_id].append({'kind': kind, 'provider_id': item['uuid']})

    rows = []
    remaining = byte_budget - len(_HEADING) - len(_CLOSE) - len(_OMITTED)
    for line, claim, evidence_id in claims[:_MAX_ROWS]:
        source, passage = passages[evidence_id]
        row = dict(report_line=line, claim=claim, project_id=context['binding']['scope']['project_id'],
                   evidence_id=evidence_id, source_name=source['source_name'],
                   source_revision=source['source_revision'], source_sha256=source['source_sha256'],
                   page=passage['page'], start=passage['start'], end=passage['end'],
                   excerpt=passage['excerpt'], excerpt_sha256=passage['excerpt_sha256'],
                   provider_sources=sorted(providers[evidence_id], key=lambda p: (p['kind'], p['provider_id'])),
                   reference_integrity='resolved', semantic_support_status='not_reviewed')
        wire = json.dumps(row, ensure_ascii=False)
        # Keep literal excerpt brackets and HTML out of the Markdown marker stream.
        wire = (_JSON_STRING.sub(lambda m: m.group().translate(_LITERAL_ESCAPES), wire) + '\n').encode('utf-8')
        if len(wire) > remaining:
            break
        rows.append(wire)
        remaining -= len(wire)
    if not rows:
        return b''
    return _HEADING + b''.join(rows) + _CLOSE + (_OMITTED if len(rows) < len(claims) else b'')

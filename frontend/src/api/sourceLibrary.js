// Pure bounded source preparation/DTO validation. Transport and authorization
// belong exclusively to createWorkbenchClient; this module never owns a token.
import { WorkbenchError, awareTimestamp } from './workbench.js'
export const TEXT_LIMIT = 1048576
export const DOCX_LIMIT = 2097152
export const PDF_LIMIT = 2097152
const enc = new TextEncoder()
const bad = (code = 'invalid_reply') => { throw new WorkbenchError(code) }
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const hex = /^[0-9a-f]{64}$/
const malformed = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u
function shape(v, keys) { if (!v || typeof v !== 'object' || Array.isArray(v) || Object.keys(v).sort().join('|') !== keys.split(' ').sort().join('|')) bad() }
function str(v, cap, min = 0) { if (typeof v !== 'string' || v.length > cap * 2 || malformed.test(v) || v.includes('\0') || Array.from(v).length < min || Array.from(v).length > cap) bad() }
function int(v, max, min = 0) { if (!Number.isSafeInteger(v) || v < min || v > max) bad() }
function id(v) { if (typeof v !== 'string' || !uuid.test(v)) bad() }
function hash(v) { if (typeof v !== 'string' || !hex.test(v)) bad() }
function array(v, max, each) { if (!Array.isArray(v) || v.length > max) bad(); v.forEach(each) }
function core(v) { if (v.schema_version !== 1 || v.binary_retained !== false || v.graph_ingestion_executed !== false) bad() }
export function sourceRevision(v) { try { id(v); return v } catch { bad('invalid_request') } }
export function sourceReadRequest(v) {
  try {
    shape(v, 'source_revision' + (Object.hasOwn(v || {}, 'project_id') ? ' project_id' : ''))
    id(v.source_revision); if (v.project_id !== undefined) id(v.project_id)
    return { ...v }
  } catch { bad('invalid_request') }
}
function metadata(v) {
  shape(v, 'project_id source_revision source_name text_sha256 byte_length codepoint_length recorded_at')
  id(v.project_id); id(v.source_revision); str(v.source_name, 256, 1); hash(v.text_sha256)
  int(v.byte_length, TEXT_LIMIT, 1); int(v.codepoint_length, v.byte_length, 1)
  if (v.byte_length > 4 * v.codepoint_length) bad()
  awareTimestamp(v.recorded_at)
}
function passages(v, length, uploaded = false) {
  const seen = new Set()
  array(v, 100, p => {
    shape(p, 'evidence_id start end page excerpt_sha256'); id(p.evidence_id); hash(p.excerpt_sha256)
    if (seen.has(p.evidence_id) || (uploaded && p.evidence_id[14] !== '5')) bad()
    seen.add(p.evidence_id); int(p.start, length); int(p.end, length, p.start + 1)
    if (p.page !== null) int(p.page, 2147483647, 1)
  })
}
export async function sha256(bytes, cryptoImpl = globalThis.crypto) {
  if (!cryptoImpl?.subtle) bad('crypto_unavailable')
  return Array.from(new Uint8Array(await cryptoImpl.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2, '0')).join('')
}
function uploadName(v) { str(v, 256, 1); if (!v.trim() || /\p{Cc}/u.test(v)) bad() }
function uploadText(v) {
  str(v, TEXT_LIMIT, 1)
  if (!v.trim() || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(v)) bad()
  let bytes = 0
  for (const c of v) { const n = c.codePointAt(0); bytes += n < 128 ? 1 : n < 2048 ? 2 : n < 65536 ? 3 : 4; if (bytes > TEXT_LIMIT) bad('limit_exceeded') }
}
function base64(bytes) {
  let binary = ''
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192))
  return btoa(binary)
}
function pdfBytes(bytes) {
  // Python bytes.rstrip removes only these six ASCII whitespace bytes.
  const header = String.fromCharCode(...bytes.subarray(0, 1024))
  let end = bytes.length
  while (end && [9, 10, 11, 12, 13, 32].includes(bytes[end - 1])) end--
  if (!bytes.length || bytes.length > PDF_LIMIT || !header.includes('%PDF-') || end < 5 || String.fromCharCode(...bytes.subarray(end - 5, end)) !== '%%EOF') bad()
}
// Python str.isspace, excluding forbidden C0 controls handled separately.
const pythonWhitespace = /^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]*$/u
async function pageEvidenceId(revision, declared, cryptoImpl) {
  if (!cryptoImpl?.subtle) bad('crypto_unavailable')
  const namespace = Uint8Array.from(revision.replaceAll('-', '').match(/../g), x => parseInt(x, 16))
  const canonical = JSON.stringify({ empty: declared.empty, end: declared.end, excerpt_sha256: declared.excerpt_sha256, page: declared.page, start: declared.start })
  const name = enc.encode('pdf-page-text-v1:' + canonical), input = new Uint8Array(16 + name.length)
  input.set(namespace); input.set(name, 16)
  const bytes = new Uint8Array(await cryptoImpl.subtle.digest('SHA-1', input)).slice(0, 16)
  bytes[6] = (bytes[6] & 15) | 80; bytes[8] = (bytes[8] & 63) | 128
  const h = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}
async function pdfExtraction(v, payload, cryptoImpl) {
  extractionCommon(v, payload)
  shape(v, 'format input_hash_verified input_sha256 input_digest_persisted blocks_persisted original_document_verified binary_persistently_bound ocr_performed page_layout semantic_quality coverage page_text pages page_count empty_page_count declared_passage_count')
  if (!Array.isArray(v.coverage) || v.coverage.length !== 1 || v.coverage[0] !== 'page_text') bad()
  int(v.page_count, 100, 1); int(v.empty_page_count, v.page_count); int(v.declared_passage_count, v.page_count, 1)
  if (!Array.isArray(v.page_text) || !Array.isArray(v.pages) || v.page_text.length !== v.page_count || v.pages.length !== v.page_count) bad()
  let offset = 0, size = 0, empty = 0
  const expected = []
  for (let i = 0; i < v.page_count; i++) {
    const text = v.page_text[i], p = v.pages[i]
    str(text, 32768)
    if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(text)) bad()
    const bytes = enc.encode(text)
    if (bytes.length > 32768) bad()
    offset += i ? 2 : 0; size += bytes.length + (i ? 2 : 0)
    if (size > TEXT_LIMIT) bad()
    const declared = { page: i + 1, start: offset, end: offset + Array.from(text).length, empty: pythonWhitespace.test(text), excerpt_sha256: await sha256(bytes, cryptoImpl) }
    shape(p, 'page start end empty excerpt_sha256')
    for (const key of Object.keys(declared)) if (p[key] !== declared[key]) bad()
    if (declared.empty) empty++
    else expected.push({ evidence_id: await pageEvidenceId(payload.source_revision, declared, cryptoImpl), start: declared.start, end: declared.end, page: declared.page, excerpt_sha256: declared.excerpt_sha256 })
    offset = declared.end
  }
  if (!expected.length || empty !== v.empty_page_count || expected.length !== v.declared_passage_count) bad()
  return { text: v.page_text.join('\n\n'), expected }
}
function extractionCommon(v, payload) {
  if (!v || v.format !== payload.format || v.input_hash_verified !== true || v.input_sha256 !== payload.input_sha256 || v.page_layout !== 'unknown' || v.semantic_quality !== 'unknown') bad()
  for (const k of ['input_digest_persisted', 'blocks_persisted', 'original_document_verified', 'binary_persistently_bound', 'ocr_performed']) if (v[k] !== false) bad()
}
export function sourcePayload(v) {
  try {
    shape(v, 'schema_version source_revision source_name format content input_sha256')
    if (v.schema_version !== 1 || !['text', 'docx', 'pdf'].includes(v.format)) bad()
    id(v.source_revision); uploadName(v.source_name); hash(v.input_sha256)
    if (v.format === 'text') uploadText(v.content)
    else {
      if (typeof v.content !== 'string' || v.content.length > Math.ceil(DOCX_LIMIT / 3) * 4 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(v.content)) bad()
      const binary = atob(v.content)
      if (!binary.length || binary.length > DOCX_LIMIT || btoa(binary) !== v.content) bad()
      if (v.format === 'pdf') pdfBytes(Uint8Array.from(binary, c => c.charCodeAt(0)))
    }
    return JSON.stringify(v)
  } catch { bad('invalid_request') }
}
export async function verifySourceInput(v, cryptoImpl = globalThis.crypto) {
  const bytes = v.format === 'text' ? enc.encode(v.content) : Uint8Array.from(atob(v.content), c => c.charCodeAt(0))
  if (await sha256(bytes, cryptoImpl) !== v.input_sha256) bad('invalid_request')
}
// One UUID per explicit prepared attempt. Edits invalidate that prepared attempt.
export async function prepareSource({ name, text, file }, cryptoImpl = globalThis.crypto) {
  try {
    uploadName(name)
    let bytes, content, format = 'text', filename = null
    if (file) {
      if (typeof file.name !== 'string' || /[\\/]/.test(file.name) || !Number.isSafeInteger(file.size) || file.size < 1) bad()
      filename = file.name
      if (/\.docx$/i.test(filename)) format = 'docx'
      else if (/\.pdf$/i.test(filename)) format = 'pdf'
      else if (!/\.(txt|md)$/i.test(filename)) bad('unsupported')
      if (file.size > (format === 'text' ? TEXT_LIMIT : DOCX_LIMIT)) bad('limit_exceeded')
      bytes = new Uint8Array(await file.arrayBuffer())
      if (bytes.length !== file.size) bad()
      // ignoreBOM:true preserves U+FEFF instead of silently stripping it.
      if (format === 'pdf') pdfBytes(bytes)
      content = format !== 'text' ? base64(bytes) : new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes)
      if (format === 'text') uploadText(content)
    } else { uploadText(text); content = text; bytes = enc.encode(text) }
    const digest = await sha256(bytes, cryptoImpl)
    if (!cryptoImpl?.randomUUID) bad('crypto_unavailable')
    const payload = { schema_version: 1, source_revision: cryptoImpl.randomUUID(), source_name: name, format, content, input_sha256: digest }
    sourcePayload(payload)
    return Object.freeze({ payload: Object.freeze(payload), filename, inputBytes: bytes.length, codepoints: format === 'text' ? Array.from(content).length : null })
  } catch (e) { if (e instanceof WorkbenchError && ['crypto_unavailable', 'unsupported', 'limit_exceeded'].includes(e.code)) throw e; bad('invalid_request') }
}
async function verifyText(v, text, cryptoImpl) {
  str(text, TEXT_LIMIT, 1)
  const points = Array.from(text), bytes = enc.encode(text)
  if (bytes.length !== v.source.byte_length || points.length !== v.source.codepoint_length || await sha256(bytes, cryptoImpl) !== v.source.text_sha256) bad()
  for (const p of v.passages) {
    const excerptBytes = enc.encode(points.slice(p.start, p.end).join(''))
    if (excerptBytes.length > 32768 || await sha256(excerptBytes, cryptoImpl) !== p.excerpt_sha256) bad()
  }
}
function extraction(v, payload, length) {
  const keys = 'format input_hash_verified input_sha256 input_digest_persisted blocks_persisted original_document_verified binary_persistently_bound ocr_performed page_layout semantic_quality'
  shape(v, keys + (payload.format === 'docx' ? ' coverage excluded_parts blocks deleted_revision_text_included field_instruction_text_included' : ''))
  if (v.format !== payload.format || v.input_hash_verified !== true || v.input_sha256 !== payload.input_sha256 || v.page_layout !== 'unknown' || v.semantic_quality !== 'unknown') bad()
  for (const k of ['input_digest_persisted', 'blocks_persisted', 'original_document_verified', 'binary_persistently_bound', 'ocr_performed']) if (v[k] !== false) bad()
  if (payload.format !== 'docx') return
  if (JSON.stringify(v.coverage) !== JSON.stringify(['main_body_paragraphs', 'main_body_table_cells']) || JSON.stringify(v.excluded_parts) !== JSON.stringify(['headers', 'footers', 'footnotes', 'endnotes']) || v.deleted_revision_text_included !== false || v.field_instruction_text_included !== false) bad()
  let end = 0
  array(v.blocks, 5000, (b, i) => {
    shape(b, 'kind start end table row cell grid_span vertical_merge ordinal empty')
    if (!['paragraph', 'table_cell'].includes(b.kind) || b.ordinal !== i || typeof b.empty !== 'boolean') bad()
    int(b.start, length, end); int(b.end, length, b.start); end = b.end
    if (b.empty !== (b.start === b.end)) bad()
    int(b.grid_span, 9999, 1)
    if (![null, 'restart', 'continue'].includes(b.vertical_merge)) bad()
    if (b.kind === 'paragraph') { if ([b.table, b.row, b.cell].some(n => n !== null) || b.grid_span !== 1 || b.vertical_merge !== null) bad() }
    else for (const n of [b.table, b.row, b.cell]) int(n, Number.MAX_SAFE_INTEGER)
  })
}
export async function validateSourceResult(method, v, request, cryptoImpl = globalThis.crypto) {
  try {
    if (method === 'sourceList') {
      shape(v, 'schema_version binary_retained graph_ingestion_executed sources has_more window_limit'); core(v)
      if (typeof v.has_more !== 'boolean' || v.window_limit !== 20 || (v.has_more && v.sources?.length !== 20)) bad()
      array(v.sources, 20, metadata)
      if (new Set(v.sources.map(s => s.source_revision)).size !== v.sources.length || new Set(v.sources.map(s => s.project_id)).size > 1) bad()
    } else {
      const retained = method === 'sourceRetain'
      if (!retained && method !== 'sourceGet') bad()
      shape(v, 'schema_version binary_retained graph_ingestion_executed source offset_unit passages ' + (retained ? 'extraction' : 'text'))
      core(v); metadata(v.source); passages(v.passages, v.source.codepoint_length, retained)
      if (v.offset_unit !== 'unicode_codepoint' || v.source.source_revision !== request.source_revision || request.project_id && v.source.project_id !== request.project_id) bad()
      if (retained) {
        if (v.source.source_name !== request.source_name) bad()
        if (request.format === 'pdf') {
          const { text, expected } = await pdfExtraction(v.extraction, request, cryptoImpl)
          if (expected.length !== v.passages.length) bad()
          for (let i = 0; i < expected.length; i++) for (const k of Object.keys(expected[i])) if (v.passages[i][k] !== expected[i][k]) bad()
          await verifyText(v, text, cryptoImpl)
        } else {
        extraction(v.extraction, request, v.source.codepoint_length)
        if (request.format === 'text') {
          if (v.source.text_sha256 !== request.input_sha256) bad()
          // Generated text receipt declarations cover the submitted text once,
          // in order. Legacy GET declarations intentionally have no such rule.
          let end = 0
          if (!v.passages.length) bad()
          for (const p of v.passages) { if (p.start !== end || p.page !== null) bad(); end = p.end }
          if (end !== v.source.codepoint_length) bad()
          await verifyText(v, request.content, cryptoImpl)
        } else {
          // Receipt shape is verified; extracted text hashes need an explicit GET.
          const declared = v.extraction.blocks.filter(b => !b.empty)
          if (declared.length !== v.passages.length || v.passages.some((p, i) => p.start !== declared[i].start || p.end !== declared[i].end || p.page !== null)) bad()
        }
        }
      } else await verifyText(v, v.text, cryptoImpl)
    }
    return v
  } catch (e) { if (e instanceof WorkbenchError && e.code === 'crypto_unavailable') throw e; bad() }
}

// V2 original-PDF contract. The V1 validators above deliberately keep their
// accepted false binary flags and exact result shapes.
export function sourceOriginalPayload(method, payload) {
  try {
    if (method === 'sourceRetainOriginal') {
      shape(payload, 'schema_version source_revision source_name format content input_sha256')
      if (payload.schema_version !== 2 || payload.format !== 'pdf') bad()
      sourcePayload({ ...payload, schema_version: 1 })
      return JSON.stringify(payload)
    }
    if (!['sourceOriginalMetadata', 'sourceOriginalRead'].includes(method)) bad()
    shape(payload, 'source_revision'); id(payload.source_revision)
    return { source_revision: payload.source_revision }
  } catch { bad('invalid_request') }
}

export async function verifyOriginalPdfInput(payload, cryptoImpl = globalThis.crypto) {
  sourceOriginalPayload('sourceRetainOriginal', payload)
  return verifySourceInput(payload, cryptoImpl)
}

function originalBinary(binary, source, request) {
  shape(binary, 'contract_version project_id source_revision media_type byte_length sha256')
  if (binary.contract_version !== 1 || binary.media_type !== 'application/pdf') bad()
  id(binary.project_id); id(binary.source_revision); hash(binary.sha256)
  int(binary.byte_length, PDF_LIMIT, 1)
  if (binary.project_id !== source.project_id || binary.source_revision !== source.source_revision ||
      binary.source_revision !== request.source_revision || request.project_id && binary.project_id !== request.project_id) bad()
}

export async function validateSourceOriginalResult(method, value, payload, cryptoImpl = globalThis.crypto) {
  try {
    if (!['sourceRetainOriginal', 'sourceOriginalMetadata', 'sourceOriginalRead'].includes(method)) bad()
    const retain = method === 'sourceRetainOriginal', read = method === 'sourceOriginalRead'
    const keys = retain
      ? 'schema_version binary_retained graph_ingestion_executed source passages offset_unit extraction binary'
      : 'schema_version binary_retained graph_ingestion_executed source binary' + (read ? ' content_base64' : '')
    shape(value, keys)
    if (value.schema_version !== 2 || value.binary_retained !== true || value.graph_ingestion_executed !== false) bad()
    metadata(value.source); originalBinary(value.binary, value.source, payload)
    if (retain) {
      if (payload.schema_version !== 2 || payload.format !== 'pdf' || value.binary.sha256 !== payload.input_sha256) bad()
      const x = value.extraction
      if (x?.input_hash_verified !== true || x.input_digest_persisted !== true ||
          x.original_document_verified !== true || x.binary_persistently_bound !== true ||
          x.blocks_persisted !== false || x.ocr_performed !== false) bad()
      const old = { ...value, schema_version: 1, binary_retained: false,
        extraction: { ...x, input_digest_persisted: false, original_document_verified: false,
          binary_persistently_bound: false } }
      delete old.binary
      await validateSourceResult('sourceRetain', old, { ...payload, schema_version: 1 }, cryptoImpl)
    }
    if (read) {
      if (typeof value.content_base64 !== 'string' || value.content_base64.length > Math.ceil(PDF_LIMIT / 3) * 4) bad()
      const binary = atob(value.content_base64)
      if (btoa(binary) !== value.content_base64 || binary.length !== value.binary.byte_length) bad()
      const bytes = Uint8Array.from(binary, c => c.charCodeAt(0))
      pdfBytes(bytes)
      if (await sha256(bytes, cryptoImpl) !== value.binary.sha256) bad()
    }
    return value
  } catch (e) { if (e instanceof WorkbenchError && e.code === 'crypto_unavailable') throw e; bad() }
}

// Authored regression sources. Main must execute these against locked tooling.
import test from 'node:test'
import assert from 'node:assert/strict'
import { webcrypto } from 'node:crypto'
import { createWorkbenchClient } from '../src/api/workbench.js'
import { prepareSource, sha256, sourcePayload, validateSourceResult, TEXT_LIMIT, DOCX_LIMIT } from '../src/api/sourceLibrary.js'
Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
const enc = new TextEncoder(), revision = '11111111-1111-4111-8111-111111111111', project = '22222222-2222-4222-8222-222222222222'
const evidence = n => `33333333-3333-5333-8333-${String(n).padStart(12, '0')}`
const graph = { graph_id: 'graph_1', nodes: [], edges: [], node_count: 0, edge_count: 0 }
const wrapper = data => new Response(JSON.stringify({ success: true, data }), { headers: { 'Content-Type': 'application/json' } })
const fixed = data => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } })
const code = expected => e => e.code === expected
const credentials = { origin: 'http://127.0.0.1:5001', graph: 'graph_1', token: 'test-only-private-token' }
async function item(text = '\uFEFF中😀 abc', spans = [[2, 5], [0, 4]]) {
  const points = Array.from(text)
  return { schema_version: 1, binary_retained: false, graph_ingestion_executed: false,
    source: { project_id: project, source_revision: revision, source_name: '<script>legacy</script>\t', text_sha256: await sha256(enc.encode(text)), byte_length: enc.encode(text).length, codepoint_length: points.length, recorded_at: '2026-10-02T00:00:00+08:00' }, offset_unit: 'unicode_codepoint', text,
    passages: await Promise.all(spans.map(async ([start, end], i) => ({ evidence_id: evidence(i), start, end, page: i ? 2 : null, excerpt_sha256: await sha256(enc.encode(points.slice(start, end).join(''))) }))) }
}
function library(sources = [], has_more = false) { return { schema_version: 1, binary_retained: false, graph_ingestion_executed: false, sources, has_more, window_limit: 20 } }
async function connected(fetchSource, opts = {}) {
  const calls = []
  const client = createWorkbenchClient({ ...opts, fetchImpl: async (url, options) => { calls.push({ url, options }); return calls.length === 1 ? wrapper(graph) : fetchSource(url, options) } })
  await client.connect(credentials)
  return { client, calls }
}
async function textReceipt(payload) {
  const v = await item(payload.content, [[0, Array.from(payload.content).length]])
  v.source.source_revision = payload.source_revision; v.source.source_name = payload.source_name; delete v.text
  v.extraction = { format: 'text', input_hash_verified: true, input_sha256: payload.input_sha256, input_digest_persisted: false, blocks_persisted: false, original_document_verified: false, binary_persistently_bound: false, ocr_performed: false, page_layout: 'unknown', semantic_quality: 'unknown' }
  return v
}
test('shared private transport uses configured fixed source paths and header only; list is explicit', async () => {
  const v = await item()
  const { client, calls } = await connected(url => wrapper(url.includes('/library/') ? library([v.source]) : v))
  assert.equal(calls.length, 1)
  await client.sourceList(); await client.sourceGet({ source_revision: revision, project_id: project })
  assert.deepEqual(calls.slice(1).map(c => c.url), ['http://127.0.0.1:5001/api/source/library/graph_1', `http://127.0.0.1:5001/api/source/item/graph_1/${revision}`])
  for (const { url, options } of calls.slice(1)) {
    assert.equal(options.headers.Authorization, `Bearer ${credentials.token}`)
    assert.equal(options.credentials, 'omit'); assert.equal(options.redirect, 'error'); assert.equal(options.cache, 'no-store'); assert.equal(options.referrerPolicy, 'no-referrer')
    assert.equal(options.method, 'GET'); assert.equal(options.body, undefined)
    assert.ok(!url.includes(credentials.token)); assert.ok(!url.includes(project))
  }
  await assert.rejects(client.sourceGet({ source_revision: '../escape' }), code('invalid_request'))
  await assert.rejects(client.sourceGet({ source_revision: revision, token: 'other' }), code('invalid_request'))
  client.disconnect(); await assert.rejects(client.sourceList(), code('disconnected'))
})
test('exact BOM, Unicode bytes/codepoints, legacy controls and overlapping reversed declaration order are retained', async () => {
  const v = await item()
  const result = await validateSourceResult('sourceGet', v, { source_revision: revision }, webcrypto)
  assert.equal(result.text[0], '\uFEFF'); assert.equal(result.source.codepoint_length, 7)
  assert.deepEqual(result.passages.map(p => [p.start, p.end]), [[2, 5], [0, 4]])
  await validateSourceResult('sourceGet', await item('legacy\u007f\ntext', []), { source_revision: revision }, webcrypto)
  for (const change of [v => { v.source.byte_length++ }, v => { v.source.codepoint_length++ }, v => { v.source.text_sha256 = '0'.repeat(64) }, v => { v.passages[0].excerpt_sha256 = '0'.repeat(64) }, v => { v.passages[1].evidence_id = v.passages[0].evidence_id }, v => { v.passages[0].end = 999 }, v => { v.text = '\ud800' }, v => { v.source.extra = true }]) {
    const altered = structuredClone(v); change(altered)
    await assert.rejects(validateSourceResult('sourceGet', altered, { source_revision: revision }, webcrypto), code('invalid_reply'))
  }
  await assert.rejects(validateSourceResult('sourceGet', v, { source_revision: revision, project_id: revision }, webcrypto), code('invalid_reply'))
})
test('bounded latest20 window permits empty but rejects extra/duplicate/mixed-project/unknown fields', async () => {
  await validateSourceResult('sourceList', library(), undefined)
  const s = (await item()).source
  const sources = Array.from({ length: 20 }, (_, i) => ({ ...s, source_revision: `11111111-1111-4111-8111-${String(i).padStart(12, '0')}` }))
  await validateSourceResult('sourceList', library(sources, true), undefined)
  for (const data of [library([s], true), library([...sources, s]), library([s, s]), library([s, { ...s, source_revision: project, project_id: revision }]), { ...library(), binary_retained: true }, { ...library(), cursor: 'fake' }]) await assert.rejects(validateSourceResult('sourceList', data), code('invalid_reply'))
})
test('prepare preserves UTF8 BOM bytes; rejects limits/format/name/control/invalidUTF8 before inappropriate reads', async () => {
  const text = '\uFEFF中😀\nexact\ttext\r\n', bytes = enc.encode(text)
  const file = { name: 'source.md', size: bytes.length, arrayBuffer: async () => bytes.buffer }
  const prepared = await prepareSource({ name: ' Source 中 ', file }, webcrypto)
  assert.equal(prepared.payload.content, text); assert.equal(prepared.inputBytes, bytes.length)
  assert.equal(prepared.payload.input_sha256, await sha256(bytes)); assert.equal(prepared.filename, 'source.md')
  assert.equal(prepared.codepoints, Array.from(text).length)
  let reads = 0
  for (const [name, size] of [['huge.txt', TEXT_LIMIT + 1], ['huge.docx', DOCX_LIMIT + 1], ['other.pdf', 1], ['C:\\fakepath\\source.txt', 1]]) {
    await assert.rejects(prepareSource({ name: 'valid', file: { name, size, arrayBuffer: async () => { reads++; return new ArrayBuffer(size) } } }))
  }
  assert.equal(reads, 0)
  await assert.rejects(prepareSource({ name: 'ok', file: { name: 'bad.txt', size: 1, arrayBuffer: async () => Uint8Array.of(255).buffer } }), code('invalid_request'))
  for (const text of ['   ', '\u0000bad', '\u000bbad', 'DEL\u007f', '\ud800', '😀'.repeat(TEXT_LIMIT / 4 + 1)]) await assert.rejects(prepareSource({ name: 'ok', text }))
  for (const name of ['', '  ', 'bad\nname', 'x'.repeat(257)]) await assert.rejects(prepareSource({ name, text: 'ok' }), code('invalid_request'))
  const binary = Uint8Array.of(80, 75, 3, 4, 255)
  const docx = await prepareSource({ name: 'docx', file: { name: 'one.docx', size: binary.length, arrayBuffer: async () => binary.buffer } }, webcrypto)
  assert.equal(docx.payload.format, 'docx'); assert.equal(docx.payload.content, 'UEsDBP8='); assert.equal(docx.codepoints, null)
  assert.equal(docx.payload.input_sha256, await sha256(binary))
  assert.throws(() => sourcePayload({ ...docx.payload, content: 'UEsDBP9=' }), code('invalid_request'))
})
test('POST is explicit, identity matches prepared attempt and receipt text hash is rechecked', async () => {
  const prepared = await prepareSource({ name: 'exact', text: '中😀' }, webcrypto)
  const { client, calls } = await connected(async (_url, options) => wrapper(await textReceipt(JSON.parse(options.body))))
  assert.equal(calls.length, 1)
  const receipt = await client.sourceRetain(prepared.payload)
  assert.equal(calls[1].url, 'http://127.0.0.1:5001/api/source/retain/graph_1')
  assert.equal(calls[1].options.method, 'POST')
  assert.deepEqual(JSON.parse(calls[1].options.body), prepared.payload)
  assert.equal(receipt.source.source_revision, prepared.payload.source_revision)
  const altered = await textReceipt(prepared.payload); altered.extraction.input_digest_persisted = true
  await assert.rejects(validateSourceResult('sourceRetain', altered, prepared.payload), code('invalid_reply'))
  altered.extraction.input_digest_persisted = false; altered.source.source_revision = revision
  await assert.rejects(validateSourceResult('sourceRetain', altered, prepared.payload), code('invalid_reply'))
  await assert.rejects(client.sourceRetain({ ...prepared.payload, input_sha256: '0'.repeat(64) }), code('invalid_request'))
  assert.equal(calls.length, 2)
})
test('DOCX transient extraction blocks are bounded, ordered and correlated without original-binary proof', async () => {
  const prepared = await prepareSource({ name: 'docx', file: { name: 'x.docx', size: 1, arrayBuffer: async () => Uint8Array.of(1).buffer } }, webcrypto)
  const v = await textReceipt({ ...prepared.payload, content: '中😀' })
  v.extraction = { ...v.extraction, format: 'docx', input_sha256: prepared.payload.input_sha256, coverage: ['main_body_paragraphs', 'main_body_table_cells'], excluded_parts: ['headers', 'footers', 'footnotes', 'endnotes'], blocks: [{ kind: 'table_cell', start: 0, end: 2, table: 0, row: 0, cell: 0, grid_span: 1, vertical_merge: null, ordinal: 0, empty: false }], deleted_revision_text_included: false, field_instruction_text_included: false }
  v.passages[0].page = null
  await validateSourceResult('sourceRetain', v, prepared.payload)
  for (const change of [v => { v.extraction.blocks[0].ordinal = 1 }, v => { v.extraction.blocks[0].extra = true }, v => { v.passages[0].start = 1 }, v => { v.extraction.ocr_performed = true }, v => { v.extraction.excluded_parts = [] }]) {
    const altered = structuredClone(v); change(altered); await assert.rejects(validateSourceResult('sourceRetain', altered, prepared.payload), code('invalid_reply'))
  }
})
test('source transport rejects duplicate escaped keys, deep/oversize DTOs, redirects and HTML without leaking', async () => {
  for (const raw of ['{"success":true,"succe\\u0073s":true,"data":{}}', JSON.stringify({ success: true, data: Array(1).fill(null).reduce(v => v, null) }).replace('null', '['.repeat(33) + '0' + ']'.repeat(33)), '<html>private token traceback</html>']) {
    const { client } = await connected(() => new Response(raw, { headers: { 'Content-Type': 'application/json' } }))
    await assert.rejects(client.sourceList(), code('invalid_reply'))
  }
  const { client } = await connected(() => new Response('{}', { headers: { 'Content-Type': 'application/json', 'Content-Length': String(4194304 + 1025) } }))
  await assert.rejects(client.sourceList(), code('result_too_large'))
  const redirected = await connected(() => ({ status: 200, redirected: true, body: new ReadableStream(), headers: new Headers({ 'Content-Type': 'application/json' }) }))
  await assert.rejects(redirected.client.sourceList(), code('invalid_reply'))
  const stream = await connected(() => new Response(new ReadableStream({ start(c) { c.enqueue(new Uint8Array(4194304 + 1025)); c.close() } }), { headers: { 'Content-Type': 'application/json' } }))
  await assert.rejects(stream.client.sourceList(), code('result_too_large'))
})
test('nonJSON401 clears the one shared connection; absent sources preserve graph authorization', async () => {
  const { client } = await connected(() => new Response('secret traceback', { status: 401 }))
  await assert.rejects(client.sourceList(), code('unauthorized'))
  await assert.rejects(client.research({ display_graph_ids: ['graph_1'], text: 'q' }), code('disconnected'))
  const missing = await connected(() => fixed({ success: false, error: { code: 'not_found' } }))
  await assert.rejects(missing.client.sourceList(), code('not_found'))
  // A second source read proves the connection was not silently revoked by404.
  await assert.rejects(missing.client.sourceList(), code('not_found'))
  assert.equal(missing.calls.length, 3)
})
test('deadline/cancel/disconnect fence late responses and never retry retention automatically', async () => {
  const prepared = await prepareSource({ name: 'a', text: 'exact' })
  let resolve, signal
  const { client, calls } = await connected((_url, options) => { signal = options.signal; return new Promise(r => { resolve = r }) }, { deadlineMs: 250 })
  await assert.rejects(client.sourceRetain(prepared.payload), code('deadline'))
  assert.equal(signal.aborted, true); assert.equal(calls.length, 2)
  resolve(wrapper(await textReceipt(prepared.payload)))
  let resolveRead
  const cancelled = await connected(() => new Promise(r => { resolveRead = r }))
  const pending = cancelled.client.sourceGet({ source_revision: revision }); cancelled.client.cancel()
  await assert.rejects(pending, code('cancelled')); resolveRead(wrapper(await item()))
  const uncertain = await connected(() => fixed({ success: false, error: { code: 'outcome_unknown' } }))
  await assert.rejects(uncertain.client.sourceRetain(prepared.payload), code('outcome_unknown'))
  assert.equal(uncertain.calls.length, 2)
  uncertain.client.disconnect(); await assert.rejects(uncertain.client.sourceList(), code('disconnected'))
})
test('new upload names reject every C1 control; legacy nonempty whitespace/C1 names and text remain eligible', async () => {
  const prepared = await prepareSource({ name: 'valid', text: 'x' })
  const legacy = await item('legacy\u0085text', [])
  for (let point = 128; point <= 159; point++) {
    const name = 'legacy' + String.fromCodePoint(point)
    await assert.rejects(prepareSource({ name, text: 'x' }), code('invalid_request'))
    assert.throws(() => sourcePayload({ ...prepared.payload, source_name: name }), code('invalid_request'))
    await validateSourceResult('sourceList', library([{ ...legacy.source, source_name: name }]))
  }
  legacy.source.source_name = ' \t '
  await validateSourceResult('sourceGet', legacy, { source_revision: revision })
  legacy.source.source_name = '\u0080legacy\u009f'
  await validateSourceResult('sourceGet', legacy, { source_revision: revision })
  for (const name of ['', 'legacy\0']) {
    const altered = structuredClone(legacy); altered.source.source_name = name
    await assert.rejects(validateSourceResult('sourceGet', altered, { source_revision: revision }), code('invalid_reply'))
    await assert.rejects(validateSourceResult('sourceList', library([altered.source])), code('invalid_reply'))
  }
})
test('GET permits bounded declared pages and zero/overlapping/reversed passages but caps each excerpt at32768 UTF8 bytes', async () => {
  const v = await item('中'.repeat(10922) + 'ab', [[0, 10924]]) //32768 bytes,10924 codepoints
  v.passages[0].page = 2147483647
  await validateSourceResult('sourceGet', v, { source_revision: revision })
  for (const page of [2147483648, 0, -1, 1.5, true]) {
    const altered = structuredClone(v); altered.passages[0].page = page
    await assert.rejects(validateSourceResult('sourceGet', altered, { source_revision: revision }), code('invalid_reply'))
  }
  const over = await item('中'.repeat(10922) + 'abc', [[0, 10925]]) //32769 bytes despite fewer codepoints
  await assert.rejects(validateSourceResult('sourceGet', over, { source_revision: revision }), code('invalid_reply'))
  await validateSourceResult('sourceGet', await item('legacy', []), { source_revision: revision })
  await validateSourceResult('sourceGet', await item('legacy', [[2, 6], [0, 4]]), { source_revision: revision })
})
test('text retain requires nonempty contiguous whole-text declaration coverage with null pages and bounded verified excerpts', async () => {
  const prepared = await prepareSource({ name: 'exact', text: '中😀abc' })
  async function receipt(spans) {
    const v = await textReceipt(prepared.payload)
    v.passages = (await item(prepared.payload.content, spans)).passages.map(p => ({ ...p, page: null }))
    return v
  }
  await validateSourceResult('sourceRetain', await receipt([[0, 2], [2, 5]]), prepared.payload)
  for (const spans of [[], [[1, 5]], [[0, 2], [3, 5]], [[2, 5], [0, 2]], [[0, 3], [2, 5]], [[0, 4]]]) {
    // The excerpt digests themselves are correct in all of these fixtures.
    await assert.rejects(validateSourceResult('sourceRetain', await receipt(spans), prepared.payload), code('invalid_reply'))
  }
  const paged = await receipt([[0, 5]]); paged.passages[0].page = 1
  await assert.rejects(validateSourceResult('sourceRetain', paged, prepared.payload), code('invalid_reply'))
  const long = await prepareSource({ name: 'long', text: 'x'.repeat(32768) + '😀' })
  await assert.rejects(validateSourceResult('sourceRetain', await textReceipt(long.payload), long.payload), code('invalid_reply'))
  const bounded = await textReceipt(long.payload)
  bounded.passages = (await item(long.payload.content, [[0, 32768], [32768, 32769]])).passages.map(p => ({ ...p, page: null }))
  await validateSourceResult('sourceRetain', bounded, long.payload)
})
test('source_denied is a fixed safe source error; arbitrary codes and server text remain rejected', async () => {
  const denied = await connected(() => new Response(JSON.stringify({ success: false, error: { code: 'source_denied' } }), { status: 403, headers: { 'Content-Type': 'application/json' } }))
  await assert.rejects(denied.client.sourceList(), e => e.code === 'source_denied' && e.message === 'source_denied')
  await assert.rejects(denied.client.sourceGet({ source_revision: revision }), code('source_denied'))
  assert.equal(denied.calls.length, 3) // Denial does not revoke the graph connection.
  for (const error of [{ code: 'private token traceback' }, { code: 'source_denied', message: 'private token traceback' }]) {
    const malformed = await connected(() => fixed({ success: false, error }))
    await assert.rejects(malformed.client.sourceList(), e => e.code === 'invalid_reply' && !e.message.includes('private'))
  }
})

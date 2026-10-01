import test from 'node:test'
import assert from 'node:assert/strict'
import { createWorkbenchClient, localOrigin, awareTimestamp, validateResult } from '../src/api/workbench.js'
const credentials = { origin: 'http://127.0.0.1:5001', graph: 'graph_1', token: 'secret-token' }
const scope = { schema_version: 1, workspace_id: '11111111-1111-1111-1111-111111111111', project_id: '22222222-2222-2222-2222-222222222222', graph_id: '33333333-3333-3333-3333-333333333333', run_id: null, branch_id: null, layer: 'source' }
const graph = () => ({ graph_id: 'graph_1', nodes: [], edges: [], node_count: 0, edge_count: 0 })
const coverage = () => ({ display_graph_id: 'graph_1', scope, pages: 0, scanned: 0, eligible: 0, excluded: 0, unknown: 0, returned: 0, truncated: false })
const research = () => ({ schema_version: 1, source_claims: [], simulation_observations: [], other_claims: [], scopes: [coverage()], passage_coverage: [], competing_claim_candidates: [], linked_citations: 0, resolved_citations: 0, unavailable_citations: 0, historical: false, historical_semantics: 'retained_edges_not_bitemporal_reconstruction', rank_basis: 'lexical_token_overlap' })
const payload = () => ({ schema_version: 1, display_graph_ids: ['graph_1'], text: '人口 😀 Melayu 中文', top_k: 10 })
const reply = data => new Response(JSON.stringify({ success: true, data }), { headers: { 'Content-Type': 'application/json' } })
const code = expected => error => error.code === expected && error.message === expected && !String(error.stack).includes('secret-token')
async function fixture(handler) {
  const calls = []; const client = createWorkbenchClient({ fetchImpl: async (url, options) => { calls.push({ url, options }); return calls.length === 1 ? reply(graph()) : handler(url, options) } })
  await client.connect(credentials); return { client, calls }
}
test('literal canonical origins only; no URL normalization creates an escape', () => {
  for (const origin of ['http://127.0.0.1:1', 'https://[::1]:65535', 'http://127.0.0.1:80']) assert.equal(localOrigin(origin), origin)
  for (const origin of ['http://localhost:5001', 'http://127.1:5001', 'http://127.0.0.1:0', 'http://127.0.0.1:65536', 'http://127.0.0.1:05001', 'http://127.0.0.1:5001/', 'http://user@127.0.0.1:5001', 'https://example.com:443', 'http://[::ffff:127.0.0.1]:5001', 'http://127.0.0.1:5001?x', 'http://127.0.0.1:5001#x']) assert.throws(() => localOrigin(origin), code('invalid_connection'))
})
test('connection validates before fetch and cannot read protected evidence until graph read succeeds', async () => {
  let calls = 0
  const client = createWorkbenchClient({ fetchImpl: () => { calls++; return new Promise(() => {}) }, deadlineMs: 20 })
  await assert.rejects(client.connect({ ...credentials, graph: '../escape' }), code('invalid_connection'))
  await assert.rejects(client.connect({ ...credentials, token: 'bad\nheader' }), code('invalid_connection'))
  assert.equal(calls, 0)
  const connecting = client.connect(credentials)
  await assert.rejects(client.research(payload()), code('disconnected'))
  await assert.rejects(connecting, code('deadline'))
  await assert.rejects(client.research(payload()), code('disconnected'))
})
test('owned routes, exact auth and fetch security options; Unicode body survives', async () => {
  const { client, calls } = await fixture(() => reply(research()))
  await client.research(payload())
  assert.equal(calls[0].url, 'http://127.0.0.1:5001/api/graph/data/graph_1')
  assert.equal(calls[1].url, 'http://127.0.0.1:5001/api/graph/research/graph_1')
  assert.equal(calls[1].options.headers.Authorization, 'Bearer secret-token')
  assert.equal(JSON.parse(calls[1].options.body).text, payload().text)
  for (const call of calls) {
    assert.equal(call.options.redirect, 'error'); assert.equal(call.options.credentials, 'omit'); assert.equal(call.options.cache, 'no-store'); assert.equal(call.options.referrerPolicy, 'no-referrer')
    assert.ok(call.options.signal instanceof AbortSignal)
  }
  client.disconnect()
  await assert.rejects(client.research(payload()), code('disconnected')); assert.equal(calls.length, 2)
})
test('scope, unknown fields, DTO lengths and aware timestamps rejected before network', async () => {
  const { client, calls } = await fixture(() => reply(research()))
  for (const value of [{ ...payload(), display_graph_ids: ['other'] }, { ...payload(), endpoint: 'https://evil.test' }, { ...payload(), top_k: 1.1 }, { ...payload(), top_k: 101 }, { ...payload(), text: '😀'.repeat(2001) }, { ...payload(), text: ' ' }, { ...payload(), valid_at: '2026-10-01T00:00:00' }, { ...payload(), valid_at: '2026-02-30T00:00:00Z' }, { ...payload(), recorded_before: '2026-10-01T24:00:00Z' }]) await assert.rejects(client.research(value), code('invalid_request'))
  assert.equal(calls.length, 1)
  assert.equal(awareTimestamp('2024-02-29T00:00:00Z'), '2024-02-29T00:00:00Z')
})
test('fixed server error; no retry; authorization failure clears client connection even with nonJSON body', async () => {
  const { client, calls } = await fixture(() => new Response('secret raw traceback', { status: 401 }))
  await assert.rejects(client.research(payload()), code('unauthorized'))
  await assert.rejects(client.research(payload()), code('disconnected')); assert.equal(calls.length, 2)
})
test('unknown envelopes and server error text never escape as errors', async () => {
  for (const body of [{ success: true, data: research(), extra: 'raw' }, { success: false, error: { code: 'raw-secret' } }, { success: false, error: { code: 'busy', detail: 'raw-secret' } }, { success: true, data: { ...research(), rank_basis: 'semantic_similarity' } }]) {
    const { client, calls } = await fixture(() => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } }))
    await assert.rejects(client.research(payload()), code('invalid_reply')); assert.equal(calls.length, 2)
  }
})
test('duplicate decoded JSON keys are refused rather than silently overwritten', async () => {
  const { client } = await fixture(() => new Response('{"success":false,"error":{"code":"busy","co\\u0064e":"research_busy"}}', { status: 409, headers: { 'Content-Type': 'application/json' } }))
  await assert.rejects(client.research(payload()), code('invalid_reply'))
})
test('known unavailable error stays fixed and never retries', async () => {
  const { client, calls } = await fixture(() => new Response(JSON.stringify({ success: false, error: { code: 'research_busy' } }), { status: 409, headers: { 'Content-Type': 'application/json' } }))
  await assert.rejects(client.research(payload()), code('research_busy')); assert.equal(calls.length, 2)
})
test('disconnect and replacement cancel old responses without affecting a new connection', async () => {
  let resolveOld, calls = 0
  const client = createWorkbenchClient({ fetchImpl: async () => { calls++; if (calls === 2) return new Promise(resolve => { resolveOld = resolve }); return reply(graph()) } })
  await client.connect(credentials)
  const old = client.research(payload()); const cancelled = assert.rejects(old, code('cancelled'))
  client.disconnect(); await client.connect({ ...credentials, token: 'replacement' })
  resolveOld(reply(research())); await cancelled
  assert.equal(calls, 3)
})
test('replacement workbench request owns its response; cancel leaves an authenticated connection reusable', async () => {
  let pending, count = 0
  const { client } = await fixture(() => ++count === 1 ? new Promise(resolve => { pending = resolve }) : reply(research()))
  const first = client.research(payload()), rejected = assert.rejects(first, code('cancelled'))
  const second = await client.research({ ...payload(), text: 'replacement' })
  pending(reply(research())); await rejected; assert.equal(second.rank_basis, 'lexical_token_overlap')
  client.cancel(); await client.research(payload())
})
test('stream size cap cancels the body; advertised cap is refused', async () => {
  let cancelled = false
  const { client } = await fixture(() => new Response(new ReadableStream({ pull(controller) { controller.enqueue(new Uint8Array(2097152 + 1025)) }, cancel() { cancelled = true } }), { headers: { 'Content-Type': 'application/json' } }))
  await assert.rejects(client.research(payload()), code('result_too_large')); assert.equal(cancelled, true)
  const large = await fixture(() => new Response('{}', { headers: { 'Content-Type': 'application/json', 'Content-Length': '2098177' } }))
  await assert.rejects(large.client.research(payload()), code('result_too_large'))
})
test('deadline covers a stalled response body and releases its reader', async () => {
  let count = 0, cancelled = false, body
  const client = createWorkbenchClient({ deadlineMs: 20, fetchImpl: async () => ++count === 1 ? reply(graph()) : new Response(body = new ReadableStream({ cancel() { cancelled = true } }), { headers: { 'Content-Type': 'application/json' } }) })
  await client.connect(credentials); await assert.rejects(client.research(payload()), code('deadline'))
  assert.equal(cancelled, true); assert.equal(body.locked, false)
})
test('redirects and malformed UTF8 are refused', async () => {
  const redirected = await fixture(() => ({ redirected: true, headers: new Headers({ 'Content-Type': 'application/json' }) }))
  await assert.rejects(redirected.client.research(payload()), code('invalid_reply'))
  const malformed = await fixture(() => new Response(new Uint8Array([0xff]), { headers: { 'Content-Type': 'application/json' } }))
  await assert.rejects(malformed.client.research(payload()), code('invalid_reply'))
})
test('citation Unicode offsets and missing links are validated', () => {
  const result = research()
  const evidenceId = '44444444-4444-4444-4444-444444444444'
  const citation = { evidence_id: evidenceId, project_id: scope.project_id, source_revision: '55555555-5555-5555-5555-555555555555', source_name: '中文 😀', source_sha256: 'a'.repeat(64), source_byte_length: 100, source_codepoint_length: 10, source_recorded_at: '2026-10-01T00:00:00Z', start: 0, end: 2, offset_unit: 'unicode_codepoint', excerpt: '中😀', excerpt_sha256: 'b'.repeat(64), declared_page: null }
  result.source_claims = [{ provider_id: 'edge-1', kind: 'edge', scope, claim_class: 'source', name: null, fact: '<script>hostile</script>', source_node_id: 'a', target_node_id: 'b', episode_ids: [], evidence_ids: [evidenceId], valid_at: null, invalid_at: null, expired_at: null, created_at: null, rank_basis: 'lexical_token_overlap', overlap_tokens: 1, query_tokens: 2, citations: [citation], unavailable_evidence_ids: [] }]
  result.linked_citations = result.resolved_citations = 1; result.scopes[0].returned = 1
  assert.equal(validateResult('research', result, 'graph_1').source_claims[0].citations[0].excerpt, '中😀')
  citation.end = 3; assert.throws(() => validateResult('research', result, 'graph_1'), code('invalid_reply'))
})
test('dossier route and request byte bound are fixed', async () => {
  const { client, calls } = await fixture(() => new Response(JSON.stringify({ success: false, error: { code: 'dossier_busy' } }), { status: 409, headers: { 'Content-Type': 'application/json' } }))
  const value = { schema_version: 1, display_graph_ids: ['graph_1'], title: 'Dossier', sections: [{ heading: '中文', query: '😀' }] }
  await assert.rejects(client.dossier(value), code('dossier_busy'))
  assert.equal(calls[1].url, 'http://127.0.0.1:5001/api/graph/dossier/graph_1')
  assert.equal(JSON.parse(calls[1].options.body).sections[0].query, '😀')
  await assert.rejects(client.dossier({ ...value, sections: Array.from({ length: 6 }, () => ({ heading: '😀'.repeat(256), query: '😀'.repeat(2000) })) }), code('invalid_request'))
  assert.equal(calls.length, 2)
})
function dossierResult(request) {
  const meta = research()
  const trace = request.sections.map((section, i) => ({ ordinal: i + 1, request_sha256: 'a'.repeat(64), response_sha256: 'b'.repeat(64), query: section.query, top_k: section.top_k ?? 10, display_graph_ids: ['graph_1'], valid_at: request.valid_at?.replace('Z', '+00:00') ?? null, recorded_before: null, scopes: meta.scopes, passage_coverage: [], competing_claim_candidates: [], linked_citations: 0, resolved_citations: 0, unavailable_citations: 0, historical: Boolean(request.valid_at), historical_semantics: meta.historical_semantics, rank_basis: meta.rank_basis }))
  return { schema_version: 1, mode: 'model_free_evidence_dossier', request: { ...request, sections: request.sections.map(s => ({ ...s, top_k: s.top_k ?? 10 })), valid_at: request.valid_at?.replace('Z', '+00:00') ?? null, recorded_before: null }, sections: request.sections.map((s, i) => ({ ordinal: i + 1, heading: s.heading, source_claim_keys: [], simulation_observation_keys: [], other_claim_keys: [] })), claims: [], references: [], research_trace: trace, summary: { section_count: request.sections.length, query_count: request.sections.length, distinct_scoped_facts: 0, reference_links: 0, resolved_references: 0, unavailable_references: 0, query_reference_links: 0, query_resolved_references: 0, query_unavailable_references: 0, scanned_per_query_sum: 0, unknown_per_query_sum: 0, truncated_query_scopes: 0, passage_coverage: [], coverage_label: 'retrieved_passage_union_per_retained_revision' }, input_sha256: 'a'.repeat(64), trace_sha256: 'b'.repeat(64), records_sha256: 'c'.repeat(64), model_generated: false, semantic_judge_used: false, claim_support_status: 'not_reviewed', consistency: 'individually_guarded_queries_not_atomic_snapshot', limitations: ['Lexical only'], markdown: 'Unused retained markdown' }
}
test('dossier accepts aware UTC serialization, preserves ordered queries and rejects substituted requests', async () => {
  const request = { schema_version: 1, display_graph_ids: ['graph_1'], title: '中文 dosier', valid_at: '2026-10-01T00:00:00Z', sections: [{ heading: 'First', query: '中😀' }, { heading: 'Second', query: 'Melayu', top_k: 5 }] }
  const { client } = await fixture(() => reply(dossierResult(request)))
  const result = await client.dossier(request)
  assert.deepEqual(result.research_trace.map(t => t.query), ['中😀', 'Melayu'])
  assert.equal(result.request.valid_at, '2026-10-01T00:00:00+00:00')
  const substituted = await fixture(() => reply(dossierResult({ ...request, title: 'substituted' })))
  await assert.rejects(substituted.client.dossier(request), code('invalid_reply'))
  const changed = dossierResult(request); changed.sections.reverse()
  assert.throws(() => validateResult('dossier', changed, 'graph_1'), code('invalid_reply'))
})
test('JSON parse failure cancels and releases the reader; malformed graph never authenticates', async () => {
  let body
  const { client } = await fixture(() => new Response(body = new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode('{bad')); c.close() } }), { headers: { 'Content-Type': 'application/json' } }))
  await assert.rejects(client.research(payload()), code('invalid_reply')); assert.equal(body.locked, false)
  const invalid = createWorkbenchClient({ fetchImpl: async () => reply({ ...graph(), node_count: 1 }) })
  await assert.rejects(invalid.connect(credentials), code('invalid_reply'))
  await assert.rejects(invalid.research(payload()), code('disconnected'))
})

// Authored contract regression sources; worker has not executed these tests.
import test from 'node:test'
import assert from 'node:assert/strict'
import { webcrypto } from 'node:crypto'
import { createWorkbenchClient } from '../src/api/workbench.js'
import { ingestionFingerprint, ingestionIdentities, ingestionPayload, validateIngestionResult, validateOntology } from '../src/api/sourceIngestion.js'
import { sha256 } from '../src/api/sourceLibrary.js'
Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
const enc = new TextEncoder(), clone = v => JSON.parse(JSON.stringify(v))
const workspace = '11111111-1111-4111-8111-111111111111', project = '22222222-2222-4222-8222-222222222222', graph = '33333333-3333-4333-8333-333333333333'
const ontologyRevision = '44444444-4444-4444-8444-444444444444', sourceRevision = '55555555-5555-4555-8555-555555555555', operation = '66666666-6666-4666-8666-666666666666', evidence = '77777777-7777-4777-8777-777777777777'
const scope = { schema_version: 1, workspace_id: workspace, project_id: project, graph_id: graph, run_id: null, branch_id: null, layer: 'source' }
const ontology = { schema_version: 1, revision: ontologyRevision, entity_types: [{ name: 'Person', description: '人 😀', attributes: [] }], edge_types: [] }
const payload = { schema_version: 1, source_revision: sourceRevision, operation_id: operation, ontology }
async function fixture(stamp = '2026-10-02T08:00:00.123456+00:00') {
  const text = 'Mira 😀猫\r\nSynthetic retained text.'
  const inspected = { schema_version: 1, binary_retained: false, graph_ingestion_executed: false, source: { project_id: project, source_revision: sourceRevision, source_name: 'Synthetic Ω', text_sha256: await sha256(enc.encode(text)), byte_length: enc.encode(text).length, codepoint_length: Array.from(text).length, recorded_at: stamp }, text, offset_unit: 'unicode_codepoint', passages: [{ evidence_id: evidence, start: 0, end: Array.from(text).length, page: null, excerpt_sha256: await sha256(enc.encode(text)) }] }
  const identities = await ingestionIdentities(scope, operation)
  const plan = { schema_version: 1, scope, operation_id: operation, episode_id: identities.episode_id, fingerprint: await ingestionFingerprint(scope, inspected, payload), evidence_ids: [evidence], graph_ingestion_executed: false, model_calls_made: false, actual_usage_microusd: null, source_revision: sourceRevision, source_sha256: inspected.source.text_sha256, source_byte_length: inspected.source.byte_length, source_codepoint_length: inspected.source.codepoint_length, ontology_revision: ontologyRevision, eligibility_codepoint_limit: 32768, spending_authorized: false }
  const result = { schema_version: 1, scope, operation_id: operation, episode_id: plan.episode_id, fingerprint: plan.fingerprint, evidence_ids: [evidence], graph_ingestion_executed: true, model_calls_made: null, actual_usage_microusd: null, state: 'completed', budget_state: 'settled', ceiling_microusd: 100, receipt: { ...identities, fingerprint: plan.fingerprint, evidence_ids: [evidence] } }
  return { inspected, plan, result }
}
const success = data => new Response(JSON.stringify({ success: true, data }), { headers: { 'content-type': 'application/json' } })
const failure = (code, status = 503) => new Response(JSON.stringify({ success: false, error: { code } }), { status, headers: { 'content-type': 'application/json' } })
const connection = { origin: 'http://127.0.0.1:5001', graph: 'display', token: 'private-token' }
const graphDTO = { graph_id: 'display', nodes: [], edges: [], node_count: 0, edge_count: 0 }
test('Main installed typed golden fingerprints preserve Unicode CRLF and six microseconds', async () => {
  for (const [stamp, expected] of [['2026-10-02T08:00:00.123456+00:00', 'aa55c4d8ad1db722662b21df619e44ef5c2de106149c2be63fdc6ad95f85d265'], ['2026-10-02T13:30:00.654321+05:30', '2a953e3542c2549d5033fa9bec4fcf775895058bc748773f6b913f80220aa96d']]) {
    const f = await fixture(stamp)
    assert.equal(f.inspected.source.text_sha256, '9942ad5266c40a8eeca6aaabc394e3c7d5c3f2d0d6820732d36a6c8f7e86881d')
    assert.equal(f.plan.fingerprint, expected)
    assert.deepEqual(await ingestionIdentities(scope, operation), { group_id: 'mf1_c68c6d604a61a38e87a295a6435eacb5f7d348343ab8f132c3cf7b38115c0d91', episode_id: 'c049f6b6-b4dc-5c80-bad7-cf55a5ac6146' })
    assert.equal(await validateIngestionResult('ingestionPlan', f.plan, { payload, inspected: f.inspected }), f.plan)
  }
})
test('ontology and payload strict names types reserved attributes duplicate and pair boundaries', () => {
  assert.equal(validateOntology(ontology), ontology)
  for (const change of [v => v.extra = true, v => v.entity_types[0].name = 'Entity', v => v.entity_types.push(clone(v.entity_types[0])), v => v.entity_types[0].description = '', v => v.entity_types[0].name = '1bad', v => v.entity_types[0].attributes.push({ name: 'UUID', type: 'text', description: 'reserved' }), v => v.entity_types[0].attributes.push({ name: 'model_field', type: 'text', description: 'reserved' }), v => v.entity_types[0].attributes.push({ name: 'ok', type: 'json', description: 'invalid' }), v => v.edge_types.push({ name: 'LINK', description: 'link', attributes: [], source_targets: [{ source: 'Person', target: 'Missing' }] })]) {
    const v = clone(ontology); change(v); assert.throws(() => validateOntology(v), { code: 'invalid_request' })
  }
  for (const v of [{ ...payload, secret: 'forbidden' }, { ...payload, operation_id: operation.slice(0, -1) + 'A' }, { ...payload, operation_id: 'invalid' }, { ...payload, schema_version: true }]) assert.throws(() => ingestionPayload(v), { code: 'invalid_request' })
  assert.throws(() => ingestionPayload({ operation_id: operation, extra: true }, true), { code: 'invalid_request' })
})
test('plan rejects every extra key flag identity metadata fingerprint evidence and scope mismatch', async () => {
  const f = await fixture()
  const changes = [v => v.extra = true, v => v.scope.layer = 'simulation', v => v.scope.run_id = operation, v => v.scope.branch_id = operation, v => v.scope.project_id = workspace, v => v.scope.extra = 1, v => v.scope.graph_id = 'INVALID', v => v.operation_id = workspace, v => v.episode_id = workspace, v => v.fingerprint = 'a'.repeat(64), v => v.source_sha256 = 'b'.repeat(64), v => v.source_byte_length++, v => v.source_codepoint_length++, v => v.source_revision = workspace, v => v.ontology_revision = workspace, v => v.eligibility_codepoint_limit = 100, v => v.spending_authorized = true, v => v.model_calls_made = null, v => v.actual_usage_microusd = 0, v => v.graph_ingestion_executed = true, v => v.evidence_ids = [], v => v.evidence_ids = [evidence, evidence], v => v.evidence_ids = [workspace]]
  for (const change of changes) { const v = clone(f.plan); change(v); await assert.rejects(validateIngestionResult('ingestionPlan', v, { payload, inspected: f.inspected }), { code: 'invalid_reply' }) }
  await assert.rejects(validateIngestionResult('ingestionPlan', f.plan, { payload, inspected: f.inspected, scope: { ...scope, graph_id: workspace } }), { code: 'invalid_reply' })
  const altered = clone(f.inspected); altered.text += '!'
  await assert.rejects(validateIngestionResult('ingestionPlan', f.plan, { payload, inspected: altered }), { code: 'invalid_reply' })
})
test('completed receipt and known-attempt status have stronger correlation than manual status', async () => {
  const f = await fixture(), context = { payload, known: f.plan, scope }
  assert.equal(await validateIngestionResult('ingestionExecute', f.result, context), f.result)
  for (const change of [v => v.receipt.group_id = 'mf1_' + 'a'.repeat(64), v => v.receipt.extra = 1, v => v.receipt.episode_id = workspace, v => v.receipt.fingerprint = 'b'.repeat(64), v => v.receipt.evidence_ids = [workspace], v => v.graph_ingestion_executed = false, v => v.model_calls_made = 1, v => v.actual_usage_microusd = 0, v => v.budget_state = 'uncertain', v => v.ceiling_microusd = Number.MAX_SAFE_INTEGER + 1]) {
    const v = clone(f.result); change(v); await assert.rejects(validateIngestionResult('ingestionExecute', v, context), { code: 'invalid_reply' })
  }
  const uncertain = { ...f.result, budget_state: 'uncertain' }
  assert.equal(await validateIngestionResult('ingestionStatus', uncertain, context), uncertain)
  const started = { ...f.result, state: 'not_admitted', budget_state: 'started', graph_ingestion_executed: false, receipt: null }
  assert.equal(await validateIngestionResult('ingestionStatus', started, context), started)
  const different = { ...started, fingerprint: 'a'.repeat(64), evidence_ids: [] }
  await assert.rejects(validateIngestionResult('ingestionStatus', different, context), { code: 'invalid_reply' })
  assert.equal(await validateIngestionResult('ingestionStatus', different, { payload: { operation_id: operation } }), different)
  await assert.rejects(validateIngestionResult('ingestionStatus', different, { payload: { operation_id: operation }, project: workspace }), { code: 'invalid_reply' })
})
test('cold client has no POST; authenticated plan snapshot execute and safe GET share private transport', async () => {
  const f = await fixture(), calls = []
  const client = createWorkbenchClient({ fetchImpl: async (url, options) => { calls.push({ url, options }); return success(url.includes('/plan/') ? f.plan : url.includes('/ingestion/') ? f.result : graphDTO) } })
  assert.equal(calls.length, 0)
  await assert.rejects(client.ingestionPlan(payload, f.inspected), { code: 'disconnected' })
  await client.connect(connection); assert.equal(calls.length, 1)
  const result = await client.ingestionPlan(payload, f.inspected)
  await client.ingestionExecute(payload, result); await client.ingestionStatus({ operation_id: operation }, result)
  assert.deepEqual(calls.map(c => c.options.method), ['GET', 'POST', 'POST', 'GET'])
  assert.equal(calls[1].url, connection.origin + '/api/source/ingestion/plan/display')
  assert.equal(calls[3].url, connection.origin + '/api/source/ingestion/operation/display/' + operation)
  assert.equal(calls[3].options.body, undefined); assert.deepEqual(JSON.parse(calls[2].options.body), payload)
  for (const c of calls) { assert.equal(c.options.credentials, 'omit'); assert.equal(c.options.redirect, 'error'); assert.equal(c.options.cache, 'no-store'); assert.equal(c.options.referrerPolicy, 'no-referrer'); assert.equal(c.options.headers.Authorization, 'Bearer private-token') }
  client.disconnect(); await assert.rejects(client.ingestionStatus({ operation_id: operation }), { code: 'disconnected' })
})
test('default-off denial, old read-only host, nonJSON and 401 return fixed codes without retry', async () => {
  const f = await fixture()
  for (const [reply, code] of [[() => failure('model_calls_disabled', 403), 'model_calls_disabled'], [() => failure('budget_denied', 403), 'budget_denied'], [() => new Response('<html>private traceback</html>', { status: 404 }), 'source_unavailable'], [() => new Response('secret', { status: 401 }), 'unauthorized'], [() => new Response('secret', { status: 503 }), 'invalid_reply'], [() => new Response('{"success":false,"error":{"code":"private_secret"}}', { status: 503, headers: { 'content-type': 'application/json' } }), 'invalid_reply']]) {
    let count = 0
    const client = createWorkbenchClient({ fetchImpl: async url => { if (!url.includes('/ingestion/')) return success(graphDTO); count++; return reply() } })
    await client.connect(connection); await assert.rejects(client.ingestionExecute(payload, f.plan), { code }); assert.equal(count, 1)
    if (code === 'unauthorized') await assert.rejects(client.ingestionStatus({ operation_id: operation }), { code: 'disconnected' })
    client.disconnect()
  }
})
test('bounded stream duplicate keys and extra envelope fields rejected', async () => {
  const f = await fixture()
  for (const raw of ['{"success":true,"success":true,"data":{}}', JSON.stringify({ success: true, data: f.plan, secret: 'no' }), ' '.repeat(263169)]) {
    const client = createWorkbenchClient({ fetchImpl: async url => url.includes('/ingestion/') ? new Response(raw, { headers: { 'content-type': 'application/json' } }) : success(graphDTO) })
    await client.connect(connection)
    await assert.rejects(client.ingestionPlan(payload, f.inspected), { code: raw.length > 263168 ? 'result_too_large' : 'invalid_reply' }); client.disconnect()
  }
})
test('lost execute reply cancellation and timeout never retry; explicit status remains usable', async () => {
  const f = await fixture(); let posts = 0, mode = 'lost'
  const client = createWorkbenchClient({ deadlineMs: 1000, fetchImpl: async (url, options) => {
    if (!url.includes('/ingestion/')) return success(graphDTO)
    if (options.method === 'POST') { posts++; if (mode === 'lost') throw new Error('private network details'); return new Promise(() => {}) }
    return success({ ...f.result, budget_state: 'uncertain' })
  } })
  await client.connect(connection)
  await assert.rejects(client.ingestionExecute(payload, f.plan), { code: 'transport_failure' }); assert.equal(posts, 1)
  assert.equal((await client.ingestionStatus({ operation_id: operation }, f.plan)).budget_state, 'uncertain')
  mode = 'pending'; const pending = client.ingestionExecute(payload, f.plan); client.cancel(); await assert.rejects(pending, { code: 'cancelled' })
  assert.equal(posts, 2)
  await assert.rejects(client.ingestionExecute(payload, f.plan), { code: 'deadline' }); assert.equal(posts, 3)
  client.disconnect()
})
test('authenticated asserted scope freezes until disconnect; request and source snapshots cannot mutate mid-flight', async () => {
  const f = await fixture(), working = clone(payload), source = clone(f.inspected)
  let release, statusScope = scope, calls = 0
  const client = createWorkbenchClient({ fetchImpl: async url => {
    if (url.includes('/plan/')) { calls++; return new Promise(resolve => { release = () => resolve(success(f.plan)) }) }
    if (url.includes('/operation/')) return success({ ...f.result, scope: statusScope })
    return success(graphDTO)
  } })
  await client.connect(connection)
  const pending = client.ingestionPlan(working, source)
  working.ontology.entity_types[0].description = 'mutated'; source.text = 'changed'; source.source.project_id = workspace
  // Source cryptography precedes fetch, so wait for the authored transport latch.
  for (let i = 0; i < 100 && !release; i++) await new Promise(r => setTimeout(r, 5))
  assert.equal(calls, 1); release(); assert.equal((await pending).fingerprint, f.plan.fingerprint)
  statusScope = { ...scope, graph_id: workspace }
  await assert.rejects(client.ingestionStatus({ operation_id: operation }), { code: 'invalid_reply' })
  client.disconnect()
})
test('origin authorization denial clears protected client state', async () => {
  const client = createWorkbenchClient({ fetchImpl: async url => url.includes('/operation/') ? failure('origin_denied', 403) : success(graphDTO) })
  await client.connect(connection)
  await assert.rejects(client.ingestionStatus({ operation_id: operation }), { code: 'origin_denied' })
  await assert.rejects(client.sourceList(), { code: 'disconnected' })
})

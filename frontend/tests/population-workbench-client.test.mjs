import test from 'node:test'
import assert from 'node:assert/strict'
import { createWorkbenchClient } from '../src/api/workbench.js'
import { populationOptions, populationTypeLabels, validatePopulationPreview, validatePopulationExport, parsePopulationCsv } from '../src/api/populationWorkbench.js'
const uid = value => `00000000-0000-0000-0000-${String(value).padStart(12, '0')}`
const graph = () => ({ graph_id: 'graph_1', nodes: [], edges: [], node_count: 0, edge_count: 0 })
const credentials = { origin: 'http://127.0.0.1:5001', graph: 'graph_1', token: 'private-token' }
const hostile = '<img src=x onerror=alert(1)> 😀 中 https://example.invalid/\nsecond line'
const synthetic = ['user_name', 'bio', 'persona', 'age', 'gender', 'mbti', 'country', 'profession', 'interested_topics', 'karma', 'friend_count', 'follower_count', 'statuses_count']
export function previewFixture() {
  const profiles = [0, 1].map(index => ({ user_id: index, user_name: `actor_${index}_123`, name: index ? '机构, "quoted"\r\n中😀' : hostile, bio: hostile, persona: `${hostile}\r\npersona`, karma: 1000, friend_count: 100, follower_count: 150, statuses_count: 500, age: index ? 30 : 0, gender: index ? 'other' : null, mbti: index ? 'ISTJ' : null, country: index ? '中国' : null, profession: index ? 'Organization' : null, interested_topics: index ? ['Public Policy', '中文😀'] : [], source_entity_uuid: uid(index + 1), source_entity_type: index ? 'Organization' : 'Person', created_at: '2026-10-05' }))
  const grounding = Object.fromEntries(profiles.map(p => [p.source_entity_uuid, { source_entity_uuid: p.source_entity_uuid, labels: ['Entity', p.source_entity_type], summary: hostile, attributes: { city: '香港', nested: { text: hostile }, list: [null, 3, true] }, episode_ids: [uid(80)], evidence_ids: [uid(90)], facts: [{ edge_uuid: uid(20), direction: p.user_id ? 'incoming' : 'outgoing', edge_name: 'KNOWS', source_node_uuid: uid(1), target_node_uuid: uid(2), fact: hostile, episode_ids: [uid(81)], evidence_ids: [uid(91)] }] }]))
  return { graph_id: 'graph_1', generator: 'inherited_rule_based_v1', enrichment: 'none', llm_used: false, simulation_executed: false, snapshot_consistent: false, profile_date: '2026-10-05', eligible_count: 2, selected_count: 2, synthetic_fields: [...synthetic], profiles, grounding }
}
const clone = value => JSON.parse(JSON.stringify(value))
const jsonReply = data => new Response(JSON.stringify({ success: true, data }), { headers: { 'Content-Type': 'application/json' } })
const errorCode = expected => error => error.code === expected && !error.message.includes('private-token')
function csv(preview) {
  const quote = value => /[",\r\n]/.test(value) ? `"${value.replaceAll('"', '""')}"` : value
  const rows = ['user_id,name,username,user_char,description']
  preview.profiles.forEach((p, index) => {
    const character = p.persona && p.persona !== p.bio ? `${p.bio} ${p.persona}` : p.bio
    rows.push([String(index), p.name, p.user_name, character.replaceAll('\n', ' ').replaceAll('\r', ' '), p.bio.replaceAll('\n', ' ').replaceAll('\r', ' ')].map(quote).join(','))
  })
  return rows.join('\r\n') + '\r\n'
}
function reddit(preview) {
  return JSON.stringify(preview.profiles.map(p => {
    const row = { user_id: p.user_id, username: p.user_name, name: p.name, bio: p.bio, persona: p.persona, karma: p.karma, created_at: p.created_at }
    for (const key of ['age', 'gender', 'mbti', 'country', 'profession']) if (p[key]) row[key] = p[key]
    if (p.interested_topics.length) row.interested_topics = p.interested_topics
    return row
  }))
}
const exportReply = (preview, platform) => new Response(platform === 'twitter' ? csv(preview) : reddit(preview), { headers: { 'Content-Type': platform === 'twitter' ? 'text/csv; charset=utf-8' : 'application/json; charset=utf-8', 'Content-Disposition': 'attachment; filename="../../untrusted.html"', 'X-Content-Type-Options': 'nosniff' } })
async function fixture(handler, deadlineMs = 125000) {
  const calls = [], client = createWorkbenchClient({ deadlineMs, fetchImpl: async (url, options) => { calls.push({ url, options }); return calls.length === 1 ? jsonReply(graph()) : handler(url, options, calls.length) } })
  await client.connect(credentials); return { client, calls }
}
test('strict options preserve zero and maximum seed; custom types are bounded and generic labels excluded', () => {
  assert.deepEqual(populationOptions({ types: [], max_agents: 1, seed: 0 }), { max_agents: 1, seed: 0 })
  assert.equal(populationOptions({ max_agents: 100, seed: 4294967295 }).seed, 4294967295)
  for (const seed of [-1, 4294967296, 1.5, '0', null, NaN, Infinity]) assert.throws(() => populationOptions({ seed }), errorCode('invalid_request'))
  for (const max_agents of [0, 101, 1.1, '10', null]) assert.throws(() => populationOptions({ max_agents }), errorCode('invalid_request'))
  for (const types of [['Entity'], ['Node'], ['Person', 'Person'], ['<script>'], Array.from({ length: 51 }, (_, i) => `Type${i}`), null]) assert.throws(() => populationOptions({ types }), errorCode('invalid_request'))
  assert.deepEqual(populationTypeLabels(['Entity', 'Node', 'Person', 'Person', 'Organization', '<img>', null]), ['Organization', 'Person'])
})
test('complete DTO is detached and preserves hostile source text, references and nested attributes', () => {
  const producer = previewFixture(), admitted = validatePopulationPreview(producer, 'graph_1')
  producer.profiles[0].persona = 'changed'; producer.grounding[uid(1)].attributes.nested.text = 'changed'; producer.grounding[uid(1)].facts[0].evidence_ids.push(uid(99))
  assert.ok(admitted.profiles[0].persona.includes(hostile)); assert.equal(admitted.grounding[uid(1)].attributes.nested.text, hostile)
  assert.deepEqual(admitted.grounding[uid(1)].facts[0].evidence_ids, [uid(91)])
  const long = previewFixture(); const full = '完整😀\n'.repeat(10000)
  long.grounding[uid(1)].facts[0].fact = full; long.grounding[uid(2)].facts[0].fact = full
  assert.equal(validatePopulationPreview(long, 'graph_1').grounding[uid(1)].facts[0].fact, full)
})
test('preview rejects malformed flags, dates, counts, identities, memberships, directions and joined facts', () => {
  const mutations = [
    p => { p.graph_id = 'foreign' }, p => { p.generator = 'model' }, p => { p.enrichment = 'llm' }, p => { p.llm_used = true }, p => { p.simulation_executed = true }, p => { p.snapshot_consistent = true },
    p => { p.profile_date = '2026-02-30' }, p => { p.selected_count = 1 }, p => { p.eligible_count = 1 }, p => { p.synthetic_fields.pop() }, p => { p.profiles[0].extra = true },
    p => { p.profiles[0].user_id = 2 }, p => { p.profiles[0].karma = -1 }, p => { p.profiles[0].age = '30' }, p => { p.profiles[0].country = {} }, p => { p.profiles[0].created_at = '2026-10-04' },
    p => { p.profiles[1].user_name = p.profiles[0].user_name }, p => { p.profiles[1].source_entity_uuid = uid(1) }, p => { p.profiles.reverse() }, p => { p.grounding[uid(3)] = p.grounding[uid(1)] },
    p => { delete p.grounding[uid(1)] }, p => { p.grounding[uid(1)].source_entity_uuid = uid(2) }, p => { p.grounding[uid(1)].labels = ['Entity', 'Organization'] },
    p => { p.grounding[uid(1)].facts[0].direction = 'incoming' }, p => { p.grounding[uid(1)].facts[0].source_node_uuid = uid(3) }, p => { p.grounding[uid(2)].facts = [] },
    p => { p.grounding[uid(2)].facts[0].fact = 'divergent' }, p => { p.grounding[uid(1)].facts[0].evidence_ids = ['not-a-uuid'] }, p => { p.grounding[uid(1)].attributes.x = Infinity }, p => { p.profiles[0].bio = '\ud800' }
    , p => { p.grounding[uid(1)].summary = null }, p => { p.grounding[uid(1)].facts[0].fact = null }
  ]
  mutations.forEach(mutate => { const p = previewFixture(); mutate(p); assert.throws(() => validatePopulationPreview(p, 'graph_1'), errorCode('invalid_reply')) })
  assert.throws(() => validatePopulationPreview(previewFixture(), 'graph_1', { types: ['Faculty'] }), errorCode('invalid_reply'))
  assert.throws(() => validatePopulationPreview(previewFixture(), 'graph_1', { max_agents: 1 }), errorCode('invalid_reply'))
})
test('native CSV parser keeps quoted commas, doubled quotes, CRLF and Unicode; malformed CSV fails', () => {
  const p = previewFixture(), raw = csv(p)
  assert.equal(validatePopulationExport(raw, 'twitter', p), 2)
  assert.equal(parsePopulationCsv(raw)[2][1], p.profiles[1].name)
  for (const bad of ['"unclosed', 'a"b,c', '"a"tail,b', raw + '\r\n', raw.replace('actor_0_123', 'foreign'), raw.replace('user_char', 'persona')]) assert.throws(() => validatePopulationExport(bad, 'twitter', p))
})
test('Reddit presence and values match inherited truthy optional behavior; extra/missing/reordered rows fail', () => {
  const p = previewFixture(), raw = reddit(p), rows = JSON.parse(raw)
  assert.equal(validatePopulationExport(raw, 'reddit', p), 2)
  assert.equal(Object.hasOwn(rows[0], 'age'), false); assert.equal(Object.hasOwn(rows[0], 'gender'), false); assert.equal(Object.hasOwn(rows[0], 'interested_topics'), false)
  assert.equal(rows[1].age, 30); assert.deepEqual(rows[1].interested_topics, ['Public Policy', '中文😀'])
  for (const mutate of [r => r.reverse(), r => r.pop(), r => r.push(r[0]), r => { r[0].user_name = r[0].username }, r => { r[0].age = 0 }, r => { delete r[1].age }, r => { r[0].persona = 'foreign' }]) { const r = clone(rows); mutate(r); assert.throws(() => validatePopulationExport(JSON.stringify(r), 'reddit', p)) }
  assert.throws(() => validatePopulationExport(raw.replace('"user_id":0', '"user_id":0,"user\\u005fid":1'), 'reddit', p))
})
test('explicit protected preparation routes return exact bytes and submitted option snapshot for later native Save', async () => {
  const p = previewFixture(), options = { types: ['Person', 'Organization'], max_agents: 100, seed: 4294967295 }
  const { client, calls } = await fixture((_url, request) => JSON.parse(request.body).platform ? exportReply(p, JSON.parse(request.body).platform) : jsonReply(p))
  const admitted = await client.populationPreview(options)
  options.seed = 0; options.types.reverse()
  for (const platform of ['twitter', 'reddit']) {
    const output = await client.populationExport(platform, { types: ['Person', 'Organization'], max_agents: 100, seed: 4294967295 }, admitted)
    assert.deepEqual(output.bytes, new TextEncoder().encode(platform === 'twitter' ? csv(p) : reddit(p)))
    assert.equal(output.filename, platform === 'twitter' ? 'oasis-twitter-profiles.csv' : 'oasis-reddit-profiles.json')
  }
  assert.deepEqual(calls.map(c => c.url), ['http://127.0.0.1:5001/api/graph/data/graph_1', 'http://127.0.0.1:5001/api/graph/population/graph_1/preview', 'http://127.0.0.1:5001/api/graph/population/graph_1/export', 'http://127.0.0.1:5001/api/graph/population/graph_1/export'])
  for (const call of calls) { assert.equal(call.options.headers.Authorization, 'Bearer private-token'); assert.equal(call.options.credentials, 'omit'); assert.equal(call.options.redirect, 'error'); assert.equal(call.options.cache, 'no-store'); assert.equal(call.options.referrerPolicy, 'no-referrer'); assert.ok(call.options.signal instanceof AbortSignal) }
  assert.equal(JSON.parse(calls[3].options.body).seed, 4294967295)
  client.disconnect()
})
test('export preparation transport allocates no browser URL and initiates no native Save', async () => {
  const originalCreate = URL.createObjectURL; let allocations = 0
  URL.createObjectURL = () => { allocations++; throw new Error('transport must not initiate browser file actions') }
  const p = previewFixture(), { client, calls } = await fixture((_url, request) => JSON.parse(request.body).platform ? exportReply(p, JSON.parse(request.body).platform) : jsonReply(p))
  try {
    const admitted = await client.populationPreview({ seed: 0 })
    const prepared = await client.populationExport('twitter', { seed: 0 }, admitted)
    assert.deepEqual(prepared.bytes, new TextEncoder().encode(csv(p))); assert.equal(allocations, 0); assert.equal(calls.length, 3)
  } finally { client.disconnect(); URL.createObjectURL = originalCreate }
})
test('export requires current private admission and rejects mutable options or preview changes before fetch', async () => {
  const { client, calls } = await fixture(() => jsonReply(previewFixture()))
  await assert.rejects(client.populationExport('twitter', {}, previewFixture()), errorCode('invalid_request'))
  const admitted = await client.populationPreview({ seed: 0 })
  await assert.rejects(client.populationExport('twitter', { seed: 1 }, admitted), errorCode('invalid_request'))
  admitted.profiles[0].name = 'changed'
  await assert.rejects(client.populationExport('twitter', { seed: 0 }, admitted), errorCode('invalid_request'))
  assert.equal(calls.length, 2)
  const current = await client.populationPreview({ seed: 0 }); client.cancel()
  await assert.rejects(client.populationExport('reddit', { seed: 0 }, current), errorCode('invalid_request'))
  client.disconnect()
})
test('MIME, redirected response, invalid UTF8, duplicate JSON, body caps and foreign export rows fail closed', async () => {
  const p = previewFixture()
  const handlers = [
    () => new Response(csv(p), { headers: { 'Content-Type': 'text/html' } }),
    () => new Response(csv(p), { headers: { 'Content-Type': 'text/csv; charset=iso-8859-1' } }),
    () => new Response(Uint8Array.of(0xff), { headers: { 'Content-Type': 'text/csv; charset=utf-8' } }),
    () => new Response(csv(p), { headers: { 'Content-Type': 'text/csv; charset=utf-8', 'Content-Length': '2097153' } }),
    () => exportReply({ ...p, profiles: [...p.profiles].reverse() }, 'twitter'),
    () => { const response = exportReply(p, 'twitter'); Object.defineProperty(response, 'redirected', { value: true }); return response }
  ]
  for (const handler of handlers) {
    const { client } = await fixture((_url, request) => JSON.parse(request.body).platform ? handler() : jsonReply(p))
    const admitted = await client.populationPreview({})
    await assert.rejects(client.populationExport('twitter', {}, admitted), error => ['invalid_reply', 'result_too_large'].includes(error.code)); client.disconnect()
  }
  const duplicate = await fixture(() => new Response(JSON.stringify({ success: true, data: p }).replace('"llm_used":false', '"llm_used":false,"llm_used":true'), { headers: { 'Content-Type': 'application/json' } }))
  await assert.rejects(duplicate.client.populationPreview({}), errorCode('invalid_reply')); duplicate.client.disconnect()
  let cancelled = false
  const large = await fixture(() => new Response(new ReadableStream({ pull(controller) { controller.enqueue(new Uint8Array(2098177)) }, cancel() { cancelled = true } }), { headers: { 'Content-Type': 'application/json' } }))
  await assert.rejects(large.client.populationPreview({}), errorCode('result_too_large')); assert.equal(cancelled, true); large.client.disconnect()
})
test('malformed 401 clears authentication; optional old host is localized code; there is no retry', async () => {
  const denied = await fixture(() => new Response('raw secret traceback', { status: 401 }))
  await assert.rejects(denied.client.populationPreview({}), errorCode('unauthorized'))
  await assert.rejects(denied.client.populationPreview({}), errorCode('disconnected')); assert.equal(denied.calls.length, 2)
  const unavailable = await fixture(() => new Response('<html>missing</html>', { status: 404, headers: { 'Content-Type': 'text/html' } }))
  await assert.rejects(unavailable.client.populationPreview({}), errorCode('population_unavailable')); assert.equal(unavailable.calls.length, 2); unavailable.client.disconnect()
})
test('disconnect and replacement discard delayed successes; deadline covers response body', async () => {
  let resolve, pendingCount = 0
  const { client } = await fixture(() => ++pendingCount === 1 ? new Promise(r => { resolve = r }) : jsonReply(previewFixture()))
  const old = client.populationPreview({}); const rejected = assert.rejects(old, errorCode('cancelled'))
  const current = await client.populationPreview({ seed: 4294967295 }); resolve(jsonReply(previewFixture())); await rejected
  assert.equal(current.profiles.length, 2); client.disconnect()
  let bodyCancelled = false
  const stalled = await fixture(() => new Response(new ReadableStream({ pull() { return new Promise(() => {}) }, cancel() { bodyCancelled = true } }), { headers: { 'Content-Type': 'application/json' } }), 30)
  await assert.rejects(stalled.client.populationPreview({}), errorCode('deadline')); assert.equal(bodyCancelled, true); stalled.client.disconnect()
})

import test from 'node:test'
import assert from 'node:assert/strict'
import { createWorkbenchClient } from '../src/api/workbench.js'
import { experimentSelection, validateExperimentCatalog, validateExperimentComparison, experimentTables } from '../src/api/experimentComparison.js'
const project = '11111111-1111-1111-1111-111111111111', hash = 'a'.repeat(64)
const seeds = ['9007199254740993', '-9223372036854775808', '9223372036854775807', '0', '-1']
function catalog() {
  return { version: 1, project_id: project, project_revision: 2, cohort_manifest_digest: hash, public_projection_digest: hash,
    members: ['completed', 'failed', 'running', 'cancelled', 'uncertain'].map((state, i) => ({ member_id: 'm' + i, member_label: '<img src=x onerror=alert(1)>中😀' + i, case_label: 'Case', run_id: `00000000-0000-0000-0000-00000000000${i}`, state, cancel_requested: i === 0 || i === 3, seed: seeds[i], max_rounds: 2, platforms: ['twitter'] })) }
}
const selection = { version: 1, title: '中😀 Observations', member_ids: ['m0', 'm1', 'm2', 'm3', 'm4'] }
function comparison(cat = catalog(), payload = selection) {
  const members = cat.members.filter(m => payload.member_ids.includes(m.member_id)).map(m => ({ ...structuredClone(m), disposition: m.state === 'completed' ? 'successful' : m.state === 'running' ? 'pending' : m.state, project_revision: 2, runtime_sha256: hash, prepared_artifact_sha256: hash, request_fingerprint: hash, record_digest: hash, metrics: null, recording: null }))
  for (const m of members.filter(m => m.disposition === 'successful')) {
    m.metrics = { twitter: { logged_action_total: 0, logged_action_by_type: {}, final_table_counts: Object.fromEntries(experimentTables.map(t => [t, t === 'post' ? 0 : null])) } }
    m.recording = { version: 1, recording_revision: hash, anchors: { graph_id: 'recorded.graph', simulation_id: 'simulation', branch_id: 'branch', run_id: m.run_id, project_id: project, project_revision: 2 }, platforms: ['twitter'], runtime_sha256: hash, runtime_versions: { python: '3.11', sqlite: '3', oasis: '0.2', camel: '0.2' }, artifact_sha256: { 'simulation_config.json': hash, 'source_grounding.json': hash, 'twitter_profiles.csv': hash } }
  }
  const accounting = Object.fromEntries(['successful', 'failed', 'cancelled', 'pending', 'uncertain'].map(d => [d, members.filter(m => m.disposition === d).length]))
  const sample = accounting.successful
  const dist = n => ({ sample_count: n, missing_count: members.length - n, min: n ? 0 : null, max: n ? 0 : null, arithmetic_mean: n ? 0 : null, median: n ? 0 : null, population_standard_deviation: n ? 0 : null })
  const matrix = []
  for (let i = 0; i < members.length; i++) for (let j = i + 1; j < members.length; j++) {
    const left = members[i], right = members[j], fields = {}
    for (const key of ['seed', 'max_rounds', 'runtime_sha256', 'platforms', 'project_revision', 'prepared_artifact_sha256']) fields[key] = { left: left[key], right: right[key], equal: JSON.stringify(left[key]) === JSON.stringify(right[key]) }
    for (const key of ['artifact_sha256', 'runtime_versions']) fields[key] = { left: left.recording?.[key] ?? null, right: right.recording?.[key] ?? null, equal: left.recording && right.recording ? true : null }
    matrix.push({ left_member_id: left.member_id, right_member_id: right.member_id, fields })
  }
  return { version: 1, title: payload.title, project_id: project, project_revision: 2, cohort_manifest_digest: hash, members, accounting, distributions: [{ case_label: 'Case', platform: 'twitter', member_count: members.length, successful_count: sample, non_successful_count: members.length - sample, distinct_declared_seed_count: members.length, distinct_successful_seed_count: sample, metrics: { logged_action_total: dist(sample), logged_action_by_type: {}, final_table_counts: Object.fromEntries(experimentTables.map(t => [t, dist(t === 'post' ? sample : 0)])) } }], cancellation_intent: { cancel_requested_count: members.filter(m => m.cancel_requested).length, overlaps_disposition_accounting: true }, distinct_declared_seed_count: members.length, distinct_successful_seed_count: sample, comparability_matrix: matrix, native_result_digest: hash, public_projection_digest: hash, causal_attribution_supported: false, provider_quality_assessed: false, shared_budget_enforcement_supported: false, actual_provider_spend: null, ensemble_launch_supported: false, coverage: { atomic_cohort_snapshot: false, statistics: 'descriptive_completed_available_observations_only', labels_prove_controlled_intervention: false, hashes_prove_semantic_equivalence: false, digests_are_signatures: false, possible_initial_log_duplicates: true, post_log_interviews_may_exist_in_trace: true, exact_event_row_links: false, historical_or_causal_truth: false, missing_metrics_are_zero: false } }
}
const response = data => new Response(JSON.stringify({ success: true, data }), { headers: { 'Content-Type': 'application/json' } })
const graph = { graph_id: 'graph_1', nodes: [], edges: [], node_count: 0, edge_count: 0 }
async function connect(client) { await client.connect({ origin: 'http://127.0.0.1:5001', graph: 'graph_1', token: 'secret' }) }
test('admission preserves exact signed64 seeds, all five dispositions, cancellation overlap and null versus zero', () => {
  const cat = catalog(), result = validateExperimentComparison(comparison(cat), cat, selection)
  assert.deepEqual(result.members.map(m => m.seed), seeds)
  assert.deepEqual(result.accounting, { successful: 1, failed: 1, cancelled: 1, pending: 1, uncertain: 1 })
  assert.equal(result.cancellation_intent.cancel_requested_count, 2)
  assert.equal(result.members[0].metrics.twitter.final_table_counts.post, 0)
  assert.equal(result.members[0].metrics.twitter.final_table_counts.follow, null)
  assert.equal(result.members[1].metrics, null)
})
test('catalog rejects duplicate IDs, bad shapes, numeric/noncanonical/out-of-range seeds and unsafe counts', () => {
  for (const mutate of [c => { c.extra = 1 }, c => { c.members[1].member_id = 'm0' }, c => { c.members[1].run_id = c.members[0].run_id }, c => { c.members[0].seed = 9007199254740993 }, c => { c.members[0].seed = '+1' }, c => { c.members[0].seed = '-0' }, c => { c.members[0].seed = '9223372036854775808' }, c => { c.project_revision = true }, c => { c.members[0].platforms = ['reddit', 'twitter'] }, c => { c.members[0].member_label = 'x\u0000' }]) {
    const c = catalog(); mutate(c); assert.throws(() => validateExperimentCatalog(c), { code: 'invalid_reply' })
  }
})
test('request admission is UTF8 bounded, literal, ordered and detached', () => {
  const cat = catalog(), payload = { version: 1, title: '中'.repeat(53) + 'a', member_ids: ['m0'] }
  const snapshot = experimentSelection(payload, cat)
  payload.title = 'changed'; cat.members[0].seed = '0'
  assert.equal(JSON.parse(snapshot.raw).title, '中'.repeat(53) + 'a'); assert.equal(snapshot.catalog.members[0].seed, seeds[0])
  for (const value of [{ ...selection, title: '中'.repeat(54) }, { ...selection, title: ' ' }, { ...selection, title: 'a\n' }, { ...selection, title: '\ud800' }, { ...selection, member_ids: [] }, { ...selection, member_ids: ['m0', 'm0'] }, { ...selection, member_ids: ['m1', 'm0'] }, { ...selection, member_ids: ['foreign'] }, { ...selection, graph: 'graph_1' }]) assert.throws(() => experimentSelection(value, catalog()), { code: 'invalid_request' })
})
test('foreign, stale, corrupt and unsupported comparison projections fail closed', () => {
  const mutations = [r => { r.title = 'foreign' }, r => { r.project_id = '22222222-2222-2222-2222-222222222222' }, r => { r.cohort_manifest_digest = 'b'.repeat(64) }, r => { r.members.reverse() }, r => { r.members[0].member_label = 'foreign' }, r => { r.members[1].metrics = {} }, r => { r.members[0].disposition = 'failed' }, r => { r.accounting.successful = true }, r => { r.cancellation_intent.cancel_requested_count = 1 }, r => { r.coverage.missing_metrics_are_zero = true }, r => { r.actual_provider_spend = 0 }, r => { r.ensemble_launch_supported = true }, r => { r.members[0].metrics.twitter.logged_action_total = 1 }, r => { r.members[0].recording.anchors.run_id = project }, r => { r.distributions[0].metrics.final_table_counts.follow.sample_count = 1 }, r => { r.distributions[0].metrics.logged_action_total.median = Infinity }, r => { r.comparability_matrix[0].fields.seed.left = '0' }, r => { r.comparability_matrix[0].fields.runtime_versions.equal = false }, r => { r.native_result_digest = 'bad' }]
  for (const mutate of mutations) { const cat = catalog(), r = comparison(cat); mutate(r); assert.throws(() => validateExperimentComparison(r, cat, selection), { code: 'invalid_reply' }) }
})
test('single private transport uses fixed explicit experiment GET/POST without graph/query/body on catalog', async () => {
  const calls = [], cat = catalog()
  const client = createWorkbenchClient({ fetchImpl: async (url, options) => { calls.push({ url, options }); return response(calls.length === 1 ? graph : url.endsWith('/catalog') ? cat : comparison(cat, JSON.parse(options.body))) } })
  try {
    assert.equal(calls.length, 0); await connect(client); assert.equal(calls.length, 1)
    const loaded = await client.experimentCatalog(); await client.experimentCompare(selection, loaded)
    assert.deepEqual(calls.map(c => c.url), ['http://127.0.0.1:5001/api/graph/data/graph_1', 'http://127.0.0.1:5001/api/experiments/catalog', 'http://127.0.0.1:5001/api/experiments/compare'])
    assert.equal(calls[1].options.method, 'GET'); assert.equal(calls[1].options.body, undefined)
    assert.equal(calls[2].options.method, 'POST'); assert.deepEqual(JSON.parse(calls[2].options.body), selection)
    for (const { options } of calls) { assert.equal(options.headers.Authorization, 'Bearer secret'); assert.equal(options.credentials, 'omit'); assert.equal(options.redirect, 'error'); assert.equal(options.cache, 'no-store'); assert.equal(options.referrerPolicy, 'no-referrer') }
  } finally { client.disconnect() }
})
test('private client freezes admitted payload/catalog across asynchronous caller mutation', async () => {
  let resolve, requestBody, count = 0
  const cat = catalog(), payload = structuredClone(selection), expected = comparison(cat)
  const client = createWorkbenchClient({ fetchImpl: async (_url, options) => ++count === 1 ? response(graph) : new Promise(r => { resolve = r; requestBody = options.body }) })
  try { await connect(client); const work = client.experimentCompare(payload, cat); payload.title = 'changed'; cat.members[0].seed = '0'; resolve(response(expected)); assert.equal((await work).title, selection.title); assert.equal(JSON.parse(requestBody).title, selection.title) } finally { client.disconnect() }
})
test('private transport rejects duplicate keys, oversized replies, malformed UTF8 and raw server causes', async () => {
  const cases = [() => new Response('{"success":true,"success":true,"data":{}}', { headers: { 'Content-Type': 'application/json' } }), () => new Response('x', { headers: { 'Content-Type': 'application/json', 'Content-Length': '600000' } }), () => new Response(new Uint8Array([0xff]), { headers: { 'Content-Type': 'application/json' } }), () => new Response(JSON.stringify({ success: false, error: { code: 'experiment_unavailable', cause: 'secret' } }), { status: 503, headers: { 'Content-Type': 'application/json' } })]
  for (const reply of cases) { let n = 0; const client = createWorkbenchClient({ fetchImpl: async () => ++n === 1 ? response(graph) : reply() }); try { await connect(client); await assert.rejects(client.experimentCatalog(), e => ['invalid_reply', 'result_too_large'].includes(e.code)); assert.equal(n, 2) } finally { client.disconnect() } }
})
test('optional unavailable has one explicit manual recovery and wrong-status code is rejected', async () => {
  for (const status of [503, 400]) {
    let calls = 0; const client = createWorkbenchClient({ fetchImpl: async () => ++calls === 1 ? response(graph) : calls === 2 ? new Response('{"success":false,"error":{"code":"experiment_unavailable"}}', { status, headers: { 'Content-Type': 'application/json' } }) : response(catalog()) })
    try { await connect(client); await assert.rejects(client.experimentCatalog(), { code: status === 503 ? 'experiment_unavailable' : 'invalid_reply' }); assert.equal(calls, 2); await client.experimentCatalog(); assert.equal(calls, 3) } finally { client.disconnect() }
  }
})
test('401/403 clear authentication; cancel/disconnect suppress late response without another request', async () => {
  for (const status of [401, 403]) { let n = 0; const client = createWorkbenchClient({ fetchImpl: async () => ++n === 1 ? response(graph) : new Response('private secret', { status }) }); try { await connect(client); await assert.rejects(client.experimentCatalog(), { code: status === 401 ? 'unauthorized' : 'origin_denied' }); await assert.rejects(client.experimentCatalog(), { code: 'disconnected' }); assert.equal(n, 2) } finally { client.disconnect() } }
  for (const action of ['cancel', 'disconnect']) {
    let resolve, signal, n = 0
    const client = createWorkbenchClient({ fetchImpl: async (_url, options) => ++n === 1 ? response(graph) : new Promise(r => { resolve = r; signal = options.signal }) })
    try { await connect(client); const work = client.experimentCatalog(); const rejected = assert.rejects(work, { code: 'cancelled' }); client[action](); assert.equal(signal.aborted, true); resolve(response(catalog())); await rejected; assert.equal(n, 2) } finally { client.disconnect() }
  }
})

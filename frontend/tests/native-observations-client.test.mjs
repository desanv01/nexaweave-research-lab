import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash, webcrypto } from 'node:crypto'
import { createWorkbenchClient } from '../src/api/workbench.js'
import { createNativeObservationsChannel, nativeObservationSelection, nativeObservationsPayload, parseObservationRecord, validateNativeObservationsResult } from '../src/api/nativeObservations.js'
globalThis.crypto ||= webcrypto
const uid = n => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`, hash = 'a'.repeat(64), clone = v => JSON.parse(JSON.stringify(v))
// Independent producer. No frontend canonicalization or digest helper is used.
function ascii(v) {
  if (Array.isArray(v)) return '[' + v.map(ascii).join(',') + ']'
  if (v && typeof v === 'object') return '{' + Object.keys(v).sort().map(k => ascii(k) + ':' + ascii(v[k])).join(',') + '}'
  const raw = JSON.stringify(v); let out = ''
  for (let i = 0; i < raw.length; i++) out += raw.charCodeAt(i) >= 127 ? '\\u' + raw.charCodeAt(i).toString(16).padStart(4, '0') : raw[i]
  return out
}
const digest = v => createHash('sha256').update(ascii(v), 'ascii').digest('hex')
const bytesHash = raw => createHash('sha256').update(raw, 'utf8').digest('hex')
const pick = (v, keys) => Object.fromEntries(keys.split(' ').map(k => [k, v[k]]))
const lines = ['{"event_type":"seed","n":1.0,"large":9007199254740993,"minus":-0,"exp":1e+2}', '{"action_type":"POST","success":true,"text":"中😀 <script>private</script>"}', '{"event_type":"action","action_type":"POST","success":false}', '{"unknown":[1,{"ok":true}]}']
function fixture() {
  const files = ['state.json', 'simulation_config.json', 'source_grounding.json', 'twitter_profiles.csv', 'reddit_profiles.json'].map(name => ({ name, sha256: hash, size: 123 }))
  const preparation = { schema_version: 1, display_graph_id: 'graph_1', scope: { schema_version: 1, workspace_id: uid(1), project_id: uid(2), graph_id: uid(3), run_id: null, branch_id: null, layer: 'source' }, project_revision: 1, operation_id: uid(9), source: { source_revision: uid(5), source_name: '中😀 <script>source</script>', source_sha256: hash }, options: { types: null, max_agents: 10, seed: 0, platforms: ['twitter', 'reddit'], max_rounds: 24, simulation_requirement: 'Study 中😀' }, actors: [{ source_entity_uuid: uid(7), name: '中😀', labels: ['Person'] }], projection_sha256: hash, plan_sha256: null, state: 'ready', progress: { stage: 'ready', completed: 100, total: 100 }, error_code: null, authorization: { model_calls_enabled: true, ceiling_microusd: '12345' }, receipt: { simulation_id: 'sim_' + uid(9).replaceAll('-', ''), artifact_sha256: digest({ schema_version: 1, files }), files }, graph_snapshot_atomic: false, model_calls_started: true, simulation_executed: false }
  preparation.plan_sha256 = digest(pick(preparation, 'schema_version display_graph_id scope project_revision operation_id source options actors projection_sha256'))
  const manifest = { schema_version: 1, files: ['twitter', 'reddit'].flatMap(p => [{ name: p + '_simulation.db', sha256: bytesHash('db-' + p), size: 9 }, { name: p + '/actions.jsonl', sha256: bytesHash(lines.join('\n') + '\n'), size: Buffer.byteLength(lines.join('\n') + '\n') }]) }
  const launch = { schema_version: 1, display_graph_id: 'graph_1', scope: clone(preparation.scope), preparation: { operation_id: preparation.operation_id, plan_sha256: preparation.plan_sha256, simulation_id: preparation.receipt.simulation_id, artifact_sha256: preparation.receipt.artifact_sha256 }, request: { schema_version: 1, principal: 'local-research', project_id: uid(2), project_revision: 1, simulation_id: preparation.receipt.simulation_id, run_id: uid(10), artifact_sha256: preparation.receipt.artifact_sha256, runtime_sha256: hash, platforms: ['twitter', 'reddit'], seed: 0, max_rounds: 24 }, limits: { max_calls: 20, max_input_bytes: 2097152, max_output_tokens: 4096, max_run_seconds: 600 }, ceiling_microusd: '12345', model_label: 'scripted 中😀', launch_sha256: null, state: 'completed', error_code: null, authorization: { model_calls_enabled: false }, workflow: null, receipt: null, cancel_requested: false, cleanup: { known: true, pending: false, owner_thread_alive: false } }
  launch.launch_sha256 = digest(pick(launch, 'schema_version display_graph_id scope preparation request limits ceiling_microusd model_label'))
  launch.workflow = { workflow_id: 'mf-native-v1-' + uid(10).replaceAll('-', '') + '-' + digest(launch.request), temporal_run_id: uid(11), native_run_id: uid(10) }
  launch.receipt = { run_id: uid(10), attempt_id: uid(12), instance_id: uid(13), request_fingerprint: digest(launch.request), outcome: 'completed', evidence_sha256: digest(manifest) }
  return { selection: { launch, preparation }, manifest }
}
const payload = (offset = 0, limit = 20, platform = 'twitter') => ({ schema_version: 1, launch_id: uid(10), launch_sha256: fixture().selection.launch.launch_sha256, platform, offset, limit })
function page(p = payload(), f = fixture()) {
  const records = lines.slice(p.offset, p.offset + p.limit).map((raw_json, i) => ({ index: p.offset + i, raw_json, record_sha256: bytesHash(raw_json) }))
  return { schema_version: 1, launch: clone(f.selection.launch), manifest: clone(f.manifest), platform: p.platform, offset: p.offset, limit: p.limit, total_records: 4, next_offset: p.offset + records.length < 4 ? p.offset + records.length : null, counts: { event_records: 2, action_records: 2, successful_action_records: 1, failed_action_records: 1 }, records }
}
const ctx = (p = payload(), f = fixture()) => ({ graph: 'graph_1', payload: p, selection: f.selection })
const code = c => e => e.code === c
const response = data => new Response(JSON.stringify({ success: true, data }), { headers: { 'Content-Type': 'application/json' } })
const credentials = { origin: 'http://127.0.0.1:5001', graph: 'graph_1', token: 'private-token' }
const graph = { graph_id: 'graph_1', nodes: [], edges: [], node_count: 0, edge_count: 0 }

test('request contains only exact receipt identity and finite page coordinates', () => {
  assert.deepEqual(JSON.parse(nativeObservationsPayload(payload())), payload())
  for (const mutate of [v => v.path = 'private', v => v.principal = 'other', v => v.schema_version = true, v => v.offset = true, v => v.offset = -1, v => v.offset = 10001, v => v.offset = 0.5, v => v.limit = 0, v => v.limit = 21, v => v.limit = false, v => v.platform = 'both', v => v.launch_id = '../x', v => v.launch_sha256 = 'A'.repeat(64)]) { const p = payload(); mutate(p); assert.throws(() => nativeObservationsPayload(p), code('invalid_request')) }
})
test('independent raw UTF8 hashes preserve float, exponent, large integer and Unicode lexemes', async () => {
  const result = await validateNativeObservationsResult(page(), ctx())
  assert.equal(result.records[0].raw_json, lines[0]); assert.equal(result.records[0].record_sha256, bytesHash(lines[0]))
  assert.notEqual(bytesHash(lines[0]), bytesHash(JSON.stringify(JSON.parse(lines[0]))))
  assert.equal(result.launch.authorization.model_calls_enabled, false)
  assert.deepEqual(result.counts, { event_records: 2, action_records: 2, successful_action_records: 1, failed_action_records: 1 })
  const selected = await nativeObservationSelection(fixture().selection, 'graph_1'); assert.equal(selected.launch.receipt.evidence_sha256, digest(fixture().manifest))
})
test('completed receipt, current source and manifest correspondence refuse rehashed forgeries', async () => {
  const cases = [
    { name: 'launch no longer completed', mutate: v => v.launch.state = 'running' },
    { name: 'completed receipt missing', mutate: v => v.launch.receipt = null },
    { name: 'receipt attempt differs from selected receipt', mutate: v => v.launch.receipt.attempt_id = uid(88) },
    { name: 'receipt instance differs from selected receipt', mutate: v => v.launch.receipt.instance_id = uid(88) },
    { name: 'receipt evidence digest explicitly corrupted', mutate: v => v.launch.receipt.evidence_sha256 = hash },
    { name: 'launch project scope differs', mutate: v => v.launch.scope.project_id = uid(88) },
    { name: 'native request project revision differs', mutate: v => v.launch.request.project_revision++ },
    { name: 'READY artifact binding differs', mutate: v => v.launch.preparation.artifact_sha256 = hash },
    { name: 'native selected platform order differs', mutate: v => v.launch.request.platforms.reverse() },
    { name: 'rehashed manifest file order reversed', rehashManifest: true, mutate: v => v.manifest.files.reverse() },
    { name: 'rehashed manifest file name escapes trusted names', rehashManifest: true, mutate: v => v.manifest.files[0].name = '../db' },
    { name: 'rehashed manifest native file exceeds 64MiB', rehashManifest: true, mutate: v => v.manifest.files[0].size = 67108865 },
    { name: 'rehashed manifest action log exceeds 8MiB', rehashManifest: true, mutate: v => v.manifest.files[1].size = 8388609 },
    { name: 'rehashed manifest file digest is uppercase', rehashManifest: true, mutate: v => v.manifest.files[0].sha256 = 'A'.repeat(64) },
    { name: 'rehashed manifest file has extra field', rehashManifest: true, mutate: v => v.manifest.files[0].extra = true },
    { name: 'rehashed manifest selected output file missing', rehashManifest: true, mutate: v => v.manifest.files.pop() },
    { name: 'observation page has extra field', mutate: v => v.extra = true }
  ]
  for (const { name, mutate, rehashManifest = false } of cases) {
    const f = fixture(), p = payload(), v = page(p, f), baseline = clone(v)
    mutate(v)
    assert.notDeepEqual(v, baseline, `${name}: mutation must alter the valid baseline`)
    if (rehashManifest) {
      // Bind both selected and returned receipts to the deliberately malformed
      // manifest so this case exercises its structure, not an incidental digest
      // mismatch. Non-manifest corruptions, especially receipt digest, stay exact.
      const evidence = digest(v.manifest)
      v.launch.receipt.evidence_sha256 = evidence
      f.selection.launch.receipt.evidence_sha256 = evidence
    }
    await assert.rejects(validateNativeObservationsResult(v, ctx(p, f)), code('invalid_reply'), name)
  }
  const f = fixture(); f.selection.preparation.source.source_revision = uid(88)
  await assert.rejects(validateNativeObservationsResult(page(), ctx(payload(), f)), code('invalid_reply'), 'selected retained source revision differs')
  for (const state of ['planned', 'queued', 'running', 'failed', 'cancelled', 'uncertain']) { const f = fixture(); f.selection.launch.state = state; await assert.rejects(nativeObservationSelection(f.selection, 'graph_1'), code('invalid_reply'), `selected launch state ${state} is not completed`) }
})
test('pagination and whole-log count correspondence admit offsets at total, refuse malformed pages', async () => {
  for (const p of [payload(0, 2), payload(2, 2), payload(4, 2), payload(0, 20, 'reddit')]) assert.equal((await validateNativeObservationsResult(page(p), ctx(p))).platform, p.platform)
  for (const mutate of [v => v.platform = 'reddit', v => v.offset++, v => v.limit = 19, v => v.total_records = true, v => v.total_records = 10001, v => v.next_offset = 4, v => v.records.pop(), v => v.records[0].index++, v => v.records[0].extra = true, v => v.records[0].record_sha256 = hash, v => v.counts.action_records = 1, v => v.counts.successful_action_records = 3, v => v.counts.event_records = 3, v => v.counts.extra = 0]) { const v = page(); mutate(v); await assert.rejects(validateNativeObservationsResult(v, ctx()), code('invalid_reply')) }
  await assert.rejects(validateNativeObservationsResult(page(payload(5)), ctx(payload(5))), code('invalid_reply'))
  const first = page(payload(0, 2)), second = page(payload(2, 2)); second.counts.event_records++
  await assert.rejects(validateNativeObservationsResult(second, { ...ctx(payload(2, 2)), knownPage: first }), code('invalid_reply'))
})
test('record parser denies malformed syntax, duplicate decoded keys, depth and UTF8 limits even when rehashed', async () => {
  const nested = depth => '{"x":'.repeat(depth) + '0' + '}'.repeat(depth)
  assert.equal(parseObservationRecord(nested(8)).x.x.x.x.x.x.x.x, 0)
  for (const raw of ['[]', 'null', '{}\n', '{}\r', '{\r"event_type":"seed"}', '{"n":NaN}', '{"n":1e999}', '{"n":' + '1' + '0'.repeat(309) + '}', '{"n":' + '9'.repeat(4000) + '}', '{"x":1,"\\u0078":2}', '{"x":{"a":1,"a":2}}', '{"x":"\\ud800"}', nested(9), '{"x":"' + '😀'.repeat(2048) + '"}', '{broken']) {
    assert.throws(() => parseObservationRecord(raw), code('invalid_reply'))
    const v = page(); v.records[0].raw_json = raw; v.records[0].record_sha256 = bytesHash(raw)
    await assert.rejects(validateNativeObservationsResult(v, ctx()), code('invalid_reply'))
  }
  assert.deepEqual(parseObservationRecord('{"n":9007199254740993}'), { n: null }) // unsafe convenience value is never rendered as evidence
  assert.deepEqual(parseObservationRecord('{"n":' + '1' + '0'.repeat(308) + '}'), { n: null })
  assert.deepEqual(parseObservationRecord('{"text":"escaped\\r\\n"}'), { text: 'escaped\r\n' })
})
test('all integer tokens use finite-double admission while original finite and escaped line evidence remains exact', async () => {
  for (const raw of ['{"event_type":"seed","n":' + '1' + '0'.repeat(308) + '}', '{"event_type":"seed","text":"escaped\\r\\n"}']) {
    const f = fixture(), p = payload(), v = page(p, f), physical = [raw, ...lines.slice(1)].join('\n')
    v.records[0] = { index: 0, raw_json: raw, record_sha256: bytesHash(raw) }
    const log = v.manifest.files.find(file => file.name === 'twitter/actions.jsonl'); log.size = Buffer.byteLength(physical); log.sha256 = bytesHash(physical)
    f.selection.launch.receipt.evidence_sha256 = digest(v.manifest); v.launch = clone(f.selection.launch)
    const result = await validateNativeObservationsResult(v, ctx(p, f))
    assert.equal(result.records[0].raw_json, raw); assert.equal(result.records[0].record_sha256, bytesHash(raw))
  }
  for (const raw of ['{"event_type":"seed","n":' + '1' + '0'.repeat(309) + '}', '{\r"event_type":"seed"}', '{"event_type":"seed"}\r', '{"event_type":"seed"}\n']) {
    const f = fixture(), p = payload(), v = page(p, f), physical = [raw, ...lines.slice(1)].join('\n')
    v.records[0] = { index: 0, raw_json: raw, record_sha256: bytesHash(raw) }
    const log = v.manifest.files.find(file => file.name === 'twitter/actions.jsonl'); log.size = Buffer.byteLength(physical); log.sha256 = bytesHash(physical)
    f.selection.launch.receipt.evidence_sha256 = digest(v.manifest); v.launch = clone(f.selection.launch)
    await assert.rejects(validateNativeObservationsResult(v, ctx(p, f)), code('invalid_reply'))
  }
})
test('selected physical log length accounts for all object bytes and separators even on partial or empty pages', async () => {
  for (const p of [payload(0, 20), payload(0, 2), payload(4, 2)]) {
    const f = fixture(), v = page(p, f)
    const minimum = 3 * v.total_records - 1 + v.records.reduce((n, r) => n + Buffer.byteLength(r.raw_json) - 2, 0)
    const log = v.manifest.files.find(file => file.name === 'twitter/actions.jsonl')
    const minimalLines = Array.from({ length: v.total_records }, (_, i) => v.records.find(r => r.index === i)?.raw_json || '{}')
    const physical = minimalLines.join('\n')
    v.counts = minimalLines.reduce((counts, raw) => { const record = JSON.parse(raw); if (typeof record.event_type === 'string') counts.event_records++; if (typeof record.action_type === 'string') { counts.action_records++; if (record.success === true) counts.successful_action_records++; if (record.success === false) counts.failed_action_records++ } return counts }, { event_records: 0, action_records: 0, successful_action_records: 0, failed_action_records: 0 })
    assert.equal(Buffer.byteLength(physical), minimum)
    log.size = minimum; log.sha256 = bytesHash(physical)
    f.selection.launch.receipt.evidence_sha256 = digest(v.manifest); v.launch = clone(f.selection.launch)
    assert.equal((await validateNativeObservationsResult(v, ctx(p, f))).manifest.files[1].size, minimum)
    log.size = minimum - 1; log.sha256 = bytesHash(physical.slice(0, -1)); f.selection.launch.receipt.evidence_sha256 = digest(v.manifest); v.launch = clone(f.selection.launch)
    await assert.rejects(validateNativeObservationsResult(v, ctx(p, f)), code('invalid_reply'))
  }
})
test('all caller-owned values detach before asynchronous cryptographic validation', async () => {
  const v = page(), context = ctx(), expected = clone(v)
  const pending = validateNativeObservationsResult(v, context)
  v.records[0].raw_json = '{}'; context.selection.launch.receipt.attempt_id = uid(88); context.payload.platform = 'reddit'
  assert.deepEqual(await pending, expected)
})
test('empty platform log produces zero counts and no synthetic record or cursor', async () => {
  const f = fixture(), p = payload()
  const log = f.manifest.files.find(v => v.name === 'twitter/actions.jsonl'); log.size = 0; log.sha256 = bytesHash('')
  f.selection.launch.receipt.evidence_sha256 = digest(f.manifest)
  const v = page(p, f); v.total_records = 0; v.next_offset = null; v.records = []; v.counts = { event_records: 0, action_records: 0, successful_action_records: 0, failed_action_records: 0 }
  assert.deepEqual((await validateNativeObservationsResult(v, ctx(p, f))).records, [])
  v.counts.event_records = 1; await assert.rejects(validateNativeObservationsResult(v, ctx(p, f)), code('invalid_reply'))
})
test('observation deadline aborts its owned fetch and does not silently retry', async () => {
  let calls = 0, signal
  const channel = createNativeObservationsChannel({ deadlineMs: 1000, connection: () => credentials, denied: () => {}, fetchImpl: (_url, options) => { calls++; signal = options.signal; return new Promise(() => {}) } })
  await assert.rejects(channel.page(payload(), fixture().selection), code('deadline'))
  assert.equal(calls, 1); assert.equal(signal.aborted, true)
})
test('workbench uses protected explicit POST and observations clear leaves pending source channel alive', async () => {
  let finishSource, sourceSignal, observationsSignal, calls = []
  const client = createWorkbenchClient({ fetchImpl: (url, options) => {
    calls.push({ url, options })
    if (url.includes('/data/')) return Promise.resolve(response(graph))
    if (url.includes('/source/')) { sourceSignal = options.signal; return new Promise(resolve => { finishSource = resolve }) }
    observationsSignal = options.signal; return Promise.resolve(response(page(JSON.parse(options.body))))
  } })
  await client.connect(credentials)
  const source = client.sourceList(); const result = await client.nativeObservationsPage(payload(), fixture().selection)
  client.clearNativeObservations(); assert.equal(sourceSignal.aborted, false); assert.equal(result.total_records, 4)
  assert.equal(calls.length, 3); const request = calls[2]
  assert.equal(request.url, 'http://127.0.0.1:5001/api/native-observations/page/graph_1'); assert.equal(request.options.method, 'POST'); assert.equal(request.options.headers.Authorization, 'Bearer private-token'); assert.equal(request.options.redirect, 'error'); assert.equal(request.options.credentials, 'omit'); assert.equal(request.options.cache, 'no-store'); assert.equal(request.options.referrerPolicy, 'no-referrer'); assert.equal(observationsSignal.aborted, true)
  finishSource(response({ schema_version: 1, binary_retained: false, graph_ingestion_executed: false, sources: [], has_more: false, window_limit: 20 })); await source; client.disconnect()
})
test('clear and disconnect reject late observation replies without aborting other workflows', async () => {
  for (const action of ['clearNativeObservations', 'clearNativeLaunch', 'disconnect']) {
    let finish, signal, invoked
    const started = new Promise(resolve => { invoked = resolve })
    const client = createWorkbenchClient({ fetchImpl: (url, options) => {
      if (url.includes('/data/')) return Promise.resolve(response(graph))
      signal = options.signal; invoked(); return new Promise(resolve => { finish = resolve })
    } })
    await client.connect(credentials); const pending = client.nativeObservationsPage(payload(), fixture().selection)
    const rejection = assert.rejects(pending, code('cancelled')); await started
    client[action](); assert.equal(signal.aborted, true); finish(response(page())); await rejection; client.disconnect()
  }
})
test('strict envelope codes, wire bounds and authentication failures fail closed without retry', async () => {
  const cases = [
    [() => new Response(JSON.stringify({ success: false, error: { code: 'busy' } }), { status: 503, headers: { 'Content-Type': 'application/json' } }), 'busy'],
    [() => new Response(JSON.stringify({ success: false, error: { code: 'busy', private: 'path' } }), { status: 503, headers: { 'Content-Type': 'application/json' } }), 'invalid_reply'],
    [() => new Response(JSON.stringify({ success: false, error: { code: 'busy' } }), { status: 400, headers: { 'Content-Type': 'application/json' } }), 'invalid_reply'],
    [() => new Response('{"success":true,"success":true,"data":{}}', { headers: { 'Content-Type': 'application/json' } }), 'invalid_reply'],
    [() => new Response('unavailable', { status: 503 }), 'observations_unavailable'],
    [() => new Response('{}', { headers: { 'Content-Type': 'application/json', 'Content-Length': '262273' } }), 'result_too_large'],
    [() => new Response(' '.repeat(262273), { headers: { 'Content-Type': 'application/json' } }), 'result_too_large'],
    [() => new Response(new Uint8Array([0xff]), { headers: { 'Content-Type': 'application/json' } }), 'invalid_reply'],
    [() => new Response('', { status: 401 }), 'unauthorized'], [() => new Response('', { status: 403 }), 'origin_denied']
  ]
  for (const [reply, expected] of cases) {
    let calls = 0, denied = 0
    const channel = createNativeObservationsChannel({ fetchImpl: async () => { calls++; return reply() }, deadlineMs: 1000, connection: () => credentials, denied: () => { denied++ } })
    await assert.rejects(channel.page(payload(), fixture().selection), code(expected)); assert.equal(calls, 1); assert.equal(denied, ['unauthorized', 'origin_denied'].includes(expected) ? 1 : 0)
  }
})

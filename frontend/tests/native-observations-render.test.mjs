import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createHash, webcrypto } from 'node:crypto'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'
import { nativeObservationsCopy, nativeObservationsCopyFor } from '../src/i18n/nativeObservations.js'
import { validateNativeObservationsResult } from '../src/api/nativeObservations.js'
import { createWorkbenchClient } from '../src/api/workbench.js'
globalThis.crypto ||= webcrypto
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://127.0.0.1:5173/research' })
for (const name of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node', 'Document', 'ShadowRoot']) globalThis[name] = name === 'window' ? dom.window : dom.window[name]
const { createApp, h, nextTick, reactive } = await import('vue')
const file = new URL('../src/components/workbench/NativeObservations.vue', import.meta.url)
const { descriptor } = parse(readFileSync(file, 'utf8'), { filename: file.pathname })
const compiled = compileScript(descriptor, { id: 'native-observations', inlineTemplate: true, genDefaultAs: '__component' })
let code = compiled.content.replace(/from (['"])vue\1/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`)
code = code.replace(/from (['"])(\.{1,2}\/[^'"]+)\1/g, (_m, _q, relative) => `from ${JSON.stringify(new URL(relative, file).href)}`)
const activeValidations = new Set(); let validationCalls = 0
globalThis.__u07eTrackValidation = (...args) => {
  validationCalls++; const promise = validateNativeObservationsResult(...args); activeValidations.add(promise)
  promise.then(() => activeValidations.delete(promise), () => activeValidations.delete(promise)); return promise
}
// Observe the actual component's validation promise to await late admission.
code = code.replace(/\bvalidateNativeObservationsResult\(/g, 'globalThis.__u07eTrackValidation(')
const component = (await import(`data:text/javascript;base64,${Buffer.from(code + '\nexport default __component').toString('base64')}`)).default
const uid = n => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`, hash = 'a'.repeat(64), clone = v => JSON.parse(JSON.stringify(v))
const hostile = '<img src=x onerror=alert(1)> <script>secret</script> 中😀 https://evil.invalid/'
function ascii(v) {
  if (Array.isArray(v)) return '[' + v.map(ascii).join(',') + ']'
  if (v && typeof v === 'object') return '{' + Object.keys(v).sort().map(k => ascii(k) + ':' + ascii(v[k])).join(',') + '}'
  const raw = JSON.stringify(v); let out = ''
  for (let i = 0; i < raw.length; i++) out += raw.charCodeAt(i) >= 127 ? '\\u' + raw.charCodeAt(i).toString(16).padStart(4, '0') : raw[i]
  return out
}
const digest = v => createHash('sha256').update(ascii(v), 'ascii').digest('hex'), bytesHash = v => createHash('sha256').update(v, 'utf8').digest('hex')
const pick = (v, keys) => Object.fromEntries(keys.split(' ').map(k => [k, v[k]]))
const lines = Array.from({ length: 23 }, (_, i) => i === 22 ? '{"unknown":"中😀","n":1.0}' : JSON.stringify({ event_type: hostile, action_type: 'POST ' + i, success: i % 2 === 0, text: hostile }))
const searchQuery = 'Mira "Vale"\\north\n' + hostile
function redditRecord(action, args, success = true) {
  return JSON.stringify({ round: 1, timestamp: '2026-10-05T00:00:00.000000', agent_id: 1, agent_name: 'Mira Vale',
    action_type: action, action_args: args, result: null, success })
}
const redditLines = [
  redditRecord('SEARCH_USER', { query: searchQuery }),
  redditRecord('LIKE_COMMENT', { comment_id: 0, like_id: 7 }),
  redditRecord('DISLIKE_COMMENT', { comment_id: 42, dislike_id: 9 }),
  redditRecord('SEARCH_USER', undefined, false),
  redditRecord('SEARCH_USER', { query: { value: 'Mira' } }, false),
  redditRecord('LIKE_COMMENT', { comment_id: '42' }, false),
  redditRecord('DISLIKE_COMMENT', { comment_id: -1 }, false),
  redditRecord('LIKE_COMMENT', { like_id: 17 }),
  redditRecord('DISLIKE_COMMENT', { comment_id: 1.5 }, false),
  redditRecord('LIKE_COMMENT', { comment_id: 0 }, false).replace('"comment_id":0', '"comment_id":2e-324'),
  redditRecord('DISLIKE_COMMENT', { comment_id: 42 }, false).replace('"comment_id":42', '"comment_id":42.000000000000001'),
  redditRecord('search_user', { query: 'An unrecognized action' }),
  '{"round":1,"timestamp":"2026-10-05T00:00:00.000000","event_type":"round_start","simulated_hour":0}',
]
const platformLogs = { twitter: lines, reddit: redditLines }
function fixture(restored = false, logs = platformLogs) {
  const files = ['state.json', 'simulation_config.json', 'source_grounding.json', 'twitter_profiles.csv', 'reddit_profiles.json'].map(name => ({ name, sha256: hash, size: 123 }))
  const preparation = { schema_version: 1, display_graph_id: 'graph_1', scope: { schema_version: 1, workspace_id: uid(1), project_id: uid(2), graph_id: uid(3), run_id: null, branch_id: null, layer: 'source' }, project_revision: 1, operation_id: uid(9), source: { source_revision: uid(5), source_name: hostile, source_sha256: hash }, options: { types: null, max_agents: 10, seed: 0, platforms: ['twitter', 'reddit'], max_rounds: 24, simulation_requirement: hostile }, actors: [{ source_entity_uuid: uid(7), name: hostile, labels: ['Person'] }], projection_sha256: hash, plan_sha256: null, state: 'ready', progress: { stage: 'ready', completed: 100, total: 100 }, error_code: null, authorization: { model_calls_enabled: true, ceiling_microusd: '12345' }, receipt: { simulation_id: 'sim_' + uid(9).replaceAll('-', ''), artifact_sha256: digest({ schema_version: 1, files }), files }, graph_snapshot_atomic: false, model_calls_started: true, simulation_executed: false }
  if (restored) {
    files.pop()
    Object.assign(preparation.options, { platforms: ['twitter'], max_rounds: 1 })
    preparation.receipt.artifact_sha256 = digest({ schema_version: 1, files })
  }
  preparation.plan_sha256 = digest(pick(preparation, 'schema_version display_graph_id scope project_revision operation_id source options actors projection_sha256'))
  const manifest = { schema_version: 1, files: preparation.options.platforms.flatMap(platform => {
    const rawLog = logs[platform].join('\n') + '\n'
    return [{ name: restored ? 'native.sqlite3' : platform + '_simulation.db', sha256: bytesHash('db'), size: 2 },
      { name: restored ? 'actions.jsonl' : platform + '/actions.jsonl', sha256: bytesHash(rawLog), size: Buffer.byteLength(rawLog) }]
  }) }
  const launch = { schema_version: 1, display_graph_id: 'graph_1', scope: clone(preparation.scope), preparation: { operation_id: preparation.operation_id, plan_sha256: preparation.plan_sha256, simulation_id: preparation.receipt.simulation_id, artifact_sha256: preparation.receipt.artifact_sha256 }, request: { schema_version: 1, principal: 'local-research', project_id: uid(2), project_revision: 1, simulation_id: preparation.receipt.simulation_id, run_id: uid(10), artifact_sha256: preparation.receipt.artifact_sha256, runtime_sha256: hash, platforms: ['twitter', 'reddit'], seed: 0, max_rounds: 24 }, limits: { max_calls: 20, max_input_bytes: 2097152, max_output_tokens: 4096, max_run_seconds: 600 }, ceiling_microusd: '12345', model_label: hostile, launch_sha256: null, state: 'completed', error_code: null, authorization: { model_calls_enabled: false }, workflow: null, receipt: null, cancel_requested: false, cleanup: { known: true, pending: false, owner_thread_alive: false } }
  if (restored) {
    launch.schema_version = 2
    const source_run_id = uid(20), checkpoint_sha256 = 'c'.repeat(64)
    launch.checkpoint = { mode: 'continue', source_run_id, checkpoint_sha256 }
    Object.assign(launch.request, { schema_version: 2, source_run_id, checkpoint_sha256, platforms: ['twitter'], max_rounds: 1 })
  }
  launch.launch_sha256 = digest(pick(launch, 'schema_version display_graph_id scope preparation request limits ceiling_microusd model_label' + (restored ? ' checkpoint' : '')))
  launch.receipt = { run_id: uid(10), attempt_id: uid(12), instance_id: uid(13), request_fingerprint: digest(launch.request), outcome: 'completed', evidence_sha256: digest(manifest) }
  return { selection: { launch, preparation }, manifest, logs }
}
function page(payload, f = fixture()) {
  const log = f.logs[payload.platform]
  const records = log.slice(payload.offset, payload.offset + payload.limit).map((raw_json, i) => ({ index: payload.offset + i, raw_json, record_sha256: bytesHash(raw_json) }))
  const counts = { event_records: 0, action_records: 0, successful_action_records: 0, failed_action_records: 0 }
  for (const raw of log) {
    const record = JSON.parse(raw)
    if (typeof record.event_type === 'string') counts.event_records++
    if (typeof record.action_type === 'string') {
      counts.action_records++
      if (record.success === true) counts.successful_action_records++
      if (record.success === false) counts.failed_action_records++
    }
  }
  return { schema_version: f.selection.launch.schema_version, launch: clone(f.selection.launch), manifest: clone(f.manifest), platform: payload.platform, offset: payload.offset, limit: payload.limit, total_records: log.length, next_offset: payload.offset + records.length < log.length ? payload.offset + records.length : null, counts, records }
}
function mount(initial = {}) {
  const props = reactive({ methods: { page: async payload => page(payload), clear: () => {} }, selection: fixture().selection, connected: true, busy: false, displayGraphId: 'graph_1', resetVersion: 0, locale: 'en', ...initial })
  const root = document.createElement('div'); document.body.append(root)
  const app = createApp({ setup: () => () => h(component, props) }); app.mount(root)
  let mounted = true
  return { props, root, cleanup() { if (mounted) { mounted = false; app.unmount(); root.remove() } } }
}
async function waitFor(predicate) {
  const end = Date.now() + 2000
  do { await nextTick(); if (predicate()) return; await new Promise(resolve => setImmediate(resolve)) } while (Date.now() < end)
  assert.ok(predicate(), 'bounded lifecycle condition did not complete')
}
const admitted = m => waitFor(() => m.root.querySelector('.load') && !m.root.querySelector('.load').disabled)
const finished = m => waitFor(() => m.root.querySelector('section').getAttribute('aria-busy') === 'false')

test('mounted selection, platform and locale changes perform no implicit page request', async () => {
  let calls = 0; const m = mount({ methods: { page: () => { calls++ } } })
  try {
    await admitted(m)
    for (const locale of ['en', 'zh', 'ms']) {
      m.props.locale = locale; await nextTick(); const copy = nativeObservationsCopyFor(locale)
      assert.ok(m.root.textContent.includes(copy.title)); assert.ok(m.root.textContent.includes(copy.distinction)); assert.ok(m.root.textContent.includes(copy.bounds))
      assert.deepEqual(Object.keys(copy).sort(), Object.keys(nativeObservationsCopy.en).sort()); assert.deepEqual(Object.keys(copy.errors).sort(), Object.keys(nativeObservationsCopy.en.errors).sort())
    }
    const select = m.root.querySelector('select'); assert.deepEqual([...select.options].map(v => v.value), ['twitter', 'reddit'])
    select.value = 'reddit'; select.dispatchEvent(new dom.window.Event('change', { bubbles: true })); await nextTick()
    assert.equal(calls, 0); assert.equal(m.root.querySelector('.records'), null); assert.equal(m.root.querySelector('.feedback').getAttribute('aria-live'), 'polite')
    assert.ok([...m.root.querySelectorAll('button')].every(b => b.type === 'button'))
  } finally { m.cleanup() }
})
test('explicit page controls render original inert JSON, honest counts, unknown records and focused status', async () => {
  const requests = []; let reply, callerSelection
  const m = mount({ methods: { page: async (p, selection) => { requests.push(clone(p)); callerSelection = selection; return reply = page(p) } } })
  try {
    await admitted(m); m.root.querySelector('.load').focus(); m.root.querySelector('.load').click(); await finished(m)
    assert.equal(requests.length, 1); assert.equal(requests[0].schema_version, 1); assert.equal(m.root.querySelectorAll('.records li').length, 20); assert.equal(document.activeElement, m.root.querySelector('.feedback'))
    assert.equal(m.root.querySelector('.previous').disabled, true); assert.equal(m.root.querySelector('.next').disabled, false)
    reply.records[0].raw_json = '{}'; callerSelection.preparation.source.source_name = 'mutated'; await nextTick()
    assert.ok(m.root.textContent.includes(hostile)); assert.equal(m.root.textContent.includes('mutated'), false); assert.equal(m.root.querySelector('a,img,script'), null)
    assert.ok(m.root.textContent.includes(nativeObservationsCopy.en.partial)); assert.ok(m.root.textContent.includes(nativeObservationsCopy.en.action_records))
    m.root.querySelector('.next').click(); await finished(m); assert.equal(requests[1].offset, 20); assert.equal(m.root.querySelectorAll('.records li').length, 3)
    assert.equal(m.root.querySelector('.next').disabled, true); assert.ok(m.root.textContent.includes(nativeObservationsCopy.en.unknown)); assert.ok(m.root.textContent.includes('"n":1.0'))
    m.root.querySelector('.previous').click(); await finished(m); assert.equal(requests[2].offset, 0)
    m.root.querySelector('.refresh').click(); await finished(m); assert.equal(requests[3].offset, 0)
    const select = m.root.querySelector('select'); select.value = 'reddit'; select.dispatchEvent(new dom.window.Event('change', { bubbles: true })); await nextTick()
    assert.equal(m.root.querySelector('.records'), null); assert.equal(requests.length, 4)
    m.root.querySelector('.load').click(); await finished(m)
    assert.deepEqual(requests[4], { ...requests[0], platform: 'reddit' })
    assert.equal(m.root.querySelector('.next').disabled, true)
    assert.equal(document.activeElement, m.root.querySelector('.feedback'))
    for (const locale of ['en', 'zh', 'ms', 'constructor']) {
      m.props.locale = locale
      await nextTick()
      const copy = nativeObservationsCopy[locale === 'constructor' ? 'en' : locale]
      const { SEARCH_USER, LIKE_COMMENT, DISLIKE_COMMENT } = copy.redditActions
      const rows = [...m.root.querySelectorAll('.records > li')]
      assert.deepEqual(rows.map(row => row.querySelector('.event-label').textContent), [
        SEARCH_USER, LIKE_COMMENT, DISLIKE_COMMENT, SEARCH_USER, SEARCH_USER, LIKE_COMMENT,
        DISLIKE_COMMENT, LIKE_COMMENT, DISLIKE_COMMENT, LIKE_COMMENT, DISLIKE_COMMENT, 'search_user', 'round_start',
      ])
      assert.deepEqual(rows.map(row => row.querySelector('.event-target dt')?.textContent), [
        copy.searchQuery, copy.commentId, copy.commentId, ...Array(10).fill(undefined),
      ])
      assert.deepEqual(rows.map(row => row.querySelector('.event-target pre')?.textContent), [
        searchQuery, '0', '42', ...Array(10).fill(undefined),
      ])
      assert.equal(m.root.querySelector('a,img,script'), null)
      assert.deepEqual(rows.map(row => row.querySelector('details pre').textContent), redditLines)
      assert.deepEqual(rows.map(row => row.querySelector('details .mono').textContent), redditLines.map(bytesHash))
      assert.equal(requests.length, 5)
    }
  } finally { m.cleanup() }
})
test('mounted restored observations request version two and render the bound output', async () => {
  const f = fixture(true), calls = []
  const m = mount({ selection: f.selection, methods: { page: async payload => {
    calls.push(clone(payload)); return page(payload, f)
  } } })
  try {
    await admitted(m)
    assert.deepEqual([...m.root.querySelector('select').options].map(option => option.value), ['twitter'])
    assert.equal(calls.length, 0)
    m.root.querySelector('.load').click(); await finished(m)
    assert.deepEqual(calls, [{ schema_version: 2, launch_id: f.selection.launch.request.run_id,
      launch_sha256: f.selection.launch.launch_sha256, platform: 'twitter', offset: 0, limit: 20 }])
    assert.equal(m.root.querySelectorAll('.records li').length, 20)
    assert.ok(m.root.textContent.includes(f.selection.launch.receipt.evidence_sha256))
    assert.deepEqual([...m.root.querySelectorAll('.records li details pre')].map(node => node.textContent), lines.slice(0, 20))
    assert.ok(m.root.querySelector('.manifest').textContent.includes('native.sqlite3'))
    assert.ok(m.root.querySelector('.manifest').textContent.includes('actions.jsonl'))
    assert.equal(m.root.querySelector('.manifest').textContent.includes('twitter/actions.jsonl'), false)
    assert.equal(m.root.textContent.includes(f.selection.launch.checkpoint.checkpoint_sha256), false)
  } finally { m.cleanup() }
})
test('mounted restored event status verifies the receipt, bounded absence and late reply refusal', async () => {
  const twitter = [
    JSON.stringify({ round: 2, event_type: 'round_start' }),
    JSON.stringify({ round: 2, action_type: 'CREATE_POST', success: true,
      action_args: { post_id: 7, content: 'Restored fixture post' } }),
    JSON.stringify({ round: 2, event_type: 'round_end', actions_count: 1 }),
  ]
  const f = fixture(true, { ...platformLogs, twitter })
  const launch = f.selection.launch
  const payload = { schema_version: 3, launch_id: launch.request.run_id, launch_sha256: launch.launch_sha256,
    platform: 'twitter', record_index: 1, record_sha256: bytesHash(twitter[1]) }
  function uuid5(namespace, label) {
    const bytes = createHash('sha1').update(Buffer.from(namespace.replaceAll('-', ''), 'hex')).update(label).digest()
    bytes[6] = (bytes[6] & 15) | 80
    bytes[8] = (bytes[8] & 63) | 128
    return bytes.subarray(0, 16).toString('hex').replace(/^(........)(....)(....)(....)/, '$1-$2-$3-$4-')
  }
  const operation = uuid5(payload.launch_id, 'mirofish:restored-twitter-action:v1:1')
  const evidence = uuid5(operation, 'mirofish:restored-twitter-action-evidence:v1')
  const scope = { ...launch.scope, layer: 'simulation', run_id: payload.launch_id }
  const group = 'mf1_' + digest(pick(scope, 'workspace_id project_id graph_id run_id branch_id layer'))
  const episode = uuid5('6ba7b811-9dad-11d1-80b4-00c04fd430c8', 'mirofish:episode:v1:' + group + ':' + operation)
  const completed = { ...payload, scope,
    operation_id: operation, fingerprint: hash, state: 'completed', attempt_id: uid(31), completion_barrier_open: true,
    receipt: { group_id: group, episode_id: episode, fingerprint: hash, evidence_ids: [evidence] } }
  const json = (value, status = 200) => new Response(JSON.stringify(value), {
    status, headers: { 'Content-Type': 'application/json' },
  })
  const success = data => json({ success: true, data })
  const calls = []
  let deliverLate, lateSignal
  const client = createWorkbenchClient({ fetchImpl: async (url, options) => {
    assert.equal(options.headers.Authorization, 'Bearer fixture-observation-token')
    if (url.endsWith('/api/graph/data/graph_1')) {
      return success({ graph_id: 'graph_1', nodes: [], edges: [], node_count: 0, edge_count: 0 })
    }
    const body = JSON.parse(options.body)
    if (url.endsWith('/api/native-observations/page/graph_1')) return success(page(body, f))
    assert.equal(url, 'http://127.0.0.1:5001/api/native-observations/event-status/graph_1')
    assert.equal(options.method, 'POST')
    assert.equal(options.cache, 'no-store')
    assert.deepEqual(body, payload)
    calls.push(body)
    if (calls.length === 1) return success(completed)
    if (calls.length === 2) return json({ success: false, error: { code: 'not_found' } }, 404)
    if (calls.length === 3) return success({ ...completed, receipt: { ...completed.receipt, episode_id: uid(32) } })
    lateSignal = options.signal
    return new Promise(resolve => { deliverLate = () => resolve(success(completed)) })
  } })
  await client.connect({ origin: 'http://127.0.0.1:5001', graph: 'graph_1', token: 'fixture-observation-token' })
  const m = mount({ selection: f.selection, methods: {
    page: client.nativeObservationsPage, eventStatus: client.nativeObservationsEventStatus,
    clear: client.clearNativeObservations,
  } })
  try {
    await admitted(m)
    m.root.querySelector('.load').click(); await finished(m)
    const button = () => m.root.querySelector('.event-status-read')
    assert.equal(m.root.querySelectorAll('.event-status-read').length, 1)
    assert.equal(button().closest('li').dataset.index, '1')
    assert.deepEqual(calls, [])

    button().click(); await finished(m)
    assert.ok(m.root.querySelector('.event-status-result').textContent.includes(evidence))
    assert.ok(m.root.textContent.includes(nativeObservationsCopy.en.eventReceipt.states.completed))

    button().click(); await finished(m)
    assert.equal(m.root.querySelector('.event-status-result'), null)
    assert.ok(m.root.textContent.includes(nativeObservationsCopy.en.eventReceipt.absent))

    button().click(); await finished(m)
    assert.equal(m.root.querySelector('.event-status-result'), null)
    assert.ok(m.root.textContent.includes(nativeObservationsCopy.en.errors.invalid_reply))

    button().click(); await waitFor(() => !!deliverLate)
    m.props.resetVersion++
    await nextTick()
    assert.equal(lateSignal.aborted, true)
    deliverLate()
    await new Promise(resolve => setImmediate(resolve))
    assert.equal(m.root.querySelector('.event-status-result'), null)
    assert.equal(m.root.querySelector('.event-status-read'), null)
    assert.equal(calls.length, 4)
  } finally {
    m.cleanup()
    client.disconnect()
  }
})

test('pending contradictory controls are disabled; clear, disconnect, source context and unmount fence late replies', async () => {
  for (const action of ['clear', 'disconnect', 'reset', 'selection', 'graph', 'unmount']) {
    let finish, cleared = 0, requests = 0, requested
    const begun = new Promise(resolve => { requested = resolve })
    const m = mount({ methods: { page: p => { requests++; requested(); return new Promise(resolve => { finish = () => resolve(page(p)) }) }, clear: () => { cleared++ } } })
    try {
      await admitted(m); m.root.querySelector('.load').click(); await begun; await nextTick()
      assert.equal(m.root.querySelector('.load').disabled, true); assert.equal(m.root.querySelector('select').disabled, true)
      if (action === 'clear') m.root.querySelector('.clear').click()
      else if (action === 'disconnect') m.props.connected = false
      else if (action === 'reset') m.props.resetVersion++
      else if (action === 'selection') m.props.selection = null
      else if (action === 'graph') m.props.displayGraphId = 'other'
      else m.cleanup()
      await nextTick(); assert.ok(cleared >= 2); const before = validationCalls; finish()
      await waitFor(() => validationCalls > before && activeValidations.size === 0)
      await nextTick(); assert.equal(m.root.querySelector('.records'), null); assert.equal(requests, 1)
    } finally { m.cleanup() }
  }
})
test('transient refusal retains visibly stale same-receipt page; denial clears all private observations', async () => {
  for (const failure of ['conflict', 'busy', 'observations_unavailable', 'unauthorized', 'origin_denied', 'tombstoned']) {
    let calls = 0
    const m = mount({ methods: { page: async p => { if (calls++) throw Object.assign(new Error('private path provider token'), { code: failure }); return page(p) }, clear: () => {} } })
    try {
      await admitted(m); m.root.querySelector('.load').click(); await finished(m); m.root.querySelector('.refresh').click(); await finished(m)
      assert.ok(m.root.textContent.includes(nativeObservationsCopy.en.errors[failure])); assert.equal(m.root.textContent.includes('private path'), false)
      assert.equal(!!m.root.querySelector('.records'), !['unauthorized', 'origin_denied', 'tombstoned'].includes(failure))
      if (['conflict', 'busy', 'observations_unavailable'].includes(failure)) assert.ok(m.root.textContent.includes(nativeObservationsCopy.en.stale))
      assert.equal(calls, 2)
    } finally { m.cleanup() }
  }
})
test('rehashed hostile malformed record and invalid selected receipt never render evidence', async () => {
  const m = mount({ methods: { page: async p => { const v = page(p); v.records[0].raw_json = '{"x":1,"x":2}'; v.records[0].record_sha256 = bytesHash(v.records[0].raw_json); return v } } })
  try { await admitted(m); m.root.querySelector('.load').click(); await finished(m); assert.equal(m.root.querySelector('.records'), null); assert.ok(m.root.textContent.includes(nativeObservationsCopy.en.errors.invalid_reply)) } finally { m.cleanup() }
  const f = fixture(); f.selection.launch.receipt = null; let calls = 0
  const invalid = mount({ selection: f.selection, methods: { page: () => { calls++ } } })
  try { await waitFor(() => invalid.root.textContent.includes(nativeObservationsCopy.en.errors.invalid_reply)); assert.equal(invalid.root.querySelector('.load'), null); assert.equal(calls, 0) } finally { invalid.cleanup() }
})
test('responsive and keyboard fixture checks finite controls, literal wrapping and unobscured focus hooks', () => {
  const css = descriptor.styles[0].content
  for (const token of ['min-height:44px', 'min-width:0', 'overflow-wrap:anywhere', 'white-space:pre-wrap', ':focus-visible', 'scroll-margin:24px', '@media(max-width:600px)']) assert.ok(css.includes(token))
  assert.equal(descriptor.template.content.includes('v-html'), false)
  // Real 320/768/1440 layout and keyboard activation remain Main browser gates.
})

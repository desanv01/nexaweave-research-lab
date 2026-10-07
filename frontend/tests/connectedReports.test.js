// Independent wire producer; Main must select this .js suite explicitly.
import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash, webcrypto } from 'node:crypto'
import { createWorkbenchClient } from '../src/api/workbench.js'
import { connectedReportPayload, createConnectedReportsChannel, parseConnectedReportJson, reportIdentity, validateConnectedReportResult, validateConnectedReportRead, validateConnectedReportDownload } from '../src/api/connectedReports.js'
globalThis.crypto ||= webcrypto
const uid = n => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`, hash = 'a'.repeat(64), clone = v => JSON.parse(JSON.stringify(v))
const code = expected => e => e.code === expected
function canonical(v) {
  if (typeof v === 'bigint') return String(v)
  if (Array.isArray(v)) return '[' + v.map(canonical).join(',') + ']'
  if (v && typeof v === 'object') return '{' + Object.keys(v).sort().map(k => canonical(k) + ':' + canonical(v[k])).join(',') + '}'
  return JSON.stringify(v).replace(/[\u007f-\uffff]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'))
}
const digest = v => createHash('sha256').update(canonical(v), 'ascii').digest('hex'), bytesHash = v => createHash('sha256').update(v).digest('hex')
const pick = (v, keys) => Object.fromEntries(keys.split(' ').map(k => [k, v[k]]))
function selection() {
  const files = ['state.json', 'simulation_config.json', 'source_grounding.json', 'twitter_profiles.csv', 'reddit_profiles.json'].map(name => ({ name, sha256: hash, size: 123 }))
  const preparation = { schema_version: 1, display_graph_id: 'graph_1', scope: { schema_version: 1, workspace_id: uid(1), project_id: uid(2), graph_id: uid(3), run_id: null, branch_id: null, layer: 'source' }, project_revision: 1, operation_id: uid(9), source: { source_revision: uid(5), source_name: '中😀 <script>source</script>', source_sha256: hash }, options: { types: null, max_agents: 10, seed: 0, platforms: ['twitter', 'reddit'], max_rounds: 24, simulation_requirement: 'Study 中😀' }, actors: [{ source_entity_uuid: uid(7), name: '中😀', labels: ['Person'] }], projection_sha256: hash, plan_sha256: null, state: 'ready', progress: { stage: 'ready', completed: 100, total: 100 }, error_code: null, authorization: { model_calls_enabled: true, ceiling_microusd: '12345' }, receipt: { simulation_id: 'sim_' + uid(9).replaceAll('-', ''), artifact_sha256: digest({ schema_version: 1, files }), files }, graph_snapshot_atomic: false, model_calls_started: true, simulation_executed: false }
  preparation.plan_sha256 = digest(pick(preparation, 'schema_version display_graph_id scope project_revision operation_id source options actors projection_sha256'))
  const launch = { schema_version: 1, display_graph_id: 'graph_1', scope: clone(preparation.scope), preparation: { operation_id: preparation.operation_id, plan_sha256: preparation.plan_sha256, simulation_id: preparation.receipt.simulation_id, artifact_sha256: preparation.receipt.artifact_sha256 }, request: { schema_version: 1, principal: 'local-research', project_id: uid(2), project_revision: 1, simulation_id: preparation.receipt.simulation_id, run_id: uid(10), artifact_sha256: preparation.receipt.artifact_sha256, runtime_sha256: hash, platforms: ['twitter', 'reddit'], seed: 0, max_rounds: 24 }, limits: { max_calls: 20, max_input_bytes: 2097152, max_output_tokens: 4096, max_run_seconds: 600 }, ceiling_microusd: '12345', model_label: 'scripted', launch_sha256: null, state: 'completed', error_code: null, authorization: { model_calls_enabled: false }, workflow: null, receipt: null, cancel_requested: false, cleanup: { known: true, pending: false, owner_thread_alive: false } }
  launch.launch_sha256 = digest(pick(launch, 'schema_version display_graph_id scope preparation request limits ceiling_microusd model_label'))
  launch.receipt = { run_id: uid(10), attempt_id: uid(12), instance_id: uid(13), request_fingerprint: digest(launch.request), outcome: 'completed', evidence_sha256: hash }
  return { launch, preparation }
}
const planning = () => ({ schema_version: 1, report_id: uid(20), launch_id: uid(10), launch_sha256: selection().launch.launch_sha256, requirement: 'Compare 中😀 evidence', output_language: 'en', native_windows: null })
function report(p = planning()) {
  const s = selection(), launch = s.launch
  const value = { schema_version: 1, report_id: p.report_id, plan_sha256: null, binding: { display_graph_id: 'graph_1', principal: launch.request.principal, scope: clone(launch.scope), project_revision: 1, source: clone(s.preparation.source), preparation: clone(launch.preparation), native: { run_id: launch.request.run_id, launch_sha256: launch.launch_sha256, request_fingerprint: launch.receipt.request_fingerprint, evidence_sha256: launch.receipt.evidence_sha256, platforms: ['twitter', 'reddit'] }, coverage: ['twitter', 'reddit'].map(platform => { const w = p.native_windows?.find(v => v.platform === platform), windows = p.native_windows === null ? [{ offset: 0, count: 2 }] : w ? [{ offset: w.offset, count: w.count }] : []; const selected_records = windows.reduce((n, v) => n + v.count, 0); return { platform, total_records: 2, selected_records, complete: selected_records === 2, windows } }), reference_keys: ['source:' + uid(30)] }, options: pick(p, 'requirement output_language native_windows'), context_sha256: hash, source_projection_sha256: hash, model_label: 'scripted 中😀', limits: { max_calls: 64, max_input_bytes: 262144, max_output_tokens: 4096, max_run_seconds: 600 }, ceiling_microusd: 12345, authorization: { model_calls_enabled: true, budget_configured: true }, state: 'planned', progress: { stage: 'planned', percent: 0, completed_sections: 0, total_sections: 0 }, workflow: null, receipt: null, receipt_sha256: null, manifest: null, cleanup: { known: false, pending: null, owner_thread_alive: null }, cancel_requested: false, error_code: null }
  for (const c of value.binding.coverage) for (const w of c.windows) for (let i = w.offset; i < w.offset + w.count; i++) value.binding.reference_keys.push(`native:${c.platform}:${i}:${hash}`)
  return rehash(value)
}
function rehash(v) { v.plan_sha256 = digest(pick(v, 'schema_version report_id binding options context_sha256 source_projection_sha256 model_label limits ceiling_microusd')); return v }
const prose = '# Report 中😀\n\nSource [[source:' + uid(30) + ']]\n\nRecorded [[native:twitter:0:' + hash + ']]\n\n<img src=x onerror=alert(1)> <script>private</script>'
function completed(known = report(), content = prose) {
  const v = clone(known), contents = { 'meta.json': '{}', 'outline.json': '{}', 'full_report.md': content, 'retrieval_evidence.json': '{}', 'native_evidence.json': '{}', 'section_01.md': content }
  v.state = 'completed'; v.progress = { stage: 'completed', percent: 100, completed_sections: 1, total_sections: 1 }; v.cleanup = { known: true, pending: false, owner_thread_alive: false }
  v.manifest = { schema_version: 1, files: Object.entries(contents).map(([name, text]) => ({ name, size: Buffer.byteLength(text), sha256: bytesHash(text) })) }
  v.receipt = { schema_version: 1, report_id: v.report_id, plan_sha256: v.plan_sha256, context_sha256: v.context_sha256, manifest_sha256: digest(v.manifest), output_language: v.options.output_language, reference_integrity: 'validated', semantic_support_status: 'not_reviewed' }; v.receipt_sha256 = digest(v.receipt)
  return v
}
const ctx = known => ({ graph: 'graph_1', payload: reportIdentity(known), known })
const envelope = v => new Response(JSON.stringify({ success: true, data: v }), { headers: { 'Content-Type': 'application/json' } })
const credentials = { origin: 'http://127.0.0.1:5001', graph: 'graph_1', token: 'private-token' }
function channel(fetchImpl, options = {}) { return createConnectedReportsChannel({ fetchImpl, deadlineMs: 1000, connection: () => credentials, denied: () => {}, ...options }) }

test('report payloads contain exact bounded identifiers, options and download kind', () => {
  assert.deepEqual(JSON.parse(connectedReportPayload(planning(), 'plan')), planning())
  for (const mutate of [v => v.extra = true, v => v.principal = 'grant', v => v.report_id = 'UPPER', v => v.schema_version = true, v => v.requirement = ' ', v => v.requirement = '\ud800', v => v.requirement = '😀'.repeat(4001), v => v.output_language = 'fr', v => v.native_windows = [], v => v.native_windows = [{ platform: 'twitter', offset: false, count: 1 }], v => v.native_windows = [{ platform: 'twitter', offset: 0, count: 1 }, { platform: 'twitter', offset: 0, count: 1 }]]) { const v = planning(); mutate(v); assert.throws(() => connectedReportPayload(v, 'plan'), code('invalid_request')) }
  assert.throws(() => connectedReportPayload({ ...reportIdentity(report()), kind: 'path', section_index: null }, 'download'), code('invalid_request'))
  assert.throws(() => connectedReportPayload({ ...reportIdentity(report()), kind: 'report', section_index: 1 }, 'download'), code('invalid_request'))
  assert.equal(JSON.parse(connectedReportPayload({ ...reportIdentity(report()), kind: 'section', section_index: 8 }, 'download')).kind, 'section')
})
test('strict raw JSON refuses duplicate escaped keys, depth, numeric rounding and noninteger lexemes', () => {
  for (const raw of ['{"a":1,"\\u0061":2}', '{"n":1.0}', '{"n":1e0}', '{"n":-0}', '{"n":9223372036854775808}', '{"n":1e999}', '{"s":"\\ud800"}', '['.repeat(18) + '0' + ']'.repeat(18)]) assert.throws(() => parseConnectedReportJson(raw))
  assert.equal(parseConnectedReportJson('{"n":1,"s":"中😀"}').s, '中😀')
})
test('signed-64 ceiling JSON lexemes remain lossless through canonical identity and snapshots', async () => {
  const v = report(); v.ceiling_microusd = 9223372036854775807n; rehash(v)
  const decoded = parseConnectedReportJson(canonical(v)); assert.equal(decoded.ceiling_microusd, 9223372036854775807n)
  const admitted = await validateConnectedReportResult(decoded, { graph: 'graph_1', payload: planning(), selection: selection(), planning: true }); assert.equal(admitted.plan_sha256, v.plan_sha256)
  const bad = parseConnectedReportJson(canonical({ ...report(), limits: { ...report().limits, max_calls: 9007199254740993n } })); await assert.rejects(validateConnectedReportResult(bad, { graph: 'graph_1', payload: planning(), selection: selection(), planning: true }), code('invalid_reply'))
})
test('independent canonical plan and native provenance bind complete and declared partial coverage', async () => {
  for (const native_windows of [null, [{ platform: 'reddit', offset: 1, count: 1 }]]) {
    const payload = { ...planning(), native_windows }, value = report(payload), admitted = await validateConnectedReportResult(value, { graph: 'graph_1', payload, selection: selection(), planning: true })
    assert.equal(admitted.plan_sha256, value.plan_sha256); value.model_label = 'mutated'; assert.notEqual(admitted.model_label, value.model_label)
  }
  // Current report projection can differ from the original preparation snapshot
  // without changing the pinned source/project/preparation/native authority.
  const current = report(), selected = selection(), originalPlanHash = current.plan_sha256
  current.source_projection_sha256 = 'b'.repeat(64); rehash(current)
  const admittedCurrent = await validateConnectedReportResult(current, { graph: 'graph_1', payload: planning(), selection: selected, planning: true })
  assert.notEqual(admittedCurrent.source_projection_sha256, selected.preparation.projection_sha256)
  assert.notEqual(admittedCurrent.plan_sha256, originalPlanHash)
  assert.deepEqual(admittedCurrent.binding, report().binding)
  assert.equal((await validateConnectedReportResult(clone(admittedCurrent), ctx(admittedCurrent))).source_projection_sha256, 'b'.repeat(64))
  await assert.rejects(validateConnectedReportResult({ ...clone(current), plan_sha256: originalPlanHash }, { graph: 'graph_1', payload: planning(), selection: selected, planning: true }), code('invalid_reply'))
  const changedCurrent = clone(admittedCurrent); changedCurrent.source_projection_sha256 = 'c'.repeat(64); rehash(changedCurrent)
  await assert.rejects(validateConnectedReportResult(changedCurrent, ctx(admittedCurrent)), code('invalid_reply'))
  for (const mutate of [v => v.binding.native.evidence_sha256 = 'b'.repeat(64), v => v.binding.principal = 'other', v => v.binding.source.source_revision = uid(99), v => v.source_projection_sha256 = 'invalid-digest', v => v.binding.preparation.artifact_sha256 = 'b'.repeat(64), v => v.binding.coverage.reverse(), v => v.binding.coverage[0].selected_records--, v => v.binding.reference_keys.push(v.binding.reference_keys[0]), v => v.binding.reference_keys.push('native:twitter:9:' + hash)]) {
    const v = report(); mutate(v); rehash(v); await assert.rejects(validateConnectedReportResult(v, { graph: 'graph_1', payload: planning(), selection: selection(), planning: true }), code('invalid_reply'))
  }
})
test('same report status never replaces immutable options, context or completed output', async () => {
  const known = report()
  for (const mutate of [v => v.options.requirement = 'changed', v => v.context_sha256 = 'b'.repeat(64), v => v.source_projection_sha256 = 'b'.repeat(64), v => v.binding.source.source_name = 'changed', v => v.limits.max_calls = 65, v => v.ceiling_microusd = 1]) { const v = clone(known); mutate(v); rehash(v); await assert.rejects(validateConnectedReportResult(v, ctx(known)), code('invalid_reply')) }
  const done = completed(); assert.equal((await validateConnectedReportResult(done, ctx(known))).receipt.semantic_support_status, 'not_reviewed')
  for (const mutate of [v => v.cleanup.known = false, v => v.cleanup.pending = true, v => v.receipt.reference_integrity = 'semantic', v => v.manifest.files[0].name = '../secret', v => v.manifest.files[2].size++, v => v.receipt_sha256 = hash, v => v.progress.completed_sections = 0]) { const v = clone(done); mutate(v); await assert.rejects(validateConnectedReportResult(v, ctx(done)), code('invalid_reply')) }
})
test('caller mutation after validation starts cannot change the admitted report snapshot', async () => {
  const value = report(), payload = planning(), selected = selection()
  const pending = validateConnectedReportResult(value, { graph: 'graph_1', payload, selection: selected, planning: true })
  value.options.requirement = 'late'; selected.preparation.source.source_name = 'late'; payload.requirement = 'late'
  const admitted = await pending; assert.equal(admitted.options.requirement, 'Compare 中😀 evidence'); assert.notEqual(admitted.binding.source.source_name, 'late')
})
test('read content and all download bytes require exact manifest/receipt/base64 proof', async () => {
  const known = completed(), payload = { ...reportIdentity(known), kind: 'report', section_index: null }, file = known.manifest.files[2]
  const read = { schema_version: 1, report: known, content: prose }
  assert.equal((await validateConnectedReportRead(read, ctx(known))).content, prose)
  await assert.rejects(validateConnectedReportRead({ ...read, content: prose + 'x' }, ctx(known)), code('invalid_reply'))
  for (const content of ['No references', '[[source:' + uid(30) + ']]', '[[native:twitter:9:' + hash + ']]', prose + '[[broken', prose + '[[[source:' + uid(30) + ']]]']) { const v = completed(report(), content); await assert.rejects(validateConnectedReportRead({ schema_version: 1, report: v, content }, ctx(v)), code('invalid_reply')) }
  const download = { schema_version: 1, ...reportIdentity(known), receipt_sha256: known.receipt_sha256, artifact: { ...file, mime: 'text/markdown', content_base64: Buffer.from(prose).toString('base64') } }
  assert.equal((await validateConnectedReportDownload(download, { graph: 'graph_1', payload, known })).artifact.name, 'full_report.md')
  for (const mutate of [v => v.report_id = uid(99), v => v.receipt_sha256 = hash, v => v.artifact.name = '../full_report.md', v => v.artifact.mime = 'text/html', v => v.artifact.content_base64 += '\n', v => v.artifact.content_base64 = Buffer.from(prose + 'x').toString('base64'), v => v.artifact.size++, v => v.artifact.sha256 = hash]) { const v = clone(download); mutate(v); await assert.rejects(validateConnectedReportDownload(v, { graph: 'graph_1', payload, known }), code('invalid_reply')) }
})
test('lost Start remains fenced after clear, reconnect and supplied-known recovery', async () => {
  const requests = []; let connected = true
  const c = channel(async (url, request) => { requests.push({ url, request }); if (url.includes('/start/')) throw new Error('private raw failure'); return envelope(report()) }, { connection: () => connected ? credentials : null })
  const known = await c.plan(planning(), selection())
  await assert.rejects(c.start(reportIdentity(known), known), e => e.code === 'transport_failure' && e.replyConfirmed !== true && e.requestSent === true); c.clear(); connected = false
  await assert.rejects(c.status(reportIdentity(known)), code('disconnected')); connected = true
  await c.status(reportIdentity(known))
  await assert.rejects(c.start(reportIdentity(known), known), code('conflict'))
  assert.equal(requests.filter(v => v.url.includes('/start/')).length, 1)
  const request = requests[0].request; assert.equal(request.credentials, 'omit'); assert.equal(request.redirect, 'error'); assert.equal(request.referrerPolicy, 'no-referrer'); assert.equal(request.headers.Authorization, 'Bearer private-token'); assert.equal(new URL(requests[0].url).search, '')
})
test('validated Start denials are confirmed while a local repeated Start sends nothing', async () => {
  for (const [denial, status] of [['budget_denied', 409], ['conflict', 409]]) {
    const requests = []
    const c = channel(async url => {
      requests.push(url)
      return url.includes('/start/')
        ? new Response(JSON.stringify({ success: false, error: { code: denial } }), { status, headers: { 'Content-Type': 'application/json' } })
        : envelope(report())
    })
    const known = await c.plan(planning(), selection())
    await assert.rejects(c.start(reportIdentity(known), known), e => e.code === denial && e.replyConfirmed === true && e.requestSent === true)
    await assert.rejects(c.start(reportIdentity(known), known), e => e.code === 'conflict' && e.replyConfirmed !== true && e.requestSent === false)
    assert.equal(requests.filter(url => url.includes('/start/')).length, 1)
  }
})
test('ordinary reviewed-plan Refresh permits its first Start; manual recovery never enables a Start', async () => {
  const c = channel(async url => envelope(url.includes('/start/') ? { ...report(), state: 'queued', progress: { stage: 'queued', percent: 0, completed_sections: 0, total_sections: 0 } } : report()))
  const p = await c.plan(planning(), selection()); await c.status(reportIdentity(p), p); await c.start(reportIdentity(p), p)
  const recovered = channel(async () => envelope(report())); await recovered.status(reportIdentity(report())); await assert.rejects(recovered.start(reportIdentity(report()), report()), code('conflict'))
})
test('cancel dispatch is fenced even when its reply is lost; deadline remains bounded', async () => {
  let calls = 0
  const c = channel(async url => { calls++; if (url.includes('/cancel/')) throw new Error('lost'); return envelope(report()) })
  const known = await c.plan(planning(), selection()); await assert.rejects(c.cancelReport(reportIdentity(known), known), code('transport_failure')); c.clear()
  await assert.rejects(c.start(reportIdentity(known), known), code('conflict')); assert.equal(calls, 2)
  await assert.rejects(channel(() => new Promise(() => {}), { deadlineMs: 5 }).status(reportIdentity(report())), code('deadline'))
})
test('cancellation aborts a noncooperative read and cancels a late response body', async () => {
  let resolve, entered; const begun = new Promise(r => { entered = r })
  const c = channel(() => { entered(); return new Promise(r => { resolve = r }) })
  const pending = c.status(reportIdentity(report())); await begun; c.clear(); await assert.rejects(pending, code('cancelled'))
  let cancelled = false; resolve({ body: { cancel: async () => { cancelled = true } } }); await new Promise(r => setImmediate(r)); assert.equal(cancelled, true)
})
test('transport rejects malformed, unsafe errors, MIME, redirects, UTF8 and declared size', async () => {
  for (const make of [() => new Response('{"success":false,"error":{"code":"internal_error","cause":"secret"}}', { status: 500, headers: { 'Content-Type': 'application/json' } }), () => new Response('{"success":false,"error":{"code":"busy"}}', { status: 503, headers: { 'Content-Type': 'application/json' } }), () => new Response('html', { headers: { 'Content-Type': 'text/html' } }), () => new Response(new Uint8Array([0xff]), { headers: { 'Content-Type': 'application/json' } }), () => ({ ...envelope(report()), redirected: true, type: 'basic', status: 200, ok: true, headers: new Headers({ 'Content-Type': 'application/json' }), body: envelope(report()).body })]) await assert.rejects(channel(async () => make()).status(reportIdentity(report())), code('invalid_reply'))
  await assert.rejects(channel(async () => new Response('', { headers: { 'Content-Type': 'application/json', 'Content-Length': '4194305' } })).status(reportIdentity(report())), code('result_too_large'))
  const malformed = channel(async url => url.includes('/start/') ? new Response('{"success":false,"error":{"code":"budget_denied","cause":"private"}}', { status: 409, headers: { 'Content-Type': 'application/json' } }) : envelope(report()))
  const known = await malformed.plan(planning(), selection())
  await assert.rejects(malformed.start(reportIdentity(known), known), e => e.code === 'invalid_reply' && e.replyConfirmed !== true && e.requestSent === true)
})
test('workbench integration keeps report credentials private and clears authorization failures', async () => {
  let authorized = true, calls = 0
  const client = createWorkbenchClient({ fetchImpl: async url => { calls++; if (url.includes('/data/')) return envelope({ graph_id: 'graph_1', nodes: [], edges: [], node_count: 0, edge_count: 0 }); return authorized ? envelope(report()) : new Response('', { status: 401 }) } })
  await client.connect(credentials); await client.connectedReportStatus(reportIdentity(report())); authorized = false
  await assert.rejects(client.connectedReportStatus(reportIdentity(report())), code('unauthorized'))
  const before = calls; await assert.rejects(client.connectedReportStatus(reportIdentity(report())), code('disconnected')); assert.equal(calls, before); assert.equal('token' in client, false)
})

// Independent static follow-up wire producer. Main selects and executes this suite.
import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash, webcrypto } from 'node:crypto'
import { connectedFollowupPayload, createConnectedFollowupsChannel, followupIdentity, validateConnectedFollowupResult, validateConnectedFollowupRead, validateConnectedFollowupDownload, validateConnectedFollowupHistory } from '../src/api/connectedFollowups.js'
globalThis.crypto ||= webcrypto
const uid = n => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`, hash = 'a'.repeat(64)
const json = v => JSON.parse(JSON.stringify(v))
function canonical(v) { if (typeof v === 'bigint') return String(v); if (Array.isArray(v)) return '[' + v.map(canonical).join(',') + ']'; if (v && typeof v === 'object') return '{' + Object.keys(v).sort().map(k => canonical(k) + ':' + canonical(v[k])).join(',') + '}'; return JSON.stringify(v).replace(/[\u007f-\uffff]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0')) }
const digest = v => createHash('sha256').update(canonical(v), 'ascii').digest('hex'), bytesHash = v => createHash('sha256').update(v).digest('hex')
const pick = (v, keys) => Object.fromEntries(keys.split(' ').map(k => [k, v[k]]))
const error = code => e => e.code === code
const question = 'Why 中😀?'
const answer = 'Source [[source:' + uid(30) + ']] and native [[native:twitter:0:' + hash + ']]. <script>inert</script>'
function binding() {
  const scope = { schema_version: 1, workspace_id: uid(1), project_id: uid(2), graph_id: uid(3), run_id: null, branch_id: null, layer: 'source' }
  return { display_graph_id: 'graph_1', principal: 'local-research', scope, report: { report_id: uid(20), plan_sha256: hash, receipt_sha256: hash, manifest_sha256: hash, full_report_sha256: hash }, native_binding: { display_graph_id: 'graph_1', principal: 'local-research', scope, project_revision: 1, source: { source_revision: uid(5), source_name: 'Source 中😀', source_sha256: hash }, preparation: { operation_id: uid(9), plan_sha256: hash, simulation_id: 'sim_' + uid(9).replaceAll('-', ''), artifact_sha256: hash }, native: { run_id: uid(10), launch_sha256: hash, request_fingerprint: hash, evidence_sha256: hash, platforms: ['twitter'] }, coverage: [{ platform: 'twitter', total_records: 1, selected_records: 1, complete: true, windows: [{ offset: 0, count: 1 }] }], reference_keys: ['source:' + uid(30), 'native:twitter:0:' + hash] } }
}
function empty(b) { return digest({ schema_version: 1, report_id: b.report.report_id, report_plan_sha256: b.report.plan_sha256, turns: [] }) }
function turn(parent = null) {
  const b = binding()
  if (parent) {
    b.native_binding = json(parent.binding); b.scope = json(parent.binding.scope)
    b.principal = parent.binding.principal; b.display_graph_id = parent.binding.display_graph_id
    b.report = { report_id: parent.report_id, plan_sha256: parent.plan_sha256,
      receipt_sha256: parent.receipt_sha256, manifest_sha256: parent.receipt.manifest_sha256,
      full_report_sha256: parent.manifest.files.find(f => f.name === 'full_report.md').sha256 }
  }
  const v = { schema_version: 1, turn_id: uid(40), plan_sha256: null, binding: b, options: { question, output_language: 'en', expected_history_sha256: null }, history: { head_sha256: empty(b), total_completed: 0, window_start: 1, pairs: [] }, report_context: { file_sha256: hash, prefix_sha256: hash, prefix_characters: 100, total_characters: 100, truncated: false }, context_sha256: hash, source_projection_sha256: hash, model_label: 'scripted', limits: { max_calls: 8, max_input_bytes: 1048576, max_output_tokens: 4096, max_run_seconds: 600 }, ceiling_microusd: 12345, authorization: { model_calls_enabled: true, budget_configured: true }, state: 'planned', progress: { stage: 'planned', percent: 0, completed_sections: 0, total_sections: 0 }, workflow: null, receipt: null, receipt_sha256: null, manifest: null, published_history_head_sha256: null, cleanup: { known: false, pending: null, owner_thread_alive: null }, cancel_requested: false, error_code: null }
  if (parent) v.report_context = { file_sha256: b.report.full_report_sha256,
    prefix_sha256: bytesHash(answer), prefix_characters: [...answer].length,
    total_characters: [...answer].length, truncated: false }
  return rehashTurn(v)
}
function rehashTurn(v) { v.plan_sha256 = digest(pick(v, 'schema_version turn_id binding options history report_context context_sha256 source_projection_sha256 model_label limits ceiling_microusd')); return v }
function parentReport(revisions = [uid(5), uid(50), uid(51)]) {
  const nativeBinding = json(binding().native_binding)
  nativeBinding.source_set = { schema_version: 1, source_revisions: [...revisions] }
  nativeBinding.reference_keys.splice(1, 0, 'source:' + uid(31), 'source:' + uid(32))
  const value = { schema_version: 1, report_id: uid(20), plan_sha256: null, binding: nativeBinding,
    options: { requirement: 'Compare source documents', output_language: 'en', native_windows: null },
    context_sha256: hash, source_projection_sha256: hash, model_label: 'scripted',
    limits: { max_calls: 64, max_input_bytes: 262144, max_output_tokens: 4096, max_run_seconds: 600 },
    ceiling_microusd: 12345, authorization: { model_calls_enabled: false, budget_configured: true },
    state: 'completed', progress: { stage: 'completed', percent: 100, completed_sections: 1, total_sections: 1 },
    workflow: null, receipt: null, receipt_sha256: null, manifest: null,
    cleanup: { known: true, pending: false, owner_thread_alive: false }, cancel_requested: false, error_code: null }
  value.plan_sha256 = digest(pick(value, 'schema_version report_id binding options context_sha256 source_projection_sha256 model_label limits ceiling_microusd'))
  value.manifest = { schema_version: 1, files: ['meta.json', 'outline.json', 'full_report.md', 'retrieval_evidence.json', 'native_evidence.json', 'section_01.md'].map(name => {
    const content = name.endsWith('.md') ? answer : '{}'
    return { name, size: Buffer.byteLength(content), sha256: bytesHash(content) }
  }) }
  value.receipt = { schema_version: 1, report_id: value.report_id, plan_sha256: value.plan_sha256,
    context_sha256: value.context_sha256, manifest_sha256: digest(value.manifest), output_language: 'en',
    reference_integrity: 'validated', semantic_support_status: 'not_reviewed' }
  value.receipt_sha256 = digest(value.receipt)
  return value
}
function planPayload(parent) {
  return { schema_version: 1, turn_id: uid(40), report_id: parent.report_id,
    report_plan_sha256: parent.plan_sha256, question, output_language: 'en', expected_history_sha256: null }
}
function finished(parent = null) {
  const v = turn(parent), b = v.binding.report
  const conversation = { schema_version: 1, report_id: b.report_id, report_plan_sha256: b.plan_sha256, total_completed: 1, pairs: [{ turn_id: v.turn_id, plan_sha256: v.plan_sha256, ordinal: 1, question, answer, answer_sha256: bytesHash(answer), predecessor_head_sha256: v.history.head_sha256, receipt_sha256: null, published_head_sha256: null }] }
  const files = { 'turn.json': '{}', 'answer.md': answer, 'conversation.json': JSON.stringify(conversation), 'retrieval_evidence.json': '{}', 'native_evidence.json': '{}', 'tool_trace.json': '{}' }
  v.manifest = { schema_version: 1, files: Object.entries(files).map(([name, content]) => ({ name, size: Buffer.byteLength(content), sha256: bytesHash(content) })) }
  v.state = 'completed'; v.progress = { stage: 'completed', percent: 100, completed_sections: 1, total_sections: 1 }; v.cleanup = { known: true, pending: false, owner_thread_alive: false }
  v.receipt = { schema_version: 1, turn_id: v.turn_id, plan_sha256: v.plan_sha256, parent_report_id: b.report_id, parent_report_plan_sha256: b.plan_sha256, parent_report_receipt_sha256: b.receipt_sha256, context_sha256: v.context_sha256, history_head_sha256: v.history.head_sha256, ordinal: 1, manifest_sha256: digest(v.manifest), output_language: 'en', reference_integrity: 'validated', semantic_support_status: 'not_reviewed' }
  v.receipt_sha256 = digest(v.receipt)
  v.published_history_head_sha256 = digest({ schema_version: 1, report_id: b.report_id, report_plan_sha256: b.plan_sha256, predecessor_head_sha256: v.history.head_sha256, ordinal: 1, turn_id: v.turn_id, plan_sha256: v.plan_sha256, question_sha256: bytesHash(question), answer_sha256: bytesHash(answer), receipt_sha256: v.receipt_sha256 })
  return { turn: v, files }
}
const context = v => ({ graph: 'graph_1', payload: followupIdentity(v), known: v })
const envelope = data => new Response(JSON.stringify({ success: true, data }), { headers: { 'Content-Type': 'application/json' } })
const credentials = { origin: 'http://127.0.0.1:5001', graph: 'graph_1', token: 'private-token' }
const channel = fetchImpl => createConnectedFollowupsChannel({ fetchImpl, deadlineMs: 1000, connection: () => credentials, denied: () => {} })

test('strict follow-up request bodies contain no history, credentials or provider grants', () => {
  const p = { schema_version: 1, turn_id: uid(40), report_id: uid(20), report_plan_sha256: hash, question, output_language: 'en', expected_history_sha256: null }
  assert.deepEqual(JSON.parse(connectedFollowupPayload(p, 'plan')), p)
  for (const mutate of [v => v.chat_history = [], v => v.token = 'secret', v => v.question = ' ', v => v.question = '\ud800', v => v.output_language = 'fr', v => v.expected_history_sha256 = 'bad']) { const bad = { ...p }; mutate(bad); assert.throws(() => connectedFollowupPayload(bad, 'plan'), error('invalid_request')) }
  assert.throws(() => connectedFollowupPayload({ ...followupIdentity(turn()), kind: 'path' }, 'download'), error('invalid_request'))
  assert.equal(JSON.parse(connectedFollowupPayload({ schema_version: 1, report_id: uid(20), report_plan_sha256: hash, before_ordinal: 1000 }, 'history')).before_ordinal, 1000)
})
test('V2-created report parent retains its selected set through protocol1 follow-up plan/status/read/download/history', async () => {
  const parent = parentReport(), planned = turn(parent), payload = planPayload(parent)
  assert.deepEqual(JSON.parse(connectedFollowupPayload(payload, 'plan')), payload)
  const admitted = await validateConnectedFollowupResult(planned, { graph: 'graph_1', payload, parentReport: parent, planning: true })
  assert.deepEqual(admitted.binding.native_binding.source_set, parent.binding.source_set)
  assert.notEqual(admitted.binding.native_binding.source_set, parent.binding.source_set)
  assert.deepEqual((await validateConnectedFollowupResult(json(admitted), context(admitted))).binding.native_binding.source_set, parent.binding.source_set)
  const { turn: done, files } = finished(parent)
  const read = await validateConnectedFollowupRead({ schema_version: 1, turn: done, content: answer }, context(planned))
  assert.equal(read.content, answer)
  assert.deepEqual(read.turn.binding.native_binding.source_set, parent.binding.source_set)
  const download = { schema_version: 1, ...followupIdentity(done), receipt_sha256: done.receipt_sha256,
    artifact: { ...done.manifest.files[1], mime: 'text/markdown', content_base64: Buffer.from(files['answer.md']).toString('base64') } }
  assert.deepEqual(await validateConnectedFollowupDownload(download, { graph: 'graph_1', payload: { ...followupIdentity(done), kind: 'answer' }, known: done }), download)
  const historyPayload = { schema_version: 1, report_id: parent.report_id, report_plan_sha256: parent.plan_sha256, before_ordinal: null }
  const page = { schema_version: 1, report_id: parent.report_id, report_plan_sha256: parent.plan_sha256,
    binding: json(planned.binding), head_sha256: empty(planned.binding), total_completed: 0, before_ordinal: null, pairs: [] }
  assert.deepEqual((await validateConnectedFollowupHistory(page, { graph: 'graph_1', payload: historyPayload, parentReport: parent })).binding.native_binding.source_set, parent.binding.source_set)
  for (const method of ['plan', 'start', 'status', 'cancel', 'read', 'download', 'history']) {
    const request = method === 'plan' ? payload : method === 'history' ? historyPayload : { ...followupIdentity(planned), ...(method === 'download' ? { kind: 'answer' } : {}) }
    assert.throws(() => connectedFollowupPayload({ ...request, schema_version: 2 }, method), error('invalid_request'))
    assert.throws(() => connectedFollowupPayload({ ...request, source_set: parent.binding.source_set }, method), error('invalid_request'))
  }
})
test('extended native parent binding requires exact strict set shape, canonical IDs, uniqueness and anchor', async () => {
  const planned = turn(parentReport())
  for (const mutate of [v => v.binding.native_binding.source_set = null,
    v => v.binding.native_binding.source_set.extra = true,
    v => delete v.binding.native_binding.source_set.source_revisions,
    ...[0, 2, true, '1', null].map(version => v => v.binding.native_binding.source_set.schema_version = version),
    ...[null, [], {}, uid(5), [true], ['BAD'], ['ABCDEFAB-0000-0000-0000-000000000000'],
      [uid(5), uid(5)], [uid(99), uid(50)], Array.from({ length: 101 }, (_, i) => uid(100 + i))]
      .map(revisions => v => v.binding.native_binding.source_set.source_revisions = revisions)]) {
    const changed = json(planned); mutate(changed); rehashTurn(changed)
    await assert.rejects(validateConnectedFollowupResult(changed, { graph: 'graph_1', payload: followupIdentity(changed) }), error('invalid_reply'))
  }
  for (const revisions of [[uid(5)], [uid(5), ...Array.from({ length: 99 }, (_, i) => uid(100 + i))]]) {
    const parent = parentReport(revisions), planned = turn(parent)
    assert.deepEqual((await validateConnectedFollowupResult(planned, { graph: 'graph_1', payload: planPayload(parent), parentReport: parent, planning: true })).binding.native_binding.source_set.source_revisions, revisions)
  }
})
test('rehashed downgraded, reordered or substituted source sets cannot replace the pinned parent or known turn', async () => {
  const parent = parentReport(), planned = turn(parent)
  assert.equal((await validateConnectedFollowupResult(planned, { graph: 'graph_1', payload: planPayload(parent), parentReport: parent, planning: true })).plan_sha256, planned.plan_sha256)
  for (const mutate of [v => delete v.binding.native_binding.source_set,
    v => v.binding.native_binding.source_set.source_revisions.pop(),
    v => v.binding.native_binding.source_set.source_revisions.splice(1, 2, uid(51), uid(50)),
    v => v.binding.native_binding.source_set.source_revisions[1] = uid(99)]) {
    const changed = json(planned); mutate(changed); rehashTurn(changed)
    assert.notEqual(changed.plan_sha256, planned.plan_sha256)
    // It is structurally valid on its own, including its new canonical digest.
    assert.equal((await validateConnectedFollowupResult(changed, { graph: 'graph_1', payload: followupIdentity(changed) })).plan_sha256, changed.plan_sha256)
    await assert.rejects(validateConnectedFollowupResult(changed, { graph: 'graph_1', payload: planPayload(parent), parentReport: parent, planning: true }), error('invalid_reply'))
    await assert.rejects(validateConnectedFollowupResult(changed, { graph: 'graph_1', payload: followupIdentity(changed), known: planned }), error('invalid_reply'))
    const page = { schema_version: 1, report_id: parent.report_id, report_plan_sha256: parent.plan_sha256,
      binding: changed.binding, head_sha256: empty(changed.binding), total_completed: 0, before_ordinal: null, pairs: [] }
    await assert.rejects(validateConnectedFollowupHistory(page, { graph: 'graph_1', payload: { schema_version: 1, report_id: parent.report_id, report_plan_sha256: parent.plan_sha256, before_ordinal: null }, parentReport: parent }), error('invalid_reply'))
  }
})
test('immutable plan, signed64 canonical ceiling, completed receipt and external chain proof', async () => {
  const planned = turn(); assert.equal((await validateConnectedFollowupResult(planned, context(planned))).plan_sha256, planned.plan_sha256)
  const wide = turn(); wide.ceiling_microusd = 9223372036854775807n; wide.plan_sha256 = digest(pick(wide, 'schema_version turn_id binding options history report_context context_sha256 source_projection_sha256 model_label limits ceiling_microusd'))
  assert.equal((await validateConnectedFollowupResult(wide, context(wide))).ceiling_microusd, 9223372036854775807n)
  const done = finished().turn; assert.equal((await validateConnectedFollowupResult(done, context(planned))).receipt.semantic_support_status, 'not_reviewed')
  for (const mutate of [v => v.options.question = 'changed', v => v.history.head_sha256 = hash, v => v.receipt.manifest_sha256 = hash, v => v.published_history_head_sha256 = hash, v => v.manifest.files[1].size++]) { const bad = json(done); mutate(bad); await assert.rejects(validateConnectedFollowupResult(bad, context(planned)), error('invalid_reply')) }
})
test('answer and complete conversation export validate bytes, current-null proof and admitted references', async () => {
  const { turn: done, files } = finished()
  assert.equal((await validateConnectedFollowupRead({ schema_version: 1, turn: done, content: answer }, context(done))).content, answer)
  for (const kind of ['answer', 'conversation']) {
    const name = kind === 'answer' ? 'answer.md' : 'conversation.json', f = done.manifest.files.find(x => x.name === name)
    const value = { schema_version: 1, turn_id: done.turn_id, plan_sha256: done.plan_sha256, receipt_sha256: done.receipt_sha256, artifact: { ...f, mime: kind === 'answer' ? 'text/markdown' : 'application/json', content_base64: Buffer.from(files[name]).toString('base64') } }
    const payload = { ...followupIdentity(done), kind }
    assert.equal((await validateConnectedFollowupDownload(value, { graph: 'graph_1', payload, known: done })).artifact.name, name)
    const bad = json(value); bad.artifact.content_base64 += '\n'; await assert.rejects(validateConnectedFollowupDownload(bad, { graph: 'graph_1', payload, known: done }), error('invalid_reply'))
  }
  const forged = json(finished()), data = JSON.parse(forged.files['conversation.json']); data.pairs[0].receipt_sha256 = hash; forged.files['conversation.json'] = JSON.stringify(data)
  forged.turn.manifest.files[2].size = Buffer.byteLength(forged.files['conversation.json']); forged.turn.manifest.files[2].sha256 = bytesHash(forged.files['conversation.json']); forged.turn.receipt.manifest_sha256 = digest(forged.turn.manifest); forged.turn.receipt_sha256 = digest(forged.turn.receipt)
  forged.turn.published_history_head_sha256 = digest({ schema_version: 1, report_id: uid(20), report_plan_sha256: hash, predecessor_head_sha256: forged.turn.history.head_sha256, ordinal: 1, turn_id: forged.turn.turn_id, plan_sha256: forged.turn.plan_sha256, question_sha256: bytesHash(question), answer_sha256: bytesHash(answer), receipt_sha256: forged.turn.receipt_sha256 })
  const f = forged.turn.manifest.files[2], value = { schema_version: 1, turn_id: forged.turn.turn_id, plan_sha256: forged.turn.plan_sha256, receipt_sha256: forged.turn.receipt_sha256, artifact: { ...f, mime: 'application/json', content_base64: Buffer.from(forged.files['conversation.json']).toString('base64') } }
  await assert.rejects(validateConnectedFollowupDownload(value, { graph: 'graph_1', payload: { ...followupIdentity(forged.turn), kind: 'conversation' }, known: forged.turn }), error('invalid_reply'))
})
test('protected latest history and older page use distinct head checks', async () => {
  const b = binding(), p = { schema_version: 1, report_id: b.report.report_id, report_plan_sha256: b.report.plan_sha256, before_ordinal: null }
  const emptyPage = { schema_version: 1, report_id: p.report_id, report_plan_sha256: p.report_plan_sha256, binding: b, head_sha256: empty(b), total_completed: 0, before_ordinal: null, pairs: [] }
  assert.equal((await validateConnectedFollowupHistory(emptyPage, { graph: 'graph_1', payload: p })).total_completed, 0)
  await assert.rejects(validateConnectedFollowupHistory({ ...emptyPage, head_sha256: hash }, { graph: 'graph_1', payload: p }), error('invalid_reply'))
})
test('lost Start is fenced across clearing; confirmed denial carries no lost-reply marker', async () => {
  const planned = turn(), requests = []
  const c = channel(async url => { requests.push(url); if (url.includes('/start/')) throw new Error('lost'); return envelope(planned) })
  await assert.rejects(c.start(followupIdentity(planned), planned), e => e.code === 'transport_failure' && e.requestSent === true && e.replyConfirmed !== true)
  c.clear(); await assert.rejects(c.start(followupIdentity(planned), planned), e => e.code === 'turn_conflict' && e.requestSent === false)
  assert.equal(requests.filter(url => url.includes('/start/')).length, 1)
  const denial = channel(async url => url.includes('/start/') ? new Response('{"success":false,"error":{"code":"history_changed"}}', { status: 409, headers: { 'Content-Type': 'application/json' } }) : envelope(planned))
  await assert.rejects(denial.start(followupIdentity(planned), planned), e => e.code === 'history_changed' && e.replyConfirmed === true)
})

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
function turn() {
  const b = binding()
  const v = { schema_version: 1, turn_id: uid(40), plan_sha256: null, binding: b, options: { question, output_language: 'en', expected_history_sha256: null }, history: { head_sha256: empty(b), total_completed: 0, window_start: 1, pairs: [] }, report_context: { file_sha256: hash, prefix_sha256: hash, prefix_characters: 100, total_characters: 100, truncated: false }, context_sha256: hash, source_projection_sha256: hash, model_label: 'scripted', limits: { max_calls: 8, max_input_bytes: 1048576, max_output_tokens: 4096, max_run_seconds: 600 }, ceiling_microusd: 12345, authorization: { model_calls_enabled: true, budget_configured: true }, state: 'planned', progress: { stage: 'planned', percent: 0, completed_sections: 0, total_sections: 0 }, workflow: null, receipt: null, receipt_sha256: null, manifest: null, published_history_head_sha256: null, cleanup: { known: false, pending: null, owner_thread_alive: null }, cancel_requested: false, error_code: null }
  v.plan_sha256 = digest(pick(v, 'schema_version turn_id binding options history report_context context_sha256 source_projection_sha256 model_label limits ceiling_microusd'))
  return v
}
function finished() {
  const v = turn(), b = v.binding.report
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

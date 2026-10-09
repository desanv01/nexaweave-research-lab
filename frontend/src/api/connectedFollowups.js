// Protected follow-up wire boundary. Browser history is never generation authority.
import { WorkbenchError } from './workbench.js'
import { parseConnectedReportJson, reportIdentity, reportSnapshot, validateConnectedReportResult } from './connectedReports.js'
import { sha256 } from './sourceLibrary.js'

const enc = new TextEncoder()
const fail = (code = 'invalid_reply') => { throw new WorkbenchError(code) }
const uuidPattern = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/
const hashPattern = /^[0-9a-f]{64}$/
const fields = (v, keys) => { if (!v || Object.getPrototypeOf(v) !== Object.prototype || Object.keys(v).sort().join('|') !== keys.split(' ').sort().join('|')) fail() }
const uuid = v => { if (typeof v !== 'string' || !uuidPattern.test(v)) fail() }
const hash = v => { if (typeof v !== 'string' || !hashPattern.test(v)) fail() }
const integer = (v, max, min = 0) => { if (!Number.isSafeInteger(v) || Object.is(v, -0) || v < min || v > max) fail() }
const bool = v => { if (typeof v !== 'boolean') fail() }
function scalar(v, max, min = 0) {
  if (typeof v !== 'string' || v.length > max * 2 || [...v].length > max || [...v].length < min || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(v)) fail()
}
const snap = v => reportSnapshot(v)
function wire(v) {
  if (typeof v === 'bigint') return String(v)
  if (Array.isArray(v)) return '[' + v.map(wire).join(',') + ']'
  if (v && typeof v === 'object') return '{' + Object.keys(v).sort().map(k => JSON.stringify(k) + ':' + wire(v[k])).join(',') + '}'
  return JSON.stringify(v)
}
const ascii = v => wire(v).replace(/[\u007f-\uffff]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'))
const digest = v => sha256(enc.encode(ascii(v)))
const same = (a, b) => ascii(a) === ascii(b)
const immutable = 'schema_version turn_id binding options history report_context context_sha256 source_projection_sha256 model_label limits ceiling_microusd'.split(' ')
const picked = v => Object.fromEntries(immutable.map(k => [k, v[k]]))
export const followupSnapshot = snap
export const followupIdentity = v => ({ schema_version: 1, turn_id: v.turn_id, plan_sha256: v.plan_sha256 })
export function newFollowupId() { if (!globalThis.crypto?.randomUUID) fail('invalid_request'); return globalThis.crypto.randomUUID() }
export const FOLLOWUP_ERROR_STATUS = Object.freeze({ invalid_request: 400, invalid_reply: 502, unauthorized: 401, origin_denied: 403, not_found: 404, conflict: 409, history_changed: 409, followup_active: 409, turn_conflict: 409, tombstoned: 410, busy: 409, result_too_large: 413, followup_unavailable: 503, model_calls_disabled: 409, budget_denied: 409, followup_failed: 409, followup_cancelled: 409, followup_uncertain: 409, timeout: 503, transport_failure: 503, internal_error: 500 })
const kinds = Object.freeze({ answer: 'answer.md', metadata: 'turn.json', conversation: 'conversation.json', evidence: 'retrieval_evidence.json', native_evidence: 'native_evidence.json', tools: 'tool_trace.json' })
const fileNames = ['turn.json', 'answer.md', 'conversation.json', 'retrieval_evidence.json', 'native_evidence.json', 'tool_trace.json']
export function connectedFollowupPayload(value, method = 'status') {
  try {
    const v = snap(value)
    if (!['plan', 'start', 'status', 'cancel', 'read', 'download', 'history'].includes(method)) fail()
    fields(v, method === 'plan' ? 'schema_version turn_id report_id report_plan_sha256 question output_language expected_history_sha256' : method === 'history' ? 'schema_version report_id report_plan_sha256 before_ordinal' : method === 'download' ? 'schema_version turn_id plan_sha256 kind' : 'schema_version turn_id plan_sha256')
    if (v.schema_version !== 1) fail()
    if (method === 'plan') { uuid(v.turn_id); uuid(v.report_id); hash(v.report_plan_sha256); scalar(v.question, 4000, 1); if (!v.question.trim() || enc.encode(v.question).length > 16000 || !['en', 'zh', 'ms'].includes(v.output_language)) fail(); if (v.expected_history_sha256 !== null) hash(v.expected_history_sha256) }
    else if (method === 'history') { uuid(v.report_id); hash(v.report_plan_sha256); if (v.before_ordinal !== null) integer(v.before_ordinal, 1000, 1) }
    else { uuid(v.turn_id); hash(v.plan_sha256); if (method === 'download' && !Object.hasOwn(kinds, v.kind)) fail() }
    const raw = JSON.stringify(v); if (enc.encode(raw).length > 32768) fail()
    return raw
  } catch { fail('invalid_request') }
}

const emptyHead = (reportId, planHash) => digest({ schema_version: 1, report_id: reportId, report_plan_sha256: planHash, turns: [] })
async function pair(v, reportId, reportPlan, expectedOrdinal = null) {
  fields(v, 'turn_id plan_sha256 ordinal question answer_sha256 answer_prefix answer_prefix_sha256 answer_characters admitted_characters truncated receipt_sha256 predecessor_head_sha256 published_head_sha256')
  uuid(v.turn_id); hash(v.plan_sha256); integer(v.ordinal, 1000, 1); if (expectedOrdinal !== null && v.ordinal !== expectedOrdinal) fail()
  scalar(v.question, 4000, 1); if (!v.question.trim() || enc.encode(v.question).length > 16000) fail()
  hash(v.answer_sha256); scalar(v.answer_prefix, 4000, 1); hash(v.answer_prefix_sha256); integer(v.answer_characters, 16384, 1); integer(v.admitted_characters, 4000, 1); bool(v.truncated)
  if ([...v.answer_prefix].length !== v.admitted_characters || v.admitted_characters !== Math.min(4000, v.answer_characters) || v.truncated !== (v.answer_characters > 4000) || await sha256(enc.encode(v.answer_prefix)) !== v.answer_prefix_sha256) fail()
  if (!v.truncated && await sha256(enc.encode(v.answer_prefix)) !== v.answer_sha256) fail()
  hash(v.receipt_sha256); hash(v.predecessor_head_sha256); hash(v.published_head_sha256)
  const next = await digest({ schema_version: 1, report_id: reportId, report_plan_sha256: reportPlan, predecessor_head_sha256: v.predecessor_head_sha256, ordinal: v.ordinal, turn_id: v.turn_id, plan_sha256: v.plan_sha256, question_sha256: await sha256(enc.encode(v.question)), answer_sha256: v.answer_sha256, receipt_sha256: v.receipt_sha256 })
  if (next !== v.published_head_sha256) fail()
}
async function pairs(value, reportId, reportPlan, count, end, head = null) {
  if (!Array.isArray(value) || value.length !== Math.min(5, end)) fail()
  const first = end - value.length + 1
  for (let i = 0; i < value.length; i++) {
    const v = value[i]
    await pair(v, reportId, reportPlan, first + i)
    if (i && v.predecessor_head_sha256 !== value[i - 1].published_head_sha256) fail()
  }
  if (first === 1 && value.length && value[0].predecessor_head_sha256 !== await emptyHead(reportId, reportPlan)) fail()
  if (head !== null && (value.length ? value[value.length - 1].published_head_sha256 : await emptyHead(reportId, reportPlan)) !== head) fail()
  if (count < end) fail()
}
function parentBinding(v, graph) {
  fields(v, 'display_graph_id principal scope report native_binding')
  if (v.display_graph_id !== graph || typeof v.principal !== 'string' || !/^[\x20-\x7e]{1,128}$/.test(v.principal) || !v.principal.trim()) fail()
  fields(v.scope, 'schema_version workspace_id project_id graph_id run_id branch_id layer')
  if (v.scope.schema_version !== 1 || v.scope.layer !== 'source' || v.scope.run_id !== null || v.scope.branch_id !== null) fail()
  for (const k of ['workspace_id', 'project_id', 'graph_id']) uuid(v.scope[k])
  fields(v.report, 'report_id plan_sha256 receipt_sha256 manifest_sha256 full_report_sha256')
  uuid(v.report.report_id); for (const k of ['plan_sha256', 'receipt_sha256', 'manifest_sha256', 'full_report_sha256']) hash(v.report[k])
  const b = v.native_binding
  fields(b, 'display_graph_id principal scope project_revision source preparation native coverage reference_keys')
  if (b.display_graph_id !== graph || b.principal !== v.principal || !same(b.scope, v.scope)) fail()
  integer(b.project_revision, 2147483647, 1)
  fields(b.source, 'source_revision source_name source_sha256'); uuid(b.source.source_revision); scalar(b.source.source_name, 256, 1); hash(b.source.source_sha256)
  fields(b.preparation, 'operation_id plan_sha256 simulation_id artifact_sha256'); uuid(b.preparation.operation_id); hash(b.preparation.plan_sha256); hash(b.preparation.artifact_sha256)
  if (b.preparation.simulation_id !== 'sim_' + b.preparation.operation_id.replaceAll('-', '')) fail()
  fields(b.native, 'run_id launch_sha256 request_fingerprint evidence_sha256 platforms'); uuid(b.native.run_id)
  for (const k of ['launch_sha256', 'request_fingerprint', 'evidence_sha256']) hash(b.native[k])
  if (![ ['twitter'], ['reddit'], ['twitter', 'reddit'] ].some(p => same(p, b.native.platforms))) fail()
  if (!Array.isArray(b.coverage) || b.coverage.length !== b.native.platforms.length || !Array.isArray(b.reference_keys) || b.reference_keys.length > 2048 || new Set(b.reference_keys).size !== b.reference_keys.length) fail()
  b.coverage.forEach((c, i) => { fields(c, 'platform total_records selected_records complete windows'); if (c.platform !== b.native.platforms[i]) fail(); integer(c.total_records, 10000); integer(c.selected_records, c.total_records); bool(c.complete); if (!Array.isArray(c.windows) || c.windows.length > 1) fail(); c.windows.forEach(w => { fields(w, 'offset count'); integer(w.offset, 9999); integer(w.count, 10000, 1); if (w.offset + w.count > c.total_records) fail() }); if (c.selected_records !== c.windows.reduce((n, w) => n + w.count, 0) || c.complete !== (c.selected_records === c.total_records)) fail() })
  const nativeKeys = []
  b.reference_keys.forEach(k => { scalar(k, 160, 1); if (/^source:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(k)) return; const m = k.match(/^native:(twitter|reddit):(0|[1-9][0-9]{0,3}):[0-9a-f]{64}$/); if (!m || !b.coverage.find(c => c.platform === m[1])?.windows.some(w => Number(m[2]) >= w.offset && Number(m[2]) < w.offset + w.count)) fail(); nativeKeys.push(`${m[1]}:${m[2]}`) })
  const expectedKeys = b.coverage.flatMap(c => c.windows.flatMap(w => Array.from({ length: w.count }, (_, i) => `${c.platform}:${w.offset + i}`)))
  if (!same(nativeKeys, expectedKeys)) fail()
}
async function frozenHistory(v, binding) {
  fields(v, 'head_sha256 total_completed window_start pairs'); hash(v.head_sha256); integer(v.total_completed, 1000); integer(v.window_start, 1000, 1)
  const end = v.total_completed, expectedLength = Math.min(5, end)
  if (v.window_start !== (end ? end - expectedLength + 1 : 1)) fail()
  await pairs(v.pairs, binding.report.report_id, binding.report.plan_sha256, end, end, v.head_sha256)
}
function context(v) { fields(v, 'file_sha256 prefix_sha256 prefix_characters total_characters truncated'); hash(v.file_sha256); hash(v.prefix_sha256); integer(v.total_characters, 2097152, 1); integer(v.prefix_characters, 15000, 1); bool(v.truncated); if (v.prefix_characters !== Math.min(15000, v.total_characters) || v.truncated !== (v.total_characters > 15000)) fail() }
function manifest(v) {
  fields(v, 'schema_version files'); if (v.schema_version !== 1 || !Array.isArray(v.files) || v.files.length !== 6) fail()
  let total = 0; v.files.forEach((f, i) => { fields(f, 'name size sha256'); if (f.name !== fileNames[i]) fail(); integer(f.size, 2097152, 1); hash(f.sha256); total += f.size })
  if (total > 16777216 || v.files.find(f => f.name === 'answer.md').size > 16384) fail()
}
export async function validateConnectedFollowupResult(input, { graph, payload, parentReport = null, known = null, planning = false } = {}) {
  try {
    const v = snap(input), p = snap(payload), parent = parentReport ? snap(parentReport) : null, prior = known ? snap(known) : null
    if (enc.encode(wire(v)).length > 262144) fail()
    connectedFollowupPayload(p, planning ? 'plan' : 'status')
    fields(v, 'schema_version turn_id plan_sha256 binding options history report_context context_sha256 source_projection_sha256 model_label limits ceiling_microusd authorization state progress workflow receipt receipt_sha256 manifest published_history_head_sha256 cleanup cancel_requested error_code')
    if (v.schema_version !== 1 || v.turn_id !== p.turn_id) fail(); uuid(v.turn_id); hash(v.plan_sha256); hash(v.context_sha256); hash(v.source_projection_sha256)
    parentBinding(v.binding, graph); fields(v.options, 'question output_language expected_history_sha256')
    scalar(v.options.question, 4000, 1); if (!v.options.question.trim() || enc.encode(v.options.question).length > 16000 || !['en', 'zh', 'ms'].includes(v.options.output_language)) fail()
    if (v.options.expected_history_sha256 !== null) hash(v.options.expected_history_sha256)
    await frozenHistory(v.history, v.binding); if (v.options.expected_history_sha256 !== null && v.options.expected_history_sha256 !== v.history.head_sha256) fail()
    context(v.report_context); hash(v.report_context.file_sha256); if (v.report_context.file_sha256 !== v.binding.report.full_report_sha256) fail()
    scalar(v.model_label, 200, 1); if (!v.model_label.trim()) fail()
    fields(v.limits, 'max_calls max_input_bytes max_output_tokens max_run_seconds')
    for (const [k, max] of Object.entries({ max_calls: 128, max_input_bytes: 2097152, max_output_tokens: 4096, max_run_seconds: 600 })) integer(v.limits[k], max, 1)
    if (v.ceiling_microusd !== null) { if (typeof v.ceiling_microusd === 'bigint') { if (v.ceiling_microusd < 1n || v.ceiling_microusd > 9223372036854775807n) fail() } else integer(v.ceiling_microusd, Number.MAX_SAFE_INTEGER, 1) }
    fields(v.authorization, 'model_calls_enabled budget_configured'); bool(v.authorization.model_calls_enabled); bool(v.authorization.budget_configured)
    if (v.authorization.budget_configured !== (v.ceiling_microusd !== null) || v.authorization.model_calls_enabled && !v.authorization.budget_configured) fail()
    if (!['planned', 'queued', 'generating', 'completed', 'failed', 'cancelled', 'uncertain'].includes(v.state)) fail()
    if (['failed', 'cancelled', 'uncertain'].includes(v.state)) { if (!Object.hasOwn(FOLLOWUP_ERROR_STATUS, v.error_code)) fail() } else if (v.error_code !== null) fail()
    fields(v.progress, 'stage percent completed_sections total_sections'); integer(v.progress.percent, 100); integer(v.progress.completed_sections, 8); integer(v.progress.total_sections, 8)
    if (v.progress.completed_sections > v.progress.total_sections || !['planned', 'queued', 'generating', 'completed', 'failed', 'cancelled', 'uncertain', 'planning', 'researching', 'writing', 'publishing'].includes(v.progress.stage)) fail()
    if (['planned', 'queued'].includes(v.state) && (v.progress.percent !== 0 || v.progress.completed_sections !== 0)) fail()
    if (v.workflow !== null) { fields(v.workflow, 'workflow_id run_id'); scalar(v.workflow.workflow_id, 256, 1); scalar(v.workflow.run_id, 256, 1) }
    bool(v.cancel_requested); fields(v.cleanup, 'known pending owner_thread_alive'); bool(v.cleanup.known)
    if (v.cleanup.known) { bool(v.cleanup.pending); bool(v.cleanup.owner_thread_alive) } else if (v.cleanup.pending !== null || v.cleanup.owner_thread_alive !== null) fail()
    if (v.state === 'completed') {
      if (v.progress.percent !== 100 || v.progress.completed_sections !== v.progress.total_sections || !v.cleanup.known || v.cleanup.pending || v.cleanup.owner_thread_alive) fail()
      manifest(v.manifest); fields(v.receipt, 'schema_version turn_id plan_sha256 parent_report_id parent_report_plan_sha256 parent_report_receipt_sha256 context_sha256 history_head_sha256 ordinal manifest_sha256 output_language reference_integrity semantic_support_status')
      const r = v.receipt, b = v.binding.report
      if (r.schema_version !== 1 || r.turn_id !== v.turn_id || r.plan_sha256 !== v.plan_sha256 || r.parent_report_id !== b.report_id || r.parent_report_plan_sha256 !== b.plan_sha256 || r.parent_report_receipt_sha256 !== b.receipt_sha256 || r.context_sha256 !== v.context_sha256 || r.history_head_sha256 !== v.history.head_sha256 || r.ordinal !== v.history.total_completed + 1 || r.output_language !== v.options.output_language || r.reference_integrity !== 'validated' || r.semantic_support_status !== 'not_reviewed' || r.manifest_sha256 !== await digest(v.manifest)) fail()
      hash(v.receipt_sha256); if (v.receipt_sha256 !== await digest(r)) fail(); hash(v.published_history_head_sha256)
      const answer = v.manifest.files[1]
      const head = await digest({ schema_version: 1, report_id: b.report_id, report_plan_sha256: b.plan_sha256, predecessor_head_sha256: v.history.head_sha256, ordinal: r.ordinal, turn_id: v.turn_id, plan_sha256: v.plan_sha256, question_sha256: await sha256(enc.encode(v.options.question)), answer_sha256: answer.sha256, receipt_sha256: v.receipt_sha256 })
      if (head !== v.published_history_head_sha256) fail()
    } else if (v.receipt !== null || v.receipt_sha256 !== null || v.manifest !== null || v.published_history_head_sha256 !== null) fail()
    if (v.plan_sha256 !== await digest(picked(v))) fail()
    if (planning) {
      if (!parent || v.state !== 'planned' || v.history.total_completed >= 1000 || v.binding.report.report_id !== p.report_id || v.binding.report.plan_sha256 !== p.report_plan_sha256 || !same(v.options, { question: p.question, output_language: p.output_language, expected_history_sha256: p.expected_history_sha256 })) fail()
      const selected = await validateConnectedReportResult(parent, { graph, payload: reportIdentity(parent), known: parent })
      if (selected.state !== 'completed' || !same(v.binding.native_binding, selected.binding) || v.binding.principal !== selected.binding.principal || !same(v.binding.scope, selected.binding.scope) || v.binding.report.receipt_sha256 !== selected.receipt_sha256 || v.binding.report.manifest_sha256 !== selected.receipt.manifest_sha256 || v.binding.report.full_report_sha256 !== selected.manifest.files.find(f => f.name === 'full_report.md')?.sha256) fail()
    } else if (v.plan_sha256 !== p.plan_sha256) fail()
    if (prior && (immutable.some(k => !same(v[k], prior[k])) || v.plan_sha256 !== prior.plan_sha256 || prior.cancel_requested && !v.cancel_requested || prior.workflow && !same(v.workflow, prior.workflow) || prior.receipt && (!same(v.receipt, prior.receipt) || !same(v.manifest, prior.manifest) || v.receipt_sha256 !== prior.receipt_sha256 || v.state !== 'completed'))) fail()
    return v
  } catch { fail() }
}
function references(content, turn) {
  const admitted = new Set(turn.binding.native_binding.reference_keys); let count = 0
  for (const match of content.matchAll(/(?<!\[)\[\[([^\[\]]+)\]\](?!\])/g)) { if (!admitted.has(match[1])) fail(); count++ }
  const remainder = content.replace(/(?<!\[)\[\[([^\[\]]+)\]\](?!\])/g, '')
  if (!count || remainder.includes('[[') || remainder.includes(']]')) fail()
}
export async function validateConnectedFollowupRead(input, context) {
  try {
    const v = snap(input); fields(v, 'schema_version turn content'); if (v.schema_version !== 1 || enc.encode(wire(v)).length > 4194304) fail()
    const turn = await validateConnectedFollowupResult(v.turn, context); if (turn.state !== 'completed') fail()
    scalar(v.content, 16384, 1); const bytes = enc.encode(v.content)
    if (bytes.length > 16384 || bytes.length !== turn.manifest.files[1].size || await sha256(bytes) !== turn.manifest.files[1].sha256) fail()
    references(v.content, turn)
    return { schema_version: 1, turn, content: v.content }
  } catch { fail() }
}
async function validateConversation(bytes, turn) {
  const raw = new TextDecoder('utf-8', { fatal: true }).decode(bytes), v = parseConnectedReportJson(raw)
  fields(v, 'schema_version report_id report_plan_sha256 total_completed pairs')
  const report = turn.binding.report
  if (v.schema_version !== 1 || v.report_id !== report.report_id || v.report_plan_sha256 !== report.plan_sha256 || v.total_completed !== turn.receipt.ordinal || !Array.isArray(v.pairs) || v.pairs.length !== v.total_completed || v.pairs.length > 1000) fail()
  let predecessor = await emptyHead(report.report_id, report.plan_sha256)
  for (let i = 0; i < v.pairs.length; i++) {
    const p = v.pairs[i], current = i === v.pairs.length - 1
    fields(p, 'turn_id plan_sha256 ordinal question answer answer_sha256 predecessor_head_sha256 receipt_sha256 published_head_sha256')
    uuid(p.turn_id); hash(p.plan_sha256); integer(p.ordinal, 1000, 1); if (p.ordinal !== i + 1 || p.predecessor_head_sha256 !== predecessor) fail()
    scalar(p.question, 4000, 1); if (!p.question.trim() || enc.encode(p.question).length > 16000) fail()
    scalar(p.answer, 16384, 1); if (enc.encode(p.answer).length > 16384) fail(); hash(p.answer_sha256)
    if (p.answer_sha256 !== await sha256(enc.encode(p.answer))) fail()
    if (current) {
      if (p.turn_id !== turn.turn_id || p.plan_sha256 !== turn.plan_sha256 || p.question !== turn.options.question || p.answer_sha256 !== turn.manifest.files[1].sha256 || p.predecessor_head_sha256 !== turn.history.head_sha256 || p.receipt_sha256 !== null || p.published_head_sha256 !== null) fail()
      if (await digest({ schema_version: 1, report_id: report.report_id, report_plan_sha256: report.plan_sha256, predecessor_head_sha256: predecessor, ordinal: p.ordinal, turn_id: p.turn_id, plan_sha256: p.plan_sha256, question_sha256: await sha256(enc.encode(p.question)), answer_sha256: p.answer_sha256, receipt_sha256: turn.receipt_sha256 }) !== turn.published_history_head_sha256) fail()
    } else {
      hash(p.receipt_sha256); hash(p.published_head_sha256)
      const next = await digest({ schema_version: 1, report_id: report.report_id, report_plan_sha256: report.plan_sha256, predecessor_head_sha256: predecessor, ordinal: p.ordinal, turn_id: p.turn_id, plan_sha256: p.plan_sha256, question_sha256: await sha256(enc.encode(p.question)), answer_sha256: p.answer_sha256, receipt_sha256: p.receipt_sha256 })
      if (next !== p.published_head_sha256) fail()
      predecessor = next
    }
  }
  for (const admitted of turn.history.pairs) {
    const p = v.pairs[admitted.ordinal - 1]
    if (!p || p.turn_id !== admitted.turn_id || p.plan_sha256 !== admitted.plan_sha256 || p.question !== admitted.question || p.answer_sha256 !== admitted.answer_sha256 || p.receipt_sha256 !== admitted.receipt_sha256 || p.published_head_sha256 !== admitted.published_head_sha256 || [...p.answer].slice(0, 4000).join('') !== admitted.answer_prefix) fail()
  }
}
export async function validateConnectedFollowupDownload(input, { graph, payload, known } = {}) {
  try {
    const v = snap(input), p = snap(payload), turn = await validateConnectedFollowupResult(known, { graph, payload: followupIdentity(known), known })
    connectedFollowupPayload(p, 'download'); if (turn.state !== 'completed') fail()
    fields(v, 'schema_version turn_id plan_sha256 receipt_sha256 artifact')
    if (v.schema_version !== 1 || v.turn_id !== p.turn_id || v.plan_sha256 !== p.plan_sha256 || v.receipt_sha256 !== turn.receipt_sha256) fail()
    const f = turn.manifest.files.find(x => x.name === kinds[p.kind]), a = v.artifact
    fields(a, 'name mime size sha256 content_base64')
    if (!f || a.name !== f.name || a.size !== f.size || a.sha256 !== f.sha256 || a.mime !== (f.name.endsWith('.md') ? 'text/markdown' : 'application/json') || enc.encode(wire(v)).length > 4194304) fail()
    if (typeof a.content_base64 !== 'string' || a.content_base64.length > 2796204 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(a.content_base64)) fail()
    const binary = atob(a.content_base64); if (btoa(binary) !== a.content_base64 || binary.length !== a.size) fail()
    const bytes = Uint8Array.from(binary, c => c.charCodeAt(0)); if (await sha256(bytes) !== a.sha256) fail()
    new TextDecoder('utf-8', { fatal: true }).decode(bytes)
    if (p.kind === 'answer') references(new TextDecoder('utf-8', { fatal: true }).decode(bytes), turn)
    if (p.kind === 'conversation') await validateConversation(bytes, turn)
    return v
  } catch { fail() }
}
export async function validateConnectedFollowupHistory(input, { graph, payload, parentReport = null } = {}) {
  try {
    const v = snap(input), p = snap(payload); connectedFollowupPayload(p, 'history')
    fields(v, 'schema_version report_id report_plan_sha256 binding head_sha256 total_completed before_ordinal pairs')
    if (v.schema_version !== 1 || v.report_id !== p.report_id || v.report_plan_sha256 !== p.report_plan_sha256 || v.before_ordinal !== p.before_ordinal || enc.encode(wire(v)).length > 262144) fail()
    parentBinding(v.binding, graph); if (v.binding.report.report_id !== v.report_id || v.binding.report.plan_sha256 !== v.report_plan_sha256) fail()
    if (parentReport) {
      const parent = await validateConnectedReportResult(parentReport, { graph, payload: reportIdentity(parentReport), known: parentReport })
      if (parent.state !== 'completed' || !same(v.binding.native_binding, parent.binding) || v.binding.report.receipt_sha256 !== parent.receipt_sha256) fail()
    }
    hash(v.head_sha256); integer(v.total_completed, 1000)
    if (v.before_ordinal !== null && v.before_ordinal > v.total_completed + 1) fail()
    const end = v.before_ordinal === null ? v.total_completed : v.before_ordinal - 1
    await pairs(v.pairs, v.report_id, v.report_plan_sha256, v.total_completed, end, v.before_ordinal === null ? v.head_sha256 : null)
    if (v.total_completed === 0 && v.head_sha256 !== await emptyHead(v.report_id, v.report_plan_sha256)) fail()
    return v
  } catch { fail() }
}

export function createConnectedFollowupsChannel({ fetchImpl, deadlineMs, connection, denied }) {
  let epoch = 0, active = null
  const plans = new Map(), attempted = new Set()
  function clear() { epoch++; active?.abort(); active = null; plans.clear() }
  function cancel() { epoch++; active?.abort(); active = null }
  async function request(method, input, parentReport = null, suppliedKnown = null) {
    let current, raw, payload, key, known
    try {
      current = connection(); if (!current) fail('disconnected')
      raw = connectedFollowupPayload(input, method); payload = JSON.parse(raw)
      parentReport = parentReport ? snap(parentReport) : null; suppliedKnown = suppliedKnown ? snap(suppliedKnown) : null
      if (method === 'plan' && !parentReport) fail('invalid_request')
      key = `${current.origin}/${current.graph}/${payload.turn_id}`
      const stored = plans.get(key) || null
      known = stored || suppliedKnown
      if (stored && suppliedKnown && (stored.plan_sha256 !== suppliedKnown.plan_sha256 || !same(picked(stored), picked(suppliedKnown)))) fail('invalid_request')
      if (method === 'start' && (attempted.has(key) || !known || known.state !== 'planned' || known.history.total_completed >= 1000 || !known.authorization.model_calls_enabled || !known.authorization.budget_configured || !known.ceiling_microusd)) fail('turn_conflict')
      if (method === 'plan' && (attempted.has(key) || known)) fail('turn_conflict')
      if (['read', 'download'].includes(method) && (!known || known.state !== 'completed')) fail('invalid_request')
      if (known && (known.turn_id !== payload.turn_id || method !== 'plan' && known.plan_sha256 !== payload.plan_sha256)) fail('invalid_request')
      if (!stored && plans.size >= 100 || method === 'start' && attempted.size >= 10000) fail('busy')
    } catch (e) { if (e instanceof WorkbenchError) e.requestSent = false; throw e }
    cancel(); const life = epoch, controller = new AbortController(); active = controller
    let timedOut = false, reader, responseBody, requestSent = false
    const timer = setTimeout(() => { timedOut = true; controller.abort() }, deadlineMs)
    const owned = () => { if (life !== epoch || controller.signal.aborted) fail(timedOut ? 'deadline' : 'cancelled') }
    const guarded = promise => new Promise((resolve, reject) => {
      const aborted = () => reject(new WorkbenchError(timedOut ? 'deadline' : 'cancelled'))
      if (controller.signal.aborted) { aborted(); return }
      controller.signal.addEventListener('abort', aborted, { once: true })
      Promise.resolve(promise).then(v => { if (controller.signal.aborted && v?.body?.cancel) { try { void v.body.cancel().catch(() => {}) } catch { /* discarded */ } } resolve(v) }, reject).finally(() => controller.signal.removeEventListener('abort', aborted))
    })
    try {
      if (method === 'plan') { const parent = await guarded(validateConnectedReportResult(parentReport, { graph: current.graph, payload: reportIdentity(parentReport), known: parentReport })); owned(); if (parent.state !== 'completed' || parent.report_id !== payload.report_id || parent.plan_sha256 !== payload.report_plan_sha256) fail('invalid_request') }
      if (known) { known = await guarded(validateConnectedFollowupResult(known, { graph: current.graph, payload: followupIdentity(known), known })); owned() }
      if (method === 'start' || method === 'cancel') attempted.add(key)
      requestSent = true
      const response = await guarded(fetchImpl(`${current.origin}/api/connected-followup/${method}/${current.graph}`, { method: 'POST', headers: { Authorization: `Bearer ${current.token}`, 'Content-Type': 'application/json' }, body: raw, signal: controller.signal, redirect: 'error', credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer' }))
      responseBody = response.body; owned()
      if ([401, 403].includes(response.status)) { denied(); fail(response.status === 401 ? 'unauthorized' : 'origin_denied') }
      if ([404, 501, 503].includes(response.status) && !/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') || '')) fail('followup_unavailable')
      if (response.redirected || response.type === 'opaqueredirect' || !/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') || '') || !response.body?.getReader) fail()
      const cap = ['read', 'download'].includes(method) ? 4194304 : 262272, length = response.headers.get('content-length')
      if (length && (!/^[0-9]+$/.test(length) || !Number.isSafeInteger(Number(length)) || Number(length) > cap)) fail('result_too_large')
      reader = response.body.getReader(); const decoder = new TextDecoder('utf-8', { fatal: true }); let size = 0, body = ''
      while (true) { const chunk = await guarded(reader.read()); owned(); if (chunk.done) break; size += chunk.value.byteLength; if (size > cap) fail('result_too_large'); body += decoder.decode(chunk.value, { stream: true }) }
      body += decoder.decode(); owned(); const value = parseConnectedReportJson(body)
      if (!response.ok || value?.success !== true) { fields(value, 'success error'); fields(value.error, 'code'); if (value.success !== false || FOLLOWUP_ERROR_STATUS[value.error.code] !== response.status) fail(); const denial = new WorkbenchError(value.error.code); denial.replyConfirmed = true; throw denial }
      fields(value, 'success data')
      const context = { graph: current.graph, payload: method === 'download' ? followupIdentity(known) : payload, known, parentReport: method === 'plan' ? parentReport : null, planning: method === 'plan' }
      const result = await guarded(method === 'history' ? validateConnectedFollowupHistory(value.data, { graph: current.graph, payload, parentReport }) : method === 'read' ? validateConnectedFollowupRead(value.data, context) : method === 'download' ? validateConnectedFollowupDownload(value.data, { graph: current.graph, payload, known }) : validateConnectedFollowupResult(value.data, context)); owned()
      if (method !== 'history') { const record = method === 'read' ? result.turn : method === 'download' ? known : result; plans.set(key, snap(record)); if (record.state !== 'planned' || method === 'cancel' || method === 'status' && !known) attempted.add(key) }
      return result
    } catch (e) {
      if (e instanceof WorkbenchError && ['unauthorized', 'origin_denied'].includes(e.code)) throw e
      try { owned() } catch (ownershipError) { if (ownershipError instanceof WorkbenchError) ownershipError.requestSent = requestSent; throw ownershipError }
      if (e instanceof WorkbenchError) { e.requestSent = requestSent; throw e }
      const mapped = new WorkbenchError(e instanceof SyntaxError || e instanceof TypeError && reader ? 'invalid_reply' : 'transport_failure'); mapped.requestSent = requestSent; throw mapped
    } finally {
      clearTimeout(timer); controller.abort()
      if (reader) { try { void reader.cancel().catch(() => {}) } catch { /* discarded */ } reader.releaseLock() }
      else if (responseBody) { try { void responseBody.cancel().catch(() => {}) } catch { /* discarded */ } }
      if (active === controller) active = null
    }
  }
  return { plan: (p, r) => request('plan', p, r), start: (p, k) => request('start', p, null, k), status: (p, k) => request('status', p, null, k), cancelTurn: (p, k) => request('cancel', p, null, k), read: (p, k) => request('read', p, null, k), download: (p, k) => request('download', p, null, k), history: p => request('history', p), cancel, clear }
}

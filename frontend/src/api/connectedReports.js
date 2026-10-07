// Receipt-bound report operations. Credentials stay in the owning private client.
import { WorkbenchError } from './workbench.js'
import { nativeObservationSelection } from './nativeObservations.js'
import { sha256 } from './sourceLibrary.js'
const enc = new TextEncoder()
const fail = (code = 'invalid_reply') => { throw new WorkbenchError(code) }
const uuidPattern = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/
const hashPattern = /^[0-9a-f]{64}$/
const fields = (v, keys) => { if (!v || Object.getPrototypeOf(v) !== Object.prototype || Object.keys(v).sort().join('|') !== keys.split(' ').sort().join('|')) fail() }
const uuid = v => { if (typeof v !== 'string' || !uuidPattern.test(v)) fail() }
const hash = v => { if (typeof v !== 'string' || !hashPattern.test(v)) fail() }
const int = (v, max, min = 0) => { if (!Number.isSafeInteger(v) || Object.is(v, -0) || v < min || v > max) fail() }
const bool = v => { if (typeof v !== 'boolean') fail() }
function text(v, max = 262144, min = 0) {
  if (typeof v !== 'string' || v.length > max * 2 || [...v].length > max || [...v].length < min || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(v)) fail()
}
function detach(v, depth = 0) {
  if (depth > 16) fail()
  if (v === null || typeof v === 'boolean') return v
  if (typeof v === 'number') { int(v, Number.MAX_SAFE_INTEGER); return v }
  if (typeof v === 'bigint') { if (v < 0n || v > 9223372036854775807n) fail(); return v }
  if (typeof v === 'string') { text(v, 4194304); return v }
  if (Array.isArray(v)) { if (v.length > 2048) fail(); return Array.from(v, x => detach(x, depth + 1)) }
  if (!v || Object.getPrototypeOf(v) !== Object.prototype || Object.keys(v).length > 100) fail()
  return Object.fromEntries(Object.entries(v).map(([k, x]) => { text(k, 256); return [k, detach(x, depth + 1)] }))
}
function wireJson(v) {
  if (typeof v === 'bigint') return v.toString()
  if (Array.isArray(v)) return '[' + v.map(wireJson).join(',') + ']'
  if (v && typeof v === 'object') return '{' + Object.keys(v).sort().map(k => JSON.stringify(k) + ':' + wireJson(v[k])).join(',') + '}'
  return JSON.stringify(v)
}
const ascii = v => wireJson(v).replace(/[\u007f-\uffff]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'))
// A detached snapshot preserves the one admitted 64-bit public integer.
export const reportSnapshot = v => detach(v)
const same = (a, b) => ascii(a) === ascii(b)
const digest = v => sha256(enc.encode(ascii(v)))
const immutable = 'schema_version report_id binding options context_sha256 source_projection_sha256 model_label limits ceiling_microusd'.split(' ')
const pickIdentity = v => Object.fromEntries(immutable.map(k => [k, v[k]]))
export const REPORT_ERROR_STATUS = Object.freeze({ invalid_request: 400, invalid_reply: 502, unauthorized: 401, origin_denied: 403, not_found: 404, conflict: 409, tombstoned: 410, busy: 409, result_too_large: 413, report_unavailable: 503, model_calls_disabled: 409, budget_denied: 409, report_failed: 409, report_cancelled: 409, report_uncertain: 409, timeout: 503, transport_failure: 503, internal_error: 500 })
export const reportIdentity = v => ({ schema_version: 1, report_id: v.report_id, plan_sha256: v.plan_sha256 })
export function newReportId() { if (!globalThis.crypto?.randomUUID) fail('invalid_request'); return globalThis.crypto.randomUUID() }
function windows(v, platforms = ['twitter', 'reddit']) {
  if (v === null) return
  if (!Array.isArray(v) || v.length < 1 || v.length > 2) fail()
  const seen = new Set()
  v.forEach(w => { fields(w, 'platform offset count'); if (!platforms.includes(w.platform) || seen.has(w.platform)) fail(); seen.add(w.platform); int(w.offset, 9999); int(w.count, 1000, 1); if (w.offset + w.count > 10000) fail() })
}
function options(v) {
  fields(v, 'requirement output_language native_windows'); text(v.requirement, 4000, 1)
  if (!v.requirement.trim() || enc.encode(v.requirement).length > 16000 || !['en', 'zh', 'ms'].includes(v.output_language)) fail()
  windows(v.native_windows)
}
export function connectedReportPayload(value, method = 'status') {
  try {
    const v = detach(value)
    fields(v, method === 'plan' ? 'schema_version report_id launch_id launch_sha256 requirement output_language native_windows' : method === 'download' ? 'schema_version report_id plan_sha256 kind section_index' : 'schema_version report_id plan_sha256')
    if (!['plan', 'start', 'status', 'cancel', 'read', 'download'].includes(method) || v.schema_version !== 1) fail()
    uuid(v.report_id)
    if (method === 'plan') { uuid(v.launch_id); hash(v.launch_sha256); options({ requirement: v.requirement, output_language: v.output_language, native_windows: v.native_windows }) }
    else hash(v.plan_sha256)
    if (method === 'download') reportArtifactName(v)
    const raw = JSON.stringify(v); if (enc.encode(raw).length > 32768) fail()
    return raw
  } catch { fail('invalid_request') }
}
export function reportArtifactName({ kind, section_index }) {
  const names = { report: 'full_report.md', outline: 'outline.json', evidence: 'retrieval_evidence.json', native_evidence: 'native_evidence.json', metadata: 'meta.json' }
  if (kind === 'section') { int(section_index, 8, 1); return 'section_' + String(section_index).padStart(2, '0') + '.md' }
  if (!Object.hasOwn(names, kind) || section_index !== null) fail()
  return names[kind]
}
function binding(v, opts, graph) {
  fields(v, 'display_graph_id principal scope project_revision source preparation native coverage reference_keys')
  if (typeof graph !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(graph) || v.display_graph_id !== graph || typeof v.principal !== 'string' || !/^[\x20-\x7e]{1,128}$/.test(v.principal) || !v.principal.trim()) fail()
  fields(v.scope, 'schema_version workspace_id project_id graph_id run_id branch_id layer')
  if (v.scope.schema_version !== 1 || v.scope.layer !== 'source' || v.scope.run_id !== null || v.scope.branch_id !== null) fail()
  ;['workspace_id', 'project_id', 'graph_id'].forEach(k => uuid(v.scope[k])); int(v.project_revision, 2147483647, 1)
  fields(v.source, 'source_revision source_name source_sha256'); uuid(v.source.source_revision); text(v.source.source_name, 256, 1); hash(v.source.source_sha256)
  fields(v.preparation, 'operation_id plan_sha256 simulation_id artifact_sha256'); uuid(v.preparation.operation_id); hash(v.preparation.plan_sha256); hash(v.preparation.artifact_sha256)
  if (v.preparation.simulation_id !== 'sim_' + v.preparation.operation_id.replaceAll('-', '')) fail()
  fields(v.native, 'run_id launch_sha256 request_fingerprint evidence_sha256 platforms'); uuid(v.native.run_id)
  ;['launch_sha256', 'request_fingerprint', 'evidence_sha256'].forEach(k => hash(v.native[k]))
  if (![ ['twitter'], ['reddit'], ['twitter', 'reddit'] ].some(p => same(p, v.native.platforms))) fail()
  windows(opts.native_windows, v.native.platforms)
  if (!Array.isArray(v.coverage) || v.coverage.length !== v.native.platforms.length) fail()
  const admittedRanges = new Map()
  v.coverage.forEach((c, i) => {
    fields(c, 'platform total_records selected_records complete windows'); if (c.platform !== v.native.platforms[i]) fail()
    int(c.total_records, 10000); int(c.selected_records, c.total_records); bool(c.complete)
    const requested = opts.native_windows?.find(w => w.platform === c.platform)
    const expected = opts.native_windows === null ? (c.total_records ? [{ offset: 0, count: c.total_records }] : []) : requested ? [{ offset: requested.offset, count: requested.count }] : []
    if (!Array.isArray(c.windows) || c.windows.length > 1 || !same(c.windows, expected)) fail()
    c.windows.forEach(w => { fields(w, 'offset count'); int(w.offset, 9999); int(w.count, 10000, 1); if (w.offset + w.count > c.total_records) fail() })
    if (c.selected_records !== c.windows.reduce((sum, w) => sum + w.count, 0) || c.complete !== (c.selected_records === c.total_records)) fail()
    admittedRanges.set(c.platform, c.windows)
  })
  if (!Array.isArray(v.reference_keys) || v.reference_keys.length > 2048 || new Set(v.reference_keys).size !== v.reference_keys.length) fail()
  const nativeKeys = []
  v.reference_keys.forEach(key => {
    text(key, 160, 1)
    if (/^source:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(key)) return
    const m = key.match(/^native:(twitter|reddit):(0|[1-9][0-9]{0,3}):([0-9a-f]{64})$/)
    if (!m || !admittedRanges.get(m[1])?.some(w => Number(m[2]) >= w.offset && Number(m[2]) < w.offset + w.count)) fail()
    nativeKeys.push(`${m[1]}:${m[2]}`)
  })
  const expectedKeys = v.coverage.flatMap(c => c.windows.flatMap(w => Array.from({ length: w.count }, (_, i) => `${c.platform}:${w.offset + i}`)))
  if (!same(nativeKeys, expectedKeys)) fail()
}
function manifest(v) {
  fields(v, 'schema_version files')
  if (v.schema_version !== 1 || !Array.isArray(v.files) || v.files.length < 6 || v.files.length > 13) fail()
  const names = ['meta.json', 'outline.json', 'full_report.md', 'retrieval_evidence.json', 'native_evidence.json', ...Array.from({ length: v.files.length - 5 }, (_, i) => 'section_' + String(i + 1).padStart(2, '0') + '.md')]
  let total = 0
  v.files.forEach((f, i) => { fields(f, 'name size sha256'); if (f.name !== names[i]) fail(); int(f.size, 2097152, 1); hash(f.sha256); total += f.size })
  if (total > 16777216) fail()
}
export async function validateConnectedReportResult(value, { graph, payload, selection = null, known = null, planning = false } = {}) {
  try {
    value = detach(value); payload = detach(payload); if (selection) selection = detach(selection); if (known) known = detach(known)
    if (enc.encode(wireJson(value)).length > 262144) fail()
    connectedReportPayload(payload, planning ? 'plan' : 'status')
    fields(value, 'schema_version report_id plan_sha256 binding options context_sha256 source_projection_sha256 model_label limits ceiling_microusd authorization state progress workflow receipt receipt_sha256 manifest cleanup cancel_requested error_code')
    if (value.schema_version !== 1 || value.report_id !== payload.report_id) fail()
    uuid(value.report_id); hash(value.plan_sha256); hash(value.context_sha256); hash(value.source_projection_sha256)
    options(value.options); binding(value.binding, value.options, graph)
    text(value.model_label, 200, 1); if (!value.model_label.trim()) fail()
    fields(value.limits, 'max_calls max_input_bytes max_output_tokens max_run_seconds')
    for (const [k, max] of Object.entries({ max_calls: 128, max_input_bytes: 2097152, max_output_tokens: 4096, max_run_seconds: 600 })) int(value.limits[k], max, 1)
    if (value.ceiling_microusd !== null) {
      if (typeof value.ceiling_microusd === 'bigint') { if (value.ceiling_microusd < 1n || value.ceiling_microusd > 9223372036854775807n) fail() }
      else int(value.ceiling_microusd, Number.MAX_SAFE_INTEGER, 1)
    }
    fields(value.authorization, 'model_calls_enabled budget_configured'); bool(value.authorization.model_calls_enabled); bool(value.authorization.budget_configured)
    if (value.authorization.budget_configured && value.ceiling_microusd === null || value.authorization.model_calls_enabled && !value.authorization.budget_configured) fail()
    const states = ['planned', 'queued', 'generating', 'completed', 'failed', 'cancelled', 'uncertain']
    if (!states.includes(value.state)) fail()
    if (['failed', 'cancelled', 'uncertain'].includes(value.state)) { if (!Object.hasOwn(REPORT_ERROR_STATUS, value.error_code)) fail() } else if (value.error_code !== null) fail()
    fields(value.progress, 'stage percent completed_sections total_sections'); int(value.progress.percent, 100); int(value.progress.total_sections, 8); int(value.progress.completed_sections, value.progress.total_sections)
    if (![...states, 'planning', 'researching', 'writing', 'publishing'].includes(value.progress.stage)) fail()
    if (['planned', 'queued'].includes(value.state) && (value.progress.percent !== 0 || value.progress.completed_sections !== 0)) fail()
    if (value.state === 'completed' && (value.progress.percent !== 100 || !value.progress.total_sections || value.progress.completed_sections !== value.progress.total_sections)) fail()
    if (value.workflow !== null) { fields(value.workflow, 'workflow_id run_id'); text(value.workflow.workflow_id, 256, 1); text(value.workflow.run_id, 256, 1) }
    bool(value.cancel_requested); fields(value.cleanup, 'known pending owner_thread_alive'); bool(value.cleanup.known)
    if (value.cleanup.known) { bool(value.cleanup.pending); bool(value.cleanup.owner_thread_alive) } else if (value.cleanup.pending !== null || value.cleanup.owner_thread_alive !== null) fail()
    if (value.state === 'completed') {
      if (!value.cleanup.known || value.cleanup.pending || value.cleanup.owner_thread_alive) fail()
      manifest(value.manifest)
      fields(value.receipt, 'schema_version report_id plan_sha256 context_sha256 manifest_sha256 output_language reference_integrity semantic_support_status')
      const r = value.receipt
      if (r.schema_version !== 1 || r.report_id !== value.report_id || r.plan_sha256 !== value.plan_sha256 || r.context_sha256 !== value.context_sha256 || r.output_language !== value.options.output_language || r.reference_integrity !== 'validated' || r.semantic_support_status !== 'not_reviewed' || r.manifest_sha256 !== await digest(value.manifest)) fail()
      hash(value.receipt_sha256); if (value.receipt_sha256 !== await digest(r) || value.manifest.files.length - 5 !== value.progress.total_sections) fail()
    } else if (value.receipt !== null || value.receipt_sha256 !== null || value.manifest !== null) fail()
    if (value.plan_sha256 !== await digest(pickIdentity(value))) fail()
    if (planning) {
      if (!selection || value.state !== 'planned' || value.binding.native.run_id !== payload.launch_id || value.binding.native.launch_sha256 !== payload.launch_sha256 || !same(value.options, { requirement: payload.requirement, output_language: payload.output_language, native_windows: payload.native_windows })) fail()
    } else if (value.plan_sha256 !== payload.plan_sha256) fail()
    if (selection) {
      const selected = await nativeObservationSelection(selection, graph), launch = selected.launch, prep = selected.preparation, b = value.binding
      if (b.principal !== launch.request.principal || !same(b.scope, launch.scope) || b.project_revision !== launch.request.project_revision || !same(b.source, prep.source) || !same(b.preparation, launch.preparation) || !same(b.native, { run_id: launch.request.run_id, launch_sha256: launch.launch_sha256, request_fingerprint: launch.receipt.request_fingerprint, evidence_sha256: launch.receipt.evidence_sha256, platforms: launch.request.platforms })) fail()
    }
    if (known && (immutable.some(k => !same(value[k], known[k])) || value.plan_sha256 !== known.plan_sha256 || known.cancel_requested && !value.cancel_requested || known.workflow && !same(value.workflow, known.workflow) || known.receipt && (!same(value.receipt, known.receipt) || !same(value.manifest, known.manifest) || value.receipt_sha256 !== known.receipt_sha256 || value.state !== 'completed'))) fail()
    return value
  } catch { fail() }
}
function references(content, report) {
  const known = new Set(report.binding.reference_keys); let native = false, count = 0
  for (const match of content.matchAll(/(?<!\[)\[\[([^\[\]]+)\]\](?!\])/g)) {
    if (!known.has(match[1])) fail()
    count++
    if (match[1].startsWith('native:')) native = true
  }
  // Broken evidence markers cannot disappear through a permissive Markdown pass.
  const stripped = content.replace(/(?<!\[)\[\[([^\[\]]+)\]\](?!\])/g, '')
  if (!count || stripped.includes('[[') || stripped.includes(']]') || report.binding.coverage.some(c => c.selected_records > 0) && !native) fail()
}
export async function validateConnectedReportRead(value, context) {
  try {
    value = detach(value); fields(value, 'schema_version report content'); if (value.schema_version !== 1 || enc.encode(wireJson(value)).length > 4194304) fail()
    const report = await validateConnectedReportResult(value.report, context); if (report.state !== 'completed') fail()
    text(value.content, 2097152, 1); const bytes = enc.encode(value.content), file = report.manifest.files.find(f => f.name === 'full_report.md')
    if (bytes.length !== file.size || await sha256(bytes) !== file.sha256) fail()
    references(value.content, report)
    return { schema_version: 1, report, content: value.content }
  } catch { fail() }
}
export async function validateConnectedReportDownload(value, { payload, known, graph } = {}) {
  try {
    value = detach(value); payload = detach(payload); known = detach(known)
    connectedReportPayload(payload, 'download'); const admitted = await validateConnectedReportResult(known, { graph, payload: reportIdentity(known), known })
    fields(value, 'schema_version report_id plan_sha256 receipt_sha256 artifact')
    if (enc.encode(wireJson(value)).length > 4194304 || admitted.state !== 'completed' || value.schema_version !== 1 || value.report_id !== payload.report_id || value.plan_sha256 !== payload.plan_sha256 || value.report_id !== admitted.report_id || value.plan_sha256 !== admitted.plan_sha256 || value.receipt_sha256 !== admitted.receipt_sha256) fail()
    const a = value.artifact, name = reportArtifactName(payload), file = admitted.manifest.files.find(f => f.name === name)
    fields(a, 'name mime size sha256 content_base64'); if (!file || a.name !== name || a.mime !== (name.endsWith('.md') ? 'text/markdown' : 'application/json') || a.size !== file.size || a.sha256 !== file.sha256) fail()
    if (typeof a.content_base64 !== 'string' || a.content_base64.length > 2796204 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(a.content_base64)) fail()
    const binary = atob(a.content_base64); if (btoa(binary) !== a.content_base64 || binary.length !== a.size) fail()
    const bytes = Uint8Array.from(binary, c => c.charCodeAt(0)); if (await sha256(bytes) !== a.sha256) fail()
    new TextDecoder('utf-8', { fatal: true }).decode(bytes)
    return value
  } catch { fail() }
}
// Parse original integer lexemes before Number can round them. Wide integers
// remain BigInt and are admitted only at the ceiling field by result validation.
// There is no BigInt in a request body and no global JSON/prototype patch.
export function parseConnectedReportJson(raw) {
  text(raw, 4194304); let i = 0
  const space = () => { while (i < raw.length && /[ \t\r\n]/.test(raw[i])) i++ }
  function string() {
    if (raw[i] !== '"') fail()
    const start = i++
    while (i < raw.length) {
      if (raw[i] === '\\') { i += 2; continue }
      if (raw[i++] === '"') { const v = JSON.parse(raw.slice(start, i)); text(v, 4194304); return v }
    }
    fail()
  }
  function value(depth) {
    if (depth > 16) fail(); space()
    if (raw[i] === '"') return string()
    if (raw[i] === '{') {
      i++; space(); const entries = [], keys = new Set()
      if (raw[i] === '}') { i++; return {} }
      while (true) {
        space(); const key = string(); if (keys.has(key) || keys.size >= 100) fail(); keys.add(key)
        space(); if (raw[i++] !== ':') fail(); entries.push([key, value(depth + 1)]); space()
        const separator = raw[i++]; if (separator === '}') return Object.fromEntries(entries); if (separator !== ',') fail()
      }
    }
    if (raw[i] === '[') {
      i++; space(); const entries = []; if (raw[i] === ']') { i++; return entries }
      while (true) { if (entries.length >= 2048) fail(); entries.push(value(depth + 1)); space(); const separator = raw[i++]; if (separator === ']') return entries; if (separator !== ',') fail() }
    }
    for (const [token, v] of [['null', null], ['true', true], ['false', false]]) if (raw.startsWith(token, i)) { i += token.length; return v }
    const token = raw.slice(i).match(/^(?:0|[1-9][0-9]*)/)?.[0]
    if (!token || token.length > 19) fail(); i += token.length
    const n = BigInt(token); if (n > 9223372036854775807n) fail()
    return n <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(n) : n
  }
  const result = value(0); space(); if (i !== raw.length) fail()
  return detach(result)
}
export function createConnectedReportsChannel({ fetchImpl, deadlineMs, connection, denied }) {
  let epoch = 0, active = null
  const plans = new Map(), attempted = new Set()
  // Fences contain no credentials/content and survive clearing and reconnect.
  function clear() { epoch++; active?.abort(); active = null; plans.clear() }
  function cancel() { epoch++; active?.abort(); active = null }
  async function request(method, input, selection = null, suppliedKnown = null) {
    let current, raw, payload, key, known
    try {
      current = connection(); if (!current) fail('disconnected')
      raw = connectedReportPayload(input, method); payload = JSON.parse(raw)
      selection = selection ? detach(selection) : null; suppliedKnown = suppliedKnown ? detach(suppliedKnown) : null
      key = `${current.origin}/${current.graph}/${payload.report_id}`
      const stored = plans.get(key) || null
      if (stored && suppliedKnown && (!same(pickIdentity(stored), pickIdentity(suppliedKnown)) || stored.plan_sha256 !== suppliedKnown.plan_sha256 || stored.receipt && (!same(stored.receipt, suppliedKnown.receipt) || !same(stored.manifest, suppliedKnown.manifest)))) fail('invalid_request')
      known = stored || suppliedKnown
      if (method === 'start' && (attempted.has(key) || !known || known.state !== 'planned' || !known.authorization.model_calls_enabled || !known.authorization.budget_configured || !known.ceiling_microusd)) fail('conflict')
      if (method === 'plan' && (attempted.has(key) || known)) fail('conflict')
      if (method === 'download' && (!known || known.state !== 'completed')) fail('invalid_request')
      if (known && (known.report_id !== payload.report_id || method !== 'plan' && known.plan_sha256 !== payload.plan_sha256)) fail('invalid_request')
      if (!stored && plans.size >= 100 || method === 'start' && attempted.size >= 10000) fail('busy')
    } catch (e) { if (e instanceof WorkbenchError) e.requestSent = false; throw e }
    cancel(); const life = epoch, controller = new AbortController(); active = controller
    let timedOut = false, reader, responseBody, requestSent = false
    const timer = setTimeout(() => { timedOut = true; controller.abort() }, deadlineMs)
    const owned = () => { if (epoch !== life || controller.signal.aborted) fail(timedOut ? 'deadline' : 'cancelled') }
    const guarded = promise => new Promise((resolve, reject) => {
      const aborted = () => reject(new WorkbenchError(timedOut ? 'deadline' : 'cancelled'))
      if (controller.signal.aborted) { aborted(); return }
      controller.signal.addEventListener('abort', aborted, { once: true })
      Promise.resolve(promise).then(v => { if (controller.signal.aborted && v?.body?.cancel) { try { void v.body.cancel().catch(() => {}) } catch { /* discarded */ } } resolve(v) }, reject).finally(() => controller.signal.removeEventListener('abort', aborted))
    })
    try {
      if (method === 'plan') { selection = await guarded(nativeObservationSelection(selection, current.graph)); owned(); if (selection.launch.request.run_id !== payload.launch_id || selection.launch.launch_sha256 !== payload.launch_sha256) fail('invalid_request'); windows(payload.native_windows, selection.launch.request.platforms) }
      if (known) { known = await guarded(validateConnectedReportResult(known, { graph: current.graph, payload: reportIdentity(known), known })); owned() }
      if (method === 'start' || method === 'cancel') attempted.add(key)
      requestSent = true
      const response = await guarded(fetchImpl(`${current.origin}/api/connected-report/${method}/${current.graph}`, { method: 'POST', headers: { Authorization: `Bearer ${current.token}`, 'Content-Type': 'application/json' }, body: raw, signal: controller.signal, redirect: 'error', credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer' }))
      responseBody = response.body; owned()
      if ([401, 403].includes(response.status)) { denied(); fail(response.status === 401 ? 'unauthorized' : 'origin_denied') }
      if ([404, 501, 503].includes(response.status) && !/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') || '')) fail('report_unavailable')
      if (response.redirected || response.type === 'opaqueredirect' || !/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') || '') || !response.body?.getReader) fail()
      const cap = ['read', 'download'].includes(method) ? 4194304 : 262272, length = response.headers.get('content-length')
      if (length && (!/^[0-9]+$/.test(length) || !Number.isSafeInteger(Number(length)) || Number(length) > cap)) fail('result_too_large')
      reader = response.body.getReader(); const decoder = new TextDecoder('utf-8', { fatal: true }); let size = 0, body = ''
      while (true) { const chunk = await guarded(reader.read()); owned(); if (chunk.done) break; size += chunk.value.byteLength; if (size > cap) fail('result_too_large'); body += decoder.decode(chunk.value, { stream: true }) }
      body += decoder.decode(); owned(); const value = parseConnectedReportJson(body)
      if (!response.ok || value?.success !== true) { fields(value, 'success error'); fields(value.error, 'code'); if (value.success !== false || REPORT_ERROR_STATUS[value.error.code] !== response.status) fail(); const denial = new WorkbenchError(value.error.code); denial.replyConfirmed = true; throw denial }
      fields(value, 'success data')
      const context = { graph: current.graph, payload: method === 'download' ? reportIdentity(known) : payload, known, selection: method === 'plan' ? selection : null, planning: method === 'plan' }
      const result = await guarded(method === 'read' ? validateConnectedReportRead(value.data, context) : method === 'download' ? validateConnectedReportDownload(value.data, { graph: current.graph, payload, known }) : validateConnectedReportResult(value.data, context)); owned()
      const record = method === 'read' ? result.report : method === 'download' ? known : result
      plans.set(key, detach(record)); if (record.state !== 'planned' || method === 'cancel' || method === 'status' && !known) attempted.add(key)
      return result
    } catch (e) {
      if (e instanceof WorkbenchError && ['unauthorized', 'origin_denied'].includes(e.code)) throw e
      try { owned() } catch (ownershipError) { if (ownershipError instanceof WorkbenchError) ownershipError.requestSent = requestSent; throw ownershipError }
      if (e instanceof WorkbenchError) { e.requestSent = requestSent; throw e }
      const mapped = new WorkbenchError(e instanceof SyntaxError || e instanceof TypeError && reader ? 'invalid_reply' : 'transport_failure')
      mapped.requestSent = requestSent
      throw mapped
    } finally {
      clearTimeout(timer); controller.abort()
      if (reader) { try { void reader.cancel().catch(() => {}) } catch { /* discarded */ } reader.releaseLock() }
      else if (responseBody) { try { void responseBody.cancel().catch(() => {}) } catch { /* discarded */ } }
      if (active === controller) active = null
    }
  }
  return { plan: (p, s) => request('plan', p, s), start: (p, k) => request('start', p, null, k), status: (p, k) => request('status', p, null, k), cancelReport: (p, k) => request('cancel', p, null, k), read: (p, k) => request('read', p, null, k), download: (p, k) => request('download', p, null, k), clear, cancel }
}

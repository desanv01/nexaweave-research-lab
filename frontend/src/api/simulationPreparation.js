// Public preparation contract only; no credentials, storage or model access.
import { WorkbenchError, awareTimestamp } from './workbench.js'
import { sha256 } from './sourceLibrary.js'
const enc = new TextEncoder()
const bad = (code = 'invalid_reply') => { throw new WorkbenchError(code) }
const fields = (v, keys) => { if (!v || typeof v !== 'object' || Array.isArray(v) || Object.getPrototypeOf(v) !== Object.prototype || Object.keys(v).sort().join('|') !== keys.split(' ').sort().join('|')) bad() }
const uuid = v => { if (typeof v !== 'string' || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(v)) bad() }
const hash = v => { if (typeof v !== 'string' || !/^[0-9a-f]{64}$/.test(v)) bad() }
const int = (v, max, min = 0) => { if (!Number.isSafeInteger(v) || v < min || v > max) bad() }
const text = (v, cap, min = 0) => { if (typeof v !== 'string' || v.includes('\0') || v.length > cap * 2 || [...v].length < min || [...v].length > cap || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(v)) bad() }
const pythonWhitespace = /^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]*$/u
const nonblank = (v, cap) => { text(v, cap, 1); if (pythonWhitespace.test(v)) bad() }
const label = v => { if (typeof v !== 'string' || !/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(v) || ['Entity', 'Node'].includes(v)) bad() }
const list = (v, max, each, min = 0) => { if (!Array.isArray(v) || v.length > max || v.length < min) bad(); for (let i = 0; i < v.length; i++) each(v[i], i) }
export const PREPARATION_CODES = new Set('invalid_request not_found unauthorized origin_denied conflict busy tombstoned empty_selection result_too_large preparation_unavailable model_calls_disabled budget_denied preparation_failed preparation_cancelled preparation_uncertain timeout transport_failure invalid_reply internal_error'.split(' '))
const canonical = v => Array.isArray(v) ? v.map(canonical) : v && typeof v === 'object' ? Object.fromEntries(Object.keys(v).sort().map(k => [k, canonical(v[k])])) : v
// Python ensure_ascii=True escapes each UTF16 unit, including both emoji surrogates.
const canonicalAscii = v => JSON.stringify(canonical(v)).replace(/[\u007f-\uffff]/g, unit => '\\u' + unit.charCodeAt(0).toString(16).padStart(4, '0'))
const equal = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b))
function detach(v, depth = 0) {
  if (depth > 16) bad()
  if (v === null || typeof v === 'boolean') return v
  if (typeof v === 'number') { if (!Number.isFinite(v)) bad(); return v }
  if (typeof v === 'string') { text(v, 262144); return v }
  if (Array.isArray(v)) { if (v.length > 10000) bad(); return Array.from(v, item => detach(item, depth + 1)) }
  if (!v || typeof v !== 'object' || Object.getPrototypeOf(v) !== Object.prototype || Object.keys(v).length > 1000) bad()
  return Object.fromEntries(Object.entries(v).map(([key, item]) => { text(key, 256); return [key, detach(item, depth + 1)] }))
}
export function preparationOptions(v) {
  fields(v, 'types max_agents seed platforms max_rounds simulation_requirement')
  if (v.types !== null) { list(v.types, 50, label); if (new Set(v.types).size !== v.types.length) bad() }
  int(v.max_agents, 100, 1); int(v.seed, 4294967295); int(v.max_rounds, 24, 1)
  if (![['twitter'], ['reddit'], ['twitter', 'reddit']].some(p => equal(p, v.platforms))) bad()
  text(v.simulation_requirement, 8192, 1)
  if (pythonWhitespace.test(v.simulation_requirement) || enc.encode(v.simulation_requirement).length > 32768) bad()
  return JSON.parse(JSON.stringify(v))
}
export function preparationPayload(v, planning = false) {
  try {
    fields(v, planning ? 'schema_version operation_id source_revision options' : 'schema_version operation_id plan_sha256')
    if (v.schema_version !== 1) bad()
    uuid(v.operation_id)
    if (planning) { uuid(v.source_revision); preparationOptions(v.options) } else hash(v.plan_sha256)
    const raw = JSON.stringify(v)
    if (enc.encode(raw).length > 65536) bad()
    return raw
  } catch { bad('invalid_request') }
}
export function preparationIdentity(plan) { return { schema_version: 1, operation_id: plan.operation_id, plan_sha256: plan.plan_sha256 } }
export function newPreparationId() { if (!globalThis.crypto?.randomUUID) bad('invalid_request'); return globalThis.crypto.randomUUID() }
export function preparationInteger(v, max, min = 0) {
  if (typeof v !== 'string' || !/^(0|[1-9][0-9]*)$/.test(v)) bad('invalid_request')
  const number = Number(v)
  try { int(number, max, min); return number } catch { bad('invalid_request') }
}
export function preparationSource(v) {
  fields(v, 'project_id source_revision source_name text_sha256 byte_length codepoint_length recorded_at')
  uuid(v.project_id); uuid(v.source_revision); nonblank(v.source_name, 256); hash(v.text_sha256)
  int(v.byte_length, 1048576, 1); int(v.codepoint_length, 32768, 1)
  if (v.byte_length < v.codepoint_length || v.byte_length > 4 * v.codepoint_length) bad()
  awareTimestamp(v.recorded_at)
  return JSON.parse(JSON.stringify(v))
}
const immutable = 'schema_version display_graph_id scope project_revision operation_id source options actors projection_sha256 plan_sha256 graph_snapshot_atomic simulation_executed'.split(' ')
const fingerprintFields = 'schema_version display_graph_id scope project_revision operation_id source options actors projection_sha256'.split(' ')
export async function validatePreparationResult(value, { graph, payload, source, known, planning = false } = {}) {
  try {
    // Freeze caller-owned replies before the asynchronous native manifest digest.
    value = detach(value)
    if (known) known = detach(known)
    payload = detach(payload)
    if (source) source = detach(source)
    if (enc.encode(JSON.stringify(value)).length > 262144) bad()
    fields(value, 'schema_version display_graph_id scope project_revision operation_id source options actors projection_sha256 plan_sha256 state progress error_code authorization receipt graph_snapshot_atomic model_calls_started simulation_executed')
    if (typeof graph !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(graph) || value.display_graph_id !== graph || value.schema_version !== 1 || value.graph_snapshot_atomic !== false || value.simulation_executed !== false || typeof value.model_calls_started !== 'boolean') bad()
    fields(value.scope, 'schema_version workspace_id project_id graph_id run_id branch_id layer')
    if (value.scope.schema_version !== 1 || value.scope.layer !== 'source' || value.scope.run_id !== null || value.scope.branch_id !== null) bad()
    for (const k of ['workspace_id', 'project_id', 'graph_id']) uuid(value.scope[k])
    int(value.project_revision, Number.MAX_SAFE_INTEGER, 1); uuid(value.operation_id)
    fields(value.source, 'source_revision source_name source_sha256'); uuid(value.source.source_revision); nonblank(value.source.source_name, 256); hash(value.source.source_sha256)
    preparationOptions(value.options); hash(value.projection_sha256); hash(value.plan_sha256)
    list(value.actors, value.options.max_agents, (actor, index) => {
      fields(actor, 'source_entity_uuid name labels'); uuid(actor.source_entity_uuid); nonblank(actor.name, 1024)
      list(actor.labels, 50, label, 1)
      if (new Set(actor.labels).size !== actor.labels.length || index && value.actors[index - 1].source_entity_uuid >= actor.source_entity_uuid || value.options.types?.length && !actor.labels.some(l => value.options.types.includes(l))) bad()
    }, 1)
    const stages = { planned: ['planned'], queued: ['queued'], preparing: ['reading', 'generating_profiles', 'generating_config', 'publishing'], ready: ['ready'], failed: ['failed'], cancelled: ['cancelled'], uncertain: ['uncertain'] }
    fields(value.progress, 'stage completed total'); int(value.progress.completed, 100)
    if (value.progress.total !== 100 || !Object.hasOwn(stages, value.state) || !stages[value.state].includes(value.progress.stage)) bad()
    if (['planned', 'queued'].includes(value.state) && (value.model_calls_started || value.progress.completed !== 0)) bad()
    if (['generating_config', 'publishing'].includes(value.progress.stage) && !value.model_calls_started) bad()
    if (['failed', 'cancelled', 'uncertain'].includes(value.state)) { if (!PREPARATION_CODES.has(value.error_code)) bad() } else if (value.error_code !== null) bad()
    fields(value.authorization, 'model_calls_enabled ceiling_microusd')
    const ceiling = value.authorization.ceiling_microusd
    if (typeof value.authorization.model_calls_enabled !== 'boolean' || ceiling !== null && (typeof ceiling !== 'string' || !/^[1-9][0-9]{0,18}$/.test(ceiling) || ceiling.length === 19 && ceiling > '9223372036854775807') || value.authorization.model_calls_enabled && ceiling === null) bad()
    if (value.state === 'ready') {
      if (value.progress.completed !== 100 || !value.model_calls_started) bad()
      fields(value.receipt, 'simulation_id artifact_sha256 files')
      if (value.receipt.simulation_id !== 'sim_' + value.operation_id.replaceAll('-', '')) bad()
      hash(value.receipt.artifact_sha256)
      const names = ['state.json', 'simulation_config.json', 'source_grounding.json', ...(value.options.platforms.includes('twitter') ? ['twitter_profiles.csv'] : []), ...(value.options.platforms.includes('reddit') ? ['reddit_profiles.json'] : [])]
      list(value.receipt.files, names.length, (file, i) => { fields(file, 'name sha256 size'); if (file.name !== names[i]) bad(); hash(file.sha256); int(file.size, 2097152, 1) }, names.length)
      // Native manifest uses canonical JSON, sorted object keys and compact separators.
      const digest = await sha256(enc.encode(JSON.stringify(canonical({ schema_version: 1, files: value.receipt.files }))))
      if (value.receipt.artifact_sha256 !== digest) bad()
    } else if (value.receipt !== null) bad()
    const identity = Object.fromEntries(fingerprintFields.map(key => [key, value[key]]))
    if (value.plan_sha256 !== await sha256(enc.encode(canonicalAscii(identity)))) bad()
    preparationPayload(payload, planning)
    if (value.operation_id !== payload.operation_id) bad()
    if (planning) {
      preparationSource(source)
      if (value.scope.project_id !== source.project_id || value.source.source_revision !== source.source_revision || value.source.source_revision !== payload.source_revision || value.source.source_sha256 !== source.text_sha256 || value.source.source_name !== source.source_name || !equal(value.options, payload.options) || value.state !== 'planned') bad()
    } else if (!known || value.plan_sha256 !== payload.plan_sha256) bad()
    if (known && immutable.some(k => !equal(value[k], known[k]))) bad()
    if (known?.model_calls_started && !value.model_calls_started || known?.receipt && (value.state !== 'ready' || !equal(value.receipt, known.receipt))) bad()
    if (enc.encode(JSON.stringify(value)).length > 262144) bad()
    return JSON.parse(JSON.stringify(value))
  } catch { bad('invalid_reply') }
}

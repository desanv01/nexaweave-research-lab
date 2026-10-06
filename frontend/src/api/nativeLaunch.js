// Identifier-only native launch contract. No model, provider or storage access.
import { WorkbenchError } from './workbench.js'
import { sha256 } from './sourceLibrary.js'
import { preparationIdentity, validatePreparationResult } from './simulationPreparation.js'
const enc = new TextEncoder()
const bad = (code = 'invalid_reply') => { throw new WorkbenchError(code) }
const fields = (v, keys) => { if (!v || typeof v !== 'object' || Array.isArray(v) || Object.getPrototypeOf(v) !== Object.prototype || Object.keys(v).sort().join('|') !== keys.split(' ').sort().join('|')) bad() }
const uuid = v => { if (typeof v !== 'string' || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(v)) bad() }
const hash = v => { if (typeof v !== 'string' || !/^[0-9a-f]{64}$/.test(v)) bad() }
const int = (v, max, min = 1) => { if (!Number.isSafeInteger(v) || v < min || v > max) bad() }
const text = (v, cap) => { if (typeof v !== 'string' || [...v].length > cap || v.includes('\0') || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(v)) bad() }
const ordered = v => Array.isArray(v) ? v.map(ordered) : v && typeof v === 'object' ? Object.fromEntries(Object.keys(v).sort().map(k => [k, ordered(v[k])])) : v
const ascii = v => JSON.stringify(ordered(v)).replace(/[\u007f-\uffff]/g, unit => '\\u' + unit.charCodeAt(0).toString(16).padStart(4, '0'))
const same = (a, b) => ascii(a) === ascii(b)
function detached(v, depth = 0) {
  if (depth > 16) bad()
  if (v === null || typeof v === 'boolean') return v
  if (typeof v === 'number') { if (!Number.isFinite(v)) bad(); return v }
  if (typeof v === 'string') { text(v, 262144); return v }
  if (Array.isArray(v)) { if (v.length > 10000) bad(); return Array.from(v, item => detached(item, depth + 1)) }
  if (!v || typeof v !== 'object' || Object.getPrototypeOf(v) !== Object.prototype || Object.keys(v).length > 1000) bad()
  return Object.fromEntries(Object.entries(v).map(([k, item]) => { text(k, 256); return [k, detached(item, depth + 1)] }))
}
export const NATIVE_LAUNCH_CODES = new Set('invalid_request not_found unauthorized origin_denied conflict busy tombstoned result_too_large model_calls_disabled budget_denied native_launch_unavailable native_launch_uncertain native_launch_failed invalid_reply internal_error'.split(' '))
export const NATIVE_ERROR_STATUS = Object.freeze({ invalid_request: 400, not_found: 404, unauthorized: 401, origin_denied: 403, conflict: 409, busy: 503, tombstoned: 410, result_too_large: 413, model_calls_disabled: 409, budget_denied: 409, native_launch_unavailable: 503, native_launch_uncertain: 503, native_launch_failed: 503, invalid_reply: 502, internal_error: 500 })
const identityKeys = 'schema_version display_graph_id scope preparation request limits ceiling_microusd model_label'.split(' ')
export const nativeLaunchIdentity = plan => ({ schema_version: 1, launch_id: plan.request.run_id, launch_sha256: plan.launch_sha256 })
export function newNativeLaunchId() { if (!globalThis.crypto?.randomUUID) bad('invalid_request'); return globalThis.crypto.randomUUID() }
export function nativeLaunchPayload(v, planning = false) {
  try {
    fields(v, planning ? 'schema_version launch_id preparation' : 'schema_version launch_id launch_sha256')
    if (v.schema_version !== 1) bad()
    uuid(v.launch_id)
    if (planning) { fields(v.preparation, 'operation_id plan_sha256'); uuid(v.preparation.operation_id); hash(v.preparation.plan_sha256) } else hash(v.launch_sha256)
    const raw = JSON.stringify(v)
    if (enc.encode(raw).length > 4096) bad()
    return raw
  } catch { bad('invalid_request') }
}
export async function nativeReadyPreparation(value, graph) {
  try {
    value = detached(value)
    if (value.state !== 'ready') bad()
    return await validatePreparationResult(value, { graph, payload: preparationIdentity(value), known: value })
  } catch { bad('invalid_reply') }
}
export async function validateNativeLaunchResult(value, { graph, payload, preparation, known, planning = false } = {}) {
  try {
    value = detached(value); payload = detached(payload); preparation = detached(preparation); if (known) known = detached(known)
    const ready = await nativeReadyPreparation(preparation, graph)
    if (enc.encode(JSON.stringify(value)).length > 65536) bad()
    fields(value, 'schema_version display_graph_id scope preparation request limits ceiling_microusd model_label launch_sha256 state error_code authorization workflow receipt cancel_requested cleanup')
    if (typeof graph !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(graph) || value.schema_version !== 1 || value.display_graph_id !== graph || !same(value.scope, ready.scope)) bad()
    fields(value.preparation, 'operation_id plan_sha256 simulation_id artifact_sha256')
    const binding = { operation_id: ready.operation_id, plan_sha256: ready.plan_sha256, simulation_id: ready.receipt.simulation_id, artifact_sha256: ready.receipt.artifact_sha256 }
    if (!same(value.preparation, binding)) bad()
    const r = value.request
    fields(r, 'schema_version principal project_id project_revision simulation_id run_id artifact_sha256 runtime_sha256 platforms seed max_rounds')
    if (r.schema_version !== 1 || typeof r.principal !== 'string' || !/^[\x20-\x7e]{1,128}$/.test(r.principal) || !r.principal.trim()) bad()
    uuid(r.project_id); uuid(r.run_id); int(r.project_revision, 2147483647); hash(r.artifact_sha256); hash(r.runtime_sha256)
    int(r.seed, 4294967295, 0); int(r.max_rounds, 24)
    if (r.project_id !== ready.scope.project_id || r.project_revision !== ready.project_revision || r.simulation_id !== binding.simulation_id || r.artifact_sha256 !== binding.artifact_sha256 || !same(r.platforms, ready.options.platforms) || r.seed !== ready.options.seed || r.max_rounds !== ready.options.max_rounds) bad()
    fields(value.limits, 'max_calls max_input_bytes max_output_tokens max_run_seconds')
    for (const [k, max] of Object.entries({ max_calls: 10000, max_input_bytes: 2097152, max_output_tokens: 4096, max_run_seconds: 600 })) int(value.limits[k], max)
    const ceiling = value.ceiling_microusd
    if (ceiling !== null && (typeof ceiling !== 'string' || !/^[1-9][0-9]{0,18}$/.test(ceiling) || ceiling.length === 19 && ceiling > '9223372036854775807')) bad()
    text(value.model_label, 128)
    if (/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]*$/u.test(value.model_label)) bad()
    fields(value.authorization, 'model_calls_enabled')
    if (typeof value.authorization.model_calls_enabled !== 'boolean' || value.authorization.model_calls_enabled && ceiling === null || typeof value.cancel_requested !== 'boolean') bad()
    if (!['planned', 'queued', 'starting', 'running', 'completed', 'failed', 'cancelled', 'uncertain'].includes(value.state) || value.error_code !== null && !NATIVE_LAUNCH_CODES.has(value.error_code)) bad()
    fields(value.cleanup, 'known pending owner_thread_alive')
    if (typeof value.cleanup.known !== 'boolean' || (value.cleanup.known ? typeof value.cleanup.pending !== 'boolean' || typeof value.cleanup.owner_thread_alive !== 'boolean' : value.cleanup.pending !== null || value.cleanup.owner_thread_alive !== null)) bad()
    const requestFingerprint = await sha256(enc.encode(ascii(r)))
    if (value.workflow !== null) {
      fields(value.workflow, 'workflow_id temporal_run_id native_run_id'); uuid(value.workflow.temporal_run_id)
      if (value.workflow.native_run_id !== r.run_id || value.workflow.workflow_id !== 'mf-native-v1-' + r.run_id.replaceAll('-', '') + '-' + requestFingerprint) bad()
    }
    if (value.receipt !== null) {
      fields(value.receipt, 'run_id attempt_id instance_id request_fingerprint outcome evidence_sha256')
      uuid(value.receipt.attempt_id); uuid(value.receipt.instance_id); hash(value.receipt.evidence_sha256)
      if (!['completed', 'failed', 'cancelled'].includes(value.state) || value.receipt.outcome !== value.state || value.receipt.run_id !== r.run_id || value.receipt.request_fingerprint !== requestFingerprint) bad()
    }
    if (value.state === 'completed' && value.receipt === null) bad()
    hash(value.launch_sha256)
    if (value.launch_sha256 !== await sha256(enc.encode(ascii(Object.fromEntries(identityKeys.map(k => [k, value[k]])))))) bad()
    nativeLaunchPayload(payload, planning)
    if (r.run_id !== payload.launch_id) bad()
    if (planning) { if (payload.preparation.operation_id !== binding.operation_id || payload.preparation.plan_sha256 !== binding.plan_sha256 || !known && value.state !== 'planned') bad() }
    else if (!known || value.launch_sha256 !== payload.launch_sha256) bad()
    if (known && (identityKeys.some(k => !same(value[k], known[k])) || value.launch_sha256 !== known.launch_sha256 || known.cancel_requested && !value.cancel_requested || known.workflow && !same(value.workflow, known.workflow) || known.receipt && (!same(value.receipt, known.receipt) || value.state !== known.state))) bad()
    return value
  } catch { bad('invalid_reply') }
}

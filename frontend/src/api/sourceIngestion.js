// Source graph DTOs only. Credentials and transport remain in workbench.js.
import { WorkbenchError, awareTimestamp } from './workbench.js'
import { sha256 } from './sourceLibrary.js'
const enc = new TextEncoder()
const bad = (code = 'invalid_reply') => { throw new WorkbenchError(code) }
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const hash = /^[0-9a-f]{64}$/
const name = /^[A-Za-z][A-Za-z0-9_]{0,63}$/
const reserved = new Set('uuid name group_id graph_id labels attributes summary created_at valid_at invalid_at expired_at episodes source_node_uuid target_node_uuid fact fact_embedding name_embedding entity_edges source content dict json copy construct schema schema_json parse_obj parse_raw parse_file validate update_forward_refs from_orm'.split(' '))
function shape(v, keys) { if (!v || typeof v !== 'object' || Array.isArray(v) || Object.keys(v).sort().join('|') !== keys.split(' ').sort().join('|')) bad() }
function id(v) { if (typeof v !== 'string' || !uuid.test(v)) bad() }
function digest(v) { if (typeof v !== 'string' || !hash.test(v)) bad() }
function int(v, max = Number.MAX_SAFE_INTEGER, min = 0) { if (!Number.isSafeInteger(v) || v < min || v > max) bad() }
function description(v) { if (typeof v !== 'string' || !v.trim() || Array.from(v).length > 500 || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(v)) bad() }
export function ontologyNameIssue(value, seen, kind) {
  if (typeof value !== 'string' || !name.test(value)) return 'name'
  if (kind === 'entity' && ['Entity', 'Episodic', 'Community', 'Saga'].includes(value) || kind === 'attribute' && (reserved.has(value.toLowerCase()) || value.startsWith('model_'))) return 'reserved'
  if (seen.has(value)) return 'duplicate'
  return ''
}
export function ontologyDescriptionIssue(value) { try { description(value); return '' } catch { return 'description' } }
function array(v, max, min = 0) { if (!Array.isArray(v) || v.length < min || v.length > max) bad() }
export function operationId(v) { try { id(v); return v } catch { bad('invalid_request') } }
export function freshId() { if (!globalThis.crypto?.randomUUID) bad('crypto_unavailable'); return globalThis.crypto.randomUUID() }
export function validateOntology(v) {
  try {
    shape(v, 'schema_version revision entity_types edge_types'); id(v.revision)
    if (v.schema_version !== 1) bad()
    array(v.entity_types, 50, 1); array(v.edge_types, 50)
    const entities = new Set(), edges = new Set()
    for (const [items, names, edge] of [[v.entity_types, entities, false], [v.edge_types, edges, true]]) for (const item of items) {
      shape(item, edge ? 'name description attributes source_targets' : 'name description attributes')
      if (ontologyNameIssue(item.name, names, edge ? 'edge' : 'entity')) bad()
      names.add(item.name); description(item.description); array(item.attributes, 20)
      const attrs = new Set()
      for (const attr of item.attributes) {
        shape(attr, 'name type description')
        if (ontologyNameIssue(attr.name, attrs, 'attribute') || !['text', 'integer', 'number', 'boolean'].includes(attr.type)) bad()
        attrs.add(attr.name); description(attr.description)
      }
      if (edge) {
        array(item.source_targets, 100, 1)
        const pairs = new Set()
        for (const pair of item.source_targets) {
          shape(pair, 'source target')
          const key = `${pair.source}/${pair.target}`
          if (!entities.has(pair.source) || !entities.has(pair.target) || pairs.has(key)) bad()
          pairs.add(key)
        }
      }
    }
    return v
  } catch { bad('invalid_request') }
}
export function ingestionPayload(v, status = false) {
  try {
    shape(v, status ? 'operation_id' : 'schema_version source_revision operation_id ontology'); id(v.operation_id)
    if (!status) { if (v.schema_version !== 1) bad(); id(v.source_revision); validateOntology(v.ontology) }
    const raw = JSON.stringify(v)
    if (enc.encode(raw).length > 262144) bad()
    return raw
  } catch { bad('invalid_request') }
}
export function validateIngestionScope(v) {
  shape(v, 'schema_version workspace_id project_id graph_id run_id branch_id layer')
  if (v.schema_version !== 1 || v.layer !== 'source' || v.run_id !== null || v.branch_id !== null) bad()
  for (const key of ['workspace_id', 'project_id', 'graph_id']) id(v[key])
  return v
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b)
const sameScope = (a, b) => Object.keys(a).every(k => a[k] === b[k])
function canonical(v) {
  if (Array.isArray(v)) return v.map(canonical)
  if (v && typeof v === 'object') return Object.fromEntries(Object.keys(v).sort().map(k => [k, canonical(v[k])]))
  return v
}
export async function ingestionFingerprint(scope, inspected, payload) {
  validateIngestionScope(scope); ingestionPayload(payload)
  const stamp = inspected.source.recorded_at
  awareTimestamp(stamp)
  // Pydantic preserves microseconds, converts UTC suffix only, and omits an
  // all-zero fractional part. Nonzero fractions serialize with six digits.
  const match = /^(.*T\d\d:\d\d:\d\d)(?:\.(\d{1,6}))?(Z|[+-]\d\d:\d\d)$/.exec(stamp)
  if (!match || match[3] !== 'Z' && (Number(match[3].slice(1, 3)) > 23 || Number(match[3].slice(4)) > 59)) bad()
  const fraction = (match[2] || '').padEnd(6, '0')
  const recorded = match[1] + (fraction === '000000' ? '' : '.' + fraction) + (match[3] === '+00:00' ? 'Z' : match[3])
  const source = { schema_version: 1, source_revision: payload.source_revision, source_sha256: inspected.source.text_sha256, ontology_revision: payload.ontology.revision, operation_id: payload.operation_id, source_kind: 'document', content: inspected.text, source_name: inspected.source.source_name, recorded_at: recorded, asserted_valid_at: null, evidence_ids: inspected.passages.map(p => p.evidence_id) }
  return sha256(enc.encode(JSON.stringify(canonical({ scope, source, ontology: payload.ontology }))))
}
export async function ingestionIdentities(scope, operation) {
  validateIngestionScope(scope); id(operation)
  const canonical = Object.fromEntries(Object.keys(scope).filter(k => k !== 'schema_version').sort().map(k => [k, scope[k]]))
  const group = 'mf1_' + await sha256(enc.encode(JSON.stringify(canonical)))
  if (!globalThis.crypto?.subtle) bad('crypto_unavailable')
  const namespace = Uint8Array.from('6ba7b8119dad11d180b400c04fd430c8'.match(/../g), x => parseInt(x, 16))
  // Retained v1 UUIDv5 domain: changing these bytes would change saved episode IDs.
  const text = enc.encode(`mirofish:episode:v1:${group}:${operation}`), input = new Uint8Array(16 + text.length)
  input.set(namespace); input.set(text, 16)
  const bytes = new Uint8Array(await globalThis.crypto.subtle.digest('SHA-1', input)).slice(0, 16)
  bytes[6] = bytes[6] & 15 | 80; bytes[8] = bytes[8] & 63 | 128
  const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('')
  return { group_id: group, episode_id: `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}` }
}
export async function validateIngestionResult(method, v, context) {
  try {
    const planning = method === 'ingestionPlan'
    const common = 'schema_version scope operation_id episode_id fingerprint evidence_ids graph_ingestion_executed model_calls_made actual_usage_microusd'
    shape(v, common + (planning ? ' source_revision source_sha256 source_byte_length source_codepoint_length ontology_revision eligibility_codepoint_limit spending_authorized' : ' state budget_state ceiling_microusd receipt'))
    validateIngestionScope(v.scope); id(v.operation_id); id(v.episode_id); digest(v.fingerprint)
    if (v.schema_version !== 1 || v.operation_id !== context.payload.operation_id || v.actual_usage_microusd !== null || context.scope && !sameScope(context.scope, v.scope) || context.project && v.scope.project_id !== context.project) bad()
    const identities = await ingestionIdentities(v.scope, v.operation_id)
    if (v.episode_id !== identities.episode_id) bad()
    array(v.evidence_ids, 100, planning || method === 'ingestionExecute' ? 1 : 0); v.evidence_ids.forEach(id)
    if (new Set(v.evidence_ids).size !== v.evidence_ids.length) bad()
    if (planning) {
      const source = context.inspected?.source
      if (!source || v.scope.project_id !== source.project_id || v.source_revision !== context.payload.source_revision || v.source_revision !== source.source_revision || v.ontology_revision !== context.payload.ontology.revision || v.source_sha256 !== source.text_sha256 || v.source_byte_length !== source.byte_length || v.source_codepoint_length !== source.codepoint_length || v.eligibility_codepoint_limit !== 32768 || v.spending_authorized !== false || v.graph_ingestion_executed !== false || v.model_calls_made !== false) bad()
      digest(v.source_sha256); id(v.source_revision); id(v.ontology_revision); int(v.source_codepoint_length, 32768, 1); int(v.source_byte_length, 4 * v.source_codepoint_length, v.source_codepoint_length)
      if (!same(v.evidence_ids, context.inspected.passages.map(p => p.evidence_id))) bad()
      if (v.fingerprint !== await ingestionFingerprint(v.scope, context.inspected, context.payload)) bad()
    } else {
      if (!['pending', 'running', 'completed', 'uncertain', 'cancelled', 'failed_no_effect', 'not_admitted'].includes(v.state) || !['reserved', 'started', 'settled', 'uncertain', 'released', 'not_reserved'].includes(v.budget_state) || typeof v.graph_ingestion_executed !== 'boolean' || v.model_calls_made !== null) bad()
      if (v.budget_state === 'not_reserved') { if (v.ceiling_microusd !== null) bad() } else int(v.ceiling_microusd, Number.MAX_SAFE_INTEGER, 1)
      if (v.budget_state === 'settled' && v.state !== 'completed' || v.state === 'not_admitted' && v.budget_state === 'not_reserved') bad()
      if (v.state === 'completed') {
        shape(v.receipt, 'group_id episode_id fingerprint evidence_ids')
        if (!v.evidence_ids.length || v.graph_ingestion_executed !== true || v.receipt.group_id !== identities.group_id || v.receipt.episode_id !== v.episode_id || v.receipt.fingerprint !== v.fingerprint || !same(v.receipt.evidence_ids, v.evidence_ids)) bad()
      } else if (v.receipt !== null || v.graph_ingestion_executed !== false) bad()
      if (method === 'ingestionExecute' && (v.state !== 'completed' || v.budget_state !== 'settled')) bad()
      const known = context.known
      if (known && (!sameScope(known.scope, v.scope) || known.operation_id !== v.operation_id || known.episode_id !== v.episode_id || known.fingerprint !== v.fingerprint || !same(known.evidence_ids, v.evidence_ids))) bad()
    }
    if (enc.encode(JSON.stringify(v)).length > 262144) bad()
    return v
  } catch (e) { if (e.code === 'crypto_unavailable') throw e; bad() }
}

import { WorkbenchError } from './workbench.js'

const MAX_BYTES = 4194304
export const MAX_PROJECT_REVISION = 2147483647
export const REVIEWED_ONTOLOGY_ERROR_STATUS = Object.freeze({
  invalid_request: 400, unauthorized: 401, origin_denied: 403, source_denied: 403,
  not_found: 404, conflict: 409, tombstoned: 410, result_too_large: 413,
  limit_exceeded: 413, busy: 503, timeout: 504, source_unavailable: 503,
  outcome_unknown: 503, internal_error: 500
})
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![\s\S])/
const entityName = /^[A-Z][A-Za-z0-9]*(?![\s\S])/
const edgeName = /^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*(?![\s\S])/
const attributeName = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)*(?![\s\S])/
const reservedAttributes = ['uuid', 'name', 'group_id', 'graph_id', 'name_embedding', 'summary', 'created_at']
const nonWhitespace = /[^\u0009-\u000d\u001c-\u001f\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/u
const invalidText = /[\u0000\uD800-\uDFFF]/u
const encoder = new TextEncoder()
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const fields = (value, required, optional = []) => object(value)
  && required.every(key => Object.hasOwn(value, key))
  && Object.keys(value).every(key => required.includes(key) || optional.includes(key))
const matches = (value, pattern) => typeof value === 'string' && pattern.test(value)
const text = value => typeof value === 'string' && nonWhitespace.test(value) && !invalidText.test(value)
const list = (value, min = 0) => Array.isArray(value) && value.length >= min && value.length <= 10
const revision = (value, max = MAX_PROJECT_REVISION) => Number.isInteger(value) && value >= 1 && value <= max
const fail = code => { throw new WorkbenchError(code) }

function typeNames(items, edge, code) {
  if (!list(items)) fail(code)
  const names = new Set()
  for (const item of items) {
    const required = ['name', 'description', 'attributes']
    if (edge) required.push('source_targets')
    if (!fields(item, required, edge ? [] : ['examples'])
        || !matches(item.name, edge ? edgeName : entityName) || names.has(item.name)
        || !text(item.description) || [...item.description].length > 100 || !list(item.attributes, 1)) fail(code)
    names.add(item.name)
    const attributes = new Set(reservedAttributes)
    for (const attr of item.attributes) {
      if (!fields(attr, ['name', 'type', 'description']) || !matches(attr.name, attributeName)
          || attributes.has(attr.name) || attr.type !== 'text' || !text(attr.description)) fail(code)
      attributes.add(attr.name)
    }
    if (Object.hasOwn(item, 'examples') && (!Array.isArray(item.examples) || !item.examples.every(text))) fail(code)
  }
  return names
}

export function validateReviewedOntology(value, code = 'invalid_request') {
  if (!fields(value, ['entity_types', 'edge_types'])) fail(code)
  const endpoints = typeNames(value.entity_types, false, code).add('Entity')
  typeNames(value.edge_types, true, code)
  for (const edge of value.edge_types) {
    if (!list(edge.source_targets, 1)) fail(code)
    const pairs = new Set()
    for (const pair of edge.source_targets) {
      if (!fields(pair, ['source', 'target']) || !endpoints.has(pair.source) || !endpoints.has(pair.target)) fail(code)
      const key = JSON.stringify([pair.source, pair.target])
      if (pairs.has(key)) fail(code)
      pairs.add(key)
    }
  }
}

export function sameOntologyValue(left, right) {
  if (left === right) return true
  if (Array.isArray(left)) return Array.isArray(right) && left.length === right.length
    && left.every((value, index) => sameOntologyValue(value, right[index]))
  return object(left) && object(right) && Object.keys(left).length === Object.keys(right).length
    && Object.keys(left).every(key => Object.hasOwn(right, key) && sameOntologyValue(left[key], right[key]))
}

function validScope(value) {
  return fields(value, ['schema_version', 'workspace_id', 'project_id', 'graph_id', 'run_id', 'branch_id', 'layer'])
    && value.schema_version === 1 && ['workspace_id', 'project_id', 'graph_id'].every(key => matches(value[key], uuid))
    && value.run_id === null && value.branch_id === null && value.layer === 'source'
}

export function reviewedOntologyRequest(method, payload, known, project) {
  if (project !== undefined && !matches(project, uuid)) fail('invalid_request')
  if (known && (!validScope(known.scope) || !revision(known.project_revision)
      || project !== undefined && project !== known.scope.project_id)) fail('invalid_request')
  const scope = known ? { ...known.scope } : undefined
  const priorRevision = known?.project_revision
  if (method === 'sourceOntologyLoad') return { scope, project, priorRevision }
  if (!known || !fields(payload, ['schema_version', 'expected_revision', 'ontology'])
      || payload.schema_version !== 1 || !revision(payload.expected_revision, MAX_PROJECT_REVISION - 1)
      || payload.expected_revision !== priorRevision) fail('invalid_request')
  validateReviewedOntology(payload.ontology)
  const raw = JSON.stringify(payload)
  if (encoder.encode(raw).length > MAX_BYTES) fail('limit_exceeded')
  return { raw, scope, project, priorRevision, submitted: JSON.parse(raw).ontology }
}

export function validateReviewedOntologyResult(value, request) {
  if (!fields(value, ['schema_version', 'scope', 'project_revision', 'ontology'])
      || value.schema_version !== 1 || !validScope(value.scope) || !revision(value.project_revision)
      || (request.project !== undefined && value.scope.project_id !== request.project)
      || (request.scope && !sameOntologyValue(value.scope, request.scope))
      || (request.priorRevision !== undefined && value.project_revision < request.priorRevision)
      || encoder.encode(JSON.stringify(value)).length > MAX_BYTES) fail('invalid_reply')
  if (value.ontology !== null) validateReviewedOntology(value.ontology, 'invalid_reply')
  if (request.submitted && (value.project_revision !== request.priorRevision + 1
      || !sameOntologyValue(value.ontology, request.submitted))) fail('invalid_reply')
  return value
}

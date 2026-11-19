// Protected read of one actor's profiles from a validated READY preparation.
import { WorkbenchError } from './workbench.js'
import { preparationPayload } from './simulationPreparation.js'

const encoder = new TextEncoder()
const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/
const scalar = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u
const blank = /^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]*$/u
const object = value => value !== null && typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype
const fields = (value, expected) => object(value) && Object.keys(value).length === expected.length &&
  expected.every(key => Object.hasOwn(value, key))
const text = value => typeof value === 'string' && !scalar.test(value)
const nonblank = value => text(value) && !blank.test(value)
const identifier = value => typeof value === 'string' && value.length === 36 && uuid.test(value)
const list = (value, test) => Array.isArray(value) && value.every(test)
const sameList = (value, expected) => Array.isArray(value) && value.length === expected.length &&
  expected.every((item, index) => value[index] === item)
const selector = ['schema_version', 'operation_id', 'plan_sha256', 'source_entity_uuid']
const reject = (code = 'invalid_reply') => { throw new WorkbenchError(code) }

export function preparedProfilePayload(value) {
  if (!fields(value, selector) || !identifier(value.source_entity_uuid)) reject('invalid_request')
  const { source_entity_uuid, ...reference } = value
  return JSON.stringify({ ...JSON.parse(preparationPayload(reference)), source_entity_uuid })
}

// Attributes are retained JSON data, with no prescribed domain keys.
function jsonValue(value, depth = 0) {
  if (depth > 32) return false
  if (value === null || typeof value === 'boolean') return true
  if (typeof value === 'number') return Number.isFinite(value)
  if (typeof value === 'string') return text(value)
  if (Array.isArray(value)) return value.every(item => jsonValue(item, depth + 1))
  return object(value) && Object.entries(value).every(([key, item]) => text(key) && jsonValue(item, depth + 1))
}

export function validatePreparedProfiles(value, graph, ready, payload) {
  const ordinal = ready.actors.findIndex(actor => actor.source_entity_uuid === payload.source_entity_uuid)
  const expected = ready.actors[ordinal]
  if (!expected || !fields(value, [...selector, 'display_graph_id', 'scope', 'project_revision',
    'artifact_sha256', 'actor', 'generated_profiles', 'frozen_support'])) reject()
  const scopeKeys = Object.keys(ready.scope)
  if (selector.some(key => value[key] !== payload[key]) || value.display_graph_id !== graph ||
      value.display_graph_id !== ready.display_graph_id || !fields(value.scope, scopeKeys) ||
      scopeKeys.some(key => value.scope[key] !== ready.scope[key]) ||
      value.project_revision !== ready.project_revision || value.artifact_sha256 !== ready.receipt.artifact_sha256) reject()
  const { actor, generated_profiles: profiles, frozen_support: support } = value
  if (!fields(actor, ['ordinal', 'name', 'labels']) || actor.ordinal !== ordinal ||
      actor.name !== expected.name || !sameList(actor.labels, expected.labels)) reject()
  if (!Array.isArray(profiles) || profiles.length !== ready.options.platforms.length ||
      profiles.some((profile, index) => !fields(profile, ['platform', 'handle', 'description', 'persona']) ||
        profile.platform !== ready.options.platforms[index] ||
        ![profile.handle, profile.description, profile.persona].every(nonblank))) reject()
  if (!fields(support, ['source_entity_uuid', 'labels', 'summary', 'attributes', 'episode_ids', 'evidence_ids', 'facts']) ||
      support.source_entity_uuid !== payload.source_entity_uuid || !list(support.labels, nonblank) ||
      !text(support.summary) || !object(support.attributes) || !jsonValue(support.attributes) ||
      !list(support.episode_ids, identifier) || !list(support.evidence_ids, identifier) ||
      !list(support.facts, fact => fields(fact, ['edge_uuid', 'fact', 'episode_ids', 'evidence_ids']) &&
        identifier(fact.edge_uuid) && text(fact.fact) && list(fact.episode_ids, identifier) &&
        list(fact.evidence_ids, identifier))) reject()
  if (encoder.encode(JSON.stringify(value)).length > 262144) reject('result_too_large')
  return value
}

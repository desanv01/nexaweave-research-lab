// Isolated protected read client. Never share the inherited Axios interceptors.
import { sourcePayload, sourceReadRequest, verifySourceInput, validateSourceResult, sourceOriginalPayload, verifyOriginalPdfInput, validateSourceOriginalResult } from './sourceLibrary.js'
import { ingestionPayload, validateIngestionResult } from './sourceIngestion.js'
import { experimentSelection, validateExperimentCatalog, validateExperimentComparison } from './experimentComparison.js'
import { populationOptions, validatePopulationPreview, validatePopulationExport } from './populationWorkbench.js'
import { preparationPayload, preparationSource, validatePreparationResult, PREPARATION_CODES } from './simulationPreparation.js'
import { nativeLaunchPayload, validateNativeLaunchResult, NATIVE_LAUNCH_CODES, NATIVE_ERROR_STATUS } from './nativeLaunch.js'
import { createNativeObservationsChannel } from './nativeObservations.js'
import { createConnectedReportsChannel } from './connectedReports.js'
import { createConnectedFollowupsChannel } from './connectedFollowups.js'
export class WorkbenchError extends Error {
  constructor(code) { super(code); this.name = 'WorkbenchError'; this.code = code }
}
const fail = (code = 'invalid_reply') => { throw new WorkbenchError(code) }
const encoder = new TextEncoder()
const idPattern = /^[A-Za-z0-9_-]{1,128}$/
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const hashPattern = /^[0-9a-f]{64}$/
const layers = ['source', 'assumption', 'inference', 'simulation', 'analysis']
export const SERVER_CODES = new Set(['invalid_request', 'invalid_configuration', 'invalid_reply', 'not_found', 'evidence_unavailable', 'unauthorized', 'origin_denied', 'busy', 'tombstoned', 'conflict', 'timeout', 'transport_failure', 'result_too_large', 'limit_exceeded', 'inconsistent_graph', 'unsupported', 'empty_selection', 'internal_error', 'research_unavailable', 'research_deadline', 'research_busy', 'dossier_unavailable', 'dossier_deadline', 'dossier_busy'])
export function localOrigin(value) {
  if (typeof value !== 'string' || !/^https?:\/\/(127\.0\.0\.1|\[::1\]):[1-9][0-9]{0,4}$/.test(value)) fail('invalid_connection')
  const port = Number(value.slice(value.lastIndexOf(':') + 1))
  if (port > 65535) fail('invalid_connection')
  // Keep the explicit port, including default ports that URL.origin normalizes.
  return value
}
export function displayId(value) { if (typeof value !== 'string' || !idPattern.test(value)) fail('invalid_connection'); return value }
const obj = v => v !== null && typeof v === 'object' && !Array.isArray(v)
function shape(v, fields) {
  if (!obj(v) || Object.keys(v).sort().join('|') !== fields.split(' ').sort().join('|')) fail()
}
export function uniqueJson(raw) {
  const parsed = JSON.parse(raw)
  // JSON.parse alone silently accepts duplicate keys. Track decoded object keys
  // after syntax parsing, including escaped spellings, with a finite depth.
  const stack = []
  for (let i = 0; i < raw.length; i++) {
    const c = raw[i]
    if (c === '"') {
      const start = i
      for (i++; i < raw.length; i++) { if (raw[i] === '\\') i++; else if (raw[i] === '"') break }
      const top = stack.at(-1)
      if (top?.object && top.key) {
        const key = JSON.parse(raw.slice(start, i + 1))
        if (top.keys.has(key)) fail()
        top.keys.add(key); top.key = false
      }
    } else if (c === '{' || c === '[') {
      stack.push({ object: c === '{', key: c === '{', keys: new Set() })
      if (stack.length > 32) fail()
    } else if (c === '}' || c === ']') stack.pop()
    else if (c === ',' && stack.at(-1)?.object) stack.at(-1).key = true
  }
  return parsed
}
function text(v, cap = 32768, minimum = 0) {
  if (typeof v !== 'string' || [...v].length < minimum || [...v].length > cap || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(v)) fail()
}
function integer(v, cap = Number.MAX_SAFE_INTEGER, min = 0) { if (!Number.isSafeInteger(v) || v < min || v > cap) fail() }
function list(v, cap, each) { if (!Array.isArray(v) || v.length > cap) fail(); v.forEach(each) }
function uuid(v) { if (typeof v !== 'string' || !uuidPattern.test(v)) fail() }
function hash(v) { if (typeof v !== 'string' || !hashPattern.test(v)) fail() }
function bool(v) { if (typeof v !== 'boolean') fail() }
function nullable(v, validate) { if (v !== null) validate(v) }
export function awareTimestamp(v) {
  if (typeof v !== 'string' || v.length > 64 || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)$/.test(v) || !Number.isFinite(Date.parse(v))) fail('invalid_request')
  const datePart = v.slice(0, 10)
  const [year, month, day] = datePart.split('-').map(Number)
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
  const maxDay = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > maxDay || Number(v.slice(11, 13)) > 23 || Number(v.slice(14, 16)) > 59 || Number(v.slice(17, 19)) > 59) fail('invalid_request')
  return v
}
function scope(v) {
  shape(v, 'schema_version workspace_id project_id graph_id run_id branch_id layer')
  if (v.schema_version !== 1 || !layers.includes(v.layer)) fail()
  ;['workspace_id', 'project_id', 'graph_id'].forEach(k => uuid(v[k]))
  ;['run_id', 'branch_id'].forEach(k => nullable(v[k], uuid))
}
const sameScope = (a, b) => Object.keys(a).every(k => a[k] === b[k]) && Object.keys(a).length === Object.keys(b).length
function citation(v) {
  shape(v, 'evidence_id project_id source_revision source_name source_sha256 source_byte_length source_codepoint_length source_recorded_at start end offset_unit excerpt excerpt_sha256 declared_page')
  ;['evidence_id', 'project_id', 'source_revision'].forEach(k => uuid(v[k]))
  text(v.source_name, 256); hash(v.source_sha256); hash(v.excerpt_sha256)
  integer(v.source_byte_length, 1048576, 1); integer(v.source_codepoint_length, 1048576, 1)
  awareTimestamp(v.source_recorded_at); integer(v.start); integer(v.end)
  text(v.excerpt, 32768, 1)
  if (v.offset_unit !== 'unicode_codepoint' || v.end <= v.start || v.end > v.source_codepoint_length || [...v.excerpt].length !== v.end - v.start || encoder.encode(v.excerpt).length > 32768) fail()
  nullable(v.declared_page, n => integer(n, Number.MAX_SAFE_INTEGER, 1))
}
function fact(v, dossier = false) {
  shape(v, dossier ? 'key scope provider_id kind claim_class text predicate source_node_id target_node_id episode_ids evidence_ids reference_keys created_at valid_at invalid_at expired_at claim_support_status reference_integrity' : 'provider_id kind scope claim_class name fact source_node_id target_node_id episode_ids evidence_ids valid_at invalid_at expired_at created_at rank_basis overlap_tokens query_tokens citations unavailable_evidence_ids')
  scope(v.scope); text(v.provider_id, 256, 1); text(v.source_node_id, 256, 1); text(v.target_node_id, 256, 1)
  if (v.kind !== 'edge' || v.claim_class !== v.scope.layer) fail()
  text(v[dossier ? 'text' : 'fact'], 32768, 1); nullable(v[dossier ? 'predicate' : 'name'], n => text(n, 256))
  list(v.episode_ids, 100, n => text(n, 256)); list(v.evidence_ids, 100, uuid)
  ;['valid_at', 'invalid_at', 'expired_at', 'created_at'].forEach(k => nullable(v[k], awareTimestamp))
  if (dossier) {
    if (!/^claim_[0-9a-f]{64}$/.test(v.key) || v.claim_support_status !== 'not_reviewed' || !['resolved', 'partly_unavailable', 'unavailable', 'no_evidence_links'].includes(v.reference_integrity)) fail()
    list(v.reference_keys, 100, k => { if (!/^ref_[0-9a-f]{64}$/.test(k)) fail() })
    if (v.reference_keys.length !== v.evidence_ids.length) fail()
  } else {
    if (v.rank_basis !== 'lexical_token_overlap') fail()
    integer(v.overlap_tokens); integer(v.query_tokens); list(v.citations, 100, citation); list(v.unavailable_evidence_ids, 100, uuid)
    const resolved = v.citations.map(c => c.evidence_id)
    const all = [...resolved, ...v.unavailable_evidence_ids]
    if (new Set(all).size !== all.length || all.length !== v.evidence_ids.length || all.some(k => !v.evidence_ids.includes(k)) || v.citations.some(c => c.project_id !== v.scope.project_id)) fail()
  }
}
function coverage(v) {
  shape(v, 'display_graph_id scope pages scanned eligible excluded unknown returned truncated')
  displayId(v.display_graph_id); scope(v.scope); integer(v.pages, 5)
  ;['scanned', 'eligible', 'excluded', 'unknown'].forEach(k => integer(v[k], 500))
  integer(v.returned, 100); bool(v.truncated)
}
function passage(v) {
  shape(v, 'project_id source_revision source_sha256 retained_codepoints retrieved_codepoints retrieved_passage_fraction')
  uuid(v.project_id); uuid(v.source_revision); hash(v.source_sha256)
  integer(v.retained_codepoints, Number.MAX_SAFE_INTEGER, 1); integer(v.retrieved_codepoints, v.retained_codepoints)
  if (typeof v.retrieved_passage_fraction !== 'number' || !Number.isFinite(v.retrieved_passage_fraction) || v.retrieved_passage_fraction < 0 || v.retrieved_passage_fraction > 1) fail()
}
function candidate(v) {
  shape(v, 'scope subject_id predicate provider_ids evidence_ids basis interpretation')
  scope(v.scope); text(v.subject_id, 256); text(v.predicate, 256)
  list(v.provider_ids, 100, n => text(n, 256)); list(v.evidence_ids, 10000, uuid)
  if (v.provider_ids.length < 2 || v.basis !== 'same_subject_predicate_differing_target_or_text' || v.interpretation !== 'candidate_for_review_no_truth_judgment') fail()
}
function researchMeta(v) {
  list(v.scopes, 5, coverage); list(v.passage_coverage, 10000, passage); list(v.competing_claim_candidates, 50, candidate)
  ;['linked_citations', 'resolved_citations', 'unavailable_citations'].forEach(k => integer(v[k]))
  bool(v.historical)
  if (v.historical_semantics !== 'retained_edges_not_bitemporal_reconstruction' || v.rank_basis !== 'lexical_token_overlap' || v.linked_citations !== v.resolved_citations + v.unavailable_citations) fail()
}
function requestPayload(method, v, graph) {
  if (!obj(v)) fail('invalid_request')
  const allowed = ['schema_version', 'display_graph_ids', 'valid_at', 'recorded_before', ...(method === 'research' ? ['text', 'top_k'] : ['title', 'sections'])]
  if (Object.keys(v).some(k => !allowed.includes(k)) || (v.schema_version !== undefined && v.schema_version !== 1) || !Array.isArray(v.display_graph_ids) || v.display_graph_ids.length !== 1 || v.display_graph_ids[0] !== graph) fail('invalid_request')
  try {
    for (const key of ['valid_at', 'recorded_before']) if (v[key] != null) awareTimestamp(v[key])
    const entries = method === 'research' ? [v] : v.sections
    if (method === 'dossier') { text(v.title, 256, 1); if (!v.title.trim() || !Array.isArray(entries) || entries.length < 1 || entries.length > 6) fail() }
    entries.forEach(e => {
      if (!obj(e)) fail()
      if (method === 'dossier') { if (Object.keys(e).some(k => !['heading', 'query', 'top_k'].includes(k))) fail(); text(e.heading, 256, 1); if (!e.heading.trim()) fail() }
      const q = e[method === 'research' ? 'text' : 'query']; text(q, 2000, 1); if (!q.trim()) fail()
      if (e.top_k !== undefined) integer(e.top_k, 100, 1)
    })
  } catch { fail('invalid_request') }
  const raw = JSON.stringify(v)
  if (encoder.encode(raw).length > (method === 'research' ? 16384 : 32768)) fail('invalid_request')
  return raw
}
function graphRecord(v, edge) {
  shape(v, edge ? 'uuid name fact fact_type source_node_uuid target_node_uuid labels summary attributes created_at valid_at invalid_at expired_at episodes evidence_ids score source_node_name target_node_name' : 'uuid name labels summary fact attributes created_at valid_at invalid_at expired_at episodes evidence_ids score')
  uuid(v.uuid); text(v.name, 32768); nullable(v.fact, n => text(n)); nullable(v.summary, n => text(n))
  list(v.labels, 64, n => text(n, 128)); list(v.episodes, 10000, uuid); list(v.evidence_ids, 10000, uuid)
  if (!obj(v.attributes)) fail()
  for (const k of ['created_at', 'valid_at', 'invalid_at', 'expired_at']) nullable(v[k], awareTimestamp)
  if (v.score !== null && (typeof v.score !== 'number' || !Number.isFinite(v.score))) fail()
  if (edge) { uuid(v.source_node_uuid); uuid(v.target_node_uuid); text(v.fact_type); text(v.source_node_name); text(v.target_node_name) }
}
function validateResultShape(method, v, graph) {
  if (method === 'graph') {
    shape(v, 'graph_id nodes edges node_count edge_count')
    if (v.graph_id !== graph) fail()
    list(v.nodes, 10000, n => graphRecord(n, false)); list(v.edges, 10000, n => graphRecord(n, true))
    if (v.node_count !== v.nodes.length || v.edge_count !== v.edges.length) fail()
  } else if (method === 'research') {
    shape(v, 'schema_version source_claims simulation_observations other_claims scopes passage_coverage competing_claim_candidates linked_citations resolved_citations unavailable_citations historical historical_semantics rank_basis')
    if (v.schema_version !== 1) fail()
    researchMeta(v)
    for (const [k, classes] of [['source_claims', ['source']], ['simulation_observations', ['simulation']], ['other_claims', ['assumption', 'inference', 'analysis']]]) list(v[k], 100, f => { fact(f); if (!classes.includes(f.claim_class)) fail() })
    const facts = [...v.source_claims, ...v.simulation_observations, ...v.other_claims]
    if (facts.length > 100 || v.linked_citations !== facts.reduce((n, f) => n + f.evidence_ids.length, 0) || v.resolved_citations !== facts.reduce((n, f) => n + f.citations.length, 0)) fail()
    if (v.scopes.length !== 1 || v.scopes[0].display_graph_id !== graph || facts.some(f => !sameScope(f.scope, v.scopes[0].scope))) fail()
  } else {
    shape(v, 'schema_version mode request sections claims references research_trace summary input_sha256 trace_sha256 records_sha256 model_generated semantic_judge_used claim_support_status consistency limitations markdown')
    if (v.schema_version !== 1 || v.mode !== 'model_free_evidence_dossier' || v.model_generated !== false || v.semantic_judge_used !== false || v.claim_support_status !== 'not_reviewed' || v.consistency !== 'individually_guarded_queries_not_atomic_snapshot') fail()
    requestPayload('dossier', v.request, graph)
    ;['input_sha256', 'trace_sha256', 'records_sha256'].forEach(k => hash(v[k]))
    text(v.markdown, 4194304); list(v.limitations, 100, n => text(n))
    list(v.claims, 600, c => fact(c, true))
    list(v.references, 60000, r => {
      shape(r, 'key claim_key scope provider_id evidence_id status citation'); scope(r.scope); text(r.provider_id, 256); uuid(r.evidence_id)
      if (!/^ref_[0-9a-f]{64}$/.test(r.key) || !/^claim_[0-9a-f]{64}$/.test(r.claim_key)) fail()
      if (r.status === 'resolved') { citation(r.citation); if (r.evidence_id !== r.citation.evidence_id || r.scope.project_id !== r.citation.project_id) fail() }
      else if (r.status !== 'unavailable' || r.citation !== null) fail()
    })
    const claims = new Map(v.claims.map(c => [c.key, c])); const refs = new Map(v.references.map(r => [r.key, r]))
    if (claims.size !== v.claims.length || refs.size !== v.references.length) fail()
    v.claims.forEach(c => c.reference_keys.forEach((k, i) => { const r = refs.get(k); if (!r || r.claim_key !== c.key || r.evidence_id !== c.evidence_ids[i] || !sameScope(c.scope, r.scope)) fail() }))
    v.references.forEach(r => { if (!claims.has(r.claim_key) || !claims.get(r.claim_key).reference_keys.includes(r.key)) fail() })
    list(v.sections, 6, s => {
      shape(s, 'ordinal heading source_claim_keys simulation_observation_keys other_claim_keys'); integer(s.ordinal, 6, 1); text(s.heading, 256, 1)
      for (const [k, classes] of [['source_claim_keys', ['source']], ['simulation_observation_keys', ['simulation']], ['other_claim_keys', ['assumption', 'inference', 'analysis']]]) list(s[k], 100, key => { if (!claims.has(key) || !classes.includes(claims.get(key).claim_class)) fail() })
    })
    list(v.research_trace, 6, t => {
      shape(t, 'ordinal request_sha256 response_sha256 query top_k display_graph_ids valid_at recorded_before scopes passage_coverage competing_claim_candidates linked_citations resolved_citations unavailable_citations historical historical_semantics rank_basis')
      integer(t.ordinal, 6, 1); hash(t.request_sha256); hash(t.response_sha256); text(t.query, 2000, 1); integer(t.top_k, 100, 1); researchMeta(t)
      if (!Array.isArray(t.display_graph_ids) || t.display_graph_ids.length !== 1 || t.display_graph_ids[0] !== graph || t.scopes.length !== 1 || t.scopes[0].display_graph_id !== graph) fail()
      nullable(t.valid_at, awareTimestamp); nullable(t.recorded_before, awareTimestamp)
    })
    shape(v.summary, 'section_count query_count distinct_scoped_facts reference_links resolved_references unavailable_references query_reference_links query_resolved_references query_unavailable_references scanned_per_query_sum unknown_per_query_sum truncated_query_scopes passage_coverage coverage_label')
    for (const k of Object.keys(v.summary).filter(k => !['passage_coverage', 'coverage_label'].includes(k))) integer(v.summary[k])
    list(v.summary.passage_coverage, 60000, passage)
    if (v.summary.coverage_label !== 'retrieved_passage_union_per_retained_revision' || v.sections.length < 1 || v.sections.length !== v.request.sections.length || v.sections.length !== v.research_trace.length || v.summary.section_count !== v.sections.length || v.summary.query_count !== v.research_trace.length || v.summary.distinct_scoped_facts !== v.claims.length || v.summary.reference_links !== v.references.length || v.summary.resolved_references !== v.references.filter(r => r.status === 'resolved').length || v.summary.unavailable_references !== v.references.filter(r => r.status === 'unavailable').length) fail()
    v.sections.forEach((s, i) => { if (s.ordinal !== i + 1 || v.research_trace[i].ordinal !== i + 1 || s.heading !== v.request.sections[i].heading || v.research_trace[i].query !== v.request.sections[i].query) fail() })
  }
  return v
}
export function validateResult(method, value, graph) {
  try { return validateResultShape(method, value, graph) } catch { fail('invalid_reply') }
}
export function createWorkbenchClient({ fetchImpl = globalThis.fetch, deadlineMs = 125000 } = {}) {
  if (!Number.isFinite(deadlineMs) || deadlineMs < 1 || deadlineMs > 150000) fail('invalid_connection')
  let connection = null, active = null, activeMethod = null, generation = 0, authenticated = false, ingestionScope = null, populationAdmission = null
  const preparationPlans = new Map(), preparationStarted = new Set()
  const nativePlans = new Map(), nativeStarted = new Set()
  const observations = createNativeObservationsChannel({ fetchImpl, deadlineMs, connection: () => authenticated && connection ? { ...connection } : null, denied: () => disconnect() })
  const reports = createConnectedReportsChannel({ fetchImpl, deadlineMs, connection: () => authenticated && connection ? { ...connection } : null, denied: () => disconnect() })
  const followups = createConnectedFollowupsChannel({ fetchImpl, deadlineMs, connection: () => authenticated && connection ? { ...connection } : null, denied: () => disconnect() })
  function cancelShared(preservePopulation = false) { generation++; active?.abort(); active = null; activeMethod = null; if (!preservePopulation) populationAdmission = null }
  function cancel(preservePopulation = false) { reports.cancel(); followups.cancel(); cancelShared(preservePopulation) }
  function clearNativeLaunch() {
    observations.clear()
    // Child resets must not invalidate another section's transport epoch.
    // Native requests retain the shared controller through digest admission.
    if (active && activeMethod?.startsWith('nativeLaunch')) cancel()
    nativePlans.clear()
  }
  function disconnect() { reports.clear(); followups.clear(); observations.clear(); cancel(); connection = null; authenticated = false; ingestionScope = null; populationAdmission = null; preparationPlans.clear(); preparationStarted.clear(); nativePlans.clear(); nativeStarted.clear() }
  async function request(method, payload, ingestionContext, options = {}) {
    if (!connection || (method !== 'graph' && !authenticated)) fail('disconnected')
    const population = method.startsWith('population')
    // Original PDF reads have no effect on report or follow-up ownership.
    if (['sourceOriginalMetadata', 'sourceOriginalRead'].includes(method)) cancelShared()
    else cancel(method === 'populationExport')
    const source = method.startsWith('source'), ingestion = method.startsWith('ingestion'), experiment = method.startsWith('experiment')
    const original = ['sourceRetainOriginal', 'sourceOriginalMetadata', 'sourceOriginalRead'].includes(method)
    const preparation = method.startsWith('preparation')
    const native = method.startsWith('nativeLaunch')
    let nativeRaw, nativeContext
    if (native) {
      nativeRaw = nativeLaunchPayload(payload, method === 'nativeLaunchPlan'); payload = JSON.parse(nativeRaw)
      const known = nativePlans.get(payload.launch_id)
      if (method === 'nativeLaunchPlan') {
        const ready = preparationPlans.get(payload.preparation.operation_id)
        if (!ready || ready.state !== 'ready' || ready.plan_sha256 !== payload.preparation.plan_sha256 || JSON.stringify(ready) !== JSON.stringify(ingestionContext)) fail('invalid_request')
        if (!known && nativePlans.size >= 100) fail('busy')
        nativeContext = { preparation: JSON.parse(JSON.stringify(ready)), known: known?.plan }
      } else {
        if (!known || known.plan.launch_sha256 !== payload.launch_sha256) fail('invalid_request')
        nativeContext = { preparation: known.preparation, known: known.plan }
      }
      if (method === 'nativeLaunchStart' && (nativeStarted.has(payload.launch_id) || nativeContext.known.state !== 'planned')) fail('conflict')
      if (method === 'nativeLaunchStart' && (!nativeContext.known.authorization.model_calls_enabled || !nativeContext.known.ceiling_microusd)) fail('model_calls_disabled')
    }
    let preparationRaw, preparationContext
    if (preparation) {
      preparationRaw = preparationPayload(payload, method === 'preparationPlan')
      payload = JSON.parse(preparationRaw)
      if (method === 'preparationPlan' && !preparationPlans.has(payload.operation_id) && preparationPlans.size >= 100) fail('busy')
      try {
        preparationContext = method === 'preparationPlan' ? { source: preparationSource(ingestionContext), known: preparationPlans.get(payload.operation_id) } : { known: preparationPlans.get(payload.operation_id) }
        if (method !== 'preparationPlan' && (!preparationContext.known || preparationContext.known.plan_sha256 !== payload.plan_sha256)) fail('invalid_request')
        if (method === 'preparationStart' && (preparationStarted.has(payload.operation_id) || preparationContext.known.state !== 'planned')) fail('conflict')
        if (method === 'preparationStart' && (!preparationContext.known.authorization.model_calls_enabled || !preparationContext.known.authorization.ceiling_microusd)) fail('model_calls_disabled')
      } catch (e) { if (e instanceof WorkbenchError) throw e; fail('invalid_request') }
    }
    if (!population || method === 'populationPreview') populationAdmission = null
    let populationRequest
    if (population) {
      try { populationRequest = populationOptions(payload?.options, payload?.platform); payload = populationRequest }
      catch { fail('invalid_request') }
      if (method === 'populationExport') {
        const previewOptions = { ...populationRequest }; delete previewOptions.platform
        try { ingestionContext = validatePopulationPreview(ingestionContext, connection.graph, previewOptions) }
        catch { fail('invalid_request') }
        if (!populationAdmission || JSON.stringify(previewOptions) !== JSON.stringify(populationAdmission.options) || JSON.stringify(ingestionContext) !== JSON.stringify(populationAdmission.preview)) fail('invalid_request')
      }
    }
    let experimentRequest
    if (method === 'experimentCompare') {
      try { experimentRequest = experimentSelection(payload, ingestionContext); payload = experimentRequest.payload } catch { fail('invalid_request') }
    }
    const ingestionRaw = ingestion ? ingestionPayload(payload, method === 'ingestionStatus') : undefined
    if (ingestion) {
      payload = JSON.parse(ingestionRaw)
      ingestionContext = JSON.parse(JSON.stringify({ ...ingestionContext, ...(ingestionScope ? { scope: ingestionScope } : {}) }))
    }
    const originalRaw = original ? sourceOriginalPayload(method, payload) : undefined
    const raw = native ? nativeRaw : preparation ? preparationRaw : population ? JSON.stringify(populationRequest) : experiment ? experimentRequest?.raw : ingestion ? (method === 'ingestionStatus' ? undefined : ingestionRaw) : original ? (method === 'sourceRetainOriginal' ? originalRaw : undefined) : method === 'graph' || method === 'sourceList' || method === 'sourceGet' ? undefined : method === 'sourceRetain' ? sourcePayload(payload) : requestPayload(method, payload, connection.graph)
    if (population && encoder.encode(raw).length > 16384) fail('invalid_request')
    const sourceRequest = original ? (method === 'sourceRetainOriginal' ? JSON.parse(originalRaw) : originalRaw) : method === 'sourceGet' ? sourceReadRequest(payload) : method === 'sourceRetain' ? JSON.parse(raw) : undefined
    const epoch = generation, current = connection, controller = new AbortController()
    active = controller
    activeMethod = method
    let timedOut = false, reader, responseBody
    const timer = setTimeout(() => { timedOut = true; controller.abort() }, deadlineMs)
    const callerAbort = () => controller.abort()
    if (options.signal?.aborted) callerAbort()
    else options.signal?.addEventListener('abort', callerAbort, { once: true })
    const owned = () => { if (generation !== epoch || controller.signal.aborted) fail(timedOut ? 'deadline' : 'cancelled') }
    const guarded = promise => new Promise((resolve, reject) => {
      const aborted = () => reject(new WorkbenchError(timedOut ? 'deadline' : 'cancelled'))
      if (controller.signal.aborted) { aborted(); return }
      controller.signal.addEventListener('abort', aborted, { once: true })
      Promise.resolve(promise).then(value => {
        if (controller.signal.aborted && value?.body?.cancel) { try { void value.body.cancel().catch(() => {}) } catch { /* discard */ } }
        resolve(value)
      }, reject).finally(() => controller.signal.removeEventListener('abort', aborted))
    })
    try {
      const paths = { graph: 'data', research: 'research', dossier: 'dossier' }
      if (method === 'sourceRetain') { await guarded(verifySourceInput(sourceRequest)); owned() }
      if (method === 'sourceRetainOriginal') { await guarded(verifyOriginalPdfInput(sourceRequest)); owned() }
      if (method === 'ingestionPlan') {
        await guarded(validateSourceResult('sourceGet', ingestionContext.inspected, { source_revision: payload.source_revision }))
        owned()
        if (ingestionContext.inspected.source.codepoint_length > 32768 || !ingestionContext.inspected.passages.length) fail('invalid_request')
      }
      const sourcePaths = { sourceList: 'library', sourceGet: 'item', sourceRetain: 'retain', sourceRetainOriginal: 'retain-original', sourceOriginalMetadata: 'original-metadata', sourceOriginalRead: 'original' }
      const ingestionPaths = { ingestionPlan: 'plan', ingestionExecute: 'execute', ingestionStatus: 'operation' }
      const path = native ? `/api/native-launch/${{ nativeLaunchPlan: 'plan', nativeLaunchStart: 'start', nativeLaunchStatus: 'status', nativeLaunchCancel: 'cancel' }[method]}/${current.graph}` : preparation ? `/api/preparation/${current.graph}/${{ preparationPlan: 'plan', preparationStart: 'start', preparationStatus: 'status' }[method]}` : population ? `/api/graph/population/${current.graph}/${method === 'populationPreview' ? 'preview' : 'export'}` : experiment ? `/api/experiments/${method === 'experimentCatalog' ? 'catalog' : 'compare'}` : ingestion ? `/api/source/ingestion/${ingestionPaths[method]}/${current.graph}${method === 'ingestionStatus' ? '/' + payload.operation_id : ''}` : source ? `/api/source/${sourcePaths[method]}/${current.graph}${['sourceGet', 'sourceOriginalMetadata', 'sourceOriginalRead'].includes(method) ? '/' + sourceRequest.source_revision : ''}` : `/api/graph/${paths[method]}/${current.graph}`
      // Fence before invoking fetch, including synchronous throws and lost replies.
      if (method === 'preparationStart') preparationStarted.add(payload.operation_id)
      if (method === 'nativeLaunchStart' || method === 'nativeLaunchCancel') nativeStarted.add(payload.launch_id)
      const response = await guarded(fetchImpl(`${current.origin}${path}`, { method: raw === undefined ? 'GET' : 'POST', headers: { Authorization: `Bearer ${current.token}`, ...(raw ? { 'Content-Type': 'application/json' } : {}) }, body: raw, signal: controller.signal, redirect: 'error', credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer' }))
      responseBody = response.body
      owned()
      if (response.status === 401) { disconnect(); fail('unauthorized') }
      if ((experiment || population || preparation || native) && response.status === 403) { disconnect(); fail('origin_denied') }
      if (native && [404, 501, 503].includes(response.status) && !/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') || '')) fail('native_launch_unavailable')
      if (preparation && [404, 501, 503].includes(response.status) && !/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') || '')) fail('preparation_unavailable')
      if ((source || ingestion) && response.status === 404 && !/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') || '')) fail('source_unavailable')
      if (population && [404, 501].includes(response.status) && !/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') || '')) fail('population_unavailable')
      const exportOk = method === 'populationExport' && response.ok && (payload.platform === 'twitter' ? /^text\/csv\s*;\s*charset=utf-8$/i : /^application\/json\s*;\s*charset=utf-8$/i).test(response.headers.get('content-type') || '')
      if (method === 'populationExport' && response.ok && !exportOk) fail()
      if (response.redirected || response.type === 'opaqueredirect' || (!exportOk && !/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') || '')) || !response.body?.getReader) fail()
      const cap = native ? 65664 : preparation ? 263168 : population ? 2097152 + (method === 'populationPreview' ? 1024 : 0) : experiment ? (method === 'experimentCatalog' ? 32768 : 524288) + 16384 : ingestion ? 263168 : (source || method === 'dossier' ? 4194304 : 2097152) + 1024
      const length = response.headers.get('content-length')
      if (length && (!/^\d+$/.test(length) || Number(length) > cap)) fail('result_too_large')
      reader = response.body.getReader()
      const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: population }); let size = 0, body = ''; const exportChunks = []
      while (true) {
        const chunk = await guarded(reader.read()); owned()
        if (chunk.done) break
        size += chunk.value.byteLength
        if (size > cap) fail('result_too_large')
        if (exportOk) exportChunks.push(new Uint8Array(chunk.value))
        try { body += decoder.decode(chunk.value, { stream: true }) } catch { fail() }
      }
      try { body += decoder.decode() } catch { fail() }
      owned()
      if (exportOk) {
        let admitted
        try { admitted = validatePopulationExport(body, payload.platform, ingestionContext) } catch { fail('invalid_reply') }
        const bytes = new Uint8Array(size); let offset = 0
        for (const chunk of exportChunks) { bytes.set(chunk, offset); offset += chunk.byteLength }
        owned(); return { bytes, mime: response.headers.get('content-type'), filename: payload.platform === 'twitter' ? 'oasis-twitter-profiles.csv' : 'oasis-reddit-profiles.json', rows: admitted }
      }
      let value
      try { value = uniqueJson(body) } catch { fail() }
      if (!obj(value) || typeof value.success !== 'boolean') fail()
      if (!response.ok || !value.success) {
        shape(value, 'success error'); shape(value.error, 'code')
        if (value.success !== false || !(native ? NATIVE_LAUNCH_CODES.has(value.error.code) && response.status === NATIVE_ERROR_STATUS[value.error.code] : preparation ? PREPARATION_CODES.has(value.error.code) : SERVER_CODES.has(value.error.code) || experiment && response.status === 503 && value.error.code === 'experiment_unavailable' || (source || ingestion) && ['source_unavailable', 'source_denied', 'outcome_unknown', 'cancelled', 'uncertain', 'model_calls_disabled', 'budget_denied'].includes(value.error.code))) fail()
        if (['unauthorized', 'origin_denied'].includes(value.error.code)) disconnect()
        fail(value.error.code)
      }
      shape(value, 'success data')
      let result
      if (native) {
        result = await guarded(validateNativeLaunchResult(value.data, { graph: current.graph, payload, ...nativeContext, planning: method === 'nativeLaunchPlan' }))
      } else if (preparation) {
        result = await guarded(validatePreparationResult(value.data, { graph: current.graph, payload, ...preparationContext, planning: method === 'preparationPlan' }))
      } else if (experiment) {
        try { result = method === 'experimentCatalog' ? validateExperimentCatalog(value.data) : validateExperimentComparison(value.data, experimentRequest.catalog, experimentRequest.payload) } catch { fail('invalid_reply') }
      } else if (population) {
        try { result = validatePopulationPreview(value.data, current.graph, populationRequest) } catch { fail('invalid_reply') }
      } else result = original ? await guarded(validateSourceOriginalResult(method, value.data, sourceRequest)) : ingestion ? await guarded(validateIngestionResult(method, value.data, { ...ingestionContext, payload })) : source ? await guarded(validateSourceResult(method, value.data, sourceRequest)) : validateResult(method, value.data, current.graph)
      if (method === 'research' && [...result.source_claims, ...result.simulation_observations, ...result.other_claims].length > (payload.top_k ?? 10)) fail()
      if (method === 'dossier') {
        const returned = result.request
        const submillisecond = stamp => (stamp.match(/\.(\d+)/)?.[1] || '').padEnd(6, '0').slice(3)
        const sameTime = (a, b) => a == null && b == null || a != null && b != null && Date.parse(a) === Date.parse(b) && submillisecond(a) === submillisecond(b)
        if (returned.title !== payload.title || returned.sections.length !== payload.sections.length || returned.sections.some((s, i) => s.heading !== payload.sections[i].heading || s.query !== payload.sections[i].query || s.top_k !== (payload.sections[i].top_k ?? 10)) || !sameTime(returned.valid_at, payload.valid_at) || !sameTime(returned.recorded_before, payload.recorded_before)) fail()
      }
      owned()
      if (native) {
        nativePlans.set(result.request.run_id, { plan: JSON.parse(JSON.stringify(result)), preparation: JSON.parse(JSON.stringify(nativeContext.preparation)) })
        if (result.state !== 'planned') nativeStarted.add(result.request.run_id)
      }
      if (preparation) {
        preparationPlans.set(result.operation_id, JSON.parse(JSON.stringify(result)))
        if (result.state !== 'planned') preparationStarted.add(result.operation_id)
      }
      if (ingestion) ingestionScope = { ...result.scope }
      if (method === 'populationPreview') populationAdmission = { options: populationOptions(populationRequest), preview: JSON.parse(JSON.stringify(result)) }
      return result
    } catch (error) {
      if (generation !== epoch || controller.signal.aborted) {
        if (error instanceof WorkbenchError && ['unauthorized', 'origin_denied'].includes(error.code)) throw error
        fail(timedOut ? 'deadline' : 'cancelled')
      }
      if (error instanceof WorkbenchError) throw error
      fail('transport_failure')
    } finally {
      clearTimeout(timer)
      options.signal?.removeEventListener('abort', callerAbort)
      controller.abort()
      if (reader) { try { void reader.cancel().catch(() => {}) } catch { /* no raw errors */ } reader.releaseLock() }
      else if (responseBody) { try { void responseBody.cancel().catch(() => {}) } catch { /* no raw errors */ } }
      if (active === controller) { active = null; activeMethod = null }
    }
  }
  return {
    async connect({ origin, graph, token }) {
      disconnect()
      const canonical = localOrigin(origin); displayId(graph)
      if (typeof token !== 'string' || !/^[\x21-\x7e]{1,505}$/.test(token)) fail('invalid_connection')
      connection = { origin: canonical, graph, token }
      const epoch = generation
      try { const data = await request('graph'); if (generation !== epoch + 1) fail('cancelled'); authenticated = true; return data } catch (e) { if (generation === epoch + 1) disconnect(); throw e }
    },
    research: payload => request('research', payload),
    dossier: payload => request('dossier', payload),
    sourceList: () => request('sourceList'),
    sourceGet: payload => request('sourceGet', payload),
    sourceRetain: payload => request('sourceRetain', payload),
    sourceRetainOriginal: (payload, options) => request('sourceRetainOriginal', payload, undefined, options),
    sourceOriginalMetadata: (payload, options) => request('sourceOriginalMetadata', payload, undefined, options),
    sourceOriginalRead: (payload, options) => request('sourceOriginalRead', payload, undefined, options),
    experimentCatalog: () => request('experimentCatalog'),
    experimentCompare: (payload, catalog) => request('experimentCompare', payload, catalog),
    populationPreview: options => request('populationPreview', { options }),
    populationExport: (platform, options, preview) => request('populationExport', { platform, options }, preview),
    preparationPlan: (payload, source) => request('preparationPlan', payload, source),
    preparationStart: payload => request('preparationStart', payload),
    preparationStatus: payload => request('preparationStatus', payload),
    nativeLaunchPlan: (payload, ready) => request('nativeLaunchPlan', payload, ready),
    nativeLaunchStart: payload => request('nativeLaunchStart', payload),
    nativeLaunchStatus: payload => request('nativeLaunchStatus', payload),
    nativeLaunchCancel: payload => request('nativeLaunchCancel', payload),
    clearNativeLaunch,
    nativeObservationsPage: (payload, selection, knownPage) => observations.page(payload, selection, knownPage),
    clearNativeObservations: () => observations.clear(),
    connectedReportPlan: (payload, selection) => reports.plan(payload, selection),
    connectedReportStart: (payload, known) => reports.start(payload, known),
    connectedReportStatus: (payload, known) => reports.status(payload, known),
    connectedReportCancel: (payload, known) => reports.cancelReport(payload, known),
    connectedReportRead: (payload, known) => reports.read(payload, known),
    connectedReportDownload: (payload, known) => reports.download(payload, known),
    clearConnectedReports: () => reports.clear(),
    connectedFollowupPlan: (payload, parentReport) => followups.plan(payload, parentReport),
    connectedFollowupStart: (payload, known) => followups.start(payload, known),
    connectedFollowupStatus: (payload, known) => followups.status(payload, known),
    connectedFollowupCancel: (payload, known) => followups.cancelTurn(payload, known),
    connectedFollowupRead: (payload, known) => followups.read(payload, known),
    connectedFollowupDownload: (payload, known) => followups.download(payload, known),
    connectedFollowupHistory: payload => followups.history(payload),
    clearConnectedFollowups: () => followups.clear(),
    ingestionPlan: (payload, inspected, scope) => request('ingestionPlan', payload, { inspected, scope }),
    ingestionExecute: (payload, known) => request('ingestionExecute', payload, { known, scope: known.scope }),
    ingestionStatus: (payload, known, scope, project) => request('ingestionStatus', payload, { known, scope, project }),
    cancel, cancelShared, disconnect
  }
}

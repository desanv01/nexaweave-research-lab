// Admission of the server-validated public projection, not a native comparator.
// Seeds never pass through Number; fingerprints are provenance, not signatures.
const encoder = new TextEncoder()
const id = /^[A-Za-z0-9_.:-]{1,128}$/
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const digest = /^[0-9a-f]{64}$/
const states = ['declared', 'starting', 'running', 'completed', 'failed', 'cancelled', 'uncertain']
export const experimentTables = ['post', 'follow', 'like', 'dislike', 'comment', 'comment_like', 'comment_dislike', 'mute', 'trace']
const memberKeys = 'member_id member_label case_label run_id state cancel_requested seed max_rounds platforms'
const coverage = { atomic_cohort_snapshot: false, statistics: 'descriptive_completed_available_observations_only', labels_prove_controlled_intervention: false, hashes_prove_semantic_equivalence: false, digests_are_signatures: false, possible_initial_log_duplicates: true, post_log_interviews_may_exist_in_trace: true, exact_event_row_links: false, historical_or_causal_truth: false, missing_metrics_are_zero: false }
const flags = { causal_attribution_supported: false, provider_quality_assessed: false, shared_budget_enforcement_supported: false, actual_provider_spend: null, ensemble_launch_supported: false, coverage }
const reject = (code = 'invalid_reply') => { const error = new Error(code); error.code = code; throw error }
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
function keys(value, expected) { if (!object(value) || Object.keys(value).sort().join('|') !== (Array.isArray(expected) ? [...expected] : expected.split(' ')).sort().join('|')) reject() }
function count(value, max = Number.MAX_SAFE_INTEGER, min = 0) { if (!Number.isSafeInteger(value) || value < min || value > max) reject() }
function pattern(value, expression) { if (typeof value !== 'string' || !expression.test(value)) reject() }
function text(value, cap = 160) {
  if (typeof value !== 'string' || !value.trim() || encoder.encode(value).length > cap || /[\u0000-\u001f\u007f]|[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)) reject()
}
function array(value, max, min = 0) { if (!Array.isArray(value) || value.length < min || value.length > max) reject() }
function equal(a, b) {
  if (Array.isArray(a)) return Array.isArray(b) && a.length === b.length && a.every((v, i) => equal(v, b[i]))
  if (object(a)) return object(b) && Object.keys(a).length === Object.keys(b).length && Object.keys(a).every(k => Object.hasOwn(b, k) && equal(a[k], b[k]))
  return a === b
}
function seed(value) {
  if (typeof value !== 'string' || value.length > 20 || !/^(?:0|-[1-9][0-9]*|[1-9][0-9]*)$/.test(value)) reject()
  const number = BigInt(value)
  if (number < -9223372036854775808n || number > 9223372036854775807n) reject()
}
function platforms(value) { if (!equal(value, ['twitter']) && !equal(value, ['reddit']) && !equal(value, ['twitter', 'reddit'])) reject() }
function member(value) {
  pattern(value.member_id, id); text(value.member_label); text(value.case_label); pattern(value.run_id, uuid)
  if (!states.includes(value.state) || typeof value.cancel_requested !== 'boolean') reject()
  seed(value.seed); count(value.max_rounds, 24, 1); platforms(value.platforms)
}
export function validateExperimentCatalog(value) {
  keys(value, 'version project_id project_revision cohort_manifest_digest members public_projection_digest')
  if (value.version !== 1) reject()
  pattern(value.project_id, uuid); count(value.project_revision, 2147483647, 1)
  pattern(value.cohort_manifest_digest, digest); pattern(value.public_projection_digest, digest)
  array(value.members, 16, 1)
  value.members.forEach(m => { keys(m, memberKeys); member(m) })
  for (const field of ['member_id', 'run_id']) if (new Set(value.members.map(m => m[field])).size !== value.members.length) reject()
  return value
}
// Detach from reactive/editable caller objects before awaiting transport.
export function experimentSelection(value, catalog) {
  try {
    keys(value, 'version title member_ids'); validateExperimentCatalog(catalog)
    if (value.version !== 1) reject()
    text(value.title); array(value.member_ids, 16, 1); value.member_ids.forEach(v => pattern(v, id))
    const ordered = catalog.members.filter(m => value.member_ids.includes(m.member_id)).map(m => m.member_id)
    if (new Set(value.member_ids).size !== value.member_ids.length || !equal(value.member_ids, ordered)) reject()
    const payload = { version: 1, title: value.title, member_ids: [...value.member_ids] }
    const raw = JSON.stringify(payload)
    // The accepted host serializes ensure_ascii=True for its 8192-byte guard.
    const canonicalSize = raw.replace(/[\u007f-\uffff]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0')).length
    if (encoder.encode(raw).length > 8192 || canonicalSize > 8192) reject()
    return { raw, payload, catalog: JSON.parse(JSON.stringify(catalog)) }
  } catch { reject('invalid_request') }
}
function recording(value, m, project) {
  keys(value, 'version recording_revision anchors platforms runtime_sha256 runtime_versions artifact_sha256')
  if (value.version !== 1 || !equal(value.platforms, m.platforms) || value.runtime_sha256 !== m.runtime_sha256) reject()
  pattern(value.recording_revision, digest)
  keys(value.anchors, 'graph_id simulation_id run_id branch_id project_id project_revision')
  for (const k of ['graph_id', 'simulation_id', 'branch_id']) pattern(value.anchors[k], id)
  if (value.anchors.run_id !== m.run_id || value.anchors.project_id !== project || value.anchors.project_revision !== m.project_revision) reject()
  keys(value.runtime_versions, 'python sqlite oasis camel'); Object.values(value.runtime_versions).forEach(v => text(v))
  keys(value.artifact_sha256, ['simulation_config.json', 'source_grounding.json', ...m.platforms.map(p => p === 'twitter' ? 'twitter_profiles.csv' : 'reddit_profiles.json')])
  Object.values(value.artifact_sha256).forEach(v => pattern(v, digest))
}
function metric(value) {
  keys(value, 'logged_action_total logged_action_by_type final_table_counts'); count(value.logged_action_total)
  if (!object(value.logged_action_by_type) || Object.keys(value.logged_action_by_type).length > 128) reject()
  let sum = 0n
  for (const [action, n] of Object.entries(value.logged_action_by_type)) { text(action, 256); count(n); sum += BigInt(n) }
  if (sum !== BigInt(value.logged_action_total)) reject()
  keys(value.final_table_counts, experimentTables)
  Object.values(value.final_table_counts).forEach(n => { if (n !== null) count(n) })
}
function distribution(value, sample, eligible) {
  keys(value, 'sample_count missing_count min max arithmetic_mean median population_standard_deviation')
  count(value.sample_count, eligible); count(value.missing_count, eligible)
  if (value.sample_count !== sample || value.missing_count !== eligible - sample) reject()
  const numbers = ['min', 'max', 'arithmetic_mean', 'median', 'population_standard_deviation']
  if (!sample) { if (numbers.some(k => value[k] !== null)) reject(); return }
  for (const k of numbers) if (typeof value[k] !== 'number' || !Number.isFinite(value[k]) || value[k] < 0 || value[k] > Number.MAX_SAFE_INTEGER) reject()
  count(value.min); count(value.max)
  if (value.min > value.max || value.arithmetic_mean < value.min || value.arithmetic_mean > value.max || value.median < value.min || value.median > value.max) reject()
}
export function validateExperimentComparison(value, catalog, payload) {
  validateExperimentCatalog(catalog)
  keys(value, ['version', 'title', 'project_id', 'project_revision', 'cohort_manifest_digest', 'members', 'accounting', 'distributions', 'cancellation_intent', 'distinct_declared_seed_count', 'distinct_successful_seed_count', 'comparability_matrix', 'native_result_digest', 'public_projection_digest', ...Object.keys(flags)])
  if (value.version !== 1 || value.title !== payload.title || ['project_id', 'project_revision', 'cohort_manifest_digest'].some(k => value[k] !== catalog[k])) reject()
  for (const k of Object.keys(flags)) if (!equal(value[k], flags[k])) reject()
  array(value.members, 16, 1)
  if (!equal(value.members.map(m => m?.member_id), payload.member_ids)) reject()
  const lookup = new Map(catalog.members.map(m => [m.member_id, m]))
  const accounting = { successful: 0, failed: 0, cancelled: 0, pending: 0, uncertain: 0 }
  for (const m of value.members) {
    keys(m, memberKeys + ' disposition project_revision runtime_sha256 prepared_artifact_sha256 request_fingerprint record_digest recording metrics'); member(m)
    const declared = lookup.get(m.member_id)
    if (!declared || memberKeys.split(' ').some(k => !equal(m[k], declared[k])) || m.project_revision !== catalog.project_revision) reject()
    for (const k of ['runtime_sha256', 'prepared_artifact_sha256', 'request_fingerprint', 'record_digest']) pattern(m[k], digest)
    const disposition = m.state === 'completed' ? 'successful' : ['failed', 'cancelled', 'uncertain'].includes(m.state) ? m.state : 'pending'
    if (m.disposition !== disposition) reject()
    accounting[disposition]++
    if (disposition === 'successful') { recording(m.recording, m, value.project_id); keys(m.metrics, m.platforms); Object.values(m.metrics).forEach(metric) }
    else if (m.metrics !== null || m.recording !== null) reject()
  }
  keys(value.accounting, Object.keys(accounting)); if (!equal(value.accounting, accounting)) reject()
  keys(value.cancellation_intent, 'cancel_requested_count overlaps_disposition_accounting')
  if (value.cancellation_intent.overlaps_disposition_accounting !== true || value.cancellation_intent.cancel_requested_count !== value.members.filter(m => m.cancel_requested).length) reject()
  if (value.distinct_declared_seed_count !== new Set(value.members.map(m => m.seed)).size || value.distinct_successful_seed_count !== new Set(value.members.filter(m => m.metrics !== null).map(m => m.seed)).size) reject()
  const groups = []
  for (const label of new Set(value.members.map(m => m.case_label))) for (const platform of ['twitter', 'reddit']) {
    const eligible = value.members.filter(m => m.case_label === label && m.platforms.includes(platform))
    if (eligible.length) groups.push({ label, platform, eligible, successful: eligible.filter(m => m.metrics !== null) })
  }
  array(value.distributions, 32)
  if (value.distributions.length !== groups.length) reject()
  value.distributions.forEach((group, i) => {
    keys(group, 'case_label platform member_count successful_count non_successful_count distinct_declared_seed_count distinct_successful_seed_count metrics')
    const { label, platform, eligible, successful } = groups[i]
    if (group.case_label !== label || group.platform !== platform || group.member_count !== eligible.length || group.successful_count !== successful.length || group.non_successful_count !== eligible.length - successful.length || group.distinct_declared_seed_count !== new Set(eligible.map(m => m.seed)).size || group.distinct_successful_seed_count !== new Set(successful.map(m => m.seed)).size) reject()
    keys(group.metrics, 'logged_action_total logged_action_by_type final_table_counts')
    distribution(group.metrics.logged_action_total, successful.length, eligible.length)
    const actions = [...new Set(successful.flatMap(m => Object.keys(m.metrics[platform].logged_action_by_type)))].sort()
    keys(group.metrics.logged_action_by_type, actions)
    Object.values(group.metrics.logged_action_by_type).forEach(v => distribution(v, successful.length, eligible.length))
    keys(group.metrics.final_table_counts, experimentTables)
    for (const table of experimentTables) distribution(group.metrics.final_table_counts[table], successful.filter(m => m.metrics[platform].final_table_counts[table] !== null).length, eligible.length)
  })
  array(value.comparability_matrix, 120)
  if (value.comparability_matrix.length !== value.members.length * (value.members.length - 1) / 2) reject()
  let index = 0
  for (let i = 0; i < value.members.length; i++) for (let j = i + 1; j < value.members.length; j++) {
    const pair = value.comparability_matrix[index++], left = value.members[i], right = value.members[j]
    keys(pair, 'left_member_id right_member_id fields')
    if (pair.left_member_id !== left.member_id || pair.right_member_id !== right.member_id) reject()
    const direct = ['seed', 'max_rounds', 'runtime_sha256', 'platforms', 'project_revision', 'prepared_artifact_sha256'], retained = ['artifact_sha256', 'runtime_versions']
    keys(pair.fields, [...direct, ...retained])
    for (const field of [...direct, ...retained]) {
      const cell = pair.fields[field], l = direct.includes(field) ? left[field] : left.recording?.[field] ?? null, r = direct.includes(field) ? right[field] : right.recording?.[field] ?? null
      keys(cell, 'left right equal')
      if (!equal(cell.left, l) || !equal(cell.right, r) || cell.equal !== (retained.includes(field) && (l === null || r === null) ? null : equal(l, r))) reject()
    }
  }
  pattern(value.native_result_digest, digest); pattern(value.public_projection_digest, digest)
  return value
}

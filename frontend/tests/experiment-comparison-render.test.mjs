// Actual compiled SFC, with controlled method promises; execution belongs to Main.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'
import { copyFor } from '../src/i18n/workbench.js'
import { experimentTables } from '../src/api/experimentComparison.js'
const dom = new JSDOM('<!doctype html><html><body></body></html>')
for (const name of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node']) globalThis[name] = name === 'window' ? dom.window : dom.window[name]
const { createApp, nextTick, h, reactive } = await import('vue')
const file = new URL('../src/components/workbench/ExperimentComparison.vue', import.meta.url)
const { descriptor } = parse(readFileSync(file, 'utf8'), { filename: file.pathname })
let code = compileScript(descriptor, { id: 'experiment-comparison', inlineTemplate: true, genDefaultAs: '__component' }).content
code = code.replace(/from (['"])vue\1/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`).replace(/from (['"])(\.{1,2}\/[^'"]+)\1/g, (_match, _quote, relative) => `from ${JSON.stringify(new URL(relative, file).href)}`)
const component = (await import(`data:text/javascript;base64,${Buffer.from(code + '\nexport default __component').toString('base64')}`)).default
const project = '11111111-1111-1111-1111-111111111111', hash = 'a'.repeat(64), malicious = '<img src=x onerror=alert(1)> 中😀 https://evil.test'
function catalog() { return { version: 1, project_id: project, project_revision: 1, cohort_manifest_digest: hash, public_projection_digest: hash, members: [{ member_id: 'm0', member_label: malicious, case_label: 'Case', run_id: project, state: 'completed', cancel_requested: true, seed: '9007199254740993', max_rounds: 2, platforms: ['twitter'] }, { member_id: 'm1', member_label: 'Pending member', case_label: 'Case', run_id: '22222222-2222-2222-2222-222222222222', state: 'running', cancel_requested: false, seed: '-9223372036854775808', max_rounds: 2, platforms: ['twitter'] }] } }
function result(cat, payload) {
  const members = cat.members.filter(m => payload.member_ids.includes(m.member_id)).map(m => ({ ...structuredClone(m), disposition: m.state === 'completed' ? 'successful' : 'pending', project_revision: 1, runtime_sha256: hash, prepared_artifact_sha256: hash, request_fingerprint: hash, record_digest: hash, metrics: null, recording: null }))
  const successful = members.filter(m => m.disposition === 'successful')
  for (const m of successful) {
    m.metrics = { twitter: { logged_action_total: 0, logged_action_by_type: {}, final_table_counts: Object.fromEntries(experimentTables.map(t => [t, t === 'post' ? 0 : null])) } }
    m.recording = { version: 1, recording_revision: hash, anchors: { graph_id: 'recorded.graph', simulation_id: 'simulation', branch_id: 'branch', run_id: m.run_id, project_id: project, project_revision: 1 }, platforms: ['twitter'], runtime_sha256: hash, runtime_versions: { python: '3.11', sqlite: '3', oasis: '0.2', camel: '0.2' }, artifact_sha256: { 'simulation_config.json': hash, 'source_grounding.json': hash, 'twitter_profiles.csv': hash } }
  }
  const dist = n => ({ sample_count: n, missing_count: members.length - n, min: n ? 0 : null, max: n ? 0 : null, arithmetic_mean: n ? 0 : null, median: n ? 0 : null, population_standard_deviation: n ? 0 : null })
  const comparability_matrix = members.length === 2 ? [{ left_member_id: 'm0', right_member_id: 'm1', fields: Object.fromEntries(['seed', 'max_rounds', 'runtime_sha256', 'platforms', 'project_revision', 'prepared_artifact_sha256', 'artifact_sha256', 'runtime_versions'].map(field => { const retained = ['artifact_sha256', 'runtime_versions'].includes(field), l = retained ? members[0].recording?.[field] ?? null : members[0][field], r = retained ? members[1].recording?.[field] ?? null : members[1][field]; return [field, { left: l, right: r, equal: retained ? null : JSON.stringify(l) === JSON.stringify(r) }] })) }] : []
  return { version: 1, title: payload.title, project_id: project, project_revision: 1, cohort_manifest_digest: hash, members, accounting: { successful: successful.length, failed: 0, cancelled: 0, pending: members.length - successful.length, uncertain: 0 }, distributions: [{ case_label: 'Case', platform: 'twitter', member_count: members.length, successful_count: successful.length, non_successful_count: members.length - successful.length, distinct_declared_seed_count: members.length, distinct_successful_seed_count: successful.length, metrics: { logged_action_total: dist(successful.length), logged_action_by_type: {}, final_table_counts: Object.fromEntries(experimentTables.map(t => [t, dist(t === 'post' ? successful.length : 0)])) } }], cancellation_intent: { cancel_requested_count: successful.length, overlaps_disposition_accounting: true }, distinct_declared_seed_count: members.length, distinct_successful_seed_count: successful.length, comparability_matrix, native_result_digest: hash, public_projection_digest: hash, causal_attribution_supported: false, provider_quality_assessed: false, shared_budget_enforcement_supported: false, actual_provider_spend: null, ensemble_launch_supported: false, coverage: { atomic_cohort_snapshot: false, statistics: 'descriptive_completed_available_observations_only', labels_prove_controlled_intervention: false, hashes_prove_semantic_equivalence: false, digests_are_signatures: false, possible_initial_log_duplicates: true, post_log_interviews_may_exist_in_trace: true, exact_event_row_links: false, historical_or_causal_truth: false, missing_metrics_are_zero: false } }
}
function mount(overrides = {}) {
  const props = reactive({ methods: { catalog: async () => catalog(), compare: async (payload, cat) => result(cat, payload) }, connected: true, busy: false, resetVersion: 0, locale: 'en', ...overrides })
  const root = document.createElement('div'); document.body.append(root)
  const app = createApp({ setup: () => () => h(component, props) }); app.mount(root)
  return { root, props, cleanup() { app.unmount(); root.remove() } }
}
async function settle() { for (let i = 0; i < 16; i++) { await Promise.resolve(); await nextTick() } }
function input(root, selector, value) { const el = root.querySelector(selector); el.value = value; el.dispatchEvent(new dom.window.Event('input', { bubbles: true })) }
function select(root, index) { root.querySelector('#experiment-member-' + index).click() }
async function prepare(mounted) { mounted.root.querySelector('.catalog-load').click(); await settle(); select(mounted.root, 1); await settle(); select(mounted.root, 0); await settle(); input(mounted.root, '#experiment-comparison-title', malicious); await settle() }
function submit(root) { root.querySelector('form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })) }
test('actual SFC makes no automatic requests on mount/connect/locale and uses labeled native controls', async () => {
  let calls = 0
  const mounted = mount({ connected: false, methods: { catalog: async () => { calls++; return catalog() } } })
  try {
    await settle(); assert.equal(calls, 0); assert.equal(mounted.root.querySelector('.catalog-load').disabled, true)
    mounted.props.connected = true; mounted.props.locale = 'zh'; await settle(); assert.equal(calls, 0)
    mounted.root.querySelector('.catalog-load').focus(); mounted.root.querySelector('.catalog-load').click(); await settle()
    assert.equal(calls, 1); assert.equal(mounted.root.querySelector('.member-choice').htmlFor, 'experiment-member-0')
    assert.equal(mounted.root.querySelector('#experiment-member-0').type, 'checkbox')
    assert.equal(mounted.root.querySelector('.feedback').getAttribute('role'), 'status')
    assert.equal(mounted.root.querySelector('.catalog-provenance summary').tagName, 'SUMMARY')
    mounted.props.locale = 'ms'; await settle(); assert.equal(calls, 1)
  } finally { mounted.cleanup() }
})
test('actual SFC renders inert labels, exact seeds, unavailable versus zero, comparability and provenance in three languages', async () => {
  for (const locale of ['en', 'zh', 'ms']) {
    let compares = 0, submitted
    const mounted = mount({ locale, methods: { catalog: async () => catalog(), compare: async (payload, cat) => { compares++; submitted = payload; return result(cat, payload) } } })
    try {
      await prepare(mounted); assert.equal(compares, 0); submit(mounted.root); await settle()
      assert.equal(compares, 1); assert.deepEqual(submitted.member_ids, ['m0', 'm1'])
      const panel = mounted.root.querySelector('.comparison-result'), copy = copyFor(locale).experiments
      assert.equal(document.activeElement, panel)
      assert.ok(panel.textContent.includes(malicious)); assert.equal(mounted.root.querySelector('img,script,a'), null)
      assert.deepEqual([...panel.querySelectorAll('.seed')].map(n => n.textContent), ['9007199254740993', '-9223372036854775808'])
      assert.equal(panel.querySelector('.logged-total').textContent, '0'); assert.ok(panel.querySelector('.unavailable-metrics').textContent.includes(copy.unavailable))
      assert.ok(panel.textContent.includes(copy.cancelOverlap)); assert.ok(panel.textContent.includes(copy.limitations)); assert.ok(panel.textContent.includes(copy.spend + ': ' + copy.unknown))
      assert.ok(panel.querySelector('.comparability').textContent.includes(copy.different)); assert.ok(panel.querySelector('.comparability').textContent.includes(copy.unavailable))
      const summary = panel.querySelector('.result-provenance summary'); summary.focus(); assert.equal(document.activeElement, summary)
      assert.ok(panel.querySelector('.distributions').textContent.includes(copy.statisticLabels.missing_count))
    } finally { mounted.cleanup() }
  }
})
test('UTF8 invalid input stays local; selection/title edits and refresh invalidate an admitted result', async () => {
  let loads = 0, compares = 0
  const mounted = mount({ methods: { catalog: async () => { loads++; return catalog() }, compare: async (payload, cat) => { compares++; return result(cat, payload) } } })
  try {
    await prepare(mounted); input(mounted.root, '#experiment-comparison-title', '中'.repeat(54)); await settle()
    assert.equal(mounted.root.querySelector('.compare-submit').disabled, true); submit(mounted.root); await settle(); assert.equal(compares, 0)
    assert.ok(mounted.root.querySelector('.feedback').textContent.includes(copyFor('en').experiments.invalid)); assert.equal(document.activeElement, mounted.root.querySelector('.feedback'))
    input(mounted.root, '#experiment-comparison-title', 'Valid'); submit(mounted.root); await settle(); assert.ok(mounted.root.querySelector('.comparison-result'))
    select(mounted.root, 1); await settle(); assert.equal(mounted.root.querySelector('.comparison-result'), null)
    submit(mounted.root); await settle(); assert.ok(mounted.root.querySelector('.comparison-result'))
    input(mounted.root, '#experiment-comparison-title', 'Changed'); await settle(); assert.equal(mounted.root.querySelector('.comparison-result'), null)
    mounted.root.querySelector('.catalog-load').click(); await settle(); assert.equal(loads, 2); assert.equal(mounted.root.querySelector('#experiment-comparison-title').value, ''); assert.equal(mounted.root.querySelector('#experiment-member-0').checked, false)
  } finally { mounted.cleanup() }
})
test('reset/disconnect/unmount discard pending catalog and comparison without repopulating protected state', async () => {
  for (const phase of ['catalog', 'compare']) for (const change of ['reset', 'disconnect', 'unmount']) {
    let resolve, returned
    const mounted = mount({ methods: { catalog: () => phase === 'catalog' ? new Promise(r => { resolve = r; returned = catalog() }) : Promise.resolve(catalog()), compare: (payload, cat) => new Promise(r => { resolve = r; returned = result(cat, payload) }) } })
    try {
      if (phase === 'compare') { await prepare(mounted); submit(mounted.root) } else mounted.root.querySelector('.catalog-load').click()
      await settle(); assert.ok(resolve)
      if (change === 'reset') mounted.props.resetVersion++
      else if (change === 'disconnect') mounted.props.connected = false
      else mounted.cleanup()
      await settle(); resolve(returned); await settle()
      assert.equal(mounted.root.querySelector('.catalog-member'), null); assert.equal(mounted.root.querySelector('.comparison-result'), null)
      if (change === 'unmount') continue
    } finally { if (change !== 'unmount') mounted.cleanup() }
  }
})
test('failed comparison clears prior results and unavailable catalog recovery is explicit with no retry', async () => {
  let loads = 0, compares = 0
  const mounted = mount({ methods: { catalog: async () => { if (++loads === 1) throw { code: 'experiment_unavailable' }; return catalog() }, compare: async (payload, cat) => { if (++compares === 2) throw { code: 'experiment_unavailable' }; return result(cat, payload) } } })
  try {
    mounted.root.querySelector('.catalog-load').click(); await settle(); assert.equal(loads, 1)
    assert.ok(mounted.root.textContent.includes(copyFor('en').experiments.unavailableHost)); assert.equal(mounted.root.querySelector('.catalog-member'), null)
    await prepare(mounted); submit(mounted.root); await settle(); assert.ok(mounted.root.querySelector('.comparison-result'))
    submit(mounted.root); await settle(); assert.equal(mounted.root.querySelector('.comparison-result'), null); assert.equal(compares, 2)
    mounted.props.locale = 'zh'; await settle(); assert.equal(loads, 2); assert.equal(compares, 2)
    assert.ok(mounted.root.textContent.includes(copyFor('zh').experiments.unavailableHost))
  } finally { mounted.cleanup() }
})
test('unknown/foreign result never displays and locale copy keys remain complete', async () => {
  const mounted = mount({ methods: { catalog: async () => catalog(), compare: async (payload, cat) => ({ ...result(cat, payload), project_revision: 2 }) } })
  try { await prepare(mounted); submit(mounted.root); await settle(); assert.equal(mounted.root.querySelector('.comparison-result'), null); assert.ok(mounted.root.textContent.includes(copyFor('en').experiments.invalidReply)) } finally { mounted.cleanup() }
  const keys = value => Object.keys(value).sort()
  for (const locale of ['zh', 'ms']) {
    assert.deepEqual(keys(copyFor(locale).experiments), keys(copyFor('en').experiments))
    for (const field of ['states', 'anchorLabels', 'metricLabels', 'statisticLabels', 'fieldLabels']) assert.deepEqual(keys(copyFor(locale).experiments[field]), keys(copyFor('en').experiments[field]))
  }
})

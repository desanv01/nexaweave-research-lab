import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createHash, webcrypto } from 'node:crypto'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'
import { nativeEnsembleCopyFor } from '../src/i18n/nativeEnsemble.js'

globalThis.crypto ||= webcrypto
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://127.0.0.1:5173/research' })
for (const name of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node', 'Document', 'ShadowRoot']) {
  globalThis[name] = name === 'window' ? dom.window : dom.window[name]
}
const { createApp, h, nextTick, reactive } = await import('vue')
const file = new URL('../src/components/workbench/NativeEnsemble.vue', import.meta.url)
const { descriptor } = parse(readFileSync(file, 'utf8'), { filename: file.pathname })
const compiled = compileScript(descriptor, { id: 'native-ensemble', inlineTemplate: true, genDefaultAs: '__component' })
let code = compiled.content.replace(/from (['"])vue\1/g, 'from ' + JSON.stringify(import.meta.resolve('vue')))
code = code.replace(/from (['"])(\.{1,2}\/[^'"]+)\1/g, (_match, _quote, relative) => 'from ' + JSON.stringify(new URL(relative, file).href))
const moduleUrl = 'data:text/javascript;base64,' + Buffer.from(code + '\nexport default __component').toString('base64')
const { default: component } = await import(moduleUrl)

const uid = n => '00000000-0000-0000-0000-' + String(n).padStart(12, '0')
const hash = 'a'.repeat(64)
const clone = value => JSON.parse(JSON.stringify(value))
const source = { project_id: uid(2), source_revision: uid(5), source_name: 'Retained source', text_sha256: hash,
  byte_length: 25, codepoint_length: 25, recorded_at: '2026-10-03T09:00:00Z' }
const graph = 'graph_1'
const scope = { schema_version: 1, workspace_id: uid(1), project_id: uid(2), graph_id: uid(3), run_id: null, branch_id: null, layer: 'source' }
const options = { types: ['Person'], max_agents: 2, seed: 3, platforms: ['twitter'], max_rounds: 1, simulation_requirement: 'Use this retained evidence.' }
const prepFields = 'schema_version display_graph_id scope project_revision operation_id source options actors projection_sha256'
const launchFields = 'schema_version display_graph_id scope preparation request limits ceiling_microusd model_label'
const pick = (value, keys) => Object.fromEntries(keys.split(' ').map(key => [key, value[key]]))
// Independent fixture digests, including canonical key order and Python ASCII escaping.
function ascii(value) {
  if (Array.isArray(value)) return '[' + value.map(ascii).join(',') + ']'
  if (value && typeof value === 'object') {
    return '{' + Object.keys(value).sort().map(key => ascii(key) + ':' + ascii(value[key])).join(',') + '}'
  }
  return JSON.stringify(value).replace(/[\u007f-\uffff]/g, unit => '\\u' + unit.charCodeAt(0).toString(16).padStart(4, '0'))
}
const digest = value => createHash('sha256').update(ascii(value), 'ascii').digest('hex')
function reviewed(payload) {
  const value = {
    schema_version: 1, display_graph_id: graph, scope, project_revision: 1, operation_id: payload.operation_id,
    source: { source_revision: source.source_revision, source_name: source.source_name, source_sha256: source.text_sha256 },
    options: payload.options, actors: [{ source_entity_uuid: uid(7), name: 'Actor', labels: ['Person'] }],
    projection_sha256: hash, plan_sha256: null, state: 'planned', progress: { stage: 'planned', completed: 0, total: 100 },
    error_code: null, authorization: { model_calls_enabled: true, ceiling_microusd: '2' },
    receipt: null, graph_snapshot_atomic: false, model_calls_started: false, simulation_executed: false,
  }
  value.plan_sha256 = digest(pick(value, prepFields))
  return value
}
function published(known) {
  const value = clone(known)
  const files = ['state.json', 'simulation_config.json', 'source_grounding.json', 'twitter_profiles.csv']
    .map(name => ({ name, sha256: digest([known.operation_id, name]), size: 123 }))
  value.state = 'ready'
  value.progress = { stage: 'ready', completed: 100, total: 100 }
  value.model_calls_started = true
  value.receipt = { simulation_id: 'sim_' + known.operation_id.replaceAll('-', ''), artifact_sha256: digest({ schema_version: 1, files }), files }
  return value
}
function nativeReview(payload, ready) {
  const value = {
    schema_version: 1, display_graph_id: graph, scope,
    preparation: { ...payload.preparation, simulation_id: ready.receipt.simulation_id, artifact_sha256: ready.receipt.artifact_sha256 },
    request: { schema_version: 1, principal: 'owner', project_id: scope.project_id, project_revision: ready.project_revision,
      simulation_id: ready.receipt.simulation_id, run_id: payload.launch_id, artifact_sha256: ready.receipt.artifact_sha256,
      runtime_sha256: hash, platforms: ready.options.platforms, seed: ready.options.seed, max_rounds: ready.options.max_rounds },
    limits: { max_calls: 20, max_input_bytes: 2097152, max_output_tokens: 4096, max_run_seconds: 120 },
    ceiling_microusd: '2', model_label: 'Scripted', launch_sha256: null, state: 'planned', error_code: null,
    authorization: { model_calls_enabled: true }, workflow: null, receipt: null, cancel_requested: false,
    cleanup: { known: false, pending: null, owner_thread_alive: null },
  }
  value.launch_sha256 = digest(pick(value, launchFields))
  return value
}
function nativeState(known, state) {
  const value = clone(known)
  const runId = value.request.run_id
  const fingerprint = digest(value.request)
  value.state = state
  value.workflow = { workflow_id: 'mf-native-v1-' + runId.replaceAll('-', '') + '-' + fingerprint, temporal_run_id: uid(11), native_run_id: runId }
  if (state === 'completed') {
    value.receipt = { run_id: runId, attempt_id: uid(12), instance_id: uid(13), request_fingerprint: fingerprint,
      outcome: 'completed', evidence_sha256: digest(['evidence', runId]) }
  }
  return value
}
const response = value => new Response(JSON.stringify({ success: true, data: value }), { headers: { 'Content-Type': 'application/json' } })
async function settle() {
  for (let i = 0; i < 20; i++) { await new Promise(resolve => setTimeout(resolve, 0)); await nextTick() }
}


const dataModule = code => 'data:text/javascript;base64,' + Buffer.from(code).toString('base64')
async function compileMounted(path, replacements = {}) {
  const filename = new URL(path, import.meta.url)
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename: filename.pathname })
  const compiled = compileScript(descriptor, { id: path, inlineTemplate: true, genDefaultAs: '__component' })
  const setup = compiled.content.replace(/from (['"])vue\1/g, 'from ' + JSON.stringify(import.meta.resolve('vue')))
    .replace(/(?:from |import\()(['"])(\.{1,2}\/[^'"]+)\1/g, (entry, quote, relative) => {
      const url = replacements[relative] || new URL(relative, filename).href
      return entry.replace(quote + relative + quote, JSON.stringify(url))
    })
  return dataModule(setup + '\nexport default __component')
}

async function mountParent() {
  const vue = JSON.stringify(import.meta.resolve('vue'))
  const locale = dataModule(`import { ref } from ${vue}; export const uiLocale = ref('en')`)
  const { uiLocale } = await import(locale)
  const empty = dataModule('export default { render() { return null } }')
  const sourceProbe = dataModule(
    `import { h } from ${vue}
    export default { emits: ['inspected'], setup(props, { emit }) {
      const source = ${JSON.stringify(source)}
      return () => h('div', [
        h('button', { class: 'select-fixture-source', onClick: () => emit('inspected', { source }) }, 'Select source'),
        h('button', { class: 'select-other-source', onClick: () => emit('inspected', { source: { ...source, source_revision: '${uid(98)}' } }) }, 'Select other source')
      ])
    } }`
  )
  const components = {
    SourceLibrary: sourceProbe,
    SimulationPreparation: await compileMounted('../src/components/workbench/SimulationPreparation.vue'),
    NativeLaunch: await compileMounted('../src/components/workbench/NativeLaunch.vue'),
    NativeEnsemble: moduleUrl,
  }
  const replacements = { '../i18n/index.js': locale }
  for (const name of ['EvidenceResults', 'TwoHopResearch', 'SourceLibrary', 'SourceIngestion', 'ExperimentComparison',
    'PopulationWorkbench', 'SimulationPreparation', 'NativeLaunch', 'NativeEnsemble', 'NativeObservations',
    'ConnectedReports', 'ConnectedFollowup']) {
    replacements['../components/workbench/' + name + '.vue'] = components[name] || empty
  }
  const { default: parent } = await import(await compileMounted('../src/views/ResearchWorkbench.vue', replacements))
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp(parent)
  app.component('RouterLink', { render() { return h('a', this.$slots.default?.()) } })
  app.mount(root)
  return { root, uiLocale, cleanup() { app.unmount(); root.remove() } }
}

function declaration(donor, payload) {
  return {
    schema_version: 1, display_graph_id: graph, scope, project_revision: donor.project_revision, state: 'ready',
    idempotency_key: payload.idempotency_key, ensemble_id: uid(30), platforms: donor.options.platforms,
    donor: { operation_id: donor.operation_id, plan_sha256: donor.plan_sha256, seed: donor.options.seed, artifact_sha256: donor.receipt.artifact_sha256 },
    members: payload.seeds.map((seed, ordinal) => {
      const operation_id = uid(40 + ordinal)
      const variant = { ...donor, operation_id, options: { ...donor.options, seed } }
      const files = donor.receipt.files.map((file, i) => i < 2 ? { ...file, sha256: digest([operation_id, file.name]) } : clone(file))
      return { operation_id, seed, launch_id: uid(50 + ordinal), plan_sha256: digest(pick(variant, prepFields)),
        receipt: { simulation_id: 'sim_' + operation_id.replaceAll('-', ''), artifact_sha256: digest({ schema_version: 1, files }), files } }
    }),
  }
}

test('workbench declares one ordered ensemble from its ready donor and recovers member one by Status', async t => {
  const calls = [], nativePlans = new Map()
  let donor, members, finishStale, staleSignal
  let secondAuthorized = false, secondStarted = false
  t.mock.method(globalThis, 'fetch', async (url, configuration) => {
    const path = new URL(url).pathname
    const payload = configuration.body ? JSON.parse(configuration.body) : null
    calls.push({ path, payload })
    assert.equal(configuration.headers.Authorization, 'Bearer private-token')
    if (path.endsWith('/seed_ensemble')) {
      const result = declaration(donor, payload)
      members = result.members
      return response(result)
    }
    if (path.startsWith('/api/graph/data/')) return response({ graph_id: graph, nodes: [], edges: [], node_count: 0, edge_count: 0 })
    if (path.endsWith('/plan')) { donor = reviewed(payload); return response(donor) }
    if (path.endsWith('/start')) { donor = published(donor); return response(donor) }
    if (path.includes('/native-launch/plan/')) {
      const member = members?.find(item => item.launch_id === payload.launch_id)
      const ready = member ? { ...donor, options: { ...donor.options, seed: member.seed }, receipt: member.receipt } : donor
      const plan = nativeReview(payload, ready)
      if (member && member.launch_id === members[1].launch_id) plan.authorization.model_calls_enabled = secondAuthorized
      nativePlans.set(payload.launch_id, plan)
      return response(plan)
    }
    const plan = nativePlans.get(payload.launch_id)
    if (path.includes('/native-launch/start/')) {
      if (payload.launch_id === members[0].launch_id) throw new Error('lost Start reply')
      secondStarted = true
      return response(nativeState(plan, 'running'))
    }
    if (path.includes('/native-launch/status/') && payload.launch_id === members[1].launch_id) {
      if (!secondStarted) return response({ ...plan, authorization: { model_calls_enabled: secondAuthorized } })
      staleSignal = configuration.signal
      return new Promise(resolve => { finishStale = () => resolve(response(nativeState(plan, 'completed'))) })
    }
    return response(nativeState(plan, 'completed'))
  })
  const { root, uiLocale, cleanup } = await mountParent()
  const control = selector => root.querySelector(selector)
  const input = (selector, value) => {
    const element = control(selector)
    element.value = value
    element.dispatchEvent(new dom.window.Event('input', { bubbles: true }))
  }
  const click = async selector => {
    const button = control(selector)
    assert.ok(button && !button.disabled, selector)
    button.click()
    await settle()
  }
  const first = '[data-member="1"]', second = '[data-member="2"]'
  const start = ' .ensemble-native-start', plan = ' .ensemble-native-review', status = ' .ensemble-native-status'
  try {
    input('#graph-id', graph)
    input('#bearer-token', 'private-token')
    control('.connection-form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
    await settle()
    await click('.select-fixture-source')
    input('#preparation-requirement', 'Retained evidence')
    input('#preparation-seed', '3')
    input('#preparation-rounds', '1')
    await click('.preparation-workbench .review')
    await click('.preparation-workbench .start')
    await click('.native-launch .review')
    const singleId = calls.at(-1).payload.launch_id

    const disclosure = control('.ensemble-panel')
    disclosure.open = true
    disclosure.dispatchEvent(new dom.window.Event('toggle'))
    await settle()
    input('#ensemble-seed-1', '3')
    input('#ensemble-seed-2', '9')
    await nextTick()
    assert.equal(control('.ensemble-declare').disabled, true)
    input('#ensemble-seed-1', '7')
    await nextTick()
    await click('.ensemble-declare')
    assert.deepEqual(calls.at(-1).payload.seeds, [7, 9])
    assert.equal(calls.at(-1).payload.operation_id, donor.operation_id)
    assert.equal(control(first + ' .artifact-reference').textContent, members[0].receipt.artifact_sha256)
    assert.equal(control('.derived-origin').textContent, nativeEnsembleCopyFor('en').derived)
    assert.equal(control('#ensemble-seed-1').disabled, true)
    assert.equal(calls.filter(call => call.path.endsWith('/preparation/' + graph + '/start')).length, 1)
    assert.notEqual(singleId, members[0].launch_id)

    await click(first + plan)
    assert.equal(control(first + start).disabled, true)
    await click(second + plan)
    assert.equal(control(first + start).disabled, true)
    assert.equal(control(second + start).disabled, true)
    control(first + start).click()
    assert.equal(calls.some(call => call.path.includes('/native-launch/start/')), false)
    secondAuthorized = true
    await click(second + status)
    assert.deepEqual(calls.at(-1), { path: '/api/native-launch/status/' + graph, payload: {
      schema_version: 1, launch_id: members[1].launch_id, launch_sha256: nativePlans.get(members[1].launch_id).launch_sha256,
    } })
    assert.equal(control(first + start).disabled, false)
    assert.equal(control(second + start).disabled, true)
    await click(first + start)
    const startSelector = calls.at(-1).payload
    assert.equal(startSelector.launch_id, members[0].launch_id)
    assert.equal(control('.native-ensemble .feedback').textContent, nativeEnsembleCopyFor('en').uncertainStart)
    assert.equal(control(first + start), null)
    assert.equal(control(second + start).disabled, true)
    await click(first + status)
    assert.deepEqual(calls.at(-1).payload, startSelector)
    assert.equal(control(first + ' .native-receipt').textContent, digest(['evidence', members[0].launch_id]))
    assert.ok(control('.native-launch article.plan'))
    assert.equal(control(second + start).disabled, false)
    await click(second + start)
    await click(second + status)
    assert.equal(staleSignal.aborted, false)
    await click('.select-other-source')
    assert.equal(staleSignal.aborted, true)
    finishStale()
    await settle()
    assert.equal(control('.native-ensemble .native-receipt'), null)
    for (const locale of ['zh', 'ms']) {
      uiLocale.value = locale
      await nextTick()
      assert.equal(control('.native-ensemble').lang, locale)
      assert.equal(control('.native-ensemble .feedback').textContent, nativeEnsembleCopyFor(locale).unsupported)
      assert.equal(control('.ensemble-panel summary').textContent, nativeEnsembleCopyFor(locale).title)
    }
  } finally {
    finishStale?.()
    cleanup()
  }
})

import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createHash, webcrypto } from 'node:crypto'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createWorkbenchClient } from '../src/api/workbench.js'
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
  const locale = dataModule('import { ref } from ' + JSON.stringify(import.meta.resolve('vue')) + '; export const uiLocale = ref("en")')
  const empty = dataModule('export default { render() { return null } }')
  const sourceProbe = dataModule(
    'import { h, ref } from ' + JSON.stringify(import.meta.resolve('vue')) + '\n' +
    'export default { props: ["methods"], emits: ["inspected"], setup(props, { emit }) {' +
    'const probe = ref(""); return () => h("div", { "data-probe": probe.value }, [' +
    'h("button", { class: "select-fixture-source", onClick: () => emit("inspected", { source: ' + JSON.stringify(source) + ' }) }, "Select source"),' +
    'h("button", { class: "probe-source-list", onClick: async () => {' +
    'try { await props.methods.list(); probe.value = "sent" } catch (error) { probe.value = error.code }' +
    '} }, "Read sources")]) } }'
  )
  const mountedComponents = {
    SourceLibrary: sourceProbe,
    SimulationPreparation: await compileMounted('../src/components/workbench/SimulationPreparation.vue'),
    NativeLaunch: await compileMounted('../src/components/workbench/NativeLaunch.vue'),
    NativeEnsemble: moduleUrl,
  }
  const replacements = { '../i18n/index.js': locale }
  for (const name of ['EvidenceResults', 'SourceLibrary', 'SourceIngestion', 'ExperimentComparison', 'PopulationWorkbench',
    'SimulationPreparation', 'NativeLaunch', 'NativeEnsemble', 'NativeObservations', 'ConnectedReports', 'ConnectedFollowup']) {
    replacements['../components/workbench/' + name + '.vue'] = mountedComponents[name] || empty
  }
  const { default: parent } = await import(await compileMounted('../src/views/ResearchWorkbench.vue', replacements))
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp(parent)
  app.component('RouterLink', { render() { return h('a', this.$slots.default?.()) } })
  app.mount(root)
  return { root, cleanup() { app.unmount(); root.remove() } }
}

async function exerciseParent(t, { fetchImpl, calls, network, pause, release }) {
  t.mock.method(globalThis, 'fetch', fetchImpl)
  const { root, cleanup } = await mountParent()
  const input = (selector, value) => {
    const field = root.querySelector(selector)
    field.value = value
    field.dispatchEvent(new dom.window.Event('input', { bubbles: true }))
  }
  const submit = selector => root.querySelector(selector).dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
  const click = async selector => {
    const control = root.querySelector(selector)
    assert.ok(control && !control.disabled, selector)
    control.click()
    await settle()
  }
  const member = '[data-member="1"]'
  const realDigest = crypto.subtle.digest.bind(crypto.subtle)
  let blockDigest = false, releaseDigest
  t.mock.method(crypto.subtle, 'digest', (algorithm, bytes) => {
    if (!blockDigest) return realDigest(algorithm, bytes)
    blockDigest = false
    return new Promise(resolve => { releaseDigest = () => resolve(realDigest(algorithm, bytes)) })
  })
  try {
    input('#graph-id', graph)
    input('#bearer-token', 'private-token')
    submit('.connection-form')
    await settle()
    await click('.select-fixture-source')
    input('#preparation-requirement', 'Retained evidence')
    input('#preparation-seed', '3')
    input('#preparation-rounds', '1')
    await click('.preparation-workbench .review')
    await click('.preparation-workbench .start')
    await click('.native-launch .review')
    const legacyReview = calls.at(-1)
    assert.match(legacyReview.path, /native-launch\/plan/)
    assert.ok(root.querySelector('.native-launch article.plan'))

    const disclosure = root.querySelector('.ensemble-panel')
    disclosure.open = true
    disclosure.dispatchEvent(new dom.window.Event('toggle'))
    await settle()
    input('#ensemble-seed-1', '7')
    input('#ensemble-seed-2', '9')
    await nextTick()

    // The child starts validating the draft while its parent still has a free transport.
    blockDigest = true
    await click(member + ' .ensemble-prep-review')
    assert.equal(typeof releaseDigest, 'function')
    pause(path => path.startsWith('/api/graph/data/'))
    await click('.graph-refresh')
    const sibling = network.at(-1)
    assert.match(sibling.path, /api\/graph\/data/)
    assert.equal(sibling.signal.aborted, false)
    const beforeClaim = network.length
    releaseDigest()
    await settle()
    assert.equal(network.length, beforeClaim)
    assert.equal(sibling.signal.aborted, false)
    assert.equal(root.querySelector(member + ' .preparation-reference'), null)
    release()
    await settle()

    await click(member + ' .ensemble-prep-review')
    await click(member + ' .ensemble-prep-start')
    await click(member + ' .ensemble-prep-status')
    await click(member + ' .ensemble-native-review')
    const review = calls.at(-1)
    assert.notEqual(review.request.launch_id, legacyReview.request.launch_id)
    await click(member + ' .ensemble-native-start')
    const start = calls.at(-1)
    assert.match(start.path, /native-launch\/start/)
    assert.equal(start.request.launch_id, review.request.launch_id)
    assert.match(root.querySelector(member + ' .member-notice').textContent, /Start was not confirmed/)

    // Clear uses the actual legacy component method. The ensemble selector can still recover.
    await click('.native-launch .clear')
    assert.equal(root.querySelector('.native-launch article.plan'), null)
    assert.ok(root.querySelector(member + ' .ensemble-native-status'))
    pause(path => path.includes('/native-launch/status/'))
    await click(member + ' .ensemble-native-status')
    const recovering = network.at(-1)
    assert.match(recovering.path, /native-launch\/status/)
    assert.deepEqual(calls.at(-1).request, start.request)

    // A source control attempts a sibling call while the ensemble owns the shared channel.
    const beforeSibling = network.length
    await click('.probe-source-list')
    submit('.query-section form')
    await settle()
    assert.equal(root.querySelector('[data-probe]').dataset.probe, 'busy')
    assert.equal(network.length, beforeSibling)
    await click('.native-launch .clear')
    assert.equal(recovering.signal.aborted, false)
    assert.equal(root.querySelector(member + ' .native-receipt'), null)
    release()
    await settle()
    assert.equal(root.querySelector(member + ' .native-receipt').textContent, digest(['evidence', start.request.launch_id]))
    assert.equal(calls.filter(call => call.path.includes('/native-launch/start/') && call.request.launch_id === start.request.launch_id).length, 1)
  } finally {
    releaseDigest?.()
    cleanup()
  }
}

test('two reviewed seeds serialize protected actions, recover exact Starts and discard stale member results', async t => {
  const prep = new Map(), launch = new Map(), calls = [], network = []
  let hold, finish = null
  const fetchImpl = async (url, configuration) => {
    assert.equal(configuration.headers.Authorization, 'Bearer private-token')
    const path = new URL(url).pathname
    network.push({ path, signal: configuration.signal })
    const graphRead = path.startsWith('/api/graph/data/')
    const request = graphRead ? null : JSON.parse(configuration.body)
    if (!graphRead) calls.push({ path, request })
    let result
    if (graphRead) {
      result = { graph_id: graph, nodes: [], edges: [], node_count: 0, edge_count: 0 }
    } else if (path.startsWith('/api/preparation/')) {
      if (path.endsWith('/plan')) {
        result = reviewed(request)
        prep.set(request.operation_id, published(result))
      } else {
        result = prep.get(request.operation_id)
        assert.equal(request.plan_sha256, result.plan_sha256)
        if (path.endsWith('/start') && result.options.seed === 7) throw new Error('private preparation detail')
      }
    } else if (path.includes('/native-launch/plan/')) {
      result = nativeReview(request, prep.get(request.preparation.operation_id))
      launch.set(request.launch_id, result)
    } else {
      const known = launch.get(request.launch_id)
      assert.equal(request.launch_sha256, known.launch_sha256)
      const starting = path.includes('/native-launch/start/')
      if (starting && known.request.seed === 7) throw new Error('private native detail')
      result = nativeState(known, starting ? 'running' : 'completed')
    }
    if (hold?.(path, request)) {
      hold = null
      return new Promise(resolve => { finish = () => resolve(response(result)) })
    }
    return response(result)
  }
  const client = createWorkbenchClient({ fetchImpl })
  await client.connect({ origin: 'http://127.0.0.1:5001', graph, token: 'private-token' })
  const base = await client.preparationPlan({ schema_version: 1, operation_id: uid(9), source_revision: source.source_revision, options }, source)
  const ready = await client.preparationStatus({ schema_version: 1, operation_id: base.operation_id, plan_sha256: base.plan_sha256 })
  const props = reactive({
    connected: true, source: clone(source), ready: clone(ready), displayGraphId: graph, locale: 'en',
    preparationMethods: { plan: client.preparationPlan, start: client.preparationStart, status: client.preparationStatus },
    nativeMethods: client.ensembleNative,
  })
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp({ setup: () => () => h(component, props) })
  app.mount(root)
  const member = index => root.querySelector('[data-member="' + index + '"]')
  const button = (index, action) => member(index).querySelector('.ensemble-' + action)
  function seed(index, value) {
    const input = root.querySelector('#ensemble-seed-' + index)
    input.value = value
    input.dispatchEvent(new dom.window.Event('input', { bubbles: true }))
  }
  async function click(index, action) {
    const target = button(index, action)
    assert.ok(target && !target.disabled)
    target.click()
    await settle()
  }
  try {
    const baselineCalls = calls.length
    assert.equal(root.querySelector('[role="status"]').getAttribute('aria-live'), 'polite')
    seed(1, '7'); seed(2, '7'); await nextTick()
    assert.equal(button(1, 'prep-review').disabled, true)
    seed(2, '8'); await nextTick()
    assert.equal(calls.length, baselineCalls)
    hold = path => path.endsWith('/plan')
    await click(1, 'prep-review')
    assert.equal(button(2, 'prep-review').disabled, true)
    button(2, 'prep-review').dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))
    await settle()
    assert.equal(calls.length, baselineCalls + 1)
    assert.equal(typeof finish, 'function')
    finish(); await settle()
    seed(2, '9'); await nextTick()
    await click(2, 'prep-review')
    const [reviewA, reviewB] = calls.slice(baselineCalls, baselineCalls + 2).map(call => call.request)
    assert.notEqual(reviewA.operation_id, reviewB.operation_id)
    assert.notEqual(reviewA.operation_id, base.operation_id)
    assert.deepEqual(reviewA.options, { ...options, seed: 7 })
    assert.deepEqual(reviewB.options, { ...options, seed: 9 })
    assert.equal(reviewA.source_revision, source.source_revision)
    assert.equal(reviewB.source_revision, source.source_revision)

    const bPrepared = member(2).textContent
    await click(1, 'prep-start')
    assert.equal(button(1, 'prep-start'), null)
    assert.match(member(1).textContent, /Start was not confirmed/)
    await click(1, 'prep-status')
    const [aStart, aStatus] = calls.slice(-2).map(call => call.request)
    assert.deepEqual(aStart, aStatus)
    assert.equal(aStart.operation_id, reviewA.operation_id)
    assert.equal(member(2).textContent, bPrepared)
    await click(2, 'prep-start')
    await click(1, 'native-review')
    await click(2, 'native-review')
    const [nativeA, nativeB] = calls.slice(-2).map(call => call.request)
    assert.notEqual(nativeA.launch_id, nativeB.launch_id)
    assert.deepEqual(nativeA.preparation, { operation_id: aStart.operation_id, plan_sha256: aStart.plan_sha256 })
    assert.equal(nativeB.preparation.operation_id, reviewB.operation_id)
    assert.equal(launch.get(nativeA.launch_id).request.seed, 7)
    assert.equal(launch.get(nativeB.launch_id).request.seed, 9)
    assert.notEqual(launch.get(nativeA.launch_id).request.artifact_sha256, launch.get(nativeB.launch_id).request.artifact_sha256)

    const bLaunch = member(2).textContent
    await click(1, 'native-start')
    assert.equal(button(1, 'native-start'), null)
    assert.equal(member(1).querySelector('.native-receipt'), null)
    assert.match(member(1).textContent, /Start was not confirmed/)
    await click(1, 'native-status')
    const [start, status] = calls.slice(-2).map(call => call.request)
    assert.deepEqual(start, status)
    assert.equal(start.launch_id, nativeA.launch_id)
    assert.match(member(1).querySelector('.native-state').textContent, /Completed with receipt/)
    assert.equal(member(1).querySelector('.native-receipt').textContent, digest(['evidence', nativeA.launch_id]))
    assert.equal(member(2).textContent, bLaunch)
    await click(2, 'native-start')
    assert.equal(member(2).querySelector('.native-receipt'), null)

    props.locale = 'ms'; await nextTick()
    assert.equal(root.querySelector('.native-ensemble').getAttribute('lang'), 'ms')
    assert.equal(member(1).querySelector('.native-state').textContent, nativeEnsembleCopyFor('ms').states.completed)
    hold = path => path.includes('/native-launch/status/')
    finish = null
    await click(2, 'native-status')
    assert.equal(calls.at(-1).request.launch_id, nativeB.launch_id)
    assert.equal(typeof finish, 'function')
    const newRevision = { ...clone(ready), project_revision: 2 }
    newRevision.plan_sha256 = digest(pick(newRevision, prepFields))
    props.ready = newRevision
    await nextTick()
    assert.equal(root.querySelector('.launch-reference, .native-receipt'), null)
    assert.equal(button(1, 'prep-review').disabled, true)
    props.source = { ...source, source_revision: uid(6) }; await nextTick()
    assert.equal(root.querySelector('.member'), null)
    props.source = clone(source)
    await nextTick()
    finish(); await settle()
    assert.equal(root.querySelector('#ensemble-seed-1').value, '')
    assert.equal(root.querySelector('.preparation-reference, .launch-reference, .native-receipt'), null)
    assert.equal(root.textContent.includes('private'), false)
  } finally {
    app.unmount()
    root.remove()
    client.disconnect()
  }
  await exerciseParent(t, {
    fetchImpl, calls, network,
    pause: predicate => { hold = predicate; finish = null },
    release: () => { assert.equal(typeof finish, 'function'); finish(); finish = null },
  })
})

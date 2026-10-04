<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { createWorkbenchClient, WorkbenchError, awareTimestamp } from '../api/workbench.js'
import { copyFor, safeError } from '../i18n/workbench.js'
import EvidenceResults from '../components/workbench/EvidenceResults.vue'
import SourceLibrary from '../components/workbench/SourceLibrary.vue'
import SourceIngestion from '../components/workbench/SourceIngestion.vue'
import ExperimentComparison from '../components/workbench/ExperimentComparison.vue'
const locale = ref('en'), copy = computed(() => copyFor(locale.value))
const origin = ref('http://127.0.0.1:5001'), graph = ref(''), token = ref(''), reveal = ref(false)
const connected = ref(false), busy = ref(false), status = ref('disconnected'), errorCode = ref('')
const ingestionRequest = ref('')
const bannerError = computed(() => {
  if (errorCode.value && ['unauthorized', 'origin_denied'].includes(errorCode.value)) return safeError(copy.value, errorCode.value)
  if (ingestionRequest.value && (errorCode.value || status.value === 'cancelled')) return copy.value.ingestionBanner[ingestionRequest.value]
  return errorCode.value ? safeError(copy.value, errorCode.value) : ''
})
const graphData = ref(null), result = ref(null), mode = ref('research'), query = ref(''), title = ref('')
const sections = ref([{ heading: '', query: '' }]), advanced = ref(false), topK = ref(10), validAt = ref(''), recordedBefore = ref('')
const graphPage = ref(0), graphKind = ref('nodes')
const client = createWorkbenchClient()
const sourceReset = ref(0)
const inspectedSource = ref(null)
const ingestionMethods = Object.freeze({
  plan: (payload, inspected) => operation(() => client.ingestionPlan(payload, inspected), false, true, 'plan'),
  execute: (payload, known) => operation(() => client.ingestionExecute(payload, known), false, true, 'execute'),
  status: (payload, known) => operation(() => client.ingestionStatus(payload, known, undefined, inspectedSource.value?.source.project_id), false, true, 'status')
})
// Methods capture the single private client. Neither token nor origin is a prop.
const sourceMethods = Object.freeze({
  list: () => operation(() => client.sourceList(), false, true),
  get: payload => operation(() => client.sourceGet(payload), false, true),
  retain: payload => operation(() => client.sourceRetain(payload), false, true)
})
const experimentMethods = Object.freeze({
  catalog: () => operation(() => client.experimentCatalog(), false, true),
  compare: (payload, catalog) => operation(() => client.experimentCompare(payload, catalog), false, true)
})
let generation = 0
const graphEntries = computed(() => graphData.value?.[graphKind.value] || [])
function clearProtected() { sourceReset.value++; inspectedSource.value = null; graphData.value = null; result.value = null; graphPage.value = 0 }
function disconnect() { generation++; client.disconnect(); token.value = ''; reveal.value = false; connected.value = false; busy.value = false; clearProtected(); errorCode.value = ''; ingestionRequest.value = ''; status.value = 'disconnected' }
function cancel() { generation++; client.cancel(); busy.value = false; status.value = 'cancelled'; errorCode.value = ''; if (!connected.value) { client.disconnect(); token.value = ''; clearProtected() } }
async function operation(action, connecting = false, source = false, ingestion = '') {
  const epoch = ++generation
  client.cancel()
  busy.value = true; errorCode.value = ''; ingestionRequest.value = ingestion; status.value = connecting ? 'connecting' : 'loading'
  if (connecting) { connected.value = false; clearProtected() } else if (!source) result.value = null
  try {
    const data = await action()
    if (epoch !== generation) { if (source) throw new WorkbenchError('cancelled'); return }
    if (connecting) { graphData.value = data; connected.value = true; status.value = 'connected' } else if (!source) { result.value = data; status.value = 'success' } else status.value = 'connected'
    return data
  } catch (e) {
    if (epoch !== generation) { if (source) throw e; return }
    errorCode.value = e instanceof WorkbenchError ? e.code : 'invalid_request'
    status.value = errorCode.value === 'cancelled' ? 'cancelled' : ['unauthorized', 'origin_denied'].includes(errorCode.value) ? 'denied' : 'error'
    if (connecting || ['unauthorized', 'origin_denied'].includes(errorCode.value)) { client.disconnect(); connected.value = false; token.value = ''; reveal.value = false; clearProtected() }
    if (source) throw e
  } finally { if (epoch === generation) busy.value = false }
}
function connect() {
  const credentials = { origin: origin.value, graph: graph.value, token: token.value }
  token.value = ''; reveal.value = false
  return operation(() => client.connect(credentials), true)
}
function submit() {
  return operation(() => {
    if (!connected.value || !graphData.value) throw new WorkbenchError('disconnected')
    const options = { schema_version: 1, display_graph_ids: [graphData.value.graph_id] }
    for (const [key, value] of [['valid_at', validAt.value], ['recorded_before', recordedBefore.value]]) {
      if (value) { if (!value.endsWith('Z')) throw new WorkbenchError('invalid_request'); options[key] = awareTimestamp(value) }
    }
    if (mode.value === 'research') return client.research({ ...options, text: query.value, top_k: topK.value })
    return client.dossier({ ...options, title: title.value, sections: sections.value.map(s => ({ heading: s.heading, query: s.query, top_k: topK.value })) })
  })
}
onBeforeUnmount(disconnect)
</script>
<template>
  <div class="workbench" :lang="locale">
    <a class="skip" href="#research-main">{{ copy.skip }}</a>
    <header class="header"><RouterLink to="/" class="brand">MIROFISH <span>{{ copy.home }}</span></RouterLink><label for="workbench-language">{{ copy.language }}<select id="workbench-language" v-model="locale"><option value="en">English</option><option value="zh">中文</option><option value="ms">Bahasa Melayu</option></select></label></header>
    <main id="research-main" tabindex="-1">
      <div class="intro"><p class="eyebrow">MIROFISH RESEARCH LAB</p><h1>{{ copy.title }}</h1><p>{{ copy.intro }}</p></div>
      <section class="connection" aria-labelledby="connection-title"><h2 id="connection-title">{{ copy.connection }}</h2>
        <form @submit.prevent="connect" class="connection-form">
          <label for="api-origin">{{ copy.origin }}<input id="api-origin" v-model="origin" :disabled="connected || busy" required spellcheck="false" autocomplete="off" aria-describedby="connection-help"></label>
          <label for="graph-id">{{ copy.graph }}<input id="graph-id" v-model="graph" :disabled="connected || busy" required maxlength="128" spellcheck="false" autocomplete="off"></label>
          <label for="bearer-token">{{ copy.token }}<input id="bearer-token" v-model="token" :type="reveal ? 'text' : 'password'" :disabled="connected || busy" required maxlength="505" autocomplete="current-password" spellcheck="false" aria-describedby="connection-help"></label>
          <button type="button" :disabled="connected || busy" :aria-pressed="reveal" @click="reveal = !reveal">{{ reveal ? copy.hide : copy.reveal }}</button>
          <button v-if="!connected" class="primary" type="submit" :disabled="busy">{{ copy.connect }}</button><button v-else type="button" @click="disconnect">{{ copy.disconnect }}</button>
        </form><p id="connection-help" class="help">{{ copy.privacy }}</p>
        <p class="status" role="status" aria-live="polite" aria-atomic="true">{{ copy.status[status] }}<span v-if="bannerError"> · {{ bannerError }}</span></p><button v-if="busy" type="button" @click="cancel">{{ copy.cancel }}</button>
      </section>
      <section v-if="graphData" class="graph-overview"><h2>{{ copy.graphOverview }} <span class="mono">{{ graphData.graph_id }}</span></h2><p>{{ copy.nodes }}: {{ graphData.node_count }} · {{ copy.edges }}: {{ graphData.edge_count }}</p>
        <details><summary>{{ copy.overview }}</summary><div class="choices"><label><input v-model="graphKind" value="nodes" type="radio" @change="graphPage = 0">{{ copy.nodes }}</label><label><input v-model="graphKind" value="edges" type="radio" @change="graphPage = 0">{{ copy.edges }}</label></div><ul><li v-for="entry in graphEntries.slice(graphPage * 10, (graphPage + 1) * 10)" :key="entry.uuid"><details><summary>{{ entry.name || entry.uuid }}</summary><p class="mono">{{ entry.uuid }}</p><p>{{ entry.summary }}</p><p>{{ entry.fact }}</p><p v-if="graphKind === 'edges'">{{ entry.source_node_name }} → {{ entry.target_node_name }}</p></details></li></ul><nav v-if="graphEntries.length > 10" class="actions" :aria-label="copy.graphOverview"><button type="button" :disabled="!graphPage" @click="graphPage--">{{ copy.previous }}</button><span>{{ copy.range }} {{ graphPage + 1 }} / {{ Math.ceil(graphEntries.length / 10) }}</span><button type="button" :disabled="(graphPage + 1) * 10 >= graphEntries.length" @click="graphPage++">{{ copy.next }}</button></nav></details>
      </section>
      <SourceLibrary :methods="sourceMethods" :connected="connected" :busy="busy" :reset-version="sourceReset" :locale="locale" @inspected="inspectedSource = $event" />
      <SourceIngestion :methods="ingestionMethods" :inspected="inspectedSource" :connected="connected" :busy="busy" :reset-version="sourceReset" :locale="locale" />
      <ExperimentComparison :methods="experimentMethods" :connected="connected" :busy="busy" :reset-version="sourceReset" :locale="locale" />
      <section class="query-section" aria-labelledby="query-title"><h2 id="query-title">{{ copy.research }}</h2>
        <form @submit.prevent="submit"><fieldset :disabled="!connected || busy"><legend>{{ copy.scope }}</legend><div class="choices"><label><input v-model="mode" type="radio" value="research">{{ copy.research }}</label><label><input v-model="mode" type="radio" value="dossier">{{ copy.dossier }}</label></div>
          <label v-if="mode === 'research'" for="question">{{ copy.query }}<textarea id="question" v-model="query" required rows="3" maxlength="4000"></textarea></label>
          <template v-else><label for="dossier-title">{{ copy.dossierTitle }}<input id="dossier-title" v-model="title" required maxlength="512"></label><div v-for="(section, i) in sections" :key="i" class="dossier-section"><label :for="`heading-${i}`">{{ copy.heading }} {{ i + 1 }}<input :id="`heading-${i}`" v-model="section.heading" required maxlength="512"></label><label :for="`query-${i}`">{{ copy.query }} {{ i + 1 }}<textarea :id="`query-${i}`" v-model="section.query" required rows="2" maxlength="4000"></textarea></label><button v-if="sections.length > 1" type="button" @click="sections.splice(i, 1)">{{ copy.remove }} {{ i + 1 }}</button></div><button type="button" :disabled="sections.length >= 6" @click="sections.push({ heading: '', query: '' })">{{ copy.add }}</button></template>
          <button type="button" class="advanced-button" :aria-expanded="advanced" aria-controls="advanced-options" @click="advanced = !advanced">{{ copy.advanced }}</button>
          <div v-show="advanced" id="advanced-options" class="advanced"><label for="top-k">{{ copy.topK }}<input id="top-k" v-model.number="topK" type="number" min="1" max="100" required></label><label for="valid-at">{{ copy.validAt }}<input id="valid-at" v-model="validAt" type="text" placeholder="2026-10-01T00:00:00Z" aria-describedby="utc-help"></label><label for="recorded-before">{{ copy.recordedBefore }}<input id="recorded-before" v-model="recordedBefore" type="text" placeholder="2026-10-01T00:00:00Z" aria-describedby="utc-help"></label><p id="utc-help" class="help">{{ copy.utcHint }}</p></div><div class="actions"><button class="primary" type="submit">{{ copy.submit }}</button></div>
        </fieldset></form>
      </section><EvidenceResults :result="result" :locale="locale" />
    </main>
  </div>
</template>
<style scoped>
.workbench{--canvas:#f4f6f9;--surface:#fff;--ink:#172638;--muted:#526174;--primary:#174d96;--border:#b9c4d2;--control-border:#7b8797;--accent:#a9650c;background:var(--canvas);color:var(--ink);min-height:100vh;font:16px/1.5 system-ui,sans-serif;overflow-wrap:anywhere}.workbench *{box-sizing:border-box}.header{background:var(--surface);border-bottom:1px solid var(--border);padding:16px clamp(16px,4vw,48px);display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:16px}.brand{font-weight:750;letter-spacing:.08em;text-decoration:none;color:var(--ink);min-height:44px;display:flex;align-items:center;flex-wrap:wrap;gap:16px}.brand span{letter-spacing:0;font-weight:500;color:var(--primary)}main{max-width:1320px;margin:0 auto;padding:32px clamp(16px,4vw,48px) 64px}.intro{margin-bottom:24px}h1{font-size:clamp(1.7rem,4vw,2.4rem);line-height:1.2;margin:8px 0 12px}h2{font-size:1.25rem;margin:0 0 16px}p{margin:8px 0}.eyebrow{font-size:.8rem;letter-spacing:.1em;color:var(--muted)}.connection,.query-section,.graph-overview{background:var(--surface);padding:24px;border:1px solid var(--border);border-radius:8px;margin-bottom:24px;min-width:0}label{display:flex;flex-direction:column;gap:6px;font-weight:550;min-width:0}input,textarea,select{width:100%;min-width:0;background:var(--surface);color:var(--ink);border:1px solid var(--control-border);border-radius:5px;font:inherit;padding:10px 12px;min-height:44px}textarea{resize:vertical}button{min-height:44px;max-width:100%;padding:10px 16px;border:1px solid var(--border);border-radius:5px;background:var(--surface);color:var(--primary);font:inherit;font-weight:550;cursor:pointer;overflow-wrap:anywhere}.primary{background:var(--primary);color:white;border-color:var(--primary)}button:hover:not(:disabled){filter:brightness(.92)}button:disabled,fieldset:disabled{opacity:.65}button:disabled{cursor:default}input:disabled,textarea:disabled{background:var(--canvas)}button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid var(--primary);outline-offset:3px}.connection-form{display:grid;gap:12px;align-items:end}.help{font-size:.875rem;color:var(--muted);margin-top:12px}.status{border-left:3px solid var(--primary);padding:8px 12px;margin-top:16px}fieldset{border:0;padding:0;margin:0;min-width:0}legend{color:var(--muted);font-size:.875rem}.choices{display:flex;flex-wrap:wrap;gap:16px;margin:12px 0 20px}.choices label{flex-direction:row;align-items:center;min-height:44px}.choices input{width:20px;min-height:20px;margin-right:4px}.actions{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin-top:20px}.advanced-button{display:block;margin:20px 0 12px}.advanced{display:grid;gap:12px}.dossier-section{border-top:1px solid var(--border);margin-top:16px;padding:16px 0;display:grid;gap:12px}.dossier-section button{justify-self:start}.mono{font:.875rem/1.6 ui-monospace,Consolas,monospace}summary{cursor:pointer;min-height:44px;padding:10px 0}ul{padding-left:20px}.skip{position:absolute;left:16px;top:-100px;padding:12px;background:var(--surface);color:var(--primary);z-index:2}.skip:focus{top:8px}@media(min-width:768px){.connection-form{grid-template-columns:repeat(3,minmax(0,1fr))}.advanced{grid-template-columns:repeat(3,minmax(0,1fr))}.advanced .help{grid-column:1/-1}}@media(max-width:480px){.connection,.query-section,.graph-overview{padding:16px}}@media(prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
</style>

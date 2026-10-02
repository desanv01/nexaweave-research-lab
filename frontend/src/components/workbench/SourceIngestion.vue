<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { freshId, operationId, validateOntology, ontologyNameIssue, ontologyDescriptionIssue } from '../../api/sourceIngestion.js'
import { copyFor } from '../../i18n/workbench.js'
const props = defineProps({ methods: { type: Object, required: true }, inspected: Object, connected: Boolean, busy: Boolean, resetVersion: Number, locale: { type: String, default: 'en' } })
const copy = computed(() => copyFor(props.locale).ingestion)
const entities = ref([]), edges = ref([]), plan = ref(null), snapshot = ref(null), attempt = ref(null), outcome = ref(null)
const reviewed = ref(false), manual = ref(''), pending = ref(false), error = ref(''), phase = ref(''), reviewPanel = ref(null), planButton = ref(null)
const validationShown = ref(false), errorSummary = ref(null)
// UI identity never enters the strict ontology payload. Removing another row
// keeps surviving controls and their linked errors at the same identifiers.
let uiIds = new WeakMap(), counters = {}
function fieldId(kind, item, field) {
  if (!uiIds.has(item)) { const number = counters[kind] || 0; counters[kind] = number + 1; uiIds.set(item, number) }
  return `${kind}-${field}-${uiIds.get(item)}`
}
function collectFieldErrors() {
  const issues = [], entityNames = new Set(entities.value.map(e => e.name))
  function add(kind, item, field, code, labels) { const id = fieldId(kind, item, field); if (code && !issues.some(e => e.id === id)) issues.push({ id, code, labels }) }
  for (const [items, kind] of [[entities.value, 'entity'], [edges.value, 'edge']]) {
    const names = new Set()
    items.forEach((item, i) => {
      const prefix = [[kind === 'entity' ? 'entities' : 'relationships', i + 1]]
      add(kind, item, 'name', ontologyNameIssue(item.name, names, kind), [...prefix, ['name']]); names.add(item.name)
      add(kind, item, 'description', ontologyDescriptionIssue(item.description), [...prefix, ['description']])
      const attrs = new Set()
      item.attributes.forEach((attr, j) => {
        const label = [...prefix, ['attributeName', j + 1]]
        add(`${kind}-attribute`, attr, 'name', ontologyNameIssue(attr.name, attrs, 'attribute'), label); attrs.add(attr.name)
        add(`${kind}-attribute`, attr, 'type', ['text', 'integer', 'number', 'boolean'].includes(attr.type) ? '' : 'type', [...prefix, ['attributeType', j + 1]])
        add(`${kind}-attribute`, attr, 'description', ontologyDescriptionIssue(attr.description), [...label, ['description']])
      })
      if (kind === 'edge') {
        const pairs = new Set()
        item.source_targets.forEach((pair, j) => {
          add('edge-pair', pair, 'source', entityNames.has(pair.source) ? '' : 'pair', [...prefix, ['from', j + 1]])
          add('edge-pair', pair, 'target', entityNames.has(pair.target) ? '' : 'pair', [...prefix, ['to', j + 1]])
          const key = `${pair.source}/${pair.target}`
          if (pairs.has(key)) add('edge-pair', pair, 'source', 'duplicatePair', [...prefix, ['from', j + 1]])
          pairs.add(key)
        })
      }
    })
  }
  return issues
}
const fieldErrors = computed(() => validationShown.value ? collectFieldErrors() : [])
const errorsById = computed(() => new Map(fieldErrors.value.map(e => [e.id, e])))
function fieldError(id) { const problem = errorsById.value.get(id); return problem ? copy.value.validation[problem.code] : '' }
function fieldProps(id) { return { id, 'aria-invalid': errorsById.value.has(id) ? 'true' : undefined, 'aria-describedby': errorsById.value.has(id) ? `${id}-error` : undefined } }
function fieldTitle(problem) { return problem.labels.map(([key, ordinal]) => copy.value[key] + (ordinal ? ` ${ordinal}` : '')).join(' · ') }
function focusField(id) { errorSummary.value?.closest('form')?.querySelector(`[id="${id}"]`)?.focus() }
let generation = 0
const locked = computed(() => !!attempt.value || pending.value || props.busy || !props.connected)
const eligible = computed(() => !!props.inspected && props.inspected.source.codepoint_length <= 32768 && props.inspected.source.codepoint_length > 0 && props.inspected.passages.length > 0)
const unavailable = computed(() => !props.connected || props.busy || pending.value)
const errorText = computed(() => {
  if (!error.value) return ''
  if (['model_calls_disabled', 'budget_denied', 'source_denied'].includes(error.value)) return copy.value.denied
  if (['not_found', 'source_unavailable', 'unsupported'].includes(error.value)) return copy.value.notFound
  if (error.value === 'invalid_request') return copy.value.invalid
  if (error.value === 'crypto_unavailable') return copy.value.crypto
  return copy.value.failed
})
function starter() {
  uiIds = new WeakMap(); counters = {}
  entities.value = [{ name: 'Person', description: 'A person mentioned in the source.', attributes: [] }, { name: 'Organization', description: 'An organization mentioned in the source.', attributes: [] }]
  edges.value = [{ name: 'WORKS_FOR', description: 'A declared employment relationship.', attributes: [], source_targets: [{ source: 'Person', target: 'Organization' }] }]
}
function clear() { generation++; validationShown.value = false; plan.value = null; snapshot.value = null; attempt.value = null; outcome.value = null; reviewed.value = false; manual.value = ''; pending.value = false; error.value = ''; phase.value = ''; starter() }
// Source changes invalidate all old protected state, including in-flight replies.
watch(() => [props.connected, props.resetVersion, props.inspected], clear, { flush: 'sync' })
watch([entities, edges], () => { if (!attempt.value) { generation++; plan.value = null; snapshot.value = null; reviewed.value = false; outcome.value = null; phase.value = '' } }, { deep: true, flush: 'sync' })
onBeforeUnmount(clear)
starter()
function addEntity() { if (!locked.value && entities.value.length < 10) entities.value.push({ name: '', description: '', attributes: [] }) }
function addEdge() { if (!locked.value && edges.value.length < 10) edges.value.push({ name: '', description: '', attributes: [], source_targets: [{ source: entities.value[0]?.name || '', target: entities.value[0]?.name || '' }] }) }
function addAttribute(item) { if (!locked.value && item.attributes.length < 5) item.attributes.push({ name: '', description: '', type: 'text' }) }
function addPair(edge) { if (!locked.value && edge.source_targets.length < 10) edge.source_targets.push({ source: entities.value[0]?.name || '', target: entities.value[0]?.name || '' }) }
async function request(action, apply) {
  if (unavailable.value) return
  const epoch = ++generation
  pending.value = true; outcome.value = null; error.value = ''; phase.value = 'working'
  try {
    const data = await action()
    if (epoch !== generation || !props.connected) return
    apply(data); await nextTick(); reviewPanel.value?.focus()
  } catch (e) { if (epoch === generation && props.connected) { error.value = e.code || 'invalid_reply'; phase.value = attempt.value ? 'unknown' : '' } }
  finally { if (epoch === generation) pending.value = false }
}
async function makePlan() {
  if (locked.value || !eligible.value) return
  plan.value = null; snapshot.value = null; outcome.value = null; reviewed.value = false
  error.value = ''; phase.value = ''
  if (collectFieldErrors().length) {
    validationShown.value = true
    const epoch = generation
    await nextTick()
    if (epoch === generation) errorSummary.value?.focus()
    return
  }
  validationShown.value = false
  try {
    const ontology = validateOntology({ schema_version: 1, revision: freshId(), entity_types: JSON.parse(JSON.stringify(entities.value)), edge_types: JSON.parse(JSON.stringify(edges.value)) })
    const payload = { schema_version: 1, source_revision: props.inspected.source.source_revision, operation_id: freshId(), ontology }
    // The transport clones the inspected source independently for correlation.
    return request(() => props.methods.plan(payload, props.inspected), data => { snapshot.value = payload; plan.value = data; phase.value = 'planned' })
  } catch (e) { error.value = e.code || 'invalid_request' }
}
function execute() {
  if (unavailable.value || attempt.value || !plan.value || !snapshot.value || !reviewed.value) return
  // Latch before entering any asynchronous code; every failure remains status-only.
  attempt.value = { payload: snapshot.value, known: plan.value }
  reviewed.value = false
  return request(() => props.methods.execute(attempt.value.payload, attempt.value.known), data => { outcome.value = data; phase.value = 'returned' })
}
function statusKnown() {
  if (!attempt.value) return
  const saved = attempt.value
  return request(() => props.methods.status({ operation_id: saved.payload.operation_id }, saved.known), data => { outcome.value = data; phase.value = 'returned' })
}
function statusManual() {
  if (unavailable.value) return
  try {
    const id = operationId(manual.value)
    // Never weaken correlation by querying a known operation as manual recovery.
    const known = attempt.value?.payload.operation_id === id ? attempt.value.known : undefined
    return request(() => props.methods.status({ operation_id: id }, known), data => { outcome.value = data; phase.value = 'returned' })
  } catch { error.value = 'invalid_request' }
}
async function dismissPlan() { if (locked.value) return; plan.value = null; snapshot.value = null; reviewed.value = false; phase.value = ''; await nextTick(); planButton.value?.focus() }
</script>
<template>
  <section class="source-ingestion" aria-labelledby="ingestion-title">
    <h2 id="ingestion-title">{{ copy.title }}</h2><p>{{ copy.intro }}</p>
    <p class="notice">{{ copy.authorization }}</p><p>{{ copy.recoveryHint }}</p>
    <p v-if="!inspected">{{ copy.inspectFirst }}</p>
    <div v-else class="selected-source"><h3>{{ inspected.source.source_name }}</h3><dl><dt>{{ copy.revision }}</dt><dd>{{ inspected.source.source_revision }}</dd><dt>{{ copy.digest }}</dt><dd>{{ inspected.source.text_sha256 }}</dd><dt>{{ copy.codepoints }}</dt><dd>{{ inspected.source.codepoint_length }} / 32768</dd></dl><p v-if="!eligible" class="notice">{{ copy.ineligible }}</p></div>
    <form @submit.prevent="makePlan">
      <div v-if="fieldErrors.length" ref="errorSummary" class="error-summary" tabindex="-1" aria-labelledby="ontology-error-title"><h3 id="ontology-error-title">{{ copy.validationSummary }}</h3><p>{{ copy.validationHint }}</p><ul><li v-for="problem in fieldErrors" :key="problem.id"><a :href="`#${problem.id}`" @click.prevent="focusField(problem.id)">{{ fieldTitle(problem) }}: {{ copy.validation[problem.code] }}</a></li></ul></div>
      <fieldset :disabled="locked || !eligible"><legend>{{ copy.ontology }}</legend><p>{{ copy.starter }}</p><p class="help">{{ copy.limits }}</p>
        <h3>{{ copy.entities }}</h3>
        <article v-for="(entity, i) in entities" :key="fieldId('entity', entity, 'name')" class="ontology-item">
          <label :for="fieldId('entity', entity, 'name')">{{ copy.name }} {{ i + 1 }}<input v-bind="fieldProps(fieldId('entity', entity, 'name'))" v-model="entity.name" required maxlength="64" autocomplete="off"><span v-if="fieldError(fieldId('entity', entity, 'name'))" :id="`${fieldId('entity', entity, 'name')}-error`" class="field-error">{{ fieldError(fieldId('entity', entity, 'name')) }}</span></label>
          <label :for="fieldId('entity', entity, 'description')">{{ copy.description }}<textarea v-bind="fieldProps(fieldId('entity', entity, 'description'))" v-model="entity.description" required rows="2" maxlength="1000"></textarea><span v-if="fieldError(fieldId('entity', entity, 'description'))" :id="`${fieldId('entity', entity, 'description')}-error`" class="field-error">{{ fieldError(fieldId('entity', entity, 'description')) }}</span></label>
          <div v-for="(attr, j) in entity.attributes" :key="fieldId('entity-attribute', attr, 'name')" class="attribute">
            <label :for="fieldId('entity-attribute', attr, 'name')">{{ copy.attributeName }}<input v-bind="fieldProps(fieldId('entity-attribute', attr, 'name'))" v-model="attr.name" required maxlength="64"><span v-if="fieldError(fieldId('entity-attribute', attr, 'name'))" :id="`${fieldId('entity-attribute', attr, 'name')}-error`" class="field-error">{{ fieldError(fieldId('entity-attribute', attr, 'name')) }}</span></label>
            <label :for="fieldId('entity-attribute', attr, 'type')">{{ copy.attributeType }}<select v-bind="fieldProps(fieldId('entity-attribute', attr, 'type'))" v-model="attr.type"><option value="text">{{ copy.text }}</option><option value="integer">{{ copy.integer }}</option><option value="number">{{ copy.number }}</option><option value="boolean">{{ copy.boolean }}</option></select><span v-if="fieldError(fieldId('entity-attribute', attr, 'type'))" :id="`${fieldId('entity-attribute', attr, 'type')}-error`" class="field-error">{{ fieldError(fieldId('entity-attribute', attr, 'type')) }}</span></label>
            <label :for="fieldId('entity-attribute', attr, 'description')">{{ copy.description }}<textarea v-bind="fieldProps(fieldId('entity-attribute', attr, 'description'))" v-model="attr.description" required rows="2" maxlength="1000"></textarea><span v-if="fieldError(fieldId('entity-attribute', attr, 'description'))" :id="`${fieldId('entity-attribute', attr, 'description')}-error`" class="field-error">{{ fieldError(fieldId('entity-attribute', attr, 'description')) }}</span></label><button type="button" @click="entity.attributes.splice(j, 1)">{{ copy.removeAttribute }} {{ j + 1 }}</button>
          </div>
          <div class="actions"><button type="button" :disabled="entity.attributes.length >= 5" @click="addAttribute(entity)">{{ copy.addAttribute }}</button><button type="button" :disabled="entities.length === 1" @click="entities.splice(i, 1)">{{ copy.removeEntity }} {{ i + 1 }}</button></div>
        </article>
        <button type="button" :disabled="entities.length >= 10" @click="addEntity">{{ copy.addEntity }}</button>
        <h3>{{ copy.relationships }}</h3>
        <article v-for="(edge, i) in edges" :key="fieldId('edge', edge, 'name')" class="ontology-item">
          <label :for="fieldId('edge', edge, 'name')">{{ copy.name }} {{ i + 1 }}<input v-bind="fieldProps(fieldId('edge', edge, 'name'))" v-model="edge.name" required maxlength="64" autocomplete="off"><span v-if="fieldError(fieldId('edge', edge, 'name'))" :id="`${fieldId('edge', edge, 'name')}-error`" class="field-error">{{ fieldError(fieldId('edge', edge, 'name')) }}</span></label>
          <label :for="fieldId('edge', edge, 'description')">{{ copy.description }}<textarea v-bind="fieldProps(fieldId('edge', edge, 'description'))" v-model="edge.description" required rows="2" maxlength="1000"></textarea><span v-if="fieldError(fieldId('edge', edge, 'description'))" :id="`${fieldId('edge', edge, 'description')}-error`" class="field-error">{{ fieldError(fieldId('edge', edge, 'description')) }}</span></label>
          <div v-for="(pair, j) in edge.source_targets" :key="fieldId('edge-pair', pair, 'source')" class="pair">
            <label :for="fieldId('edge-pair', pair, 'source')">{{ copy.from }} {{ j + 1 }}<select v-bind="fieldProps(fieldId('edge-pair', pair, 'source'))" v-model="pair.source"><option v-for="entity in entities" :key="fieldId('entity', entity, 'name')" :value="entity.name">{{ entity.name }}</option></select><span v-if="fieldError(fieldId('edge-pair', pair, 'source'))" :id="`${fieldId('edge-pair', pair, 'source')}-error`" class="field-error">{{ fieldError(fieldId('edge-pair', pair, 'source')) }}</span></label>
            <label :for="fieldId('edge-pair', pair, 'target')">{{ copy.to }} {{ j + 1 }}<select v-bind="fieldProps(fieldId('edge-pair', pair, 'target'))" v-model="pair.target"><option v-for="entity in entities" :key="fieldId('entity', entity, 'name')" :value="entity.name">{{ entity.name }}</option></select><span v-if="fieldError(fieldId('edge-pair', pair, 'target'))" :id="`${fieldId('edge-pair', pair, 'target')}-error`" class="field-error">{{ fieldError(fieldId('edge-pair', pair, 'target')) }}</span></label><button type="button" :disabled="edge.source_targets.length === 1" @click="edge.source_targets.splice(j, 1)">{{ copy.removePair }} {{ j + 1 }}</button>
          </div>
          <div v-for="(attr, j) in edge.attributes" :key="fieldId('edge-attribute', attr, 'name')" class="attribute">
            <label :for="fieldId('edge-attribute', attr, 'name')">{{ copy.attributeName }}<input v-bind="fieldProps(fieldId('edge-attribute', attr, 'name'))" v-model="attr.name" required maxlength="64"><span v-if="fieldError(fieldId('edge-attribute', attr, 'name'))" :id="`${fieldId('edge-attribute', attr, 'name')}-error`" class="field-error">{{ fieldError(fieldId('edge-attribute', attr, 'name')) }}</span></label>
            <label :for="fieldId('edge-attribute', attr, 'type')">{{ copy.attributeType }}<select v-bind="fieldProps(fieldId('edge-attribute', attr, 'type'))" v-model="attr.type"><option value="text">{{ copy.text }}</option><option value="integer">{{ copy.integer }}</option><option value="number">{{ copy.number }}</option><option value="boolean">{{ copy.boolean }}</option></select><span v-if="fieldError(fieldId('edge-attribute', attr, 'type'))" :id="`${fieldId('edge-attribute', attr, 'type')}-error`" class="field-error">{{ fieldError(fieldId('edge-attribute', attr, 'type')) }}</span></label>
            <label :for="fieldId('edge-attribute', attr, 'description')">{{ copy.description }}<textarea v-bind="fieldProps(fieldId('edge-attribute', attr, 'description'))" v-model="attr.description" required rows="2" maxlength="1000"></textarea><span v-if="fieldError(fieldId('edge-attribute', attr, 'description'))" :id="`${fieldId('edge-attribute', attr, 'description')}-error`" class="field-error">{{ fieldError(fieldId('edge-attribute', attr, 'description')) }}</span></label><button type="button" @click="edge.attributes.splice(j, 1)">{{ copy.removeAttribute }} {{ j + 1 }}</button>
          </div>
          <div class="actions"><button type="button" :disabled="edge.attributes.length >= 5" @click="addAttribute(edge)">{{ copy.addAttribute }}</button><button type="button" :disabled="edge.source_targets.length >= 10" @click="addPair(edge)">{{ copy.addPair }}</button><button type="button" @click="edges.splice(i, 1)">{{ copy.removeEdge }} {{ i + 1 }}</button></div>
        </article>
        <button type="button" :disabled="edges.length >= 10" @click="addEdge">{{ copy.addEdge }}</button><div class="actions"><button ref="planButton" type="submit" class="primary">{{ copy.plan }}</button></div>
      </fieldset>
    </form>
    <p class="feedback" role="status" aria-live="polite" aria-atomic="true">{{ copy[phase] || '' }} {{ errorText }}</p>
    <article v-if="plan" ref="reviewPanel" class="plan-review" tabindex="-1" @keydown.esc.stop.prevent="dismissPlan"><h3>{{ copy.review }}</h3><p>{{ copy.planFlags }}</p><dl><dt>{{ copy.operation }}</dt><dd class="operation-id">{{ plan.operation_id }}</dd><dt>{{ copy.episode }}</dt><dd>{{ plan.episode_id }}</dd><dt>{{ copy.fingerprint }}</dt><dd>{{ plan.fingerprint }}</dd><dt>{{ copy.evidence }}</dt><dd>{{ plan.evidence_ids.length }}</dd><dt>{{ copy.ontologyRevision }}</dt><dd>{{ plan.ontology_revision }}</dd></dl><ul><li v-for="id in plan.evidence_ids" :key="id">{{ id }}</li></ul>
      <p>{{ copy.charge }}</p><label class="acknowledgement"><input v-model="reviewed" type="checkbox" :disabled="!!attempt || unavailable">{{ copy.acknowledge }}</label><div class="actions"><button type="button" class="primary" :disabled="!reviewed || !!attempt || unavailable" @click="execute">{{ copy.execute }}</button><button type="button" :disabled="locked" @click="dismissPlan">{{ copy.dismiss }}</button></div>
    </article>
    <div v-if="attempt" class="attempt"><h3>{{ copy.submitted }}</h3><p class="operation-id">{{ attempt.payload.operation_id }}</p><p>{{ copy.noRetry }}</p><button type="button" :disabled="unavailable" @click="statusKnown">{{ copy.checkStatus }}</button></div>
    <article v-if="outcome" class="outcome"><h3>{{ copy.outcome }}</h3><p class="operation-id">{{ outcome.operation_id }}</p><dl><dt>{{ copy.graphState }}</dt><dd>{{ copy.graphStates[outcome.state] }}</dd><dt>{{ copy.budgetState }}</dt><dd>{{ copy.budgetStates[outcome.budget_state] }}</dd><dt>{{ copy.ceiling }}</dt><dd>{{ outcome.ceiling_microusd ?? copy.unknown }}</dd><dt>{{ copy.actualCharge }}</dt><dd>{{ copy.unknown }}</dd><dt>{{ copy.modelCalls }}</dt><dd>{{ copy.unknown }}</dd></dl><p>{{ copy.separate }}</p></article>
    <form class="manual-recovery" @submit.prevent="statusManual"><h3>{{ copy.manual }}</h3><p>{{ copy.manualHint }}</p><label for="ingestion-operation">{{ copy.operation }}<input id="ingestion-operation" v-model="manual" :disabled="unavailable" required maxlength="36" spellcheck="false" autocomplete="off"></label><button type="submit" :disabled="unavailable">{{ copy.recover }}</button></form>
  </section>
</template>
<style scoped>
.error-summary{border:2px solid var(--accent);border-radius:5px;padding:16px;margin:16px 0;overflow-wrap:anywhere}.error-summary h3{margin-top:0}.error-summary a{color:var(--primary);display:inline-block;min-height:44px;padding:10px 0;line-height:1.5}.field-error{font-weight:500;border-left:3px solid var(--accent);padding:6px 10px}.error-summary:focus,.error-summary a:focus-visible{outline:3px solid var(--primary);outline-offset:3px}[aria-invalid="true"]{border:2px solid var(--accent)}
.source-ingestion{background:var(--surface);color:var(--ink);border:1px solid var(--border);border-radius:8px;padding:24px;margin:24px 0;min-width:0;overflow-wrap:anywhere}.source-ingestion *{box-sizing:border-box}h2{font-size:1.25rem}h3{font-size:1.1rem;margin:20px 0 12px}fieldset{border:0;padding:0;margin:0;min-width:0}legend{font-weight:600}label{display:flex;flex-direction:column;gap:6px;margin:12px 0;min-width:0}input,textarea,select,button{font:inherit;min-height:44px;max-width:100%;min-width:0;border:1px solid var(--control-border);border-radius:5px;padding:10px 12px;color:var(--ink);background:var(--surface)}input,textarea,select{width:100%}textarea{resize:vertical}button{color:var(--primary);cursor:pointer;touch-action:manipulation}.primary{background:var(--primary);color:var(--surface)}button:disabled{opacity:.65;cursor:default}button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,.plan-review:focus{outline:3px solid var(--primary);outline-offset:3px}button:hover:not(:disabled){filter:brightness(.92)}.actions,.pair{display:flex;flex-wrap:wrap;gap:12px;align-items:end}.pair label{flex:1 1 140px}.ontology-item,.attribute,.plan-review,.attempt,.outcome,.manual-recovery{border-top:1px solid var(--border);padding:16px 0;margin-top:16px}.attribute{padding-left:12px;border-left:3px solid var(--border)}.notice{border-left:3px solid var(--accent);padding:8px 12px}.help{color:var(--muted)}.feedback{min-height:24px}.acknowledgement{flex-direction:row;align-items:center;min-height:44px}.acknowledgement input{width:20px;min-height:20px;flex-shrink:0}dl{display:grid;gap:8px}dt{font-weight:600}dd{margin:0;min-width:0}.operation-id,dd,li{overflow-wrap:anywhere}.manual-recovery button{margin-top:12px}@media(min-width:768px){dl{grid-template-columns:minmax(0,1fr) minmax(0,3fr)}}@media(max-width:480px){.source-ingestion{padding:16px}}@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { freshId, ingestionPassages, operationId, validateOntology, ontologyNameIssue, ontologyDescriptionIssue } from '../../api/sourceIngestion.js'
import { copyFor } from '../../i18n/workbench.js'
import { sourcePassageCopyFor } from '../../i18n/sourcePassages.js'
import { MAX_PROJECT_REVISION, sameOntologyValue, validateReviewedOntology } from '../../api/reviewedOntology.js'
import { reviewedOntologyCopyFor } from '../../i18n/reviewedOntology.js'
const props = defineProps({ methods: { type: Object, required: true }, inspected: Object, connected: Boolean, busy: Boolean, resetVersion: Number, locale: { type: String, default: 'en' } })
const copy = computed(() => copyFor(props.locale).ingestion)
const passageCopy = computed(() => sourcePassageCopyFor(props.locale))
const sourceCopy = computed(() => copyFor(props.locale).sources)
const selectedEvidence = ref('')
const inlineEntities = ref([]), inlineEdges = ref([])
const plan = ref(null), snapshot = ref(null), attempt = ref(null), outcome = ref(null)
const editingReviewed = ref(false), ontologyCopy = computed(() => reviewedOntologyCopyFor(props.locale))
const loadedOntology = ref(null), projectDraft = ref(null), ontologyPending = ref(false)
const ontologyStatus = ref('initial'), mustLoadOntology = ref(false), needsDraftChoice = ref(false)
const ineligibleRevision = ref(null)
const loadButton = ref(null)
let ontologyEpoch = 0
const emptyOntology = () => ({ entity_types: [], edge_types: [] })
const entities = computed(() => editingReviewed.value ? projectDraft.value?.entity_types ?? [] : inlineEntities.value)
const edges = computed(() => editingReviewed.value ? projectDraft.value?.edge_types ?? [] : inlineEdges.value)
const attributeLimit = computed(() => editingReviewed.value ? 10 : 5)
const endpointNames = computed(() => {
  const names = entities.value.map(entity => entity.name)
  return editingReviewed.value ? [...new Set([...names, 'Entity'])] : names
})
const ontologyDirty = computed(() => !!projectDraft.value
  && !sameOntologyValue(projectDraft.value, loadedOntology.value?.ontology ?? emptyOntology()))
const ontologyMessage = computed(() => {
  const message = ontologyCopy.value[ontologyStatus.value]
  return typeof message === 'function' ? message(loadedOntology.value?.project_revision) : message
})
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
const fieldErrors = computed(() => !editingReviewed.value && validationShown.value ? collectFieldErrors() : [])
const errorsById = computed(() => new Map(fieldErrors.value.map(e => [e.id, e])))
function fieldError(id) { const problem = errorsById.value.get(id); return problem ? copy.value.validation[problem.code] : '' }
function fieldProps(id) { return { id, 'aria-invalid': errorsById.value.has(id) ? 'true' : undefined, 'aria-describedby': errorsById.value.has(id) ? `${id}-error` : undefined } }
function fieldTitle(problem) { return problem.labels.map(([key, ordinal]) => copy.value[key] + (ordinal ? ` ${ordinal}` : '')).join(' · ') }
function focusField(id) { errorSummary.value?.closest('form')?.querySelector(`[id="${id}"]`)?.focus() }
let generation = 0
const unavailable = computed(() => !props.connected || props.busy || pending.value || ontologyPending.value)
const locked = computed(() => !!attempt.value || unavailable.value)
const ontologyLocked = computed(() => unavailable.value || !loadedOntology.value || mustLoadOntology.value || needsDraftChoice.value)
const saveDisabled = computed(() => ontologyLocked.value || (loadedOntology.value.ontology !== null && !ontologyDirty.value)
  || loadedOntology.value.project_revision >= MAX_PROJECT_REVISION)
const passages = computed(() => ingestionPassages(props.inspected))
const selectedPassage = computed(() => passages.value.find(p => p.evidence_id === selectedEvidence.value))
const wholeSourceEligible = computed(() => !!props.inspected && props.inspected.source.codepoint_length <= 32768 && props.inspected.source.codepoint_length > 0 && props.inspected.passages.length > 0)
const eligible = computed(() => selectedEvidence.value ? !!selectedPassage.value : wholeSourceEligible.value)
const savedIneligible = computed(() => ineligibleRevision.value !== null &&
  ineligibleRevision.value === loadedOntology.value?.project_revision)
const savedEligible = computed(() => editingReviewed.value && wholeSourceEligible.value &&
  !!loadedOntology.value?.ontology && !ontologyDirty.value && !mustLoadOntology.value &&
  !needsDraftChoice.value && !savedIneligible.value)
const savedReviewCurrent = computed(() => savedEligible.value &&
  snapshot.value?.source_revision === props.inspected?.source.source_revision &&
  snapshot.value?.project_revision === loadedOntology.value?.project_revision)
const executeDisabled = computed(() => locked.value || !reviewed.value ||
  (snapshot.value?.schema_version === 4 ? !savedReviewCurrent.value : editingReviewed.value))
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
  inlineEntities.value = [{ name: 'Person', description: 'A person mentioned in the source.', attributes: [] }, { name: 'Organization', description: 'An organization mentioned in the source.', attributes: [] }]
  inlineEdges.value = [{ name: 'WORKS_FOR', description: 'A declared employment relationship.', attributes: [], source_targets: [{ source: 'Person', target: 'Organization' }] }]
}
function clear() { clearProjectOntology(); generation++; validationShown.value = false; plan.value = null; snapshot.value = null; attempt.value = null; outcome.value = null; reviewed.value = false; manual.value = ''; pending.value = false; error.value = ''; phase.value = ''; selectedEvidence.value = ''; starter() }
// Source changes invalidate all old protected state, including in-flight replies.
watch(() => [props.connected, props.resetVersion, props.inspected], clear, { flush: 'sync' })
watch(selectedEvidence, () => {
  if (attempt.value) return
  generation++
  plan.value = null; snapshot.value = null; outcome.value = null; reviewed.value = false
  pending.value = false; error.value = ''; phase.value = ''
}, { flush: 'sync' })
function clearUnsentReview() {
  if (attempt.value) return
  generation++
  plan.value = null; snapshot.value = null; reviewed.value = false; outcome.value = null; phase.value = ''
  pending.value = false; error.value = ''
}
watch([inlineEntities, inlineEdges], clearUnsentReview, { deep: true, flush: 'sync' })
watch(projectDraft, () => {
  if (editingReviewed.value) clearUnsentReview()
}, { deep: true, flush: 'sync' })

function clearProjectOntology() {
  ontologyEpoch++
  editingReviewed.value = false
  loadedOntology.value = null
  projectDraft.value = null
  ontologyPending.value = false
  ontologyStatus.value = 'initial'
  mustLoadOntology.value = false
  needsDraftChoice.value = false
  ineligibleRevision.value = null
}
function toggleOntologyMode() {
  if (unavailable.value) return
  clearUnsentReview()
  editingReviewed.value = !editingReviewed.value
}
function applySavedDraft() {
  projectDraft.value = JSON.parse(JSON.stringify(loadedOntology.value.ontology ?? emptyOntology()))
}
async function chooseOntologyDraft(keep) {
  if (unavailable.value || !needsDraftChoice.value) return
  const epoch = ontologyEpoch
  if (!keep) applySavedDraft()
  needsDraftChoice.value = false
  ontologyStatus.value = loadedOntology.value.ontology === null ? 'empty' : 'loaded'
  await nextTick()
  if (epoch === ontologyEpoch) loadButton.value?.focus()
}
async function ontologyAction(working, action, apply, onError) {
  const epoch = ++ontologyEpoch
  ontologyPending.value = true
  ontologyStatus.value = working
  try {
    const data = await action()
    if (epoch === ontologyEpoch && props.connected) apply(data)
  } catch (e) {
    if (epoch === ontologyEpoch && props.connected) onError(e?.code)
  } finally {
    if (epoch === ontologyEpoch) {
      ontologyPending.value = false
      await nextTick()
      if (epoch === ontologyEpoch && mustLoadOntology.value) loadButton.value?.focus()
    }
  }
}
function loadOntology() {
  if (!editingReviewed.value || unavailable.value) return
  clearUnsentReview()
  const keepDraft = ontologyDirty.value || needsDraftChoice.value || mustLoadOntology.value && !!projectDraft.value
  const prior = loadedOntology.value
  return ontologyAction('loading', () => props.methods.loadOntology(prior), data => {
    if (prior?.project_revision === data.project_revision && !sameOntologyValue(prior.ontology, data.ontology)) {
      mustLoadOntology.value = true
      ontologyStatus.value = 'loadFailed'
      return
    }
    if (prior?.project_revision !== data.project_revision) {
      clearUnsentReview()
      ineligibleRevision.value = null
    }
    const matchesDraft = sameOntologyValue(projectDraft.value, data.ontology)
    loadedOntology.value = data
    mustLoadOntology.value = false
    needsDraftChoice.value = keepDraft && !matchesDraft
    if (!needsDraftChoice.value && (!projectDraft.value || !matchesDraft)) applySavedDraft()
    ontologyStatus.value = needsDraftChoice.value ? 'choose'
      : data.ontology === null ? 'empty' : matchesDraft && keepDraft ? 'saved' : 'loaded'
  }, code => {
    mustLoadOntology.value = true
    ontologyStatus.value = code === 'conflict' ? 'conflict' : 'loadFailed'
  })
}
function saveOntology() {
  if (!editingReviewed.value || saveDisabled.value) return
  try { validateReviewedOntology(projectDraft.value) }
  catch { ontologyStatus.value = 'invalid'; return }
  const prior = loadedOntology.value
  const payload = { schema_version: 1, expected_revision: prior.project_revision, ontology: projectDraft.value }
  return ontologyAction('saving', () => props.methods.saveOntology(payload, prior), data => {
    clearUnsentReview()
    loadedOntology.value = data
    ineligibleRevision.value = null
    ontologyStatus.value = 'saved'
  }, code => {
    if (code === 'limit_exceeded') { ontologyStatus.value = 'limit'; return }
    if (code === 'invalid_request') { ontologyStatus.value = 'invalid'; return }
    mustLoadOntology.value = true
    ontologyStatus.value = code === 'conflict' ? 'conflict' : 'unknown'
  })
}
onBeforeUnmount(clear)
starter()
const editorLocked = computed(() => editingReviewed.value ? ontologyLocked.value : locked.value || !eligible.value)
function addEntity() { if (!editorLocked.value && entities.value.length < 10) entities.value.push({ name: '', description: '', attributes: [] }) }
function addExample(entity) {
  if (editorLocked.value) return
  if (!entity.examples) entity.examples = []
  entity.examples.push('')
}
function defaultPair() {
  const name = entities.value[0]?.name || (editingReviewed.value ? 'Entity' : '')
  return { source: name, target: name }
}
function addEdge() { if (!editorLocked.value && edges.value.length < 10) edges.value.push({ name: '', description: '', attributes: [], source_targets: [defaultPair()] }) }
function addAttribute(item) { if (!editorLocked.value && item.attributes.length < attributeLimit.value) item.attributes.push({ name: '', description: '', type: 'text' }) }
function addPair(edge) { if (!editorLocked.value && edge.source_targets.length < 10) edge.source_targets.push(defaultPair()) }
async function request(action, apply, projectRevision = null) {
  if (unavailable.value) return
  const epoch = ++generation
  pending.value = true; outcome.value = null; error.value = ''; phase.value = 'working'
  try {
    const data = await action()
    if (epoch !== generation || !props.connected) return
    apply(data); await nextTick(); reviewPanel.value?.focus()
  } catch (e) {
    if (epoch === generation && props.connected) {
      if (projectRevision !== null && e?.code === 'conflict') {
        mustLoadOntology.value = true
        ontologyStatus.value = 'conflict'
        plan.value = null; snapshot.value = null; reviewed.value = false
      } else if (projectRevision !== null && e?.code === 'ontology_ineligible') {
        ineligibleRevision.value = projectRevision
        plan.value = null; snapshot.value = null; reviewed.value = false
      }
      error.value = savedIneligible.value && e?.code === 'ontology_ineligible' ? '' : e?.code || 'invalid_reply'
      phase.value = attempt.value ? 'unknown' : ''
    }
  }
  finally { if (epoch === generation) pending.value = false }
}
async function makePlan() {
  if (editingReviewed.value || locked.value || !eligible.value) return
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
    const source_revision = props.inspected.source.source_revision
    const payload = selectedPassage.value
      ? { schema_version: 2, source_revision, evidence_id: selectedPassage.value.evidence_id, ontology }
      : { schema_version: 1, source_revision, operation_id: freshId(), ontology }
    // The transport clones the inspected source independently for correlation.
    return request(() => props.methods.plan(payload, props.inspected), data => { snapshot.value = payload; plan.value = data; phase.value = 'planned' })
  } catch (e) { error.value = e.code || 'invalid_request' }
}
function makeSavedPlan() {
  if (locked.value || !savedEligible.value) return
  clearUnsentReview()
  try {
    const saved = loadedOntology.value
    const payload = { schema_version: 4, source_revision: props.inspected.source.source_revision,
      operation_id: freshId(), project_revision: saved.project_revision }
    return request(() => props.methods.plan(payload, props.inspected, saved), data => {
      if (!savedEligible.value || loadedOntology.value !== saved) return
      snapshot.value = payload; plan.value = data; phase.value = 'planned'
    }, saved.project_revision)
  } catch (e) { error.value = e?.code || 'invalid_request' }
}
function execute() {
  if (!plan.value || !snapshot.value || executeDisabled.value) return
  const savedRequest = snapshot.value.schema_version === 4
  const payload = savedRequest ? { ...snapshot.value, fingerprint: plan.value.fingerprint,
    execution_fingerprint: plan.value.execution_fingerprint } : snapshot.value
  // Latch before entering any asynchronous code; every failure remains status-only.
  attempt.value = { payload, known: plan.value }
  reviewed.value = false
  return request(() => props.methods.execute(attempt.value.payload, attempt.value.known),
    data => { outcome.value = data; phase.value = 'returned' }, savedRequest ? payload.project_revision : null)
}
function statusKnown() {
  if (!attempt.value) return
  const saved = attempt.value
  return request(() => props.methods.status({ operation_id: saved.known.operation_id }, saved.known), data => { outcome.value = data; phase.value = 'returned' })
}
function statusManual() {
  if (unavailable.value) return
  try {
    const id = operationId(manual.value)
    // Never weaken correlation by querying a known operation as manual recovery.
    const known = attempt.value?.known.operation_id === id ? attempt.value.known : undefined
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
    <div v-else class="selected-source">
      <h3>{{ inspected.source.source_name }}</h3>
      <dl><dt>{{ copy.revision }}</dt><dd>{{ inspected.source.source_revision }}</dd><dt>{{ copy.digest }}</dt><dd>{{ inspected.source.text_sha256 }}</dd><dt>{{ copy.codepoints }}</dt><dd>{{ inspected.source.codepoint_length }}<template v-if="!passages.length"> / 32768</template></dd></dl>
      <label v-if="passages.length" for="ingestion-passage">
        {{ passageCopy.selection }}
        <select id="ingestion-passage" v-model="selectedEvidence" :disabled="locked || editingReviewed">
          <option value="">{{ passageCopy.choose }}</option>
          <option v-for="passage in passages" :key="passage.evidence_id" :value="passage.evidence_id">
            {{ passageCopy.position(passage.ordinal, passage.count) }} · {{ passage.start }}–{{ passage.end }}<template v-if="passage.page !== null"> · {{ sourceCopy.page }} {{ passage.page }}</template> · {{ passage.evidence_id }}
          </option>
        </select>
      </label>
      <p v-else-if="!eligible" class="notice">{{ copy.ineligible }}</p>
    </div>
    <div class="actions">
      <button id="reviewed-ontology-mode" type="button" :aria-pressed="editingReviewed" :disabled="unavailable" @click="toggleOntologyMode">{{ ontologyCopy.project }}</button>
      <button v-if="editingReviewed" id="reviewed-ontology-load" ref="loadButton" type="button" :disabled="unavailable" aria-describedby="reviewed-ontology-status" @click="loadOntology">{{ ontologyCopy.load }}</button>
    </div>
    <template v-if="editingReviewed">
      <p id="reviewed-ontology-status" class="feedback" role="status" aria-live="polite" aria-atomic="true">{{ ontologyMessage }}<template v-if="loadedOntology?.project_revision === MAX_PROJECT_REVISION"> {{ ontologyCopy.exhausted }}</template></p>
      <p id="saved-ingestion-help" class="help">{{ ontologyCopy.planHelp }}</p>
      <p v-if="savedIneligible" id="saved-ingestion-ineligible" class="notice" role="status">{{ ontologyCopy.ineligible }}</p>
      <button id="saved-ingestion-plan" ref="planButton" type="button" :disabled="locked || !savedEligible" :aria-describedby="savedIneligible ? 'saved-ingestion-help saved-ingestion-ineligible' : 'saved-ingestion-help'" @click="makeSavedPlan">{{ ontologyCopy.plan }}</button>
      <div v-if="needsDraftChoice && !mustLoadOntology" class="actions">
        <button id="reviewed-ontology-keep" type="button" :disabled="unavailable" @click="chooseOntologyDraft(true)">{{ ontologyCopy.keepDraft }}</button>
        <button type="button" :disabled="unavailable" @click="chooseOntologyDraft(false)">{{ ontologyCopy.useSaved }}</button>
      </div>
    </template>
    <form :novalidate="editingReviewed" @submit.prevent="editingReviewed ? saveOntology() : makePlan()">
      <div v-if="fieldErrors.length" ref="errorSummary" class="error-summary" tabindex="-1" aria-labelledby="ontology-error-title"><h3 id="ontology-error-title">{{ copy.validationSummary }}</h3><p>{{ copy.validationHint }}</p><ul><li v-for="problem in fieldErrors" :key="problem.id"><a :href="`#${problem.id}`" @click.prevent="focusField(problem.id)">{{ fieldTitle(problem) }}: {{ copy.validation[problem.code] }}</a></li></ul></div>
      <fieldset v-if="!editingReviewed || loadedOntology" :disabled="editorLocked">
        <legend>{{ editingReviewed ? ontologyCopy.project : copy.ontology }}</legend>
        <p v-if="!editingReviewed">{{ copy.starter }}</p>
        <p class="help">{{ editingReviewed ? ontologyCopy.limits : copy.limits }}</p>
        <h3>{{ copy.entities }}</h3>
        <article v-for="(entity, i) in entities" :key="fieldId('entity', entity, 'name')" class="ontology-item">
          <label :for="fieldId('entity', entity, 'name')">{{ copy.name }} {{ i + 1 }}<input v-bind="fieldProps(fieldId('entity', entity, 'name'))" v-model="entity.name" required :maxlength="editingReviewed ? undefined : 64" autocomplete="off"><span v-if="fieldError(fieldId('entity', entity, 'name'))" :id="`${fieldId('entity', entity, 'name')}-error`" class="field-error">{{ fieldError(fieldId('entity', entity, 'name')) }}</span></label>
          <label :for="fieldId('entity', entity, 'description')">{{ copy.description }}<textarea v-bind="fieldProps(fieldId('entity', entity, 'description'))" v-model="entity.description" required rows="2" :maxlength="editingReviewed ? undefined : 1000"></textarea><span v-if="fieldError(fieldId('entity', entity, 'description'))" :id="`${fieldId('entity', entity, 'description')}-error`" class="field-error">{{ fieldError(fieldId('entity', entity, 'description')) }}</span></label>
          <div v-for="(attr, j) in entity.attributes" :key="fieldId('entity-attribute', attr, 'name')" class="attribute">
            <label :for="fieldId('entity-attribute', attr, 'name')">{{ copy.attributeName }}<input v-bind="fieldProps(fieldId('entity-attribute', attr, 'name'))" v-model="attr.name" required :maxlength="editingReviewed ? undefined : 64"><span v-if="fieldError(fieldId('entity-attribute', attr, 'name'))" :id="`${fieldId('entity-attribute', attr, 'name')}-error`" class="field-error">{{ fieldError(fieldId('entity-attribute', attr, 'name')) }}</span></label>
            <label :for="fieldId('entity-attribute', attr, 'type')">{{ copy.attributeType }}<select v-bind="fieldProps(fieldId('entity-attribute', attr, 'type'))" v-model="attr.type"><option value="text">{{ copy.text }}</option><option v-if="!editingReviewed" value="integer">{{ copy.integer }}</option><option v-if="!editingReviewed" value="number">{{ copy.number }}</option><option v-if="!editingReviewed" value="boolean">{{ copy.boolean }}</option></select><span v-if="fieldError(fieldId('entity-attribute', attr, 'type'))" :id="`${fieldId('entity-attribute', attr, 'type')}-error`" class="field-error">{{ fieldError(fieldId('entity-attribute', attr, 'type')) }}</span></label>
            <label :for="fieldId('entity-attribute', attr, 'description')">{{ copy.description }}<textarea v-bind="fieldProps(fieldId('entity-attribute', attr, 'description'))" v-model="attr.description" required rows="2" :maxlength="editingReviewed ? undefined : 1000"></textarea><span v-if="fieldError(fieldId('entity-attribute', attr, 'description'))" :id="`${fieldId('entity-attribute', attr, 'description')}-error`" class="field-error">{{ fieldError(fieldId('entity-attribute', attr, 'description')) }}</span></label><button type="button" @click="entity.attributes.splice(j, 1)">{{ copy.removeAttribute }} {{ j + 1 }}</button>
          </div>
          <div v-if="editingReviewed">
            <h4>{{ ontologyCopy.examples }}</h4>
            <div v-for="(_, exampleIndex) in entity.examples" :key="exampleIndex">
              <label>{{ ontologyCopy.examples }} {{ exampleIndex + 1 }}
                <textarea v-model="entity.examples[exampleIndex]" rows="2"></textarea>
              </label>
              <button type="button" @click="entity.examples.splice(exampleIndex, 1)">{{ ontologyCopy.removeExample }} {{ exampleIndex + 1 }}</button>
            </div>
            <button type="button" @click="addExample(entity)">{{ ontologyCopy.addExample }}</button>
          </div>
          <div class="actions"><button type="button" :disabled="entity.attributes.length >= attributeLimit" @click="addAttribute(entity)">{{ copy.addAttribute }}</button><button type="button" :disabled="!editingReviewed && entities.length === 1" @click="entities.splice(i, 1)">{{ copy.removeEntity }} {{ i + 1 }}</button></div>
        </article>
        <button type="button" :disabled="entities.length >= 10" @click="addEntity">{{ copy.addEntity }}</button>
        <h3>{{ copy.relationships }}</h3>
        <article v-for="(edge, i) in edges" :key="fieldId('edge', edge, 'name')" class="ontology-item">
          <label :for="fieldId('edge', edge, 'name')">{{ copy.name }} {{ i + 1 }}<input v-bind="fieldProps(fieldId('edge', edge, 'name'))" v-model="edge.name" required :maxlength="editingReviewed ? undefined : 64" autocomplete="off"><span v-if="fieldError(fieldId('edge', edge, 'name'))" :id="`${fieldId('edge', edge, 'name')}-error`" class="field-error">{{ fieldError(fieldId('edge', edge, 'name')) }}</span></label>
          <label :for="fieldId('edge', edge, 'description')">{{ copy.description }}<textarea v-bind="fieldProps(fieldId('edge', edge, 'description'))" v-model="edge.description" required rows="2" :maxlength="editingReviewed ? undefined : 1000"></textarea><span v-if="fieldError(fieldId('edge', edge, 'description'))" :id="`${fieldId('edge', edge, 'description')}-error`" class="field-error">{{ fieldError(fieldId('edge', edge, 'description')) }}</span></label>
          <div v-for="(pair, j) in edge.source_targets" :key="fieldId('edge-pair', pair, 'source')" class="pair">
            <label :for="fieldId('edge-pair', pair, 'source')">{{ copy.from }} {{ j + 1 }}<select v-bind="fieldProps(fieldId('edge-pair', pair, 'source'))" v-model="pair.source"><option v-for="(name, index) in endpointNames" :key="index" :value="name">{{ name }}</option></select><span v-if="fieldError(fieldId('edge-pair', pair, 'source'))" :id="`${fieldId('edge-pair', pair, 'source')}-error`" class="field-error">{{ fieldError(fieldId('edge-pair', pair, 'source')) }}</span></label>
            <label :for="fieldId('edge-pair', pair, 'target')">{{ copy.to }} {{ j + 1 }}<select v-bind="fieldProps(fieldId('edge-pair', pair, 'target'))" v-model="pair.target"><option v-for="(name, index) in endpointNames" :key="index" :value="name">{{ name }}</option></select><span v-if="fieldError(fieldId('edge-pair', pair, 'target'))" :id="`${fieldId('edge-pair', pair, 'target')}-error`" class="field-error">{{ fieldError(fieldId('edge-pair', pair, 'target')) }}</span></label><button type="button" :disabled="edge.source_targets.length === 1" @click="edge.source_targets.splice(j, 1)">{{ copy.removePair }} {{ j + 1 }}</button>
          </div>
          <div v-for="(attr, j) in edge.attributes" :key="fieldId('edge-attribute', attr, 'name')" class="attribute">
            <label :for="fieldId('edge-attribute', attr, 'name')">{{ copy.attributeName }}<input v-bind="fieldProps(fieldId('edge-attribute', attr, 'name'))" v-model="attr.name" required :maxlength="editingReviewed ? undefined : 64"><span v-if="fieldError(fieldId('edge-attribute', attr, 'name'))" :id="`${fieldId('edge-attribute', attr, 'name')}-error`" class="field-error">{{ fieldError(fieldId('edge-attribute', attr, 'name')) }}</span></label>
            <label :for="fieldId('edge-attribute', attr, 'type')">{{ copy.attributeType }}<select v-bind="fieldProps(fieldId('edge-attribute', attr, 'type'))" v-model="attr.type"><option value="text">{{ copy.text }}</option><option v-if="!editingReviewed" value="integer">{{ copy.integer }}</option><option v-if="!editingReviewed" value="number">{{ copy.number }}</option><option v-if="!editingReviewed" value="boolean">{{ copy.boolean }}</option></select><span v-if="fieldError(fieldId('edge-attribute', attr, 'type'))" :id="`${fieldId('edge-attribute', attr, 'type')}-error`" class="field-error">{{ fieldError(fieldId('edge-attribute', attr, 'type')) }}</span></label>
            <label :for="fieldId('edge-attribute', attr, 'description')">{{ copy.description }}<textarea v-bind="fieldProps(fieldId('edge-attribute', attr, 'description'))" v-model="attr.description" required rows="2" :maxlength="editingReviewed ? undefined : 1000"></textarea><span v-if="fieldError(fieldId('edge-attribute', attr, 'description'))" :id="`${fieldId('edge-attribute', attr, 'description')}-error`" class="field-error">{{ fieldError(fieldId('edge-attribute', attr, 'description')) }}</span></label><button type="button" @click="edge.attributes.splice(j, 1)">{{ copy.removeAttribute }} {{ j + 1 }}</button>
          </div>
          <div class="actions"><button type="button" :disabled="edge.attributes.length >= attributeLimit" @click="addAttribute(edge)">{{ copy.addAttribute }}</button><button type="button" :disabled="edge.source_targets.length >= 10" @click="addPair(edge)">{{ copy.addPair }}</button><button type="button" @click="edges.splice(i, 1)">{{ copy.removeEdge }} {{ i + 1 }}</button></div>
        </article>
        <button type="button" :disabled="edges.length >= 10" @click="addEdge">{{ copy.addEdge }}</button><div class="actions"><button v-if="editingReviewed" id="reviewed-ontology-save" type="submit" class="primary" :disabled="saveDisabled" aria-describedby="reviewed-ontology-status">{{ ontologyCopy.save }}</button><button v-else ref="planButton" type="submit" class="primary">{{ copy.plan }}</button></div>
      </fieldset>
    </form>
    <p class="feedback" role="status" aria-live="polite" aria-atomic="true">{{ copy[phase] || '' }} {{ errorText }}</p>
    <article v-if="plan && editingReviewed === (plan.schema_version === 4)" ref="reviewPanel" class="plan-review" tabindex="-1" @keydown.esc.stop.prevent="dismissPlan"><h3>{{ copy.review }}</h3><p>{{ copy.planFlags }}</p><dl><dt>{{ copy.operation }}</dt><dd class="operation-id">{{ plan.operation_id }}</dd><dt>{{ copy.episode }}</dt><dd>{{ plan.episode_id }}</dd><dt>{{ plan.schema_version === 4 ? ontologyCopy.reviewFingerprint : copy.fingerprint }}</dt><dd>{{ plan.fingerprint }}</dd><template v-if="plan.schema_version === 4"><dt>{{ ontologyCopy.executionFingerprint }}</dt><dd>{{ plan.execution_fingerprint }}</dd><dt>{{ ontologyCopy.projectRevision }}</dt><dd>{{ plan.project_revision }}</dd></template><dt>{{ copy.evidence }}</dt><dd>{{ plan.evidence_ids.length }}</dd><dt>{{ copy.ontologyRevision }}</dt><dd>{{ plan.ontology_revision }}</dd></dl><ul><li v-for="id in plan.evidence_ids" :key="id">{{ id }}</li></ul>
      <dl v-if="plan.window" class="passage-review">
        <dt>{{ passageCopy.selection }}</dt><dd>{{ passageCopy.position(plan.window.ordinal, plan.window.count) }}</dd>
        <dt>{{ sourceCopy.offsets }}</dt><dd>{{ plan.window.start }}–{{ plan.window.end }}</dd>
        <dt>{{ sourceCopy.page }}</dt><dd>{{ plan.window.page ?? sourceCopy.unknownLayout }}</dd>
        <dt>{{ sourceCopy.excerptDigest }}</dt><dd>{{ plan.window.sha256 }}</dd>
      </dl>
      <p>{{ copy.charge }}</p><label class="acknowledgement"><input v-model="reviewed" type="checkbox" :disabled="locked || (plan.schema_version === 4 && !savedReviewCurrent)">{{ copy.acknowledge }}</label><div class="actions"><button type="button" class="primary" :disabled="executeDisabled" @click="execute">{{ copy.execute }}</button><button type="button" :disabled="locked" @click="dismissPlan">{{ copy.dismiss }}</button></div>
    </article>
    <div v-if="attempt" class="attempt"><h3>{{ copy.submitted }}</h3><p class="operation-id">{{ attempt.known.operation_id }}</p><p>{{ copy.noRetry }}</p><button type="button" :disabled="unavailable" @click="statusKnown">{{ copy.checkStatus }}</button></div>
    <article v-if="outcome" class="outcome"><h3>{{ copy.outcome }}</h3><p class="operation-id">{{ outcome.operation_id }}</p><dl><dt>{{ copy.graphState }}</dt><dd>{{ copy.graphStates[outcome.state] }}</dd><dt>{{ copy.budgetState }}</dt><dd>{{ copy.budgetStates[outcome.budget_state] }}</dd><dt>{{ copy.ceiling }}</dt><dd>{{ outcome.ceiling_microusd ?? copy.unknown }}</dd><dt>{{ copy.actualCharge }}</dt><dd>{{ copy.unknown }}</dd><dt>{{ copy.modelCalls }}</dt><dd>{{ copy.unknown }}</dd></dl><p>{{ copy.separate }}</p></article>
    <form class="manual-recovery" @submit.prevent="statusManual"><h3>{{ copy.manual }}</h3><p>{{ copy.manualHint }}</p><label for="ingestion-operation">{{ copy.operation }}<input id="ingestion-operation" v-model="manual" :disabled="unavailable" required maxlength="36" spellcheck="false" autocomplete="off"></label><button type="submit" :disabled="unavailable">{{ copy.recover }}</button></form>
  </section>
</template>
<style scoped>
.error-summary{border:2px solid var(--accent);border-radius:5px;padding:16px;margin:16px 0;overflow-wrap:anywhere}.error-summary h3{margin-top:0}.error-summary a{color:var(--primary);display:inline-block;min-height:44px;padding:10px 0;line-height:1.5}.field-error{font-weight:500;border-left:3px solid var(--accent);padding:6px 10px}.error-summary:focus,.error-summary a:focus-visible{outline:3px solid var(--primary);outline-offset:3px}[aria-invalid="true"]{border:2px solid var(--accent)}
.source-ingestion{background:var(--surface);color:var(--ink);border:1px solid var(--border);border-radius:8px;padding:24px;margin:24px 0;min-width:0;overflow-wrap:anywhere}.source-ingestion *{box-sizing:border-box}h2{font-size:1.25rem}h3{font-size:1.1rem;margin:20px 0 12px}fieldset{border:0;padding:0;margin:0;min-width:0}legend{font-weight:600}label{display:flex;flex-direction:column;gap:6px;margin:12px 0;min-width:0}input,textarea,select,button{font:inherit;min-height:44px;max-width:100%;min-width:0;border:1px solid var(--control-border);border-radius:5px;padding:10px 12px;color:var(--ink);background:var(--surface)}input,textarea,select{width:100%}textarea{resize:vertical}button{color:var(--primary);cursor:pointer;touch-action:manipulation}.primary{background:var(--primary);color:var(--surface)}button:disabled{opacity:.65;cursor:default}button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,.plan-review:focus{outline:3px solid var(--primary);outline-offset:3px}button:hover:not(:disabled){filter:brightness(.92)}.actions,.pair{display:flex;flex-wrap:wrap;gap:12px;align-items:end}.pair label{flex:1 1 140px}.ontology-item,.attribute,.plan-review,.attempt,.outcome,.manual-recovery{border-top:1px solid var(--border);padding:16px 0;margin-top:16px}.attribute{padding-left:12px;border-left:3px solid var(--border)}.notice{border-left:3px solid var(--accent);padding:8px 12px}.help{color:var(--muted)}.feedback{min-height:24px}.acknowledgement{flex-direction:row;align-items:center;min-height:44px}.acknowledgement input{width:20px;min-height:20px;flex-shrink:0}dl{display:grid;gap:8px}dt{font-weight:600}dd{margin:0;min-width:0}.operation-id,dd,li{overflow-wrap:anywhere}.manual-recovery button{margin-top:12px}@media(min-width:768px){dl{grid-template-columns:minmax(0,1fr) minmax(0,3fr)}}@media(max-width:480px){.source-ingestion{padding:16px}}@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style>

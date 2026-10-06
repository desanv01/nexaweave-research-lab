<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { preparationIdentity, preparationInteger, preparationOptions, preparationSource, newPreparationId, validatePreparationResult } from '../../api/simulationPreparation.js'
import { populationTypeLabels } from '../../api/populationWorkbench.js'
import { preparationCopyFor, preparationError } from '../../i18n/simulationPreparation.js'
const props = defineProps({ methods: { type: Object, required: true }, connected: Boolean, busy: Boolean, resetVersion: { type: Number, default: 0 }, locale: { type: String, default: 'en' }, source: { type: Object, default: null }, displayGraphId: { type: String, default: '' }, typeLabels: { type: Array, default: () => [] } })
const emit = defineEmits(['ready', 'cleared'])
const copy = computed(() => preparationCopyFor(props.locale)), labels = computed(() => populationTypeLabels(props.typeLabels))
const selected = ref([]), maximum = ref('10'), seed = ref('0'), rounds = ref('10'), requirement = ref(''), twitter = ref(true), reddit = ref(false)
const plan = ref(null), durable = ref(null), pending = ref(false), attempted = ref(false), error = ref(''), notice = ref(''), feedback = ref(null)
let lifetime = 0, reviewEpoch = 0
const locked = computed(() => !props.connected || props.busy || pending.value)
const canStart = computed(() => !locked.value && plan.value?.state === 'planned' && plan.value.authorization.model_calls_enabled && !!plan.value.authorization.ceiling_microusd && !(attempted.value && durable.value?.operation_id === plan.value.operation_id))
const records = computed(() => [plan.value && { key: 'plan', value: plan.value }, durable.value && durable.value.operation_id !== plan.value?.operation_id && { key: 'durable', value: durable.value }].filter(Boolean))
function changed() { reviewEpoch++; if (plan.value) notice.value = 'changed'; plan.value = null; error.value = ''; emit('ready', null) }
watch([selected, maximum, seed, rounds, requirement, twitter, reddit], changed, { deep: true, flush: 'sync' })
watch(() => props.source, changed, { deep: true, flush: 'sync' })
watch(() => props.typeLabels, changed, { deep: true, flush: 'sync' })
function clear() {
  lifetime++; reviewEpoch++; plan.value = null; durable.value = null; attempted.value = false; pending.value = false
  selected.value = []; maximum.value = '10'; seed.value = '0'; rounds.value = '10'; requirement.value = ''; twitter.value = true; reddit.value = false
  error.value = ''; notice.value = ''
  emit('ready', null); emit('cleared')
}
watch(() => props.resetVersion, clear, { flush: 'sync' })
watch(() => props.connected, connected => { if (!connected) clear() }, { flush: 'sync' })
watch(() => props.displayGraphId, clear, { flush: 'sync' })
onBeforeUnmount(clear)
async function announce() { await nextTick(); feedback.value?.focus() }
function options() { return preparationOptions({ types: selected.value.length ? [...selected.value] : null, max_agents: preparationInteger(maximum.value, 100, 1), seed: preparationInteger(seed.value, 4294967295), platforms: [...(twitter.value ? ['twitter'] : []), ...(reddit.value ? ['reddit'] : [])], max_rounds: preparationInteger(rounds.value, 24, 1), simulation_requirement: requirement.value }) }
async function review() {
  if (locked.value) return
  const life = lifetime, epoch = ++reviewEpoch
  plan.value = null; error.value = ''; notice.value = ''; pending.value = true
  try {
    const source = preparationSource(props.source), payload = { schema_version: 1, operation_id: newPreparationId(), source_revision: source.source_revision, options: options() }
    const value = await props.methods.plan(payload, source)
    const admitted = await validatePreparationResult(value, { graph: props.displayGraphId, payload, source, planning: true })
    if (life !== lifetime || epoch !== reviewEpoch) return
    plan.value = admitted
  } catch (e) { if (life === lifetime && epoch === reviewEpoch) { if (['unauthorized', 'origin_denied', 'disconnected'].includes(e?.code)) clear(); error.value = e?.code || 'invalid_request' } }
  finally { if (life === lifetime) { pending.value = false; await announce() } }
}
async function durableRequest(starting) {
  if (locked.value || starting && !canStart.value || !starting && !durable.value) return
  const life = lifetime, known = JSON.parse(JSON.stringify(starting ? plan.value : durable.value)), payload = preparationIdentity(known)
  if (starting) { durable.value = known; attempted.value = true }
  pending.value = true; error.value = ''; notice.value = ''
  try {
    const value = await (starting ? props.methods.start(payload) : props.methods.status(payload))
    const admitted = await validatePreparationResult(value, { graph: props.displayGraphId, payload, known })
    if (life !== lifetime) return
    durable.value = admitted
    if (plan.value?.operation_id === admitted.operation_id) plan.value = admitted
    if (admitted.state === 'ready') emit('ready', JSON.parse(JSON.stringify(admitted)))
  } catch (e) {
    if (life === lifetime) {
      if (['unauthorized', 'origin_denied', 'disconnected'].includes(e?.code)) { clear(); error.value = e.code }
      else { error.value = e?.code || 'transport_failure'; if (starting && !['model_calls_disabled', 'budget_denied'].includes(error.value)) notice.value = 'lost' }
    }
  } finally { if (life === lifetime) { pending.value = false; await announce() } }
}
</script>
<template>
  <section class="preparation-workbench" :lang="locale" aria-labelledby="preparation-title">
    <h2 id="preparation-title">{{ copy.title }}</h2><p>{{ copy.intro }}</p>
    <p v-if="source" class="source"><strong>{{ copy.source }}</strong>: {{ source.source_name }}<br><span class="mono">{{ source.source_revision }}</span></p><p v-else>{{ copy.selectSource }}</p>
    <form @submit.prevent="review">
      <fieldset :disabled="locked || !source"><legend>{{ copy.review }}</legend>
        <fieldset class="type-choices"><legend>{{ copy.types }}</legend><label v-for="name in labels" :key="name"><input v-model="selected" type="checkbox" :value="name">{{ name }}</label></fieldset>
        <div class="options"><label for="preparation-maximum">{{ copy.maximum }} (1–100)<input id="preparation-maximum" v-model="maximum" inputmode="numeric" type="text" required maxlength="3"></label><label for="preparation-seed">{{ copy.seed }} (0–4294967295)<input id="preparation-seed" v-model="seed" inputmode="numeric" type="text" required maxlength="10"></label><label for="preparation-rounds">{{ copy.rounds }} (1–24)<input id="preparation-rounds" v-model="rounds" inputmode="numeric" type="text" required maxlength="2"></label></div>
        <fieldset class="platforms"><legend>{{ copy.platforms }}</legend><label><input v-model="twitter" type="checkbox">Twitter</label><label><input v-model="reddit" type="checkbox">Reddit</label></fieldset>
        <label for="preparation-requirement">{{ copy.requirement }}<textarea id="preparation-requirement" v-model="requirement" rows="3" required maxlength="16384" aria-describedby="preparation-assumptions"></textarea></label>
        <div class="actions"><button class="primary review" type="submit">{{ copy.review }}</button></div>
      </fieldset>
    </form>
    <p id="preparation-assumptions" class="limitations">{{ copy.assumptions }}</p><p class="limitations">{{ copy.snapshot }}</p><p class="limitations">{{ copy.reservation }}</p><p class="limitations">{{ copy.disconnected }}</p>
    <p ref="feedback" class="feedback" role="status" aria-live="polite" aria-atomic="true" tabindex="-1"><span v-if="pending">{{ copy.pending }}</span><span v-else-if="error">{{ preparationError(copy, error) }}</span><span v-if="notice"> {{ copy[notice] }}</span><span v-if="!pending && !error && plan"> {{ copy.states[plan.state] }}</span></p>
    <article v-for="record in records" :key="record.key" class="plan" :aria-label="record.key === 'durable' ? copy.recovered : copy.review">
      <h3>{{ record.key === 'durable' ? copy.recovered : copy.states[record.value.state] }}</h3>
      <dl><dt>{{ copy.project }}</dt><dd class="mono">{{ record.value.scope.project_id }}</dd><dt>{{ copy.revision }}</dt><dd>{{ record.value.project_revision }}</dd><dt>{{ copy.source }}</dt><dd>{{ record.value.source.source_name }}<br><span class="mono">{{ record.value.source.source_revision }}<br>{{ record.value.source.source_sha256 }}</span></dd><dt>{{ copy.operation }}</dt><dd class="mono">{{ record.value.operation_id }}</dd><dt>{{ copy.projection }}</dt><dd class="mono">{{ record.value.projection_sha256 }}</dd><dt>{{ copy.digest }}</dt><dd class="mono">{{ record.value.plan_sha256 }}</dd><dt>{{ copy.ceiling }}</dt><dd>{{ record.value.authorization.ceiling_microusd || '—' }}</dd></dl>
      <p>{{ copy.progress }}: {{ copy.stages[record.value.progress.stage] }} · {{ record.value.progress.completed }} / {{ record.value.progress.total }}</p>
      <dl><dt>{{ copy.maximum }}</dt><dd>{{ record.value.options.max_agents }}</dd><dt>{{ copy.seed }}</dt><dd>{{ record.value.options.seed }}</dd><dt>{{ copy.platforms }}</dt><dd>{{ record.value.options.platforms.join(', ') }}</dd><dt>{{ copy.rounds }}</dt><dd>{{ record.value.options.max_rounds }}</dd><dt>{{ copy.requirement }}</dt><dd>{{ record.value.options.simulation_requirement }}</dd></dl>
      <p v-if="record.value.error_code">{{ preparationError(copy, record.value.error_code) }}</p>
      <details><summary>{{ copy.actors }} ({{ record.value.actors.length }})</summary><ol><li v-for="actor in record.value.actors" :key="actor.source_entity_uuid"><strong>{{ actor.name }}</strong><p>{{ actor.labels.join(', ') }}</p><p class="mono">{{ actor.source_entity_uuid }}</p></li></ol></details>
      <template v-if="record.value.receipt"><p class="ready">{{ copy.ready }}</p><dl><dt>{{ copy.simulation }}</dt><dd class="mono">{{ record.value.receipt.simulation_id }}</dd><dt>{{ copy.artifact }}</dt><dd class="mono">{{ record.value.receipt.artifact_sha256 }}</dd></dl><h4>{{ copy.files }}</h4><ul class="manifest"><li v-for="file in record.value.receipt.files" :key="file.name"><strong>{{ file.name }}</strong> · {{ file.size }} {{ copy.bytes }}<p class="mono">{{ file.sha256 }}</p></li></ul></template>
    </article>
    <p v-if="plan?.state === 'planned' && (!plan.authorization.model_calls_enabled || !plan.authorization.ceiling_microusd)" class="limitations">{{ copy.disabled }}</p>
    <div class="actions"><button v-if="plan?.state === 'planned'" class="primary start" type="button" :disabled="!canStart" @click="durableRequest(true)">{{ copy.start }}</button><button v-if="durable" class="refresh" type="button" :disabled="locked" @click="durableRequest(false)">{{ copy.refresh }}</button><button class="clear" type="button" @click="clear">{{ copy.reset }}</button></div>
  </section>
</template>
<style scoped>
.preparation-workbench{background:var(--surface,#fff);color:var(--ink,#172638);border:1px solid var(--border,#b9c4d2);border-radius:8px;padding:24px;margin-bottom:24px;min-width:0;overflow-wrap:anywhere;font:16px/1.5 system-ui,sans-serif}.preparation-workbench *{box-sizing:border-box}h2{font-size:1.25rem;margin:0 0 16px}h3{font-size:1.1rem}p{margin:8px 0}fieldset{border:0;padding:0;margin:16px 0;min-width:0}legend{color:var(--muted,#526174)}label{display:flex;flex-direction:column;gap:6px;font-weight:550;min-width:0}.options{display:grid;gap:12px}.type-choices,.platforms{display:flex;flex-wrap:wrap;gap:8px 16px}.type-choices label,.platforms label{flex-direction:row;align-items:center;min-height:44px}input,textarea{min-width:0;width:100%;border:1px solid var(--control-border,#7b8797);border-radius:5px;min-height:44px;padding:10px 12px;font:inherit;background:var(--surface,#fff);color:inherit}input[type=checkbox]{width:20px;min-height:20px}textarea{resize:vertical}button{min-height:44px;padding:10px 16px;border:1px solid var(--border,#b9c4d2);border-radius:5px;font:inherit;color:var(--primary,#174d96);background:var(--surface,#fff);cursor:pointer;max-width:100%;overflow-wrap:anywhere}.primary{color:white;background:var(--primary,#174d96);border-color:var(--primary,#174d96)}button:disabled{opacity:.65;cursor:default}button:hover:not(:disabled){filter:brightness(.92)}:is(button,input,textarea,summary,.feedback):focus-visible{outline:3px solid var(--primary,#174d96);outline-offset:3px}.actions{display:flex;flex-wrap:wrap;gap:12px;margin-top:16px}.limitations{color:var(--muted,#526174);font-size:.875rem}.source,.plan{border-top:1px solid var(--border,#b9c4d2);padding-top:16px;margin-top:16px}.feedback{border-left:3px solid var(--primary,#174d96);padding:8px 12px}.feedback:empty{display:none}.mono{font:.875rem/1.6 ui-monospace,Consolas,monospace}dl{display:grid;gap:4px 16px}dt{font-weight:550}dd{margin:0 0 8px;min-width:0}summary{min-height:44px;padding:10px 0;cursor:pointer}li{margin:12px 0}.ready{border-left:3px solid var(--primary,#174d96);padding:8px 12px}@media(min-width:768px){.options{grid-template-columns:repeat(3,minmax(0,1fr))}dl{grid-template-columns:minmax(140px,1fr) minmax(0,3fr)}}@media(max-width:480px){.preparation-workbench{padding:16px}}
</style>

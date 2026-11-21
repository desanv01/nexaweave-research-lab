<script setup>
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { preparationInteger } from '../../api/simulationPreparation.js'
import { ensembleCanStart, ensembleReviewedToStart } from '../../api/nativeEnsemble.js'
import { nativeEnsembleCopyFor } from '../../i18n/nativeEnsemble.js'

const props = defineProps({
  methods: { type: Object, required: true },
  source: { type: Object, default: null },
  ready: { type: Object, default: null },
  displayGraphId: { type: String, default: '' },
  resetVersion: { type: Number, default: 0 },
  locale: { type: String, default: 'en' },
  connected: Boolean,
  busy: Boolean,
})
const copy = computed(() => nativeEnsembleCopyFor(props.locale))
const seeds = ref(['', '']), retained = ref(false), feedback = ref(null)
const ensemble = shallowRef(null), active = shallowRef(null), update = ref(null)
const members = computed(() => ensemble.value?.members ?? [null, null])
const eligible = computed(() => {
  const { connected, source, ready, displayGraphId } = props
  return !!(connected && source && ready?.state === 'ready' && ready.model_calls_started && ready.receipt &&
    ready.display_graph_id === displayGraphId && ready.scope?.project_id === source.project_id &&
    ready.source?.source_revision === source.source_revision && ready.source?.source_sha256 === source.text_sha256 &&
    ready.source?.source_name === source.source_name)
})
const scopeKey = computed(() => JSON.stringify([props.connected, props.displayGraphId, props.source, props.ready, props.resetVersion]))
function reset() {
  props.methods.cancel()
  active.value = null
  ensemble.value = null
  update.value = null
  const pending = eligible.value ? props.methods.pendingSeeds(props.ready) : null
  retained.value = !!pending
  seeds.value = pending ? pending.map(String) : ['', '']
}
watch(scopeKey, reset, { immediate: true, flush: 'sync' })
onBeforeUnmount(() => props.methods.cancel())

const seedValues = computed(() => seeds.value.map(value => {
  try { return preparationInteger(value, 4294967295) } catch { return null }
}))
const validPair = computed(() => seedValues.value.every(seed => seed !== null && seed !== props.ready?.options?.seed) &&
  seedValues.value[0] !== seedValues.value[1])
const locked = computed(() => props.busy || !!active.value || !eligible.value)

function launchState(member) {
  if (!member?.launch) return copy.value.missing
  if (member.attempted && member.launch.state === 'planned') return copy.value.startUnconfirmed
  return copy.value.states[member.launch.state]
}
function launchHint(index) {
  if (!ensembleReviewedToStart(members.value[index])) return copy.value.notAuthorized
  if (ensembleCanStart(members.value, index)) return ''
  if (index === 1) return copy.value.waitForFirst
  return members.value[1]?.launch?.state === 'planned' ? copy.value.notAuthorized : copy.value.reviewBoth
}
function messageFor(error, action) {
  if (['unauthorized', 'origin_denied', 'disconnected'].includes(error?.code)) return 'denied'
  if (error?.code === 'scope_changed') return 'unsupported'
  if (['budget_denied', 'model_calls_disabled'].includes(error?.code)) return 'notAuthorized'
  if (error?.code === 'busy') return 'retryBusy'
  if (action === 'start' && error?.attempted && !error.replyConfirmed) return 'uncertainStart'
  if (action === 'declare') return error?.code === 'conflict' ? 'conflictingDeclaration' : 'retryDeclaration'
  return 'failedRequest'
}
async function act(action, event, index) {
  if (locked.value || (action === 'declare' ? !validPair.value || ensemble.value :
      !members.value[index] || action === 'start' && !ensembleCanStart(members.value, index))) return
  const owner = { scope: scopeKey.value }
  const trigger = event.currentTarget
  const stillOwned = () => active.value === owner && scopeKey.value === owner.scope && eligible.value
  active.value = owner
  update.value = null
  try {
    const result = action === 'declare'
      ? await props.methods.declare(seedValues.value, props.ready)
      : await props.methods.launch(action, index, props.ready)
    if (!stillOwned()) return
    ensemble.value = result
    update.value = action === 'declare' ? { notice: 'declared' } : { index }
  } catch (error) {
    if (!stillOwned()) return
    if (action === 'start' && (error?.attempted || error?.refreshFirst)) {
      const current = ensemble.value
      const next = current.members.slice()
      if (error.attempted) next[index] = { ...next[index], attempted: true }
      if (error.refreshFirst) next[0] = { ...next[0], settled: false }
      ensemble.value = { ...current, members: next }
    }
    update.value = { notice: messageFor(error, action) }
  } finally {
    if (stillOwned()) {
      retained.value = !!props.methods.pendingSeeds(props.ready)
      await nextTick()
      if (stillOwned()) {
        active.value = null
        if (document.activeElement === trigger || document.activeElement === document.body) feedback.value?.focus()
      }
    }
  }
}
const statusText = computed(() => {
  if (!eligible.value) return copy.value.unsupported
  if (active.value) return copy.value.pending
  if (update.value?.notice) return copy.value[update.value.notice]
  const member = members.value[update.value?.index]
  return member ? copy.value.memberStatus(member.seed, launchState(member)) : ''
})
</script>

<template>
  <section class="native-ensemble" :lang="locale" :aria-label="copy.title">
    <p v-if="eligible">{{ copy.instructions }}</p>
    <p ref="feedback" class="feedback" role="status" aria-live="polite" aria-atomic="true" tabindex="-1">{{ statusText }}</p>
    <p v-if="eligible" class="bound-source">{{ copy.binding(ready.source.source_name, ready.project_revision) }}</p>
    <p v-if="ensemble" class="derived-origin">{{ copy.derived }}</p>
    <div v-if="eligible" class="members">
      <fieldset v-for="(member, index) in members" :key="index" class="member" :data-member="index + 1">
        <legend>{{ copy.member(index + 1) }}</legend>
        <label :for="'ensemble-seed-' + (index + 1)">{{ copy.seed }}
          <input :id="'ensemble-seed-' + (index + 1)" v-model="seeds[index]" type="text" inputmode="numeric" maxlength="10"
            :disabled="locked || retained || !!ensemble" aria-describedby="ensemble-seed-help">
        </label>
        <template v-if="member">
          <dl class="preparation-reference">
            <dt>{{ copy.operation }}</dt><dd class="mono">{{ member.operation_id }}</dd>
            <dt>{{ copy.planDigest }}</dt><dd class="mono">{{ member.plan_sha256 }}</dd>
            <dt>{{ copy.artifactDigest }}</dt><dd class="mono artifact-reference">{{ member.receipt.artifact_sha256 }}</dd>
          </dl>
          <h3>{{ copy.native }}</h3>
          <p class="native-state">{{ launchState(member) }}</p>
          <dl class="launch-reference">
            <dt>{{ copy.run }}</dt><dd class="mono">{{ member.launch_id }}</dd>
            <template v-if="member.launch">
              <dt>{{ copy.launchDigest }}</dt><dd class="mono">{{ member.launch.launch_sha256 }}</dd>
              <template v-if="member.launch.ceiling_microusd">
                <dt>{{ copy.ceiling }}</dt><dd>{{ member.launch.ceiling_microusd }}</dd>
              </template>
              <template v-if="member.launch.receipt">
                <dt>{{ copy.evidenceDigest }}</dt><dd class="mono native-receipt">{{ member.launch.receipt.evidence_sha256 }}</dd>
              </template>
            </template>
          </dl>
          <p v-if="member.launch?.state === 'planned' && !member.attempted">
            {{ launchHint(index) }}
          </p>
          <div class="actions">
            <button v-if="!member.launch" type="button" class="ensemble-native-review" :disabled="locked" @click="act('plan', $event, index)">{{ copy.reviewNative }}</button>
            <button v-if="member.launch?.state === 'planned' && !member.attempted" type="button" class="ensemble-native-start primary" :disabled="locked || !ensembleCanStart(members, index)" @click="act('start', $event, index)">{{ copy.startNative }}</button>
            <button v-if="member.launch" type="button" class="ensemble-native-status" :disabled="locked" @click="act('status', $event, index)">{{ copy.refreshNative }}</button>
          </div>
        </template>
      </fieldset>
    </div>
    <p v-if="eligible" id="ensemble-seed-help" class="help">{{ copy.seedHelp }}</p>
    <div v-if="eligible" class="actions">
      <button v-if="!ensemble" type="button" class="ensemble-declare primary" :disabled="locked || !validPair" @click="act('declare', $event)">{{ retained ? copy.retry : copy.declare }}</button>
      <button v-if="active" type="button" class="ensemble-cancel" @click="methods.cancel()">{{ copy.cancel }}</button>
    </div>
  </section>
</template>

<style scoped>
.native-ensemble { min-width: 0; overflow-wrap: anywhere; color: var(--ink, #172638); }
.members { display: grid; gap: 16px; }
.member { border: 1px solid var(--border, #b9c4d2); border-radius: 8px; padding: 16px; min-width: 0; margin: 0; }
legend { padding-inline: 6px; font-weight: 650; }
label { display: flex; flex-direction: column; gap: 6px; font-weight: 550; }
input, button { min-height: 44px; max-width: 100%; padding: 10px 12px; border: 1px solid var(--control-border, #7b8797); border-radius: 5px; background: var(--surface, #fff); font: inherit; color: inherit; }
input { width: 100%; min-width: 0; }
button { color: var(--primary, #174d96); cursor: pointer; }
button:disabled, input:disabled { opacity: .65; cursor: default; }
.primary { background: var(--primary, #174d96); color: #fff; }
:where(button, input, .feedback):focus-visible { outline: 3px solid var(--primary, #174d96); outline-offset: 3px; }
h3 { margin: 16px 0 4px; font-size: 1rem; }
p, dl { margin: 8px 0; }
dt { font-weight: 550; }
dd { margin: 0 0 6px; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; }
.feedback:not(:empty) { border-inline-start: 3px solid var(--primary, #174d96); padding: 8px 12px; }
.help { color: var(--muted, #526174); }
.mono { font: .875rem/1.6 ui-monospace, Consolas, monospace; }
@media (min-width: 768px) { .members { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { newPreparationId, preparationIdentity, preparationInteger, preparationOptions, preparationSource, validatePreparationResult } from '../../api/simulationPreparation.js'
import { nativeLaunchIdentity, nativeReadyPreparation, newNativeLaunchId, validateNativeLaunchResult } from '../../api/nativeLaunch.js'
import { nativeEnsembleCopyFor } from '../../i18n/nativeEnsemble.js'

const props = defineProps({
  preparationMethods: { type: Object, required: true },
  nativeMethods: { type: Object, required: true },
  source: { type: Object, default: null },
  ready: { type: Object, default: null },
  displayGraphId: { type: String, default: '' },
  resetVersion: { type: Number, default: 0 },
  locale: { type: String, default: 'en' },
  connected: Boolean,
  busy: Boolean,
})

const copy = computed(() => nativeEnsembleCopyFor(props.locale))
const clone = value => JSON.parse(JSON.stringify(value))
const scopeFields = ['schema_version', 'workspace_id', 'project_id', 'graph_id', 'run_id', 'branch_id', 'layer']
const uncertainStart = new Set(['cancelled', 'deadline', 'timeout', 'transport_failure', 'invalid_reply', 'preparation_uncertain', 'native_launch_uncertain'])
const seeds = ref(['', ''])
const members = shallowRef([null, null])
const active = shallowRef(null)
const feedback = ref(null), announcement = ref(null)
let generation = 0

const eligible = computed(() => {
  const { connected, source, ready, displayGraphId } = props
  return !!(connected && source && ready?.schema_version === 1 && ready.state === 'ready' && ready.receipt &&
    ready.display_graph_id === displayGraphId && ready.scope?.project_id === source.project_id &&
    ready.source?.source_revision === source.source_revision && ready.source?.source_sha256 === source.text_sha256 &&
    ready.source?.source_name === source.source_name)
})
function invalidate() {
  generation++
  seeds.value = ['', '']
  members.value = [null, null]
  announcement.value = null
  props.nativeMethods.clear?.()
}
const scopeKey = computed(() => JSON.stringify([props.connected, props.displayGraphId, props.source, props.ready, props.resetVersion]))
watch(scopeKey, invalidate, { flush: 'sync' })
onBeforeUnmount(invalidate)

function readSeed(value) {
  try { return preparationInteger(value, 4294967295) } catch { return null }
}
const seedValues = computed(() => seeds.value.map(readSeed))
const validPair = computed(() => seedValues.value.every(seed => seed !== null) && seedValues.value[0] !== seedValues.value[1])
const locked = computed(() => !eligible.value || props.busy || active.value !== null)

function put(index, record) {
  const next = [...members.value]
  next[index] = Object.freeze(record)
  members.value = next
}
function stateLabel(record, stage, attempted = false) {
  if (!record) return copy.value.missing
  if (attempted && record.state === 'planned') return copy.value.startUnconfirmed
  if (stage === 'native' && record.state === 'completed' && record.receipt?.outcome !== 'completed') return copy.value.missing
  return copy.value.states[record.state]
}
function mayStart(record, stage) {
  if (!record) return false
  const plan = stage === 'prep' ? record.preparation : record.launch
  const attempted = stage === 'prep' ? record.prepAttempted : record.launchAttempted
  const ceiling = stage === 'prep' ? plan?.authorization.ceiling_microusd : plan?.ceiling_microusd
  return !attempted && plan?.state === 'planned' && plan.authorization.model_calls_enabled && !!ceiling
}
function messageFor(error, starting) {
  if (['unauthorized', 'origin_denied', 'disconnected'].includes(error?.code)) return 'denied'
  if (starting && uncertainStart.has(error?.code)) return 'uncertainStart'
  if (error?.code === 'not_found') return 'notFound'
  if (['budget_denied', 'model_calls_disabled'].includes(error?.code)) return 'notAuthorized'
  if (error?.code === 'scope_changed') return 'unsupported'
  return 'failedRequest'
}
async function act(index, stage, action, event) {
  if (locked.value) return
  const previous = members.value[index]
  const preparation = stage === 'prep'
  const planning = action === 'plan'
  const starting = action === 'start'
  const known = preparation ? previous?.preparation : previous?.launch
  if (planning && (known || (preparation ? !validPair.value : previous?.preparation?.state !== 'ready'))) return
  if (starting && !mayStart(previous, stage)) return
  if (action === 'status' && (!known || !(preparation ? previous.prepAttempted : previous.launchAttempted))) return

  const owner = { generation, scope: scopeKey.value }
  const trigger = event.currentTarget
  const graph = props.displayGraphId
  const base = clone(props.ready)
  const source = clone(props.source)
  let record = previous ?? { seed: seedValues.value[index] }
  const current = () => active.value === owner && generation === owner.generation && scopeKey.value === owner.scope && eligible.value &&
    members.value[index] === record
  active.value = owner
  announcement.value = null
  put(index, { ...record, notice: '' })
  record = members.value[index]
  try {
    if (planning && preparation) {
      const reviewed = await nativeReadyPreparation(base, graph).catch(() => { throw { code: 'scope_changed' } })
      if (!current()) return
      const metadata = preparationSource(source)
      const payload = record.prepDraft ?? {
        schema_version: 1,
        operation_id: newPreparationId(),
        source_revision: metadata.source_revision,
        options: preparationOptions({ ...reviewed.options, seed: record.seed }),
      }
      put(index, { ...record, prepDraft: clone(payload) })
      record = members.value[index]
      const reply = await props.preparationMethods.plan(clone(payload), metadata)
      if (!current()) return
      const admitted = await validatePreparationResult(reply, { graph, payload, source, planning: true })
      if (!current()) return
      if (admitted.project_revision !== base.project_revision || scopeFields.some(key => admitted.scope[key] !== base.scope[key])) {
        throw { code: 'scope_changed' }
      }
      put(index, { ...record, preparation: admitted })
    } else if (planning) {
      const ready = await nativeReadyPreparation(clone(record.preparation), graph)
      if (!current()) return
      const payload = record.launchDraft ?? {
        schema_version: 1,
        launch_id: newNativeLaunchId(),
        preparation: { operation_id: ready.operation_id, plan_sha256: ready.plan_sha256 },
      }
      put(index, { ...record, launchDraft: clone(payload), launchPreparation: ready })
      record = members.value[index]
      const reply = await props.nativeMethods.plan(clone(payload), clone(ready))
      if (!current()) return
      const admitted = await validateNativeLaunchResult(reply, { graph, payload, preparation: ready, planning: true })
      if (!current()) return
      put(index, { ...record, launch: admitted })
    } else {
      const snapshot = clone(known)
      const ready = preparation ? null : clone(record.launchPreparation)
      const selector = preparation ? preparationIdentity(snapshot) : nativeLaunchIdentity(snapshot)
      if (starting) {
        put(index, { ...record, [preparation ? 'prepAttempted' : 'launchAttempted']: true })
        record = members.value[index]
      }
      const methods = preparation ? props.preparationMethods : props.nativeMethods
      const reply = await methods[action](clone(selector))
      if (!current()) return
      const admitted = preparation
        ? await validatePreparationResult(reply, { graph, payload: selector, known: snapshot })
        : await validateNativeLaunchResult(reply, { graph, payload: selector, preparation: ready, known: snapshot })
      if (!current()) return
      put(index, { ...record, [preparation ? 'preparation' : 'launch']: admitted })
    }
    record = members.value[index]
    announcement.value = { index, stage }
  } catch (error) {
    if (!current()) return
    const notice = messageFor(error, starting)
    if (notice === 'denied' || notice === 'unsupported') {
      invalidate()
      announcement.value = { notice }
      return
    }
    const notDispatched = starting && error?.code === 'busy' && error.beforeDispatch === true
    put(index, { ...record, ...(notDispatched ? { [preparation ? 'prepAttempted' : 'launchAttempted']: false } : {}), notice })
    record = members.value[index]
    announcement.value = { index, stage }
  } finally {
    if (active.value === owner) active.value = null
    await nextTick()
    if (generation === owner.generation && !active.value && announcement.value?.index === index &&
        (document.activeElement === trigger || document.activeElement === document.body)) feedback.value?.focus()
  }
}
const statusText = computed(() => {
  if (!eligible.value) return copy.value.unsupported
  if (active.value) return copy.value.pending
  if (!announcement.value) return ''
  if (announcement.value.notice) return copy.value[announcement.value.notice]
  const { index, stage } = announcement.value
  const member = members.value[index]
  if (!member) return ''
  const status = member.notice ? copy.value[member.notice] : stateLabel(stage === 'prep' ? member.preparation : member.launch,
    stage, stage === 'prep' ? member.prepAttempted : member.launchAttempted)
  return copy.value.memberStatus(member.seed, status)
})
</script>

<template>
  <section class="native-ensemble" :lang="locale" :aria-label="copy.title">
    <p>{{ copy.instructions }}</p>
    <p ref="feedback" class="feedback" role="status" aria-live="polite" aria-atomic="true" tabindex="-1">{{ statusText }}</p>
    <p v-if="eligible" class="bound-source">{{ copy.binding(ready.source.source_name, ready.project_revision) }}</p>
    <div v-if="eligible" class="members">
      <fieldset v-for="index in [0, 1]" :key="index" class="member" :data-member="index + 1">
        <legend>{{ copy.member(index + 1) }}</legend>
        <label :for="'ensemble-seed-' + (index + 1)">{{ copy.seed }}
          <input :id="'ensemble-seed-' + (index + 1)" v-model="seeds[index]" type="text" inputmode="numeric" maxlength="10"
            :disabled="locked || !!members[index]" aria-describedby="ensemble-seed-help">
        </label>
        <h3>{{ copy.preparation }}</h3>
        <p class="preparation-state">{{ stateLabel(members[index]?.preparation, 'prep', members[index]?.prepAttempted) }}</p>
        <dl v-if="members[index]?.preparation" class="preparation-reference">
          <dt>{{ copy.operation }}</dt><dd class="mono">{{ members[index].preparation.operation_id }}</dd>
          <dt>{{ copy.planDigest }}</dt><dd class="mono">{{ members[index].preparation.plan_sha256 }}</dd>
          <template v-if="members[index].preparation.authorization.ceiling_microusd">
            <dt>{{ copy.ceiling }}</dt><dd>{{ members[index].preparation.authorization.ceiling_microusd }}</dd>
          </template>
          <template v-if="members[index].preparation.receipt">
            <dt>{{ copy.artifactDigest }}</dt><dd class="mono artifact-reference">{{ members[index].preparation.receipt.artifact_sha256 }}</dd>
          </template>
        </dl>
        <p v-if="members[index]?.preparation?.state === 'planned' && !members[index]?.prepAttempted && !mayStart(members[index], 'prep')">{{ copy.notAuthorized }}</p>
        <div class="actions">
          <button v-if="!members[index]?.preparation" type="button" class="ensemble-prep-review" :disabled="locked || !validPair" @click="act(index, 'prep', 'plan', $event)">{{ copy.reviewPreparation }}</button>
          <button v-if="mayStart(members[index], 'prep')" type="button" class="ensemble-prep-start primary" :disabled="locked" @click="act(index, 'prep', 'start', $event)">{{ copy.startPreparation }}</button>
          <button v-if="members[index]?.prepAttempted" type="button" class="ensemble-prep-status" :disabled="locked" @click="act(index, 'prep', 'status', $event)">{{ copy.refreshPreparation }}</button>
        </div>
        <h3>{{ copy.native }}</h3>
        <p class="native-state">{{ stateLabel(members[index]?.launch, 'native', members[index]?.launchAttempted) }}</p>
        <dl v-if="members[index]?.launch" class="launch-reference">
          <dt>{{ copy.run }}</dt><dd class="mono">{{ members[index].launch.request.run_id }}</dd>
          <dt>{{ copy.launchDigest }}</dt><dd class="mono">{{ members[index].launch.launch_sha256 }}</dd>
          <template v-if="members[index].launch.ceiling_microusd">
            <dt>{{ copy.ceiling }}</dt><dd>{{ members[index].launch.ceiling_microusd }}</dd>
          </template>
          <template v-if="members[index].launch.receipt">
            <dt>{{ copy.evidenceDigest }}</dt><dd class="mono native-receipt">{{ members[index].launch.receipt.evidence_sha256 }}</dd>
          </template>
        </dl>
        <p v-if="members[index]?.launch?.state === 'planned' && !members[index]?.launchAttempted && !mayStart(members[index], 'native')">{{ copy.notAuthorized }}</p>
        <div class="actions">
          <button v-if="members[index]?.preparation?.state === 'ready' && !members[index]?.launch" type="button" class="ensemble-native-review" :disabled="locked" @click="act(index, 'native', 'plan', $event)">{{ copy.reviewNative }}</button>
          <button v-if="mayStart(members[index], 'native')" type="button" class="ensemble-native-start primary" :disabled="locked" @click="act(index, 'native', 'start', $event)">{{ copy.startNative }}</button>
          <button v-if="members[index]?.launchAttempted" type="button" class="ensemble-native-status" :disabled="locked" @click="act(index, 'native', 'status', $event)">{{ copy.refreshNative }}</button>
        </div>
        <p v-if="members[index]?.notice" class="member-notice">{{ copy[members[index].notice] }}</p>
      </fieldset>
    </div>
    <p v-if="eligible" id="ensemble-seed-help" class="help">{{ copy.seedHelp }}</p>
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
